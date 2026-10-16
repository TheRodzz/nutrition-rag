import json
import logging
import pickle
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from app.config import INDEX_DIR
from app.models import (
    DocumentChunk,
    DocumentDetectionReport,
    StructuredMenuItem,
)

logger = logging.getLogger(__name__)

SYNONYM_MAP = {
    "bbq": "barbeque",
    "mc": "mcdonalds ",
    "sub": "subway ",
    "veggie": "vegetable veggie",
    "fries": "french fries",
    "coke": "coca cola coke",
    "cal": "calories kcal",
}


def _tokenize(text: str) -> List[str]:
    low = text.lower()
    for k, v in SYNONYM_MAP.items():
        low = re.sub(rf"\b{k}\b", f"{k} {v}", low)
    return re.findall(r"[a-z0-9]+", low)


def _normalize_item_name(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[®™©\*]", "", s)
    s = re.sub(r"\b(burger|sandwich|sub|regular|medium|large|small)\b", lambda m: m.group(0), s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return " ".join(s.split())


class NutritionVectorDB:
    """
    Vector Database & Hybrid Index that completely rebuilds whenever documents are added,
    modified, or removed. Combines:
    1. Dense SVD Embeddings + Word/Char N-Gram TF-IDF Vector Index
    2. BM25Okapi Lexical Index
    3. Structured Menu Item Catalog for deterministic macro verification
    """

    def __init__(self, index_dir: Path = INDEX_DIR) -> None:
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.index_dir / "index_state.json"
        self.matrix_file = self.index_dir / "vector_index.pkl"

        self.index_version: int = 0
        self.last_rebuilt_at: Optional[str] = None
        self.last_rebuild_duration_ms: int = 0
        self.documents: List[DocumentDetectionReport] = []
        self.chunks: List[DocumentChunk] = []
        self.items: List[StructuredMenuItem] = []

        self.word_vectorizer: Optional[TfidfVectorizer] = None
        self.char_vectorizer: Optional[TfidfVectorizer] = None
        self.svd: Optional[TruncatedSVD] = None
        self.word_matrix: Optional[ Any ] = None
        self.char_matrix: Optional[ Any ] = None
        self.dense_matrix: Optional[np.ndarray] = None
        self.bm25: Optional[BM25Okapi] = None

        self._load_if_exists()

    def _load_if_exists(self) -> None:
        if not self.state_file.exists() or not self.matrix_file.exists():
            return
        try:
            meta = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.index_version = meta.get("index_version", 0)
            self.last_rebuilt_at = meta.get("last_rebuilt_at")
            self.last_rebuild_duration_ms = meta.get("last_rebuild_duration_ms", 0)
            self.documents = [DocumentDetectionReport(**d) for d in meta.get("documents", [])]
            self.chunks = [DocumentChunk(**c) for c in meta.get("chunks", [])]
            self.items = [StructuredMenuItem(**i) for i in meta.get("items", [])]

            with open(self.matrix_file, "rb") as f:
                saved = pickle.load(f)
                self.word_vectorizer = saved["word_vectorizer"]
                self.char_vectorizer = saved["char_vectorizer"]
                self.svd = saved["svd"]
                self.word_matrix = saved["word_matrix"]
                self.char_matrix = saved["char_matrix"]
                self.dense_matrix = saved["dense_matrix"]
                self.bm25 = saved["bm25"]
            logger.info(
                f"Loaded Vector DB v{self.index_version} ({len(self.chunks)} chunks, {len(self.items)} structured items)"
            )
        except Exception as exc:
            logger.warning(f"Could not load existing vector index ({exc}); will rebuild.")

    def rebuild(
        self,
        documents: List[DocumentDetectionReport],
        chunks: List[DocumentChunk],
        items: List[StructuredMenuItem],
    ) -> Dict[str, Any]:
        """
        Completely rebuild the Vector DB, BM25 index, and Structured Catalog from scratch.
        Persists to disk and increments index_version.
        """
        t0 = time.perf_counter()
        self.documents = documents
        self.chunks = chunks
        self.items = items
        self.index_version += 1
        self.last_rebuilt_at = datetime.now(timezone.utc).isoformat()

        if chunks:
            corpus = [c.content for c in chunks]
            self.word_vectorizer = TfidfVectorizer(
                ngram_range=(1, 3),
                lowercase=True,
                sublinear_tf=True,
                max_features=25000,
            )
            self.word_matrix = self.word_vectorizer.fit_transform(corpus)

            self.char_vectorizer = TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(3, 5),
                lowercase=True,
                sublinear_tf=True,
                max_features=25000,
            )
            self.char_matrix = self.char_vectorizer.fit_transform(corpus)

            n_components = min(128, max(2, self.word_matrix.shape[0] - 1, self.word_matrix.shape[1] - 1))
            if self.word_matrix.shape[0] >= 4 and self.word_matrix.shape[1] >= 4:
                self.svd = TruncatedSVD(n_components=min(n_components, self.word_matrix.shape[0] - 1, self.word_matrix.shape[1] - 1), random_state=42)
                dense_raw = self.svd.fit_transform(self.word_matrix)
                self.dense_matrix = normalize(dense_raw)
            else:
                self.svd = None
                self.dense_matrix = normalize(self.word_matrix.toarray())

            tokenized_corpus = [_tokenize(c) for c in corpus]
            self.bm25 = BM25Okapi(tokenized_corpus)
        else:
            self.word_vectorizer = None
            self.char_vectorizer = None
            self.svd = None
            self.word_matrix = None
            self.char_matrix = None
            self.dense_matrix = None
            self.bm25 = None

        self.last_rebuild_duration_ms = int((time.perf_counter() - t0) * 1000)

        # Persist state
        meta_payload = {
            "index_version": self.index_version,
            "last_rebuilt_at": self.last_rebuilt_at,
            "last_rebuild_duration_ms": self.last_rebuild_duration_ms,
            "documents": [d.model_dump() for d in self.documents],
            "chunks": [c.model_dump() for c in self.chunks],
            "items": [i.model_dump() for i in self.items],
        }
        self.state_file.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

        with open(self.matrix_file, "wb") as f:
            pickle.dump(
                {
                    "word_vectorizer": self.word_vectorizer,
                    "char_vectorizer": self.char_vectorizer,
                    "svd": self.svd,
                    "word_matrix": self.word_matrix,
                    "char_matrix": self.char_matrix,
                    "dense_matrix": self.dense_matrix,
                    "bm25": self.bm25,
                },
                f,
            )

        return self.get_stats()

    def get_stats(self) -> Dict[str, Any]:
        tabular_docs = sum(1 for d in self.documents if d.detected_modality == "tabular")
        non_tabular_docs = sum(1 for d in self.documents if d.detected_modality == "non_tabular")
        hybrid_docs = sum(1 for d in self.documents if d.detected_modality == "hybrid")
        tabular_chunks = sum(1 for c in self.chunks if c.doc_modality == "tabular")
        non_tabular_chunks = sum(1 for c in self.chunks if c.doc_modality == "non_tabular")
        restaurants = sorted(list({d.restaurant_detected for d in self.documents}))

        return {
            "index_version": self.index_version,
            "last_rebuilt_at": self.last_rebuilt_at,
            "last_rebuild_duration_ms": self.last_rebuild_duration_ms,
            "total_documents": len(self.documents),
            "tabular_documents": tabular_docs,
            "non_tabular_documents": non_tabular_docs,
            "hybrid_documents": hybrid_docs,
            "total_chunks": len(self.chunks),
            "tabular_chunks": tabular_chunks,
            "non_tabular_chunks": non_tabular_chunks,
            "total_structured_items": len(self.items),
            "restaurants": restaurants,
        }

    def search_chunks(
        self,
        query: str,
        restaurant_filter: Optional[str] = None,
        top_k: int = 12,
    ) -> List[Tuple[DocumentChunk, float]]:
        """
        Perform Hybrid Vector + Character N-Gram + BM25 search over all indexed chunks.
        Returns [(DocumentChunk, score_0_to_1), ...].
        """
        if not self.chunks or self.word_vectorizer is None or self.word_matrix is None:
            return []

        q_word = self.word_vectorizer.transform([query])
        word_scores = (self.word_matrix @ q_word.T).toarray().ravel()

        if self.char_vectorizer is not None and self.char_matrix is not None:
            q_char = self.char_vectorizer.transform([query])
            char_scores = (self.char_matrix @ q_char.T).toarray().ravel()
        else:
            char_scores = np.zeros_like(word_scores)

        if self.svd is not None and self.dense_matrix is not None:
            q_dense = normalize(self.svd.transform(q_word))
            dense_scores = np.clip((self.dense_matrix @ q_dense.T).ravel(), 0.0, 1.0)
        else:
            dense_scores = word_scores

        if self.bm25 is not None:
            q_tokens = _tokenize(query)
            bm25_raw = np.array(self.bm25.get_scores(q_tokens), dtype=float)
            max_bm25 = float(bm25_raw.max()) if bm25_raw.size > 0 else 0.0
            bm25_scores = bm25_raw / max_bm25 if max_bm25 > 0 else np.zeros_like(word_scores)
        else:
            bm25_scores = np.zeros_like(word_scores)

        combined = (
            0.35 * word_scores
            + 0.25 * char_scores
            + 0.15 * dense_scores
            + 0.25 * bm25_scores
        )

        # Normalize top score curve into calibrated [0, 1] retrieval similarity
        scored: List[Tuple[DocumentChunk, float]] = []
        q_norm = _normalize_item_name(query)
        for idx, chunk in enumerate(self.chunks):
            if restaurant_filter and restaurant_filter.lower() not in chunk.restaurant.lower():
                continue
            base_s = float(combined[idx])
            # Boost chunks that contain exact query substrings or menu item names
            fuzzy_s = fuzz.partial_ratio(q_norm, _normalize_item_name(chunk.content[:350])) / 100.0
            final_s = min(0.99, round(0.55 * min(1.0, base_s * 1.65) + 0.45 * fuzzy_s, 4))
            scored.append((chunk, final_s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def search_structured_items(
        self,
        item_query: str,
        restaurant_filter: Optional[str] = None,
        top_k: int = 8,
    ) -> List[Tuple[StructuredMenuItem, float, List[str]]]:
        """
        Match a requested food item against the structured menu catalog, returning
        [(StructuredMenuItem, confidence_0_to_100, confidence_reasons), ...].
        """
        if not self.items:
            return []

        q_clean = _normalize_item_name(item_query)
        q_tokens = set(_tokenize(item_query)) - {"mcdonalds", "subway", "california", "burrito", "india", "with", "and", "the", "a", "an"}

        candidates: List[Tuple[StructuredMenuItem, float, List[str]]] = []
        for item in self.items:
            if restaurant_filter and restaurant_filter.lower() not in item.restaurant.lower():
                continue

            item_clean = _normalize_item_name(item.item_name)
            cat_clean = _normalize_item_name(item.category)
            full_clean = f"{item_clean} {cat_clean} {_normalize_item_name(item.restaurant)}"

            w_ratio = fuzz.WRatio(q_clean, item_clean)
            token_set = fuzz.token_set_ratio(q_clean, item_clean)
            token_sort = fuzz.token_sort_ratio(q_clean, item_clean)
            partial = fuzz.partial_ratio(q_clean, full_clean)

            item_tokens = set(_tokenize(item.item_name))
            overlap_ratio = (
                len(q_tokens & item_tokens) / max(len(q_tokens), 1)
                if q_tokens
                else 0.5
            )

            raw_score = (
                0.35 * w_ratio
                + 0.30 * token_set
                + 0.15 * token_sort
                + 0.20 * (overlap_ratio * 100.0)
            )

            # Bonus if exact normalized match
            reasons: List[str] = []
            if q_clean == item_clean:
                raw_score = max(raw_score, 98.5)
                reasons.append(f"Exact menu item name match ('{item.item_name}')")
            elif q_clean in item_clean or item_clean in q_clean:
                raw_score = max(raw_score, min(95.0, raw_score + 8.0))
                reasons.append(f"Direct substring match on menu item ('{item.item_name}')")
            elif overlap_ratio >= 0.75:
                reasons.append(f"High token overlap ({int(overlap_ratio*100)}%) with '{item.item_name}'")
            else:
                reasons.append(f"Fuzzy lexical similarity ({round(w_ratio, 1)}%) to '{item.item_name}'")

            if restaurant_filter and restaurant_filter.lower() in item.restaurant.lower():
                raw_score = min(99.0, raw_score + 3.0)
                reasons.append(f"Verified restaurant source ({item.restaurant})")

            reasons.append(
                f"Grounded in {item.doc_modality.replace('_', '-')} source {item.source_file} ({item.source_page_or_section})"
            )

            candidates.append((item, round(min(99.5, max(0.0, raw_score)), 1), reasons))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]
