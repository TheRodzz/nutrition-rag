import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

logger = logging.getLogger(__name__)

CB_NUTRITION_URL = "https://www.californiaburrito.in/nutrition"
CB_BASE_URL = "https://www.californiaburrito.in"


def _js_obj_to_json(js_str: str) -> Any:
    """Convert minified JS object/array literal with unquoted keys and `.5` floats into parsed Python data."""
    s = js_str.strip()
    # Convert single-quoted JS strings like '12" TORTILLA' into double-quoted JSON strings "12\" TORTILLA"
    s = re.sub(
        r"'([^'\\]*(?:\\.[^'\\]*)*)'",
        lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
        s,
    )
    # Replace !0 and !1 booleans
    s = re.sub(r":!0\b", ":true", s)
    s = re.sub(r":!1\b", ":false", s)
    # Fix leading decimal numbers like :.6 or ,.5
    s = re.sub(r"([:,\[])\.(\d+)", r"\g<1>0.\2", s)
    # Quote unquoted keys: {name: or ,cal:
    s = re.sub(r'([{\[,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', s)
    return json.loads(s)


def _extract_balanced(text: str, start_idx: int, open_ch: str = "{", close_ch: str = "}") -> str:
    """Extract a balanced {...} or [...] substring starting at start_idx."""
    depth = 0
    in_str = False
    str_ch = ""
    escape = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_str:
            if ch == str_ch:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            str_ch = ch
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]
    raise ValueError(f"Unbalanced {open_ch}{close_ch} starting at {start_idx}")


