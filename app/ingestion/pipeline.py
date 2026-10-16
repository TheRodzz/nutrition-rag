import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DOCUMENTS_DIR, SETTINGS
from app.ingestion.parsers import compute_sha256, parse_document
from app.models import (
    DocumentChunk,
    DocumentDetectionReport,
    StructuredMenuItem,
)
from app.rag.vector_store import NutritionVectorDB

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".csv", ".tsv", ".json", ".md", ".txt", ".html", ".htm"}


class IngestionPipeline:
    """
    Manages document parsing, automatic Tabular vs. Non-Tabular classification,
    and full Vector DB rebuilds whenever documents are added, modified, or deleted.
    """

    def __init__(self, vector_db: NutritionVectorDB, documents_dir: Path = DOCUMENTS_DIR) -> None:
        self.vector_db = vector_db
        self.documents_dir = documents_dir
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._last_directory_fingerprint: str = self._compute_directory_fingerprint()
        self.is_rebuilding: bool = False
        self.rebuild_history: List[Dict[str, Any]] = []
        # Cache parsed documents by (filename, sha256) so unchanged large PDFs don't have to re-run layout extraction during a vector DB rebuild
        self._parse_cache: Dict[str, Any] = {}
        self._hydrate_parse_cache_from_db()

    def _hydrate_parse_cache_from_db(self) -> None:
        if not self.vector_db.documents:
            return
        items_by_file: Dict[str, List[StructuredMenuItem]] = {}
        for it in self.vector_db.items:
            items_by_file.setdefault(it.source_file, []).append(it)
        chunks_by_file: Dict[str, List[DocumentChunk]] = {}
        for ch in self.vector_db.chunks:
            chunks_by_file.setdefault(ch.source_file, []).append(ch)
        for doc in self.vector_db.documents:
            cache_key = f"{doc.filename}:{doc.sha256}"
            self._parse_cache[cache_key] = (
                doc,
                items_by_file.get(doc.filename, []),
                chunks_by_file.get(doc.filename, []),
            )

    def _list_supported_files(self) -> List[Path]:
        files = [
            p
            for p in self.documents_dir.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith(".")
        ]
        files.sort(key=lambda p: p.name.lower())
        return files

    def _compute_directory_fingerprint(self) -> str:
        files = self._list_supported_files()
        h = hashlib.sha256()
        for f in files:
            st = f.stat()
            h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime_ns)}|".encode("utf-8"))
        return h.hexdigest()

    async def rebuild_all(self, trigger_reason: str = "Manual trigger") -> Dict[str, Any]:
        """
        Parse all documents in documents_dir (detecting tabular/non-tabular/hybrid modality)
        and rebuild the entire Vector DB from scratch.
        """
        async with self._lock:
            self.is_rebuilding = True
            t0 = time.perf_counter()
            try:
                files = self._list_supported_files()
                reports: List[DocumentDetectionReport] = []
                all_items: List[StructuredMenuItem] = []
                all_chunks: List[DocumentChunk] = []

                for f_path in files:
                    file_sha = compute_sha256(f_path)
                    cache_key = f"{f_path.name}:{file_sha}"
                    if cache_key in self._parse_cache:
                        rep, items, chunks = self._parse_cache[cache_key]
                    else:
                        rep, items, chunks = await asyncio.to_thread(parse_document, f_path)
                        self._parse_cache[cache_key] = (rep, items, chunks)

                    reports.append(rep)
                    all_items.extend(items)
                    all_chunks.extend(chunks)

                stats = await asyncio.to_thread(
                    self.vector_db.rebuild, reports, all_chunks, all_items
                )
                self._last_directory_fingerprint = self._compute_directory_fingerprint()
                total_ms = int((time.perf_counter() - t0) * 1000)

                event = {
                    "index_version": stats["index_version"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger_reason": trigger_reason,
                    "documents_count": stats["total_documents"],
                    "chunks_count": stats["total_chunks"],
                    "structured_items_count": stats["total_structured_items"],
                    "total_duration_ms": total_ms,
                }
                self.rebuild_history.insert(0, event)
                self.rebuild_history = self.rebuild_history[:25]
                logger.info(
                    f"Vector DB rebuilt (v{stats['index_version']}) in {total_ms}ms — "
                    f"reason='{trigger_reason}', docs={stats['total_documents']}, "
                    f"chunks={stats['total_chunks']}, items={stats['total_structured_items']}"
                )
                return {**stats, "trigger_reason": trigger_reason, "total_pipeline_ms": total_ms}
            finally:
                self.is_rebuilding = False

    async def watch_documents_loop(self) -> None:
        """Background task that watches `data/documents/` and automatically rebuilds the Vector DB when files change."""
        while True:
            await asyncio.sleep(SETTINGS.watch_interval_seconds)
            if not SETTINGS.auto_watch_documents or self.is_rebuilding:
                continue
            current_fp = self._compute_directory_fingerprint()
            if current_fp != self._last_directory_fingerprint:
                logger.info("Document folder change detected by watcher; rebuilding Vector DB...")
                try:
                    await self.rebuild_all(trigger_reason="File watcher detected document addition/change")
                except Exception as exc:
                    logger.error(f"Auto-rebuild failed: {exc}")
