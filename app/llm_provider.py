import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import (
    AVAILABLE_AGY_MODELS,
    AVAILABLE_OLLAMA_MODELS,
    SETTINGS,
)

logger = logging.getLogger(__name__)


def resolve_agy_binary() -> Optional[str]:
    """Locate the agy CLI binary across host and container paths."""
    candidates = [
        SETTINGS.agy_bin,
        shutil.which("agy"),
        "/home/ubuntu/.local/bin/agy",
        "/root/.local/bin/agy",
        "/usr/local/bin/agy",
    ]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def extract_json_object(text: str) -> Dict[str, Any]:
    """Robustly extract a JSON object from an LLM response string."""
    if not text:
        raise ValueError("Empty response from LLM")

    cleaned = text.strip()
    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Find outermost { ... }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


async def generate_with_agy(
    prompt: str,
    model: Optional[str] = None,
    json_schema: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 90.0,
) -> Tuple[str, str]:
    """
    Run a single-turn non-interactive prompt using the local `agy` CLI.
    Returns (response_text, model_used).
    """
    agy_bin = resolve_agy_binary()
    if not agy_bin:
        raise RuntimeError("`agy` CLI binary was not found on PATH or standard locations.")

    chosen_model = model or SETTINGS.agy_model
    cmd = [
        agy_bin,
        "-p",
        prompt,
        "--model",
        chosen_model,
        "--effort",
        "low",
        "--output-format",
        "json",
        "--disable-slash-commands",
    ]

    if json_schema:
        cmd.extend(["--json-schema", json.dumps(json_schema)])

    # Run inside an isolated empty directory so agy does not scan workspace files unnecessarily
    with tempfile.TemporaryDirectory(prefix="agy_rag_") as tmpdir:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmpdir,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"agy CLI timed out after {timeout_sec}s (model={chosen_model})")

    stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0 and not stdout_str:
        raise RuntimeError(
            f"agy CLI exited with code {proc.returncode}: {stderr_str or 'Unknown error'}"
        )

    # Parse agy's JSON wrapper output: {"conversation_id": ..., "status": "SUCCESS", "response": "..."}
    for line in reversed(stdout_str.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                wrapper = json.loads(line)
                if isinstance(wrapper, dict) and "response" in wrapper:
                    return str(wrapper["response"]).strip(), chosen_model
            except json.JSONDecodeError:
                pass

    return stdout_str, chosen_model


async def generate_with_ollama(
    prompt: str,
    model: Optional[str] = None,
    expect_json: bool = True,
    timeout_sec: float = 120.0,
) -> Tuple[str, str]:
    """
    Run a prompt using the local Ollama server.
    Returns (response_text, model_used).
    """
    chosen_model = model or SETTINGS.ollama_model
    base_urls = [
        SETTINGS.ollama_base_url.rstrip("/"),
        "http://127.0.0.1:11434",
        "http://host.docker.internal:11434",
    ]
    # Deduplicate while preserving order
    seen = set()
    unique_urls = [u for u in base_urls if not (u in seen or seen.add(u))]

    payload: Dict[str, Any] = {
        "model": chosen_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 8192,
        },
    }
    if expect_json:
        payload["format"] = "json"

    last_err = None
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        for base_url in unique_urls:
            try:
                resp = await client.post(f"{base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return str(data.get("response", "")).strip(), chosen_model
            except Exception as exc:
                last_err = exc
                continue

    raise RuntimeError(f"Failed to reach Ollama ({chosen_model}): {last_err}")


async def generate_structured_json(
    prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    json_schema: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Generate a structured JSON response using the requested provider ('agy' or 'ollama'),
    automatically falling back to the secondary provider if the primary fails.
    Returns (parsed_json, provider_used, model_used).
    """
    chosen_provider = (provider or SETTINGS.llm_provider or "agy").lower()

    if chosen_provider == "agy":
        try:
            raw_text, model_used = await generate_with_agy(
                prompt=prompt, model=model, json_schema=json_schema
            )
            return extract_json_object(raw_text), "agy", model_used
        except Exception as agy_err:
            logger.warning(f"agy generation failed ({agy_err}); attempting Ollama fallback...")
            raw_text, model_used = await generate_with_ollama(
                prompt=prompt, model=None, expect_json=True
            )
            return extract_json_object(raw_text), "ollama (fallback)", model_used
    else:
        try:
            raw_text, model_used = await generate_with_ollama(
                prompt=prompt, model=model, expect_json=True
            )
            return extract_json_object(raw_text), "ollama", model_used
        except Exception as ollama_err:
            logger.warning(f"Ollama generation failed ({ollama_err}); attempting agy fallback...")
            raw_text, model_used = await generate_with_agy(
                prompt=prompt, model=None, json_schema=json_schema
            )
            return extract_json_object(raw_text), "agy (fallback)", model_used


async def get_available_providers_status() -> Dict[str, Any]:
    """Return status and available models for both agy and Ollama."""
    agy_bin = resolve_agy_binary()
    ollama_models = list(AVAILABLE_OLLAMA_MODELS)
    ollama_online = False

    base_urls = [
        SETTINGS.ollama_base_url.rstrip("/"),
        "http://127.0.0.1:11434",
        "http://host.docker.internal:11434",
    ]
    async with httpx.AsyncClient(timeout=2.5) as client:
        for url in base_urls:
            try:
                r = await client.get(f"{url}/api/tags")
                if r.status_code == 200:
                    ollama_online = True
                    tags = r.json().get("models", [])
                    if tags:
                        ollama_models = [
                            {
                                "id": m["name"],
                                "label": f"{m['name']} ({round(m.get('size', 0) / 1e9, 1)} GB)",
                            }
                            for m in tags
                        ]
                    break
            except Exception:
                continue

    return {
        "active_provider": SETTINGS.llm_provider,
        "active_agy_model": SETTINGS.agy_model,
        "active_ollama_model": SETTINGS.ollama_model,
        "agy": {
            "available": agy_bin is not None,
            "binary_path": agy_bin,
            "models": AVAILABLE_AGY_MODELS,
        },
        "ollama": {
            "available": ollama_online,
            "base_url": SETTINGS.ollama_base_url,
            "models": ollama_models,
        },
    }
