import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.ingestion.detector import (
    MACRO_KEYWORDS,
    MD_TABLE_ROW_PATTERN,
    NUMERIC_ROW_PATTERN,
    analyze_text_block_modality,
    detect_restaurant_name,
)
from app.models import (
    DocumentChunk,
    DocumentDetectionReport,
    StructuredMenuItem,
)

logger = logging.getLogger(__name__)

SKIP_ROW_PREFIXES = (
    "per serve percentage",
    "contribution in % to rda",
    "contribution to rda",
    "recommended dietary allowance",
    "value per serving",
    "allergens present in the product",
)


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _parse_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("-", "na", "n/a", "nil", "none", ""):
        return None
    if s.lower() in ("tr", "trace", "<0.5", "<1", "<0.1", "nd"):
        return 0.0
    m = re.search(r"(-?\d+(?:\.\d+)?)", s.replace(",", ""))
    if m:
        try:
            return round(float(m.group(1)), 2)
        except ValueError:
            return None
    return None


def _build_tabular_chunk_text(item: StructuredMenuItem, extra_notes: str = "") -> str:
    fiber_str = f", Dietary Fiber: {item.fiber_g}g" if item.fiber_g is not None else ""
    serv_str = (
        f" | Serving Size: {item.variant_or_size or item.serving_size}"
        if (item.serving_size or item.variant_or_size)
        else ""
    )
    allergen_str = f" | Allergens: {', '.join(item.allergens)}" if item.allergens else ""
    diet_str = f" | Diet: {', '.join(item.dietary_tags)}" if item.dietary_tags else ""
    notes_str = f" | Notes: {extra_notes}" if extra_notes else ""
    return (
        f"[Restaurant: {item.restaurant} | Category: {item.category} | "
        f"Source: {item.source_file} ({item.source_page_or_section}) | Modality: Tabular Row]\n"
        f"Menu Item: {item.item_name}{serv_str}\n"
        f"Macros — Calories: {item.calories_kcal} kcal, Protein: {item.protein_g}g, "
        f"Carbohydrates: {item.carbs_g}g, Total Fat: {item.fat_g}g{fiber_str}"
        f"{allergen_str}{diet_str}{notes_str}"
    )


