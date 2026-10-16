import logging
from pathlib import Path
from typing import Any, Dict, List

import httpx
from bs4 import BeautifulSoup

from app.config import DOCUMENTS_DIR
from app.scrapers.california_burrito import scrape_california_burrito

logger = logging.getLogger(__name__)

OFFICIAL_SOURCES = [
    {
        "filename": "mcdonalds_india_west_south_nutrition.pdf",
        "restaurant": "McDonald's India (West & South)",
        "url": "https://assets.mcdelivery.co.in/docs/DownloadNutritionInfo.pdf",
        "description": "Official McDonald's India (West & South) Nutritional & Allergen Information PDF",
    },
    {
        "filename": "mcdonalds_india_north_east_nutrition.pdf",
        "restaurant": "McDonald's India (North & East)",
        "url": "https://mcdindia.com/wp-content/uploads/2025/01/all-product-nutritional-information_cprl_main-store.pdf",
        "description": "Official McDonald's India (North & East CPRL) 79-Page Product Nutritional Booklet PDF",
    },
    {
        "filename": "subway_india_nutrition_august_2026.pdf",
        "restaurant": "Subway India",
        "url": "https://qnet.blob.core.windows.net/central/Nutritional%20Allergen%20Chart%20-%20August%202026%20-%201786080088269.pdf",
        "description": "Official Subway India Product Nutritional & Allergen Chart (August 2026) PDF",
    },
]


def seed_initial_restaurant_data(
    output_dir: Path = DOCUMENTS_DIR, force_download: bool = False
) -> Dict[str, Any]:
    """
    Download official publicly available nutrition PDFs for McDonald's India and Subway India,
    and scrape California Burrito India's live nutrition calculator.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[Dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36"}

    with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        for src in OFFICIAL_SOURCES:
            target = output_dir / src["filename"]
            if target.exists() and target.stat().st_size > 10000 and not force_download:
                downloaded.append(
                    {
                        "filename": src["filename"],
                        "status": "cached",
                        "bytes": target.stat().st_size,
                        "url": src["url"],
                    }
                )
                continue

            resp = client.get(src["url"])
            resp.raise_for_status()
            target.write_bytes(resp.content)
            downloaded.append(
                {
                    "filename": src["filename"],
                    "status": "downloaded",
                    "bytes": len(resp.content),
                    "url": src["url"],
                }
            )

    cb_result = scrape_california_burrito(output_dir)
    return {
        "official_pdfs": downloaded,
        "california_burrito_scrape": cb_result,
    }


def fetch_custom_url_to_documents(
    url: str, custom_name: str = "", output_dir: Path = DOCUMENTS_DIR
) -> Dict[str, Any]:
    """
    Download a PDF, CSV, JSON, or scrape an HTML web page from any URL into `data/documents/`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if "californiaburrito.in" in url.lower():
        res = scrape_california_burrito(output_dir)
        return {"filename": res["csv_file"], "type": "California Burrito Live Scraper", "details": res}

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36"}
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").lower()

        url_path = Path(url.split("?")[0]).name
        if "pdf" in ctype or url_path.lower().endswith(".pdf"):
            fname = custom_name or url_path or "downloaded_document.pdf"
            if not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            target = output_dir / fname
            target.write_bytes(resp.content)
            return {"filename": fname, "type": "PDF", "bytes": len(resp.content)}
        elif "csv" in ctype or url_path.lower().endswith(".csv"):
            fname = custom_name or url_path or "downloaded_data.csv"
            if not fname.lower().endswith(".csv"):
                fname += ".csv"
            target = output_dir / fname
            target.write_bytes(resp.content)
            return {"filename": fname, "type": "CSV", "bytes": len(resp.content)}
        elif "json" in ctype or url_path.lower().endswith(".json"):
            fname = custom_name or url_path or "downloaded_data.json"
            if not fname.lower().endswith(".json"):
                fname += ".json"
            target = output_dir / fname
            target.write_bytes(resp.content)
            return {"filename": fname, "type": "JSON", "bytes": len(resp.content)}
        else:
            fname = custom_name or (url_path if url_path else "scraped_page.html")
            if not fname.lower().endswith((".html", ".htm", ".txt", ".md")):
                fname += ".html"
            target = output_dir / fname
            target.write_bytes(resp.content)
            return {"filename": fname, "type": "HTML", "bytes": len(resp.content)}
