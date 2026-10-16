# MacroRAG — High-Accuracy Restaurant Nutrition RAG

A self-hosted, Dockerized Retrieval-Augmented Generation (RAG) system and Web Application for querying exact restaurant meal macros (**Calories, Protein, Carbs, Fat, and optional Fiber**) with **Confidence Scores**, **Deterministic Arithmetic**, **Adaptive Tabular vs. Non-Tabular Document Processing**, and **Automatic Vector DB Rebuilds**.

## Key Features

1. **Pre-Loaded Indian Restaurant Datasets**:
   - **McDonald's India (West & South)**: Official McDelivery Nutritional & Allergen PDF (`mcdonalds_india_west_south_nutrition.pdf`).
   - **McDonald's India (North & East)**: Official CPRL 79-Page Product Nutritional Booklet PDF (`mcdonalds_india_north_east_nutrition.pdf`).
   - **Subway India**: Official August 2026 Product Nutritional & Allergen Chart PDF (`subway_india_nutrition_august_2026.pdf`).
   - **California Burrito India**: Live-scraped directly from `https://www.californiaburrito.in/nutrition` into both a structured dataset (`california_burrito_india_nutrition.csv`, 409 component & complete build rows) and a non-tabular narrative customization & allergen guide (`california_burrito_customization_and_allergen_guide.txt`).

2. **Automatic Tabular vs. Non-Tabular Modality Detection**:
   - Inspects every uploaded document (`.pdf`, `.csv`, `.tsv`, `.json`, `.md`, `.txt`, `.html`) and every PDF page to classify it as **`tabular`**, **`non_tabular` (prose)**, or **`hybrid`**.
   - **Tabular Strategy**: Preserves table headers (including rotated vertical headers and multi-page table continuations), extracts structured item rows, and generates header-injected self-contained chunks.
   - **Non-Tabular Strategy**: Applies semantic paragraph-aware sliding-window chunking with metadata headers and extracts inline macro mentions.

3. **Automatic Vector DB Rebuild on Every Document Addition**:
   - Rebuilds the Hybrid Vector DB (Dense SVD Embeddings + Word/Char N-Gram TF-IDF + BM25Okapi Lexical Index + Structured Catalog) immediately when files are uploaded, scraped via URL, deleted, or dropped into `data/documents/` (monitored by a 5-second background watcher).

4. **Maximum Accuracy & Calibrated Confidence Score (0–100%)**:
   - Locks matched items to their ground-truth table values and uses **deterministic Python math** to multiply quantities and sum multi-item orders (zero LLM arithmetic drift).
   - Displays per-item and overall confidence scores (0–100%) with source file/page citations and explicit assumptions for customizable items (e.g., Subway bread/sauces or California Burrito rice/beans/toppings).

5. **Configurable LLM Backend (`agy` CLI + Local `ollama`)**:
   - Uses `agy` (Antigravity CLI) with any configurable model (`gemini-3.8-flash-high`, `gemini-4-argon-high`, `claude-sonnet-5-5-high`, etc.) and supports local `ollama` (`qwen2.5:7b`, `qwen3.5:9b`, `gemma4:e4b`, etc.) switchable live from the Web UI header or `/api/settings`.

---

## Quick Start

### 1. Run with Docker Compose (Recommended)
```bash
docker compose up -d --build
```
The Web App and REST API will be served at **`http://localhost:8080`**.

### 2. Run Locally with Python Virtualenv
```bash
./.venv/bin/python scripts/fetch_initial_data.py
./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### 3. Run Automated Tests
```bash
./.venv/bin/pytest -v
```