def _chunk_prose_text(
    text: str,
    source_file: str,
    restaurant: str,
    page_or_section: str,
    chunk_size: int = 900,
    overlap: int = 150,
) -> List[DocumentChunk]:
    """Split non-tabular narrative text into semantic paragraph-aware chunks with metadata headers."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [ln.strip() for ln in text.splitlines() if ln.strip()]

    chunks: List[DocumentChunk] = []
    current_buf: List[str] = []
    current_len = 0
    idx = 0

    def flush_buf(buf_lines: List[str], chunk_idx: int) -> DocumentChunk:
        body = "\n".join(buf_lines).strip()
        header = (
            f"[Restaurant: {restaurant} | Source: {source_file} ({page_or_section}) | "
            f"Modality: Non-Tabular Prose]\n"
        )
        cid = hashlib.md5(
            f"{source_file}:{page_or_section}:prose:{chunk_idx}:{body[:80]}".encode()
        ).hexdigest()[:14]
        return DocumentChunk(
            chunk_id=f"chunk_{cid}",
            source_file=source_file,
            restaurant=restaurant,
            page_or_section=page_or_section,
            doc_modality="non_tabular",
            content=header + body,
            metadata={"chunk_index": chunk_idx, "raw_length": len(body)},
        )

    for para in paragraphs:
        if current_len + len(para) > chunk_size and current_buf:
            chunks.append(flush_buf(current_buf, idx))
            idx += 1
            tail: List[str] = []
            tail_len = 0
            for prev_p in reversed(current_buf):
                if tail_len + len(prev_p) <= overlap:
                    tail.insert(0, prev_p)
                    tail_len += len(prev_p)
                else:
                    break
            current_buf = tail
            current_len = tail_len

        current_buf.append(para)
        current_len += len(para) + 1

    if current_buf:
        chunks.append(flush_buf(current_buf, idx))

    return chunks


def _normalize_header_cell(raw_h: Any) -> str:
    """
    Normalize a table header cell. Also handles vertically rotated PDF headers
    where characters are extracted in reverse order (e.g., `)lack( seirolaC` -> `calories (kcal)`).
    """
    s = re.sub(r"\s+", " ", str(raw_h or "").strip().lower())
    rev = s[::-1]
    # If reversed string contains recognizable English nutrition keywords, include both
    if any(k in rev for k in ("calories", "protein", "carbohydrates", "total fat", "serving", "saturated", "sodium", "sugars", "gluten", "milk")):
        return f"{s} {rev}"
    return s


def _infer_column_mapping(headers: List[Any]) -> Dict[str, Any]:
    """Map table header names (including rotated/reversed PDF headers) to standard nutritional fields."""
    mapping: Dict[str, Any] = {"allergen_cols": {}}
    for idx, raw_h in enumerate(headers):
        h = _normalize_header_cell(raw_h)
        if not h:
            continue
        if (
            any(
                k in h
                for k in (
                    "item_name",
                    "item name",
                    "menu item",
                    "product",
                    "food item",
                    "items",
                    "dish",
                    "sub name",
                    "description",
                )
            )
            and "base_item" not in h
        ):
            mapping.setdefault("item_name", idx)
        elif h in ("name", "item", "menu"):
            mapping.setdefault("item_name", idx)
        elif any(k in h for k in ("category", "menu category", "section", "group")) and "component" not in h:
            mapping.setdefault("category", idx)
        elif any(k in h for k in ("serving", "serve size", "portion", "weight", "size_or_variant", "size")):
            mapping.setdefault("serving_size", idx)
        elif any(k in h for k in ("kcal", "calor", "energy")) and "kjoule" not in h and "(kj)" not in h:
            mapping.setdefault("calories", idx)
        elif "protein" in h:
            mapping.setdefault("protein", idx)
        elif ("carb" in h or re.search(r"\bcho\b", h)) and "cholesterol" not in h:
            mapping.setdefault("carbs", idx)
        elif (
            any(k in h for k in ("total fat", "fat (g)", "fat_g", "fats", "lipid")) or h == "fat"
        ) and "saturated" not in h and "sat fat" not in h and "trans" not in h:
            mapping.setdefault("fat", idx)
        elif any(k in h for k in ("fiber", "fibre", "dietary fiber", "dietary fibre")):
            mapping.setdefault("fiber", idx)
        elif "allergen" in h and len(headers) <= 16:
            mapping.setdefault("allergens", idx)
        elif any(k in h for k in ("diet", "veg", "vegan")):
            mapping.setdefault("diet", idx)
        elif any(k in h for k in ("restaurant", "company", "brand", "chain")):
            mapping.setdefault("restaurant", idx)
        elif "note" in h:
            mapping.setdefault("notes", idx)

        # Check individual allergen indicator columns (e.g. Subway / McDonald's allergen grids)
        for alg_key, alg_label in [
            ("gluten", "Gluten"),
            ("crustacean", "Crustacean"),
            ("milk", "Milk/Dairy"),
            ("egg", "Egg"),
            ("fish", "Fish"),
            ("nut", "Nuts"),
            ("soy", "Soy"),
            ("sulphite", "Sulphites"),
        ]:
            if alg_key in h:
                mapping["allergen_cols"][idx] = alg_label

    if "item_name" not in mapping and len(mapping) >= 4:
        mapping["item_name"] = 0

    return mapping


def _try_parse_transposed_kv_table(
    table: List[List[Any]],
    restaurant: str,
    source_file: str,
    page_label: str,
) -> Tuple[List[StructuredMenuItem], List[DocumentChunk]]:
    """
    Detect and parse vertical Key-Value product tables (such as McDonald's North & East 79-page booklet,
    where each page is a single product table with rows like `['McVeggie Burger', 'Serve Size (g)', '168 g']`,
    `[None, 'Energy (kCal)', '387.88']`, `[None, 'Protein (g)', '10.24']`, etc.).
    """
    cleaned_rows = [
        [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
        for row in table
        if row and any(str(c or "").strip() for c in row)
    ]
    if len(cleaned_rows) < 5 or len(cleaned_rows) > 45:
        return [], []

    item_name = ""
    serving_size: Optional[str] = None
    cal: Optional[float] = None
    prot: Optional[float] = None
    carb: Optional[float] = None
    fat: Optional[float] = None
    fib: Optional[float] = None
    allergens: List[str] = []

    kv_hits = 0
    for row in cleaned_rows:
        row_join = " ".join(c for c in row if c).lower()
        # Look for product name in column 0 when column 1 is a metric label
        if len(row) >= 3:
            c0, c1, c2 = row[0], row[1].lower(), row[2]
            if c0 and c0.lower() not in ("menu", "allergen contains", "recommended dietary allowance (rda)"):
                item_name = c0
            if "serve size" in c1 or "serving size" in c1:
                serving_size = c2
                kv_hits += 1
            elif "energy" in c1 or "kcal" in c1 or "calor" in c1:
                cal = _parse_float(c2)
                kv_hits += 1
            elif "protein" in c1:
                prot = _parse_float(c2)
                kv_hits += 1
            elif "total fat" in c1 or c1 == "fat (g)":
                fat = _parse_float(c2)
                kv_hits += 1
            elif "carbohydrate" in c1 or "carbs" in c1:
                carb = _parse_float(c2)
                kv_hits += 1
            elif "fiber" in c1 or "fibre" in c1:
                fib = _parse_float(c2)
                kv_hits += 1
        if len(row) >= 2 and "allergen contains" in row[0].lower() and row[1]:
            allergens = [a.strip() for a in row[1].split(",") if a.strip()]
        elif "total sugars" in row_join and carb is not None:
            pass

    if kv_hits < 3 or not item_name or cal is None:
        return [], []

    item_id = hashlib.md5(f"{source_file}:{page_label}:kv:{item_name}".encode()).hexdigest()[:14]
    s_item = StructuredMenuItem(
        item_id=f"item_{item_id}",
        restaurant=restaurant,
        category="Booklet Product Sheet",
        item_name=item_name,
        variant_or_size=serving_size,
        calories_kcal=cal or 0.0,
        protein_g=prot or 0.0,
        carbs_g=carb or 0.0,
        fat_g=fat or 0.0,
        fiber_g=fib,
        serving_size=serving_size,
        allergens=allergens,
        source_file=source_file,
        source_page_or_section=page_label,
        doc_modality="tabular",
        raw_text=f"{item_name} | Serve Size: {serving_size} | {cal} kcal | P: {prot}g | C: {carb}g | F: {fat}g",
    )
    chunk = DocumentChunk(
        chunk_id=f"chunk_{item_id}",
        source_file=source_file,
        restaurant=restaurant,
        page_or_section=page_label,
        doc_modality="tabular",
        content=_build_tabular_chunk_text(s_item),
        metadata={"item_id": s_item.item_id, "item_name": s_item.item_name, "category": s_item.category},
    )
    return [s_item], [chunk]


def _extract_items_from_table_grid(
    table: List[List[Any]],
    default_restaurant: str,
    source_file: str,
    page_label: str,
    inherited_mapping: Optional[Dict[str, Any]] = None,
    inherited_col_count: Optional[int] = None,
    initial_category: str = "Menu",
) -> Tuple[List[StructuredMenuItem], List[DocumentChunk], Optional[Dict[str, Any]], Optional[int], str]:
    """
    Extract structured menu items and chunks from a 2D table grid.
    Supports header carry-over across multi-page PDF tables.
    """
    # First check if this is a single-product transposed Key-Value table
    kv_items, kv_chunks = _try_parse_transposed_kv_table(table, default_restaurant, source_file, page_label)
    if kv_items:
        return kv_items, kv_chunks, inherited_mapping, inherited_col_count, initial_category

    cleaned_rows = [
        [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
        for row in table
        if row and any(str(c or "").strip() for c in row)
    ]
    if len(cleaned_rows) < 2:
        return [], [], inherited_mapping, inherited_col_count, initial_category

    best_header_idx = -1
    best_mapping: Dict[str, Any] = {}
    for r_idx in range(min(5, len(cleaned_rows))):
        candidate_map = _infer_column_mapping(cleaned_rows[r_idx])
        # Count core macro keys found
        core_keys = [k for k in ("calories", "protein", "carbs", "fat") if k in candidate_map]
        best_core = [k for k in ("calories", "protein", "carbs", "fat") if k in best_mapping]
        if len(core_keys) > len(best_core):
            best_mapping = candidate_map
            best_header_idx = r_idx

    col_count = len(cleaned_rows[0])
    core_found = [k for k in ("calories", "protein", "carbs", "fat") if k in best_mapping]
    if len(core_found) < 3 and inherited_mapping and inherited_col_count == col_count:
        best_mapping = inherited_mapping
        best_header_idx = -1
    elif len(core_found) >= 3:
        inherited_mapping = best_mapping
        inherited_col_count = col_count

    items: List[StructuredMenuItem] = []
    chunks: List[DocumentChunk] = []
    current_category = initial_category

    for r_idx, row in enumerate(cleaned_rows):
        if best_header_idx >= 0 and r_idx <= best_header_idx:
            continue

        non_empty_cells = [c for c in row if c]
        if not non_empty_cells:
            continue

        first_non_empty = non_empty_cells[0]
        if any(first_non_empty.lower().startswith(p) for p in SKIP_ROW_PREFIXES):
            continue

        # Detect category banner rows inside tables (e.g. "REGULAR MENU", "Subs (6-inch)- Nutritional values...")
        if len(non_empty_cells) == 1 and len(first_non_empty) < 140:
            # Extract clean category title before any dash/explanation
            cat_title = first_non_empty.split("-")[0].strip()
            if cat_title and not any(cat_title.lower().startswith(p) for p in SKIP_ROW_PREFIXES):
                current_category = cat_title
            continue

        if "calories" in best_mapping and len([k for k in ("calories", "protein", "carbs", "fat") if k in best_mapping]) >= 2:
            name_idx = best_mapping.get("item_name", 0)
            if name_idx >= len(row):
                continue
            item_name = row[name_idx].strip()
            if not item_name or any(item_name.lower().startswith(p) for p in SKIP_ROW_PREFIXES):
                continue
            if item_name.lower() in ("item", "item name", "product", "menu item", "menu items", "total"):
                continue

            cal = _parse_float(row[best_mapping["calories"]]) if best_mapping["calories"] < len(row) else None
            prot = _parse_float(row[best_mapping["protein"]]) if "protein" in best_mapping and best_mapping["protein"] < len(row) else None
            carb = _parse_float(row[best_mapping["carbs"]]) if "carbs" in best_mapping and best_mapping["carbs"] < len(row) else None
            fat = _parse_float(row[best_mapping["fat"]]) if "fat" in best_mapping and best_mapping["fat"] < len(row) else None
            fib = _parse_float(row[best_mapping["fiber"]]) if "fiber" in best_mapping and best_mapping["fiber"] < len(row) else None
            serv = row[best_mapping["serving_size"]] if "serving_size" in best_mapping and best_mapping["serving_size"] < len(row) else None
            if serv and serv.replace(".", "").isdigit():
                serv = f"{serv}g"

            cat = (
                row[best_mapping["category"]]
                if "category" in best_mapping and best_mapping["category"] < len(row) and row[best_mapping["category"]]
                else current_category
            )
            rest = (
                row[best_mapping["restaurant"]]
                if "restaurant" in best_mapping and best_mapping["restaurant"] < len(row) and row[best_mapping["restaurant"]]
                else default_restaurant
            )
            notes = row[best_mapping["notes"]] if "notes" in best_mapping and best_mapping["notes"] < len(row) else ""
            allergens_raw = row[best_mapping["allergens"]] if "allergens" in best_mapping and best_mapping["allergens"] < len(row) else ""
            diet_raw = row[best_mapping["diet"]] if "diet" in best_mapping and best_mapping["diet"] < len(row) else ""

            if cal is None and prot is None and carb is None and fat is None:
                continue

            allergens = [
                a.strip()
                for a in allergens_raw.split(",")
                if a.strip() and a.strip().lower() not in ("none declared", "no", "-")
            ]
            for alg_col_idx, alg_name in best_mapping.get("allergen_cols", {}).items():
                if alg_col_idx < len(row):
                    val = row[alg_col_idx].strip()
                    if val in ("●", "•", "YES", "yes"):
                        allergens.append(alg_name)
                    elif val in ("#", "MAYBE", "maybe"):
                        allergens.append(f"{alg_name} (May contain)")

            diet_tags = [d.strip() for d in diet_raw.split(",") if d.strip() and len(d.strip()) > 1]

            # Disambiguate items that appear under different categories on the same menu
            # (e.g. Subway "Paneer Tikka" 6-inch Sub vs Wrap vs Salad vs Rice Bowl)
            display_item_name = item_name
            if (
                cat
                and cat.lower() not in ("menu", "regular menu", "booklet product sheet")
                and cat.lower() not in item_name.lower()
                and "subway" in rest.lower()
            ):
                display_item_name = f"{item_name} ({cat})"

            item_id = hashlib.md5(
                f"{source_file}:{page_label}:{r_idx}:{display_item_name}".encode()
            ).hexdigest()[:14]

            s_item = StructuredMenuItem(
                item_id=f"item_{item_id}",
                restaurant=rest,
                category=cat,
                item_name=display_item_name,
                variant_or_size=serv,
                calories_kcal=cal or 0.0,
                protein_g=prot or 0.0,
                carbs_g=carb or 0.0,
                fat_g=fat or 0.0,
                fiber_g=fib,
                serving_size=serv,
                allergens=allergens,
                dietary_tags=diet_tags,
                source_file=source_file,
                source_page_or_section=page_label,
                doc_modality="tabular",
                raw_text=" | ".join(c for c in row if c),
            )
            items.append(s_item)
            chunk_text = _build_tabular_chunk_text(s_item, extra_notes=notes)
            chunks.append(
                DocumentChunk(
                    chunk_id=f"chunk_{item_id}",
                    source_file=source_file,
                    restaurant=rest,
                    page_or_section=page_label,
                    doc_modality="tabular",
                    content=chunk_text,
                    metadata={"item_id": s_item.item_id, "item_name": s_item.item_name, "category": cat},
                )
            )

    return items, chunks, inherited_mapping, inherited_col_count, current_category


def _extract_items_from_aligned_text_lines(
    text: str,
    restaurant: str,
    source_file: str,
    page_label: str,
) -> Tuple[List[StructuredMenuItem], List[DocumentChunk]]:
    """Fallback parser for tabular text lines where columns are whitespace-aligned."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items: List[StructuredMenuItem] = []
    chunks: List[DocumentChunk] = []
    current_category = "Menu"

    header_text = " ".join(lines[:15]).lower()
    fat_pos = header_text.find("fat")
    carb_pos = header_text.find("carb")
    prot_pos = header_text.find("protein")
    has_serving_col = any(k in header_text for k in ("serving", "serve size", "weight (g)", "portion"))
    has_fiber_col = any(k in header_text for k in ("fiber", "fibre"))

    row_regex = re.compile(
        r"^([A-Za-z][A-Za-z0-9\s\(\)\-\&\'\.,\/™®\"%]+?)\s+"
        r"((?:\d+(?:\.\d+)?(?:\s*(?:g|mg|kcal|cal|ml))?\s+){3,11}\d+(?:\.\d+)?(?:\s*(?:g|mg|kcal|cal|ml))?)"
    )

    for idx, ln in enumerate(lines):
        if any(ln.lower().startswith(p) for p in SKIP_ROW_PREFIXES):
            continue
        if len(ln) < 55 and ln.isupper() and not re.search(r"\d", ln):
            current_category = ln.title()
            continue

        m = row_regex.match(ln)
        if not m:
            continue

        item_name = m.group(1).strip(" -|:")
        if len(item_name) < 3 or any(
            item_name.lower().startswith(w)
            for w in ("page ", "table ", "nutritional", "allergen", "note", "source", "per serve", "contribution")
        ):
            continue

        raw_nums = re.findall(r"\d+(?:\.\d+)?", m.group(2))
        nums = [float(x) for x in raw_nums]
        if len(nums) < 4:
            continue

        serv_str: Optional[str] = None
        fib_val: Optional[float] = None

        if has_serving_col and len(nums) >= 5:
            serv_str = f"{int(nums[0]) if nums[0].is_integer() else nums[0]}g"
            macro_nums = nums[1:]
        else:
            macro_nums = nums

        cal_val = macro_nums[0]
        if 0 <= prot_pos < carb_pos < fat_pos or prot_pos == -1:
            prot_val = macro_nums[1]
            carb_val = macro_nums[2]
            fat_val = macro_nums[3]
        elif 0 <= prot_pos < fat_pos < carb_pos:
            prot_val = macro_nums[1]
            fat_val = macro_nums[2]
            carb_val = macro_nums[3]
        elif 0 <= fat_pos < prot_pos < carb_pos:
            fat_val = macro_nums[1]
            prot_val = macro_nums[2]
            carb_val = macro_nums[3]
        else:
            prot_val, carb_val, fat_val = macro_nums[1], macro_nums[2], macro_nums[3]

        if has_fiber_col and len(macro_nums) >= 5:
            fib_val = macro_nums[4]

        item_id = hashlib.md5(f"{source_file}:{page_label}:line:{idx}:{item_name}".encode()).hexdigest()[:14]
        s_item = StructuredMenuItem(
            item_id=f"item_{item_id}",
            restaurant=restaurant,
            category=current_category,
            item_name=item_name,
            variant_or_size=serv_str,
            calories_kcal=round(cal_val, 2),
            protein_g=round(prot_val, 2),
            carbs_g=round(carb_val, 2),
            fat_g=round(fat_val, 2),
            fiber_g=round(fib_val, 2) if fib_val is not None else None,
            serving_size=serv_str,
            source_file=source_file,
            source_page_or_section=page_label,
            doc_modality="tabular",
            raw_text=ln,
        )
        items.append(s_item)
        chunks.append(
            DocumentChunk(
                chunk_id=f"chunk_{item_id}",
                source_file=source_file,
                restaurant=restaurant,
                page_or_section=page_label,
                doc_modality="tabular",
                content=_build_tabular_chunk_text(s_item),
                metadata={"item_id": s_item.item_id, "item_name": s_item.item_name, "category": current_category},
            )
        )

    return items, chunks


