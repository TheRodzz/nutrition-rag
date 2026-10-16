import os
from pathlib import Path
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DOCUMENTS_DIR = DATA_DIR / "documents"
INDEX_DIR = DATA_DIR / "index"

DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
INDEX_DIR.mkdir(parents=True, exist_ok=True)


class RuntimeSettings(BaseModel):
    llm_provider: str = os.getenv("LLM_PROVIDER", "agy")  # "agy" or "ollama"
    agy_bin: str = os.getenv("AGY_BIN", "/home/ubuntu/.local/bin/agy")
    agy_model: str = os.getenv("AGY_MODEL", "gemini-3.8-flash-high")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "fastembed")  # fastembed / TF-IDF+SVD / ollama
    auto_watch_documents: bool = os.getenv("AUTO_WATCH_DOCUMENTS", "true").lower() == "true"
    watch_interval_seconds: int = int(os.getenv("WATCH_INTERVAL_SECONDS", "5"))


# Global mutable runtime config so users can switch providers/models live from the UI or API
SETTINGS = RuntimeSettings()

AVAILABLE_AGY_MODELS = [
    {"id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High) — Fast & Accurate"},
    {"id": "gemini-4-argon-high", "label": "Gemini 4 Argon (High) — Max Reasoning"},
    {"id": "gemini-4-argon-medium", "label": "Gemini 4 Argon (Medium)"},
    {"id": "gemini-3.8-flash-medium", "label": "Gemini 3.8 Flash (Medium)"},
    {"id": "gemini-3.8-flash-low", "label": "Gemini 3.8 Flash (Low)"},
    {"id": "claude-sonnet-5-5-high", "label": "Claude Sonnet 5.5 (High)"},
    {"id": "claude-sonnet-5-5-medium", "label": "Claude Sonnet 5.5 (Medium)"},
    {"id": "claude-opus-5-5-high", "label": "Claude Opus 5.5 (High)"},
    {"id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)"},
]

AVAILABLE_OLLAMA_MODELS = [
    {"id": "qwen2.5:7b", "label": "Qwen 2.5 7B (Local Ollama)"},
    {"id": "qwen3.5:9b", "label": "Qwen 3.5 9B (Local Ollama)"},
    {"id": "gemma4:e4b", "label": "Gemma 4 E4B (Local Ollama)"},
    {"id": "qwen2.5:3b", "label": "Qwen 2.5 3B (Local Ollama - Fast)"},
    {"id": "qwen2.5:1.5b", "label": "Qwen 2.5 1.5B (Local Ollama - Ultra Fast)"},
]
