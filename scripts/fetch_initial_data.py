#!/usr/bin/env python3
"""
CLI utility to download publicly available nutrition PDFs for McDonald's India and Subway India,
scrape California Burrito India's live nutrition calculator, and build the initial Vector DB.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DOCUMENTS_DIR
from app.ingestion.pipeline import IngestionPipeline
from app.rag.vector_store import NutritionVectorDB
from app.scrapers.downloader import seed_initial_restaurant_data


async def main() -> None:
    print("1. Fetching McDonald's India PDFs, Subway India PDF, and scraping California Burrito India...")
    res = seed_initial_restaurant_data(DOCUMENTS_DIR, force_download=False)
    for pdf_info in res["official_pdfs"]:
        print(f"   - {pdf_info['filename']}: {pdf_info['status']} ({pdf_info['bytes']} bytes)")
    cb = res["california_burrito_scrape"]
    print(f"   - California Burrito scraped: {cb['rows_extracted']} rows ({cb['csv_file']}) + {cb['guide_file']}")

    print("\n2. Detecting document modalities (Tabular vs Non-Tabular) and rebuilding Vector DB...")
    vdb = NutritionVectorDB()
    pipe = IngestionPipeline(vector_db=vdb, documents_dir=DOCUMENTS_DIR)
    stats = await pipe.rebuild_all(trigger_reason="Initial CLI seed and Vector DB build")

    print("\n3. Vector DB Build Summary:")
    print(f"   - Index Version:          v{stats['index_version']}")
    print(f"   - Total Documents:        {stats['total_documents']} "
          f"(Tabular: {stats['tabular_documents']}, Non-Tabular: {stats['non_tabular_documents']}, Hybrid: {stats['hybrid_documents']})")
    print(f"   - Total Indexed Chunks:   {stats['total_chunks']} "
          f"(Tabular rows: {stats['tabular_chunks']}, Prose chunks: {stats['non_tabular_chunks']})")
    print(f"   - Structured Menu Items:  {stats['total_structured_items']}")
    print(f"   - Restaurants Indexed:    {', '.join(stats['restaurants'])}")
    print(f"   - Rebuild Duration:       {stats['total_pipeline_ms']} ms")


if __name__ == "__main__":
    asyncio.run(main())