def _extract_inline_prose_items(
    text: str,
    restaurant: str,
    source_file: str,
    page_label: str,
) -> List[StructuredMenuItem]:
    """Extract structured macro mentions embedded inline within narrative prose sentences."""
    pattern = re.compile(
        r"([A-Z][A-Za-z0-9\s\-\'\(\)\/&\"]+?)[\s:]*\(\s*"
        r"(\d+(?:\.\d+)?)\s*(?:kcal|cal)\s*,\s*"
        r"(\d+(?:\.\d+)?)\s*g\s*(?:protein|fat|carbs)\s*,\s*"
        r"(\d+(?:\.\d+)?)\s*g\s*(?:protein|fat|carbs)\s*,\s*"
        r"(\d+(?:\.\d+)?)\s*g\s*(?:protein|fat|carbs)"
        r"(?:\s*,\s*(\d+(?:\.\d+)?)\s*g\s*(?:fiber|fibre))?\s*\)",
        re.IGNORECASE,
    )
    items: List[StructuredMenuItem] = []
    for idx, m in enumerate(pattern.finditer(text)):
        raw_span = m.group(0)
        name_part = m.group(1).strip(" -•*:\n\t")
        if len(name_part) > 75 or len(name_part) < 3:
            continue
        cal = float(m.group(2))
        prot_m = re.search(r"(\d+(?:\.\d+)?)\s*g\s*protein", raw_span, re.I)
        fat_m = re.search(r"(\d+(?:\.\d+)?)\s*g\s*fat", raw_span, re.I)
        carb_m = re.search(r"(\d+(?:\.\d+)?)\s*g\s*carb", raw_span, re.I)
        fib_m = re.search(r"(\d+(?:\.\d+)?)\s*g\s*(?:fiber|fibre)", raw_span, re.I)
        if not (prot_m and fat_m and carb_m):
            continue
        item_id = hashlib.md5(f"{source_file}:{page_label}:prose_inline:{idx}:{name_part}".encode()).hexdigest()[:14]
        items.append(
            StructuredMenuItem(
                item_id=f"item_{item_id}",
                restaurant=restaurant,
                category="Narrative Mention",
                item_name=name_part,
                calories_kcal=cal,
                protein_g=float(prot_m.group(1)),
                carbs_g=float(carb_m.group(1)),
                fat_g=float(fat_m.group(1)),
                fiber_g=float(fib_m.group(1)) if fib_m else None,
                source_file=source_file,
                source_page_or_section=page_label,
                doc_modality="non_tabular",
                raw_text=raw_span,
            )
        )
    return items


