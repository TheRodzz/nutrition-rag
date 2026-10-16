import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import DOCUMENTS_DIR, SETTINGS
from app.ingestion.pipeline import SUPPORTED_EXTENSIONS, IngestionPipeline
from app.llm_provider import get_available_providers_status
from app.models import QueryRequest, QueryResponse
from app.rag.engine import NutritionRAGEngine
from app.rag.vector_store import NutritionVectorDB
from app.scrapers.downloader import (
    fetch_custom_url_to_documents,
    seed_initial_restaurant_data,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

vector_db = NutritionVectorDB()
pipeline = IngestionPipeline(vector_db=vector_db, documents_dir=DOCUMENTS_DIR)
rag_engine = NutritionRAGEngine(vector_db=vector_db)


class ScrapeUrlRequest(BaseModel):
    url: str
    custom_name: Optional[str] = ""


class UpdateSettingsRequest(BaseModel):
    llm_provider: Optional[str] = None
    agy_model: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_base_url: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure initial documents exist; if documents folder is empty, seed McDonald's, Subway & California Burrito
    existing_docs = [
        p
        for p in DOCUMENTS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not existing_docs:
        logger.info("No documents found in data/documents; seeding McDonald's India, Subway India & California Burrito...")
        try:
            await asyncio.to_thread(seed_initial_restaurant_data, DOCUMENTS_DIR, False)
        except Exception as exc:
            logger.error(f"Initial seed warning: {exc}")

    # Rebuild vector DB on startup if not yet built or if documents changed
    if vector_db.index_version == 0 or len(vector_db.documents) != len(
        [p for p in DOCUMENTS_DIR.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    ):
        await pipeline.rebuild_all(trigger_reason="Application startup initialization")

    watcher_task = asyncio.create_task(pipeline.watch_documents_loop())
    try:
        yield
    finally:
        watcher_task.cancel()


app = FastAPI(
    title="Restaurant Nutrition Macro RAG",
    description=(
        "High-accuracy adaptive RAG for restaurant nutrition data (McDonald's India, Subway India, "
        "California Burrito India & custom documents) with automatic tabular/non-tabular detection, "
        "automatic Vector DB rebuilds, deterministic macro summation, and calibrated confidence scoring."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_ui():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return {"message": "Nutrition RAG API is running. UI files missing."}
    return FileResponse(str(index_path))


@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "is_rebuilding": pipeline.is_rebuilding,
        "index_version": vector_db.index_version,
        "last_rebuilt_at": vector_db.last_rebuilt_at,
        "documents_count": len(vector_db.documents),
        "chunks_count": len(vector_db.chunks),
        "structured_items_count": len(vector_db.items),
        "active_llm_provider": SETTINGS.llm_provider,
        "active_model": (
            SETTINGS.agy_model if SETTINGS.llm_provider == "agy" else SETTINGS.ollama_model
        ),
    }


@app.get("/api/stats")
async def get_stats() -> Dict[str, Any]:
    stats = vector_db.get_stats()
    return {
        **stats,
        "is_rebuilding": pipeline.is_rebuilding,
        "rebuild_history": pipeline.rebuild_history,
    }


@app.get("/api/documents")
async def list_documents() -> Dict[str, Any]:
    return {
        "index_version": vector_db.index_version,
        "last_rebuilt_at": vector_db.last_rebuilt_at,
        "documents": [d.model_dump() for d in vector_db.documents],
    }


@app.post("/api/documents/upload")
async def upload_documents(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    saved_files: List[str] = []
    for upload in files:
        if not upload.filename:
            continue
        safe_name = Path(upload.filename).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file extension '{suffix}'. Supported: {sorted(list(SUPPORTED_EXTENSIONS))}",
            )
        dest = DOCUMENTS_DIR / safe_name
        content = await upload.read()
        dest.write_bytes(content)
        saved_files.append(safe_name)

    if not saved_files:
        raise HTTPException(status_code=400, detail="No valid files uploaded.")

    rebuild_stats = await pipeline.rebuild_all(
        trigger_reason=f"Uploaded {len(saved_files)} document(s): {', '.join(saved_files)}"
    )
    uploaded_reports = [
        d.model_dump() for d in vector_db.documents if d.filename in saved_files
    ]
    return {
        "status": "rebuilt",
        "uploaded_files": saved_files,
        "detection_reports": uploaded_reports,
        "index_stats": rebuild_stats,
    }


@app.post("/api/documents/scrape-url")
async def scrape_url_endpoint(req: ScrapeUrlRequest) -> Dict[str, Any]:
    try:
        res = await asyncio.to_thread(
            fetch_custom_url_to_documents, req.url, req.custom_name or "", DOCUMENTS_DIR
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch/scrape URL: {exc}")

    rebuild_stats = await pipeline.rebuild_all(
        trigger_reason=f"Scraped/downloaded URL: {req.url}"
    )
    return {
        "status": "rebuilt",
        "scrape_result": res,
        "index_stats": rebuild_stats,
    }


@app.post("/api/documents/seed-defaults")
async def seed_defaults_endpoint(force: bool = False) -> Dict[str, Any]:
    try:
        seed_res = await asyncio.to_thread(seed_initial_restaurant_data, DOCUMENTS_DIR, force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to seed default restaurant data: {exc}")

    rebuild_stats = await pipeline.rebuild_all(
        trigger_reason="Seeded McDonald's India, Subway India & California Burrito India datasets"
    )
    return {
        "status": "rebuilt",
        "seed_result": seed_res,
        "index_stats": rebuild_stats,
    }


@app.delete("/api/documents/{filename}")
async def delete_document(filename: str) -> Dict[str, Any]:
    safe_name = Path(filename).name
    target = DOCUMENTS_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Document '{safe_name}' not found.")
    target.unlink()
    rebuild_stats = await pipeline.rebuild_all(
        trigger_reason=f"Deleted document: {safe_name}"
    )
    return {
        "status": "rebuilt",
        "deleted_file": safe_name,
        "index_stats": rebuild_stats,
    }


@app.post("/api/index/rebuild")
async def manual_rebuild_endpoint() -> Dict[str, Any]:
    rebuild_stats = await pipeline.rebuild_all(trigger_reason="Manual Vector DB rebuild requested")
    return {
        "status": "rebuilt",
        "index_stats": rebuild_stats,
    }


@app.get("/api/items")
async def search_catalog_items(
    q: str = Query(default="", description="Optional search query for menu items"),
    restaurant: Optional[str] = Query(default=None, description="Optional restaurant filter"),
    limit: int = Query(default=50, ge=1, le=250),
) -> Dict[str, Any]:
    if q.strip():
        matches = vector_db.search_structured_items(q, restaurant_filter=restaurant, top_k=limit)
        return {
            "total": len(matches),
            "items": [
                {**it.model_dump(), "match_confidence": sc, "match_reasons": reasons}
                for it, sc, reasons in matches
            ],
        }

    filtered = [
        it
        for it in vector_db.items
        if not restaurant or restaurant.lower() in it.restaurant.lower()
    ]
    return {
        "total": len(filtered),
        "items": [it.model_dump() for it in filtered[:limit]],
    }


@app.post("/api/query", response_model=QueryResponse)
async def query_macros_endpoint(req: QueryRequest) -> QueryResponse:
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    return await rag_engine.query(req)


@app.get("/api/settings")
async def get_settings_endpoint() -> Dict[str, Any]:
    return await get_available_providers_status()


@app.post("/api/settings")
async def update_settings_endpoint(req: UpdateSettingsRequest) -> Dict[str, Any]:
    if req.llm_provider and req.llm_provider.lower() in ("agy", "ollama"):
        SETTINGS.llm_provider = req.llm_provider.lower()
    if req.agy_model:
        SETTINGS.agy_model = req.agy_model.strip()
    if req.ollama_model:
        SETTINGS.ollama_model = req.ollama_model.strip()
    if req.ollama_base_url:
        SETTINGS.ollama_base_url = req.ollama_base_url.strip()
    return await get_available_providers_status()
