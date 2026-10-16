import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DOCUMENTS_DIR
from app.ingestion.parsers import parse_document
from app.ingestion.pipeline import IngestionPipeline
from app.models import QueryRequest
from app.rag.engine import NutritionRAGEngine
from app.rag.vector_store import NutritionVectorDB


def test_tabular_vs_non_tabular_detection():
    """Verify automatic detection of Tabular, Non-Tabular (Prose), and Hybrid documents."""
    cb_csv = DOCUMENTS_DIR / "california_burrito_india_nutrition.csv"
    cb_guide = DOCUMENTS_DIR / "california_burrito_customization_and_allergen_guide.txt"
    mcd_ne_pdf = DOCUMENTS_DIR / "mcdonalds_india_north_east_nutrition.pdf"
    subway_pdf = DOCUMENTS_DIR / "subway_india_nutrition_august_2026.pdf"

    rep_csv, items_csv, _ = parse_document(cb_csv)
    assert rep_csv.detected_modality == "tabular"
    assert rep_csv.tabular_confidence >= 0.95
    assert len(items_csv) >= 350

    rep_guide, _, chunks_guide = parse_document(cb_guide)
    assert rep_guide.detected_modality == "non_tabular"
    assert rep_guide.tabular_confidence < 0.45
    assert all(c.doc_modality == "non_tabular" for c in chunks_guide)

    rep_mcd, items_mcd, _ = parse_document(mcd_ne_pdf)
    assert rep_mcd.detected_modality in ("tabular", "hybrid")
    assert len(items_mcd) >= 70

    rep_sub, items_sub, _ = parse_document(subway_pdf)
    assert rep_sub.detected_modality in ("tabular", "hybrid")
    assert len(items_sub) >= 150


@pytest.mark.asyncio
async def test_vector_db_rebuild_on_new_document():
    """Verify that adding and removing a document completely rebuilds the Vector DB and increments index_version."""
    vdb = NutritionVectorDB()
    pipe = IngestionPipeline(vector_db=vdb, documents_dir=DOCUMENTS_DIR)

    initial_version = vdb.index_version
    initial_docs_count = len(vdb.documents)

    # Create a temporary non-tabular prose document with an inline dish macro profile
    temp_doc = DOCUMENTS_DIR / "test_custom_restaurant_notes.txt"
    temp_doc.write_text(
        "SPECIALTY CAFE INDIA — CHEF NARRATIVE NOTES\n\n"
        "Our signature Smoked Tofu Protein Wrap is prepared fresh every morning on whole-wheat lavash. "
        "Smoked Tofu Protein Wrap (410 kcal, 28.5g protein, 14.0g fat, 42.0g carbs, 6.5g fiber) is vegan and nut-free.\n"
        "We also serve an Artisan Cold Brew Tonic which contains zero fat and minimal calories.",
        encoding="utf-8",
    )

    try:
        stats_after_add = await pipe.rebuild_all(trigger_reason="Test adding new non-tabular document")
        assert stats_after_add["index_version"] == initial_version + 1
        assert stats_after_add["total_documents"] == initial_docs_count + 1

        # Verify the new document was classified as non_tabular and its item is searchable
        doc_reports = {d.filename: d for d in vdb.documents}
        assert "test_custom_restaurant_notes.txt" in doc_reports
        assert doc_reports["test_custom_restaurant_notes.txt"].detected_modality == "non_tabular"

        matches = vdb.search_structured_items("Smoked Tofu Protein Wrap", top_k=3)
        assert matches
        top_item, score, _ = matches[0]
        assert "Smoked Tofu Protein Wrap" in top_item.item_name
        assert top_item.calories_kcal == 410.0
        assert top_item.protein_g == 28.5
        assert top_item.fiber_g == 6.5
        assert score >= 85.0
    finally:
        if temp_doc.exists():
            temp_doc.unlink()
        stats_restored = await pipe.rebuild_all(trigger_reason="Test cleanup restore")
        assert stats_restored["total_documents"] == initial_docs_count


@pytest.mark.asyncio
async def test_rag_end_to_end_accuracy_and_confidence():
    """Verify end-to-end RAG query accuracy, deterministic macro summation, and confidence scoring."""
    vdb = NutritionVectorDB()
    engine = NutritionRAGEngine(vector_db=vdb)

    req = QueryRequest(
        query="1 McSpicy Paneer Burger and 1 McAloo Tikki Burger from McDonald's India",
        restaurant_filter="McDonald's India",
    )
    resp = await engine.query(req)

    assert resp.overall_confidence_score >= 80.0
    assert resp.overall_confidence_level in ("High", "Medium")
    assert len(resp.items) >= 2
    assert len(resp.citations) > 0

    # Verify deterministic math: total_macros must equal the exact sum of item total_macros
    expected_cal = round(sum(i.total_macros.calories_kcal for i in resp.items), 2)
    expected_prot = round(sum(i.total_macros.protein_g for i in resp.items), 2)
    expected_carb = round(sum(i.total_macros.carbs_g for i in resp.items), 2)
    expected_fat = round(sum(i.total_macros.fat_g for i in resp.items), 2)

    assert resp.total_macros.calories_kcal == expected_cal
    assert resp.total_macros.protein_g == expected_prot
    assert resp.total_macros.carbs_g == expected_carb
    assert resp.total_macros.fat_g == expected_fat