def parse_document(file_path: Path) -> Tuple[DocumentDetectionReport, List[StructuredMenuItem], List[DocumentChunk]]:
    """
    Inspect a document file, automatically detect whether it is tabular, non-tabular, or hybrid,
    and process it using the optimal extraction strategy.
    """
    filename = file_path.name
    suffix = file_path.suffix.lower()
    file_size = file_path.stat().st_size
    sha256 = compute_sha256(file_path)
    now_iso = datetime.now(timezone.utc).isoformat()

    all_items: List[StructuredMenuItem] = []
    all_chunks: List[DocumentChunk] = []

    # 1. CSV / TSV files -> Strictly Tabular
    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        restaurant = detect_restaurant_name(filename, raw_text[:4000])
        reader = list(csv.reader(io.StringIO(raw_text), delimiter=delimiter))
        items, chunks, _, _, _ = _extract_items_from_table_grid(reader, restaurant, filename, "CSV Table")
        report = DocumentDetectionReport(
            filename=filename,
            file_type=suffix.lstrip(".").upper(),
            file_size_bytes=file_size,
            sha256=sha256,
            detected_modality="tabular",
            tabular_confidence=1.0,
            detection_reasoning=f"Structured {suffix.upper()} dataset with {len(reader)-1} data rows and explicit macro columns.",
            restaurant_detected=restaurant,
            total_pages_or_sections=1,
            tabular_sections_count=1,
            prose_sections_count=0,
            extracted_items_count=len(items),
            created_chunks_count=len(chunks),
            indexed_at=now_iso,
        )
        return report, items, chunks

    # 2. JSON files
    if suffix == ".json":
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        restaurant = detect_restaurant_name(filename, raw_text[:4000])
        data = json.loads(raw_text)
        records = data if isinstance(data, list) else data.get("items", data.get("menu", []))
        if isinstance(records, list) and records and isinstance(records[0], dict):
            headers = list(records[0].keys())
            grid = [headers] + [[str(r.get(h, "")) for h in headers] for r in records]
            items, chunks, _, _, _ = _extract_items_from_table_grid(grid, restaurant, filename, "JSON Records")
            modality = "tabular" if items else "non_tabular"
            if not chunks:
                chunks = _chunk_prose_text(raw_text, filename, restaurant, "JSON Document")
            report = DocumentDetectionReport(
                filename=filename,
                file_type="JSON",
                file_size_bytes=file_size,
                sha256=sha256,
                detected_modality=modality,
                tabular_confidence=0.98 if items else 0.1,
                detection_reasoning=f"JSON array containing {len(records)} structured objects.",
                restaurant_detected=restaurant,
                total_pages_or_sections=1,
                tabular_sections_count=1 if items else 0,
                prose_sections_count=0 if items else 1,
                extracted_items_count=len(items),
                created_chunks_count=len(chunks),
                indexed_at=now_iso,
            )
            return report, items, chunks

    # 3. PDF files -> Page-by-page Tabular vs. Non-Tabular / Hybrid inspection
    if suffix == ".pdf":
        sample_text = ""
        page_modalities: List[str] = []
        page_scores: List[float] = []
        page_reasons: List[str] = []
        tabular_pages = 0
        prose_pages = 0

        inherited_mapping: Optional[Dict[str, Any]] = None
        inherited_col_count: Optional[int] = None
        running_category: str = "Menu"

        try:
            with pdfplumber.open(file_path) as pdf:
                total_pages = len(pdf.pages)
                for p_idx in range(min(5, total_pages)):
                    sample_text += "\n" + (pdf.pages[p_idx].extract_text() or "")
                restaurant = detect_restaurant_name(filename, sample_text)

                for p_idx, page in enumerate(pdf.pages):
                    page_label = f"Page {p_idx + 1}"
                    p_text = page.extract_text() or ""
                    tables = page.extract_tables() or []
                    valid_tables = [t for t in tables if t and len(t) >= 2]

                    modality, score, reason = analyze_text_block_modality(p_text, len(valid_tables))
                    page_modalities.append(modality)
                    page_scores.append(score)
                    page_reasons.append(f"{page_label} ({modality}): {reason}")

                    page_items: List[StructuredMenuItem] = []
                    page_chunks: List[DocumentChunk] = []

                    if modality in ("tabular", "hybrid") or valid_tables:
                        for t_idx, tbl in enumerate(valid_tables):
                            t_items, t_chunks, inherited_mapping, inherited_col_count, running_category = (
                                _extract_items_from_table_grid(
                                    tbl,
                                    restaurant,
                                    filename,
                                    f"{page_label}, Table {t_idx + 1}",
                                    inherited_mapping=inherited_mapping,
                                    inherited_col_count=inherited_col_count,
                                    initial_category=running_category,
                                )
                            )
                            page_items.extend(t_items)
                            page_chunks.extend(t_chunks)

                        if not page_items:
                            line_items, line_chunks = _extract_items_from_aligned_text_lines(
                                p_text, restaurant, filename, page_label
                            )
                            page_items.extend(line_items)
                            page_chunks.extend(line_chunks)

                    if page_items:
                        tabular_pages += 1

                    if modality in ("non_tabular", "hybrid") or not page_chunks:
                        if p_text.strip():
                            prose_pages += 1
                            prose_chunks = _chunk_prose_text(p_text, filename, restaurant, page_label)
                            page_chunks.extend(prose_chunks)

                    all_items.extend(page_items)
                    all_chunks.extend(page_chunks)
        except Exception as pdf_err:
            logger.warning(f"pdfplumber fallback to pypdf on {filename}: {pdf_err}")
            reader = PdfReader(str(file_path))
            total_pages = len(reader.pages)
            full_text = "\n".join(p.extract_text() or "" for p in reader.pages)
            restaurant = detect_restaurant_name(filename, full_text[:3000])
            for p_idx, page in enumerate(reader.pages):
                page_label = f"Page {p_idx + 1}"
                p_text = page.extract_text() or ""
                modality, score, reason = analyze_text_block_modality(p_text, 0)
                page_scores.append(score)
                page_reasons.append(f"{page_label}: {reason}")
                if modality in ("tabular", "hybrid"):
                    tabular_pages += 1
                    l_items, l_chunks = _extract_items_from_aligned_text_lines(
                        p_text, restaurant, filename, page_label
                    )
                    all_items.extend(l_items)
                    all_chunks.extend(l_chunks)
                if modality in ("non_tabular", "hybrid") or not all_chunks:
                    prose_pages += 1
                    all_chunks.extend(_chunk_prose_text(p_text, filename, restaurant, page_label))

        avg_score = round(sum(page_scores) / max(len(page_scores), 1), 2)
        if tabular_pages > 0 and prose_pages > 0 and (prose_pages >= 2 or "hybrid" in page_modalities):
            overall_modality = "hybrid" if prose_pages > total_pages * 0.15 else "tabular"
        elif tabular_pages > 0:
            overall_modality = "tabular"
        else:
            overall_modality = "non_tabular"

        report = DocumentDetectionReport(
            filename=filename,
            file_type="PDF",
            file_size_bytes=file_size,
            sha256=sha256,
            detected_modality=overall_modality,
            tabular_confidence=avg_score,
            detection_reasoning=" | ".join(page_reasons[:4]),
            restaurant_detected=restaurant,
            total_pages_or_sections=max(total_pages, 1),
            tabular_sections_count=tabular_pages,
            prose_sections_count=prose_pages,
            extracted_items_count=len(all_items),
            created_chunks_count=len(all_chunks),
            indexed_at=now_iso,
        )
        return report, all_items, all_chunks

    # 4. HTML / Markdown / Text files
    raw_text = file_path.read_text(encoding="utf-8", errors="replace")
    restaurant = detect_restaurant_name(filename, raw_text[:4000])
    explicit_tables: List[List[List[str]]] = []

    if suffix in (".html", ".htm"):
        soup = BeautifulSoup(raw_text, "html.parser")
        for tbl in soup.find_all("table"):
            grid = []
            for tr in tbl.find_all(["tr"]):
                row = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
                if row:
                    grid.append(row)
            if len(grid) >= 2:
                explicit_tables.append(grid)
        clean_text = soup.get_text("\n", strip=True)
    else:
        clean_text = raw_text
        cur_md_table: List[List[str]] = []
        for ln in clean_text.splitlines():
            if MD_TABLE_ROW_PATTERN.match(ln):
                if re.match(r"^\s*\|[\s\-:\|]+\|\s*$", ln):
                    continue
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                cur_md_table.append(cells)
            else:
                if len(cur_md_table) >= 2:
                    explicit_tables.append(cur_md_table)
                cur_md_table = []
        if len(cur_md_table) >= 2:
            explicit_tables.append(cur_md_table)

    modality, score, reason = analyze_text_block_modality(clean_text, len(explicit_tables))
    tabular_sec = 0
    prose_sec = 0

    if modality in ("tabular", "hybrid"):
        tabular_sec += max(1, len(explicit_tables))
        for t_idx, grid in enumerate(explicit_tables):
            t_items, t_chunks, _, _, _ = _extract_items_from_table_grid(
                grid, restaurant, filename, f"Table {t_idx + 1}"
            )
            all_items.extend(t_items)
            all_chunks.extend(t_chunks)

        l_items, l_chunks = _extract_items_from_aligned_text_lines(
            clean_text, restaurant, filename, "Text Table"
        )
        existing_names = {it.item_name.lower() for it in all_items}
        for li, lc in zip(l_items, l_chunks):
            if li.item_name.lower() not in existing_names:
                all_items.append(li)
                all_chunks.append(lc)
                existing_names.add(li.item_name.lower())

    if modality in ("non_tabular", "hybrid") or not all_chunks:
        prose_sec += 1
        all_chunks.extend(_chunk_prose_text(clean_text, filename, restaurant, "Document Body"))
        inline_items = _extract_inline_prose_items(clean_text, restaurant, filename, "Narrative Body")
        existing_names = {it.item_name.lower() for it in all_items}
        for ii in inline_items:
            if ii.item_name.lower() not in existing_names:
                all_items.append(ii)
                existing_names.add(ii.item_name.lower())

    report = DocumentDetectionReport(
        filename=filename,
        file_type=suffix.lstrip(".").upper() or "TXT",
        file_size_bytes=file_size,
        sha256=sha256,
        detected_modality=modality,
        tabular_confidence=score,
        detection_reasoning=reason,
        restaurant_detected=restaurant,
        total_pages_or_sections=tabular_sec + prose_sec,
        tabular_sections_count=tabular_sec,
        prose_sections_count=prose_sec,
        extracted_items_count=len(all_items),
        created_chunks_count=len(all_chunks),
        indexed_at=now_iso,
    )
    return report, all_items, all_chunks