def parse_cb_js_bundle(js_text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parse the California Burrito nutrition JS bundle (`nutrition.*.js`) to extract:
    1. All menu items & customizable components across categories and sizes
    2. Allergen declarations
    3. Special diet (Vegetarian / Vegan) declarations
    """
    # 1. Extract base `s={ricebowl:...,sides:[...]}`
    s_match = re.search(r",s=(\{ricebowl:\{)", js_text)
    if not s_match:
        raise RuntimeError("Could not locate nutrition dataset `s={ricebowl:...}` in JS bundle")

    s_raw = _extract_balanced(js_text, s_match.start(1), "{", "}")
    base_data = _js_obj_to_json(s_raw)

    # 2. Extract s.quesadilla and s.munchies and taco shells/fillings
    ques_match = re.search(r"s\.quesadilla=(\{)", js_text)
    if ques_match:
        ques_raw = _extract_balanced(js_text, ques_match.start(1), "{", "}")
        base_data["quesadilla"] = _js_obj_to_json(ques_raw)

    munch_match = re.search(r"s\.munchies=(\{)", js_text)
    if munch_match:
        munch_raw = _extract_balanced(js_text, munch_match.start(1), "{", "}")
        base_data["munchies"] = _js_obj_to_json(munch_raw)

    # Extract taco shells & overcrowded taco fillings
    taco_shell_match = re.search(r"shell:(\{three:\[)", js_text)
    taco_filling_match = re.search(r"filling:(\{overcrowded:\[)", js_text)
    tacos_data: Dict[str, Any] = {
        "proteins": {
            "three": base_data["ricebowl"]["proteins"]["regular"],
            "one": base_data["ricebowl"]["proteins"]["regular"],
        },
        "rice": {
            "three": base_data["ricebowl"]["rice"]["regular"],
            "one": base_data["ricebowl"]["rice"]["mini"],
        },
        "beans": {
            "three": base_data["ricebowl"]["beans"]["regular"],
            "one": base_data["ricebowl"]["beans"]["regular"],
        },
        "toppings": {
            "three": base_data["ricebowl"]["toppings"]["regular"],
            "one": base_data["ricebowl"]["toppings"]["mini"],
        },
    }
    if taco_shell_match:
        tacos_data["shell"] = _js_obj_to_json(_extract_balanced(js_text, taco_shell_match.start(1), "{", "}"))
    if taco_filling_match:
        tacos_data["filling"] = _js_obj_to_json(_extract_balanced(js_text, taco_filling_match.start(1), "{", "}"))
    base_data["tacos"] = tacos_data

    # Nachos shares ricebowl regular proteins/beans/toppings, with Brown Rice at 45 cal
    base_data["nachos"] = {
        "proteins": {"regular": base_data["ricebowl"]["proteins"]["regular"]},
        "rice": {
            "regular": [
                {"name": "BROWN RICE", "cal": 45, "fat": 0.9, "protein": 0.6, "carbs": 9.0},
                *[r for r in base_data["ricebowl"]["rice"]["regular"] if r["name"] != "BROWN RICE"],
            ]
        },
        "beans": {"regular": base_data["ricebowl"]["beans"]["regular"]},
        "toppings": {"regular": base_data["ricebowl"]["toppings"]["regular"]},
    }

    # 3. Extract Allergens `Ne=[...]` and Special Diet `ue=[...]`
    allergens_list: List[Dict[str, Any]] = []
    ne_match = re.search(r"const Ne=(\[\{name:)", js_text)
    if ne_match:
        ne_raw = _extract_balanced(js_text, ne_match.start(1), "[", "]")
        allergens_list = _js_obj_to_json(ne_raw)

    diet_list: List[Dict[str, Any]] = []
    ue_match = re.search(r",ue=(\[\{name:)", js_text)
    if ue_match:
        ue_raw = _extract_balanced(js_text, ue_match.start(1), "[", "]")
        diet_list = _js_obj_to_json(ue_raw)

    # Build lookup maps for allergens and vegan/veg tags
    allergen_map: Dict[str, List[str]] = {}
    for entry in allergens_list:
        name = entry.get("name", "").upper().strip()
        flags = []
        for k in ("dairy", "soy", "gluten", "nuts"):
            if entry.get(k):
                flags.append(k.capitalize())
        allergen_map[name] = flags

    diet_map: Dict[str, str] = {}
    for entry in diet_list:
        name = entry.get("name", "").upper().strip()
        vegan_info = entry.get("vegan", "")
        veg_flag = "Vegetarian" if entry.get("veg") else ""
        diet_map[name] = f"{veg_flag} ({vegan_info})".strip(" ()")

    # Flatten into structured rows
    rows: List[Dict[str, Any]] = []

    def add_row(
        category: str,
        sub_group: str,
        size: str,
        item: Dict[str, Any],
        notes: str = "",
    ) -> None:
        raw_name = item["name"].strip()
        upper_name = raw_name.upper()
        allergens = allergen_map.get(upper_name, [])
        diet = diet_map.get(upper_name, item.get("type", ""))
        size_label = size.upper() if size else "STANDARD"
        display_name = (
            f"{raw_name.title()} ({category.title()} - {sub_group.title()}, {size_label})"
            if sub_group not in ("Standalone Side", "Munchies / Snacks", "Complete Build")
            else f"{raw_name.title()} ({category.title()})"
        )
        rows.append(
            {
                "restaurant": "California Burrito India",
                "category": category.title(),
                "component_group": sub_group.title(),
                "item_name": display_name,
                "base_item_name": raw_name.title(),
                "size_or_variant": size_label,
                "calories_kcal": round(float(item.get("cal", 0)), 1),
                "protein_g": round(float(item.get("protein", 0)), 1),
                "carbs_g": round(float(item.get("carbs", 0)), 1),
                "fat_g": round(float(item.get("fat", 0)), 1),
                "fiber_g": "",
                "allergens": ", ".join(allergens) if allergens else "None declared",
                "dietary_info": diet,
                "notes": notes,
            }
        )

    # Process each category
    for cat_key, cat_val in base_data.items():
        if cat_key == "sides" and isinstance(cat_val, list):
            for side_item in cat_val:
                add_row("Sides (Full Portion)", "Standalone Side", "Regular", side_item, "Standalone side dish portion")
            continue

        if not isinstance(cat_val, dict):
            continue

        for group_key, group_val in cat_val.items():
            if isinstance(group_val, list):
                for item in group_val:
                    add_row(cat_key, group_key, "Standard", item)
            elif isinstance(group_val, dict):
                for size_key, size_items in group_val.items():
                    if isinstance(size_items, list):
                        for item in size_items:
                            add_row(cat_key, group_key, size_key, item)

    # Also synthesize complete standard builds for Rice Bowls, Burritos, Salad Bowls, and Quesadillas
    # so single-item orders like "Regular BBQ Chicken Rice Bowl" or "Mini Mexican Paneer Burrito"
    # have exact pre-summed rows in addition to component-level rows!
    rb = base_data["ricebowl"]
    default_rb_rice_reg = next(x for x in rb["rice"]["regular"] if x["name"] == "CILANTRO RICE")
    default_rb_rice_mini = next(x for x in rb["rice"]["mini"] if x["name"] == "CILANTRO RICE")
    default_rb_beans = next(x for x in rb["beans"]["regular"] if x["name"] == "BLACK BEANS")
    # Standard classic toppings: Grilled Onion & Capsicum (28) + Corn Salsa (41) + Fresh Tomato Salsa (13) + Lettuce (2)
    classic_toppings_cal = 28 + 41 + 13 + 2
    classic_toppings_fat = round(0.6 + 0.6 + 0.1 + 0.0, 1)
    classic_toppings_prot = round(1.4 + 1.6 + 0.6 + 0.1, 1)
    classic_toppings_carb = round(5.5 + 8.9 + 2.9 + 0.0, 1)

    for size_key, rice_obj in [("Regular", default_rb_rice_reg), ("Mini", default_rb_rice_mini)]:
        for prot in rb["proteins"][size_key.lower()]:
            tot_cal = prot["cal"] + rice_obj["cal"] + default_rb_beans["cal"] + classic_toppings_cal
            tot_fat = prot["fat"] + rice_obj["fat"] + default_rb_beans["fat"] + classic_toppings_fat
            tot_prot = prot["protein"] + rice_obj["protein"] + default_rb_beans["protein"] + classic_toppings_prot
            tot_carb = prot["carbs"] + rice_obj["carbs"] + default_rb_beans["carbs"] + classic_toppings_carb
            add_row(
                "Rice Bowl (Complete Standard Build)",
                "Complete Build",
                size_key,
                {
                    "name": f"{size_key} {prot['name']} Rice Bowl (with Cilantro Rice, Black Beans, Fajita Veggies, Pico & Corn Salsa)",
                    "cal": tot_cal,
                    "fat": tot_fat,
                    "protein": tot_prot,
                    "carbs": tot_carb,
                },
                notes=(
                    f"Standard {size_key} Rice Bowl build: {prot['name'].title()} ({prot['cal']} kcal) + "
                    f"Cilantro Rice ({rice_obj['cal']} kcal) + Black Beans ({default_rb_beans['cal']} kcal) + "
                    f"Fajita Veggies, Corn Salsa, Fresh Tomato Salsa & Lettuce ({classic_toppings_cal} kcal). "
                    f"Add Sour Cream (+57 kcal), Cheese (+23 kcal), or Guacamole (+148 kcal) if ordered."
                ),
            )

    # Pro Rice Bowls (Pro protein entry already includes base in California Burrito calculator, or with Cilantro Rice + Black Beans)
    for prot in rb["proteins"].get("pro", []):
        add_row(
            "Pro Rice Bowl",
            "Complete Build",
            "Pro",
            {
                "name": f"Pro {prot['name']} Rice Bowl (High Protein Build)",
                "cal": prot["cal"],
                "fat": prot["fat"],
                "protein": prot["protein"],
                "carbs": prot["carbs"],
            },
            notes="Official California Burrito PRO Rice Bowl high-protein base macro profile.",
        )

    # Standard Burrito Builds (12" Tortilla for Regular, 10" Tortilla for Mini)
    bur = base_data["burrito"]
    bur_rice = next(x for x in bur["rice"]["regular"] if x["name"] == "CILANTRO RICE")
    for size_key, tort_size in [("Regular", '12" TORTILLA (142 kcal)'), ("Mini", '10" TORTILLA (105 kcal)')]:
        tort = bur["tortilla"][size_key.lower()][0]
        for prot in bur["proteins"][size_key.lower()]:
            tot_cal = tort["cal"] + prot["cal"] + bur_rice["cal"] + default_rb_beans["cal"] + classic_toppings_cal
            tot_fat = tort["fat"] + prot["fat"] + bur_rice["fat"] + default_rb_beans["fat"] + classic_toppings_fat
            tot_prot = tort["protein"] + prot["protein"] + bur_rice["protein"] + default_rb_beans["protein"] + classic_toppings_prot
            tot_carb = tort["carbs"] + prot["carbs"] + bur_rice["carbs"] + default_rb_beans["carbs"] + classic_toppings_carb
            add_row(
                "Burrito (Complete Standard Build)",
                "Complete Build",
                size_key,
                {
                    "name": f"{size_key} {prot['name']} Burrito (with {tort['name']}, Cilantro Rice, Black Beans, Fajita Veggies, Pico & Corn Salsa)",
                    "cal": tot_cal,
                    "fat": tot_fat,
                    "protein": tot_prot,
                    "carbs": tot_carb,
                },
                notes=(
                    f"Standard {size_key} Burrito build: {tort['name']} ({tort['cal']} kcal) + "
                    f"{prot['name'].title()} ({prot['cal']} kcal) + Cilantro Rice ({bur_rice['cal']} kcal) + "
                    f"Black Beans ({default_rb_beans['cal']} kcal) + Classic Veggie/Salsa Toppings ({classic_toppings_cal} kcal). "
                    f"Add Sour Cream (+57 kcal), Cheese (+23 kcal), or Guacamole (+148 kcal) if ordered."
                ),
            )

    # Habanero Burritos
    tort_hab = bur["tortilla"]["habanero"][0]
    for prot in bur["proteins"].get("habanero", []):
        add_row(
            "Habanero Burrito",
            "Complete Build",
            "Habanero",
            {
                "name": f"{prot['name']} Burrito (with 10\" Tortilla)",
                "cal": tort_hab["cal"] + prot["cal"],
                "fat": tort_hab["fat"] + prot["fat"],
                "protein": tort_hab["protein"] + prot["protein"],
                "carbs": tort_hab["carbs"] + prot["carbs"],
            },
            notes=f"Includes 10\" Tortilla ({tort_hab['cal']} kcal) + {prot['name'].title()} ({prot['cal']} kcal).",
        )

    return rows, allergens_list, diet_list


def scrape_california_burrito(output_dir: Path) -> Dict[str, Any]:
    """
    Live-scrape California Burrito India's official nutrition portal (https://www.californiaburrito.in/nutrition)
    and write both a tabular CSV dataset and a narrative/hybrid Markdown guide into output_dir.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        page_resp = client.get(CB_NUTRITION_URL)
        page_resp.raise_for_status()
        html = page_resp.text

        # Find the JS bundle URL dynamically
        bundle_match = re.search(r'src=["\']?(/js/nutrition\.[a-zA-Z0-9_-]+\.js)["\']?', html)
        if not bundle_match:
            bundle_path = "/js/nutrition.BslodCyz.js"
        else:
            bundle_path = bundle_match.group(1)

        bundle_url = f"{CB_BASE_URL}{bundle_path}"
        js_resp = client.get(bundle_url)
        js_resp.raise_for_status()
        js_text = js_resp.text

    rows, allergens, diets = parse_cb_js_bundle(js_text)

    csv_path = output_dir / "california_burrito_india_nutrition.csv"
    fieldnames = [
        "restaurant",
        "category",
        "component_group",
        "item_name",
        "base_item_name",
        "size_or_variant",
        "calories_kcal",
        "protein_g",
        "carbs_g",
        "fat_g",
        "fiber_g",
        "allergens",
        "dietary_info",
        "notes",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Also write a rich non-tabular narrative guide explaining California Burrito customization rules,
    # portion differences, and allergen/vegan notes so the RAG has both tabular and non-tabular docs.
    md_path = output_dir / "california_burrito_customization_and_allergen_guide.txt"
    prose_lines = [
        "CALIFORNIA BURRITO INDIA — OFFICIAL NUTRITION, CUSTOMIZATION & ALLERGEN GUIDE",
        f"Source URL: {CB_NUTRITION_URL} (Live Scraped from {bundle_url})",
        "",
        "OVERVIEW & HOW TO CALCULATE CUSTOM MEALS AT CALIFORNIA BURRITO INDIA:",
        "California Burrito India serves build-your-own Mexican meals including Rice Bowls, Burritos, Salad Bowls, Nachos, Tacos, Quesadillas, Munchies (Tostadas, Popcorn Chicken/Mushroom/Potato, Snachos), and Standalone Sides.",
        "",
        "1. RICE BOWLS (REGULAR, MINI, AND PRO):",
        "- A Regular or Mini Rice Bowl is built by combining: (a) 1 Protein choice, (b) 1 Rice choice, (c) 1 Beans choice, and (d) any number of Toppings, Extra Toppings, Extra Fillings, or 'Make It Rich' add-ons.",
        "- Interestingly, in California Burrito's official nutrition calculator, the protein portion macros for Regular and Mini Rice Bowls are identical (e.g., Grilled Barbeque Chicken is 190 kcal, 21.4g protein, 10.2g fat, 1.8g carbs in both Regular and Mini), whereas the Rice portion changes significantly: Regular Cilantro Rice is 314 kcal (55.5g carbs, 5.5g protein, 6.9g fat) vs Mini Cilantro Rice at 209 kcal (37g carbs, 3.7g protein, 4.6g fat). Similarly, Regular Brown Rice is 268 kcal vs Mini Brown Rice at 178 kcal.",
        "- Beans (Black Beans: 94 kcal, 4.9g protein, 13.3g carbs, 3.4g fat; Pinto Beans: 71 kcal, 3.7g protein, 10.5g carbs, 1.8g fat) have the same macros across Regular and Mini bowls.",
        "- PRO Rice Bowls are high-protein bowls: Pro Grilled Barbeque Chicken base has 612 kcal, 54g protein, 19g fat, 62g carbs; Pro Mexican Paneer base has 847 kcal, 43g protein, 49g fat, 65g carbs.",
        "",
        "2. BURRITOS (REGULAR, MINI, AND HABANERO):",
        "- A Regular Burrito uses a 12-inch Flour Tortilla (142 kcal, 3.7g protein, 29.2g carbs, 1.9g fat).",
        "- A Mini Burrito or Habanero Burrito uses a 10-inch Flour Tortilla (105 kcal, 2.7g protein, 21.6g carbs, 1.4g fat).",
        "- Inside a Burrito, Cilantro Rice adds 158 kcal (2.8g protein, 27.9g carbs, 3.5g fat) and Brown Rice adds 135 kcal (2.8g protein, 26.7g carbs, 1.6g fat).",
        "- Habanero Burritos come in two signature builds: Chicken Habanero filling (226 kcal, 25.4g protein, 11.8g fat, 5.6g carbs, plus 105 kcal for the 10-inch tortilla = 331 kcal, 28.1g protein total) and Paneer Habanero filling (268 kcal, 15.8g protein, 19.5g fat, 7.4g carbs, plus 105 kcal for the 10-inch tortilla = 373 kcal, 18.5g protein total).",
        "",
        "3. SALAD BOWLS & DRESSINGS:",
        "- Salad Bowls include Lettuce (23 kcal, 2.1g protein in Regular; 15 kcal, 1.4g protein in Mini) plus optional Rice, Beans, Protein, and Toppings.",
        "- Salad Dressings on the side include Chilli Lime Vinaigrette (28 kcal, 0.2g fat, 0.4g protein, 6.5g carbs), Ranch Dressing (10 kcal, 1.1g fat), and Chipotle Mayo (24 kcal, 0.8g fat, 0.8g protein, 1.2g carbs).",
        "- Pro Salad Bowls provide 596 kcal & 54g protein for Grilled Barbeque Chicken, or 836 kcal & 43g protein for Mexican Paneer.",
        "",
        "4. TACOS (ONE, THREE, OR OVERCROWDED):",
        "- Single Taco (ONE) shell options: Soft Shell (57 kcal, 1.5g protein, 11.7g carbs, 0.8g fat) or Crunchy Shell (70 kcal, 1.3g protein, 10g carbs, 3.2g fat).",
        "- Three Tacos (THREE) shell options: 3 Soft Shells (171 kcal, 4.5g protein, 35.1g carbs, 2.4g fat) or 3 Crunchy Shells (210 kcal, 3.9g protein, 30g carbs, 9.6g fat).",
        "- Overcrowded Tacos have pre-configured fillings (plus the shell): 7 Layer Veg Taco filling (664 kcal, 22.8g protein, 24.4g fat, 94.8g carbs), Chipotle Potato Taco filling (660 kcal, 17.2g protein, 26g fat, 92.4g carbs), 7 Layer Chicken Taco filling (728 kcal, 33.2g protein, 31.6g fat, 80.4g carbs), and Chipotle Crispy Chicken Taco filling (712 kcal, 32.4g protein, 35.6g fat, 71.2g carbs).",
        "",
        "5. QUESADILLAS & MUNCHIES:",
        "- Quesadillas come in 7 varieties: Cheesy Chipotle Chicken (343 kcal, 19.7g protein, 21.2g fat, 16.6g carbs), Cheesy Chicken Dominator (339 kcal, 21g protein, 20.8g fat, 15g carbs), California Chicken (373 kcal, 22.3g protein, 21.2g fat, 23.5g carbs), Peppy Paneer (437 kcal, 18.5g protein, 28.5g fat, 27.4g carbs), Southwest Mushroom (334 kcal, 11.1g protein, 23.2g fat, 19.2g carbs), Mexican Corn Fiesta (285 kcal, 11.2g protein, 15g fat, 27.8g carbs), and Cheese Melt (272 kcal, 11.9g protein, 13.8g fat, 24.6g carbs).",
        "- Popular Munchies/Snacks: Snachos (346 kcal, 4.4g protein), Cheesy Tostada (316 kcal, 9g protein), Crispy Peri-Peri Chicken Popper (215 kcal, 11g protein), Popcorn Chicken (205 kcal, 11g protein), Popcorn Mushroom with dip (190 kcal, 4g protein), Popcorn Potato (170 kcal, 3g protein), Topped Nachos - Chicken (322 kcal, 10g protein), Topped Nachos - Veg (244 kcal, 3.2g protein), Avocado Tostada (279 kcal, 4.4g protein), Veg Tostada (148 kcal, 4g protein), Tortilla Chips (120 kcal, 2g protein), and Plain Nachos (295 kcal, 3.4g protein).",
        "",
        "6. HIGH-IMPACT ADD-ONS ('MAKE IT RICH') & SIDES:",
        "- Watch out for calorie-dense extras when estimating meal macros: Guacamole inside a bowl/burrito adds 148 kcal (13.3g fat, 1.9g protein, 9.2g carbs), Melted Cheese Queso adds 107 kcal (8g fat, 4g protein, 4g carbs), Sour Cream adds 57 kcal (5.6g fat, 0.5g protein, 1.1g carbs), Cheese adds 23 kcal (1.8g fat, 1.2g protein), Chipotle Mayo adds 24 kcal, and Southwest Sauce adds 18 kcal.",
        "- Full Standalone Sides are large shareable tubs and have much higher macros than in-bowl toppings: e.g., a Standalone Side of Grilled BBQ Chicken is 592 kcal and 67g protein; a Standalone Side of Chili Chipotle Chicken is 647 kcal and 56.2g protein; a Standalone Side of Guacamole is 617 kcal (55.2g fat).",
        "",
        "7. ALLERGEN & VEGAN / VEGETARIAN DECLARATIONS:",
    ]
    for a in allergens:
        item_name = a.get("name", "")
        flags = [k.upper() for k in ("dairy", "soy", "gluten", "nuts") if a.get(k)]
        flag_str = ", ".join(flags) if flags else "Allergen-Free (No Dairy, Soy, Gluten, or Nuts)"
        prose_lines.append(f"- {item_name}: {flag_str}")

    prose_lines.append("")
    prose_lines.append("8. SPECIAL DIET (VEGETARIAN & VEGAN) NOTES:")
    for d in diets:
        item_name = d.get("name", "")
        v_note = d.get("vegan", "")
        prose_lines.append(f"- {item_name}: Vegetarian=Yes, Vegan Status={v_note}")

    md_path.write_text("\n".join(prose_lines), encoding="utf-8")

    return {
        "source_url": CB_NUTRITION_URL,
        "bundle_url": bundle_url,
        "csv_file": str(csv_path.name),
        "guide_file": str(md_path.name),
        "rows_extracted": len(rows),
        "allergens_extracted": len(allergens),
    }
