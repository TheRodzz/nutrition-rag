import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

MACRO_KEYWORDS = re.compile(
    r"\b(calories|kcal|energy|protein|proteins|carb|carbs|carbohydrate|carbohydrates|"
    r"fat|fats|lipid|fiber|fibre|serving\s*size|allergen|sodium|sugar)\b",
    re.IGNORECASE,
)

# Matches a line that has an item label followed by at least 3 numeric values (common in PDF table text extraction)
NUMERIC_ROW_PATTERN = re.compile(
    r"^[A-Za-z0-9\s\(\)\-\&\'\.,\/™®\"%]+?(?:\s+\d+(?:\.\d+)?(?:\s*(?:g|mg|kcal|cal|ml|%))?){3,}\s*$",
    re.IGNORECASE,
)

MD_TABLE_ROW_PATTERN = re.compile(r"^\s*\|.*\|.*\|\s*$")


def detect_restaurant_name(filename: str, sample_text: str) -> str:
    """Detect the restaurant brand from filename and text content."""
    combined = f"{filename} {sample_text[:3000]}".lower()
    if any(k in combined for k in ("mcdonald", "mcdindia", "mcaloo", "mcspicy", "mcchicken", "maharaja mac")):
        return "McDonald's India"
    if any(k in combined for k in ("subway", "veggie delite", "paneer tikka sub", "aloo patty sub", "6-inch", "footlong")):
        return "Subway India"
    if any(k in combined for k in ("california burrito", "californiaburrito", "cilantro rice", "snachos", "chocodilla")):
        return "California Burrito India"
    if any(k in combined for k in ("burger king", "burgerking", "whopper")):
        return "Burger King India"
    if any(k in combined for k in ("kfc", "kentucky fried", "hot & crispy", "zinger", "popcorn chicken")):
        return "KFC India"
    if any(k in combined for k in ("pizza hut", "pizzahut")):
        return "Pizza Hut India"
    if any(k in combined for k in ("taco bell", "tacobell", "chalupa", "quesadilla")):
        return "Taco Bell India"
    if any(k in combined for k in ("haldiram", "haldirams", "bikaneri bhujia")):
        return "Haldiram's India"
    if any(k in combined for k in ("domino's", "dominos", "jubilant")):
        return "Domino's Pizza India"
    if "indian_restaurant" in filename.lower() or "street_food" in filename.lower():
        return "Indian Restaurant & Street Food"

    # Derive clean title from filename if unknown brand
    stem = Path(filename).stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\b(nutrition|nutritional|info|information|menu|data|scraped|guide|pdf|doc|table)\b", "", stem, flags=re.I)
    clean = " ".join(stem.split()).strip().title()
    return clean if clean else "General Restaurant"


def analyze_text_block_modality(text: str, explicit_tables_count: int = 0) -> Tuple[str, float, str]:
    """
    Determine whether a page or text block is 'tabular', 'non_tabular', or 'hybrid'.
    Returns (modality, tabular_score_0_to_1, reasoning).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines and explicit_tables_count == 0:
        return "non_tabular", 0.0, "Empty section"

    numeric_rows = sum(1 for ln in lines if NUMERIC_ROW_PATTERN.match(ln))
    md_table_rows = sum(1 for ln in lines if MD_TABLE_ROW_PATTERN.match(ln))
    macro_header_hits = len(MACRO_KEYWORDS.findall(text))
    long_prose_lines = sum(
        1
        for ln in lines
        if len(ln) > 80 and not NUMERIC_ROW_PATTERN.match(ln) and not MD_TABLE_ROW_PATTERN.match(ln)
    )

    total_lines = max(len(lines), 1)
    table_line_ratio = (numeric_rows + md_table_rows) / total_lines

    score = 0.0
    reasons: List[str] = []

    if explicit_tables_count > 0:
        score += 0.65
        reasons.append(f"{explicit_tables_count} explicit grid table(s) detected")

    if numeric_rows >= 3 or md_table_rows >= 3:
        row_boost = min(0.65, 0.15 + table_line_ratio * 0.9)
        score += row_boost
        reasons.append(f"{numeric_rows + md_table_rows}/{total_lines} aligned numeric/table rows")

    if macro_header_hits >= 3:
        score += 0.15
        reasons.append(f"{macro_header_hits} macro column keywords")

    score = min(1.0, round(score, 2))

    if score >= 0.55 and long_prose_lines >= 5 and table_line_ratio < 0.65:
        return "hybrid", score, "; ".join(reasons) + f" + {long_prose_lines} narrative sentences"
    elif score >= 0.45:
        return "tabular", score, "; ".join(reasons) or "Structured macro rows"
    else:
        return (
            "non_tabular",
            score,
            f"Predominantly narrative/prose text ({long_prose_lines} prose lines, {numeric_rows} numeric rows)",
        )
