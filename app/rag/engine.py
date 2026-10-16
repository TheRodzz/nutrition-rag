import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from app.config import SETTINGS
from app.llm_provider import generate_structured_json
from app.models import (
    MacroBreakdown,
    QueryRequest,
    QueryResponse,
    ResolvedOrderItem,
    SourceCitation,
    StructuredMenuItem,
)
from app.rag.vector_store import NutritionVectorDB

logger = logging.getLogger(__name__)


def _confidence_level(score: float) -> str:
    if score >= 85.0:
        return "High"
    elif score >= 65.0:
        return "Medium"
    return "Low"


def _split_order_clauses(query: str) -> List[str]:
    """Decompose a multi-item natural language order into candidate sub-queries for retrieval."""
    parts = re.split(r"(?:,|\band\b|\bplus\b|\bwith\b|\+|\n|;)", query, flags=re.IGNORECASE)
    cleaned = [p.strip() for p in parts if len(p.strip()) >= 3]
    return cleaned if cleaned else [query.strip()]


class NutritionRAGEngine:
    def __init__(self, vector_db: NutritionVectorDB) -> None:
        self.vector_db = vector_db

    def _gather_candidates(
        self, query: str, restaurant_filter: Optional[str], top_k: int
    ) -> Tuple[List[Tuple[Any, float]], List[Tuple[StructuredMenuItem, float, List[str]]]]:
        """
        Retrieve both hybrid vector/BM25 chunks and structured catalog items across
        the full query and decomposed item clauses.
        """
        clauses = _split_order_clauses(query)

        # Detect if query mentions a specific restaurant even if restaurant_filter wasn't set
        effective_filter = restaurant_filter
        if not effective_filter:
            q_low = query.lower()
            matching_rests = []
            for rest in self.vector_db.get_stats().get("restaurants", []):
                base_name = (
                    rest.lower()
                    .replace(" bangalore", "")
                    .replace(" india", "")
                    .replace(" & street food", "")
                    .strip()
                )
                if base_name in q_low or rest.lower() in q_low:
                    matching_rests.append(rest)
            # Extra brand aliases
            if "mcdonald" in q_low or "mcd " in q_low or "mcaloo" in q_low or "mcspicy" in q_low:
                matching_rests.append("McDonald's India")
            if "subway" in q_low or "footlong" in q_low or "sub craver" in q_low:
                matching_rests.append("Subway India")
            if "california burrito" in q_low or "snachos" in q_low:
                matching_rests.append("California Burrito India")
            if "bbk" in q_low or "biryani by kilo" in q_low:
                matching_rests.append("Biryani By Kilo")
            if "corner house" in q_low or "death by chocolate" in q_low or "dbc" in q_low:
                matching_rests.append("Corner House Ice Creams Bangalore")
            if "brik oven" in q_low:
                matching_rests.append("Brik Oven Bangalore")
            if "asha tiffin" in q_low:
                matching_rests.append("Asha Tiffins Bangalore")
            if "plan b" in q_low:
                matching_rests.append("Plan B Bangalore")
            if "al daaz" in q_low:
                matching_rests.append("Al Daaz Bangalore")
            if "boom pizza" in q_low:
                matching_rests.append("Boom Pizza Bangalore")

            unique_matching = list(dict.fromkeys(matching_rests))
            if len(unique_matching) == 1:
                effective_filter = unique_matching[0]

        chunk_map: Dict[str, Tuple[Any, float]] = {}
        for q_part in [query] + clauses:
            for chunk, score in self.vector_db.search_chunks(
                q_part, restaurant_filter=effective_filter, top_k=top_k
            ):
                prev = chunk_map.get(chunk.chunk_id)
                if prev is None or score > prev[1]:
                    chunk_map[chunk.chunk_id] = (chunk, score)

        # If effective_filter narrowed too much, also include global top chunks
        if effective_filter and len(chunk_map) < 5:
            for chunk, score in self.vector_db.search_chunks(query, restaurant_filter=None, top_k=top_k):
                chunk_map.setdefault(chunk.chunk_id, (chunk, score))

        top_chunks = sorted(chunk_map.values(), key=lambda x: x[1], reverse=True)[: max(top_k, 14)]

        item_map: Dict[str, Tuple[StructuredMenuItem, float, List[str]]] = {}
        per_clause_k = max(6, top_k // max(1, len(clauses)))
        for q_part in clauses + [query]:
            for item, sc, reasons in self.vector_db.search_structured_items(
                q_part, restaurant_filter=effective_filter, top_k=per_clause_k
            ):
                prev = item_map.get(item.item_id)
                if prev is None or sc > prev[1]:
                    item_map[item.item_id] = (item, sc, reasons)

        top_items = sorted(item_map.values(), key=lambda x: x[1], reverse=True)[:22]
        return top_chunks, top_items

    def _build_prompt(
        self,
        query: str,
        top_chunks: List[Tuple[Any, float]],
        top_items: List[Tuple[StructuredMenuItem, float, List[str]]],
    ) -> str:
        items_block_lines = []
        for item, sc, _ in top_items:
            fib_part = f", fiber_g={item.fiber_g}" if item.fiber_g is not None else ", fiber_g=null"
            serv_part = f", serving_size=\"{item.serving_size}\"" if item.serving_size else ""
            items_block_lines.append(
                f'- item_id="{item.item_id}" | restaurant="{item.restaurant}" | category="{item.category}" | '
                f'item_name="{item.item_name}"{serv_part} | '
                f"calories_kcal={item.calories_kcal}, protein_g={item.protein_g}, carbs_g={item.carbs_g}, fat_g={item.fat_g}{fib_part} | "
                f'source="{item.source_file} ({item.source_page_or_section})" | modality="{item.doc_modality}" | match_score={sc}%'
            )

        chunks_block_lines = []
        for chunk, sc in top_chunks:
            chunks_block_lines.append(
                f"--- [Chunk ID: {chunk.chunk_id} | Source: {chunk.source_file} ({chunk.page_or_section}) | "
                f"Modality: {chunk.doc_modality} | Retrieval Score: {round(sc * 100, 1)}%] ---\n"
                f"{chunk.content}"
            )

        return f"""You are a high-precision Restaurant Nutrition RAG Engine for McDonald's India, Subway India, California Burrito India, and any other uploaded restaurant documents.
CRITICAL RULE: DO NOT CALL ANY TOOLS (do not call view_file, run_command, grep, or any other tool). All required data is already provided below. Respond immediately in a single turn with ONLY the raw JSON object.
Accuracy is the #1 requirement. You must NEVER invent or guess macro numbers. Every macro value MUST come directly from the retrieved candidate items or document chunks below.

USER ORDER / QUERY:
{query}

CANDIDATE STRUCTURED MENU ITEMS (FROM TABULAR/HYBRID EXTRACTION):
{chr(10).join(items_block_lines) if items_block_lines else "(None)"}

RETRIEVED DOCUMENT CHUNKS (TABULAR & NON-TABULAR PROSE):
{chr(10).join(chunks_block_lines) if chunks_block_lines else "(None)"}

INSTRUCTIONS:
1. Break down the user's order into every distinct item or customizable component they ordered.
   - If the user ordered a customizable meal (e.g., California Burrito bowl/burrito with specific rice, beans, protein, toppings, or a Subway sub with extra cheese/sauce):
     * Either select the exact individual component rows (e.g. Protein row + Rice row + Beans row + each Topping row) so they sum deterministically to the exact custom meal, OR if the user didn't specify customizations (e.g. just "Regular Grilled BBQ Chicken Rice Bowl" or "6-inch Paneer Tikka Sub"), select the complete standard build row (or standard components) and explicitly document the assumed base/rice/beans/bread/toppings in `assumptions_made`.
   - Pay attention to quantities (e.g. "2 McAloo Tikki burgers" -> quantity: 2.0).
   - If the user's query is a general nutrition/allergen question rather than an order, still populate `resolved_items` with the relevant menu items discussed.
2. For each item in `resolved_items`, provide `matched_item_id` (matching `item_id` from CANDIDATE STRUCTURED MENU ITEMS above whenever applicable) so deterministic verification can lock in the exact numbers.
3. Provide a realistic `llm_confidence` (0 to 100) for each item:
   - 92-100: Exact menu item & portion/size found in official source with no ambiguity.
   - 78-91: Item matched in official source, but minor assumptions were needed (e.g., assumed Regular/Medium size, or assumed standard bread/rice/beans because user didn't specify add-ons).
   - 50-77: Partial/closest menu item match or regional formulation difference (e.g., McDonald's West/South vs North/East slight recipe variation).
   - <50: Item not directly found in documents; closest substitute shown.
4. Return ONLY a valid JSON object matching this exact structure (no markdown fences or extra text outside JSON):
{{
  "answer_summary": "Concise, helpful markdown summary answering the user's query and highlighting key macro takeaways and any regional/customization notes.",
  "resolved_items": [
    {{
      "requested_item": "string (how the user referred to this item/component)",
      "matched_item_id": "string or null (exact item_id from candidate list if matched)",
      "matched_item_name": "string (exact item name from document)",
      "restaurant": "string",
      "category": "string",
      "quantity": 1.0,
      "unit_macros": {{
        "calories_kcal": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "fiber_g": null,
        "serving_size": "string or null"
      }},
      "llm_confidence": 95.0,
      "confidence_reasons": ["Exact match in official PDF table", "Serving size verified"],
      "assumptions_made": ["Assumed standard 6-inch multigrain build without extra mayo"],
      "source_file": "string (filename)",
      "source_location": "string (Page / Table)",
      "doc_modality": "tabular"
    }}
  ],
  "overall_confidence_explanation": "1-2 sentences explaining the overall confidence score, source coverage, and any assumptions.",
  "assumptions_and_notes": [
    "Explicit note on any unstated add-ons, bread/rice/sauce defaults, or regional differences (e.g. McDonald's West/South vs North/East)."
  ]
}}"""

    async def query(self, req: QueryRequest) -> QueryResponse:
        t0 = time.perf_counter()
        top_chunks, top_items = self._gather_candidates(
            req.query, req.restaurant_filter, req.top_k
        )

        catalog_by_id: Dict[str, Tuple[StructuredMenuItem, float, List[str]]] = {
            it.item_id: (it, sc, reasons) for it, sc, reasons in top_items
        }
        # Also allow lookup across all items in vector_db by id
        all_items_by_id: Dict[str, StructuredMenuItem] = {
            it.item_id: it for it in self.vector_db.items
        }

        prompt = self._build_prompt(req.query, top_chunks, top_items)
        provider_req = req.llm_provider or SETTINGS.llm_provider
        model_req = req.llm_model

        try:
            llm_data, provider_used, model_used = await generate_structured_json(
                prompt=prompt,
                provider=provider_req,
                model=model_req,
            )
        except Exception as llm_err:
            logger.error(f"LLM generation failed ({llm_err}); using deterministic catalog fallback.")
            llm_data = self._deterministic_fallback(req.query, top_items)
            provider_used = "deterministic-catalog-fallback"
            model_used = "rule-engine"

        resolved_items: List[ResolvedOrderItem] = []
        raw_items = llm_data.get("resolved_items", []) or []

        for r_item in raw_items:
            matched_id = r_item.get("matched_item_id")
            qty = float(r_item.get("quantity") or 1.0)
            if qty <= 0:
                qty = 1.0

            gt_item: Optional[StructuredMenuItem] = None
            catalog_score: Optional[float] = None
            catalog_reasons: List[str] = []

            if matched_id and matched_id in catalog_by_id:
                gt_item, catalog_score, catalog_reasons = catalog_by_id[matched_id]
            elif matched_id and matched_id in all_items_by_id:
                gt_item = all_items_by_id[matched_id]
                catalog_score = 92.0
                catalog_reasons = [
                    f"Verified in structured catalog ({gt_item.source_file}, {gt_item.source_page_or_section})"
                ]
            else:
                # Try fuzzy matching matched_item_name against top_items to lock in deterministic numbers
                m_name = str(r_item.get("matched_item_name") or "").strip()
                if m_name and top_items:
                    best_match = max(
                        top_items,
                        key=lambda x: fuzz.WRatio(m_name.lower(), x[0].item_name.lower()),
                    )
                    if fuzz.WRatio(m_name.lower(), best_match[0].item_name.lower()) >= 86:
                        gt_item, catalog_score, catalog_reasons = best_match

            assumptions = [str(a) for a in (r_item.get("assumptions_made") or []) if str(a).strip()]
            llm_reasons = [str(c) for c in (r_item.get("confidence_reasons") or []) if str(c).strip()]

            if gt_item is not None:
                unit_macros = MacroBreakdown(
                    calories_kcal=round(gt_item.calories_kcal, 2),
                    protein_g=round(gt_item.protein_g, 2),
                    carbs_g=round(gt_item.carbs_g, 2),
                    fat_g=round(gt_item.fat_g, 2),
                    fiber_g=round(gt_item.fiber_g, 2) if gt_item.fiber_g is not None else None,
                    serving_size=gt_item.serving_size or gt_item.variant_or_size,
                )
                matched_name = gt_item.item_name
                restaurant = gt_item.restaurant
                category = gt_item.category
                source_file = gt_item.source_file
                source_loc = gt_item.source_page_or_section
                doc_modality = gt_item.doc_modality
            else:
                um = r_item.get("unit_macros") or {}
                unit_macros = MacroBreakdown(
                    calories_kcal=round(float(um.get("calories_kcal") or 0.0), 2),
                    protein_g=round(float(um.get("protein_g") or 0.0), 2),
                    carbs_g=round(float(um.get("carbs_g") or 0.0), 2),
                    fat_g=round(float(um.get("fat_g") or 0.0), 2),
                    fiber_g=round(float(um["fiber_g"]), 2) if um.get("fiber_g") is not None else None,
                    serving_size=um.get("serving_size"),
                )
                matched_name = str(r_item.get("matched_item_name") or r_item.get("requested_item") or "Unknown Item")
                restaurant = str(r_item.get("restaurant") or "Unknown Restaurant")
                category = r_item.get("category")
                source_file = str(r_item.get("source_file") or (top_chunks[0][0].source_file if top_chunks else "N/A"))
                source_loc = str(r_item.get("source_location") or (top_chunks[0][0].page_or_section if top_chunks else "N/A"))
                doc_modality = str(r_item.get("doc_modality") or "non_tabular")

            # Deterministic multiplication for total item macros
            total_item_macros = MacroBreakdown(
                calories_kcal=round(unit_macros.calories_kcal * qty, 2),
                protein_g=round(unit_macros.protein_g * qty, 2),
                carbs_g=round(unit_macros.carbs_g * qty, 2),
                fat_g=round(unit_macros.fat_g * qty, 2),
                fiber_g=round(unit_macros.fiber_g * qty, 2) if unit_macros.fiber_g is not None else None,
                serving_size=unit_macros.serving_size,
            )

            # Calibrated composite confidence score (0-100)
            llm_conf = float(r_item.get("llm_confidence") or 85.0)
            top_chunk_score = (top_chunks[0][1] * 100.0) if top_chunks else 70.0
            cat_sc = catalog_score if catalog_score is not None else top_chunk_score

            composite_conf = 0.45 * llm_conf + 0.35 * cat_sc + 0.20 * top_chunk_score
            if gt_item is not None:
                composite_conf = max(composite_conf, min(98.5, 0.55 * llm_conf + 0.45 * cat_sc))
            # Apply slight penalty if assumptions were needed for unstated size/add-ons
            if assumptions:
                composite_conf = min(composite_conf, 93.5)

            composite_conf = round(max(15.0, min(99.5, composite_conf)), 1)
            combined_reasons = list(dict.fromkeys(catalog_reasons + llm_reasons))
            if gt_item is not None:
                combined_reasons.insert(0, "Deterministic macro lock: exact values verified against structured table row")

            resolved_items.append(
                ResolvedOrderItem(
                    requested_item=str(r_item.get("requested_item") or matched_name),
                    matched_item_name=matched_name,
                    restaurant=restaurant,
                    category=category,
                    quantity=qty,
                    unit_macros=unit_macros,
                    total_macros=total_item_macros,
                    confidence_score=composite_conf,
                    confidence_level=_confidence_level(composite_conf),
                    confidence_reasons=combined_reasons[:4],
                    assumptions_made=assumptions,
                    source_file=source_file,
                    source_location=source_loc,
                    doc_modality=doc_modality,
                )
            )

        # Deterministic summation across all items in the meal
        tot_cal = round(sum(i.total_macros.calories_kcal for i in resolved_items), 2)
        tot_prot = round(sum(i.total_macros.protein_g for i in resolved_items), 2)
        tot_carb = round(sum(i.total_macros.carbs_g for i in resolved_items), 2)
        tot_fat = round(sum(i.total_macros.fat_g for i in resolved_items), 2)
        any_fiber = any(i.total_macros.fiber_g is not None for i in resolved_items)
        tot_fiber = (
            round(sum(i.total_macros.fiber_g or 0.0 for i in resolved_items), 2)
            if any_fiber
            else None
        )

        total_macros = MacroBreakdown(
            calories_kcal=tot_cal,
            protein_g=tot_prot,
            carbs_g=tot_carb,
            fat_g=tot_fat,
            fiber_g=tot_fiber,
        )

        if resolved_items:
            overall_conf = round(
                sum(i.confidence_score for i in resolved_items) / len(resolved_items), 1
            )
        elif top_chunks:
            overall_conf = round(top_chunks[0][1] * 100.0, 1)
        else:
            overall_conf = 0.0

        citations = [
            SourceCitation(
                chunk_id=c.chunk_id,
                source_file=c.source_file,
                restaurant=c.restaurant,
                page_or_section=c.page_or_section,
                doc_modality=c.doc_modality,
                retrieval_score=round(sc * 100.0, 1),
                snippet=c.content[:360] + ("..." if len(c.content) > 360 else ""),
            )
            for c, sc in top_chunks[:8]
        ]

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return QueryResponse(
            query=req.query,
            answer_summary=str(llm_data.get("answer_summary") or "Calculated macros from matched restaurant documents."),
            items=resolved_items,
            total_macros=total_macros,
            overall_confidence_score=overall_conf,
            overall_confidence_level=_confidence_level(overall_conf),
            confidence_explanation=str(
                llm_data.get("overall_confidence_explanation")
                or f"Composite confidence ({overall_conf}%) based on hybrid vector + lexical retrieval and deterministic table verification."
            ),
            assumptions_and_notes=[
                str(n) for n in (llm_data.get("assumptions_and_notes") or []) if str(n).strip()
            ],
            citations=citations,
            llm_provider_used=provider_used,
            llm_model_used=model_used,
            index_version=self.vector_db.index_version,
            latency_ms=latency_ms,
        )

    def _deterministic_fallback(
        self, query: str, top_items: List[Tuple[StructuredMenuItem, float, List[str]]]
    ) -> Dict[str, Any]:
        """Fallback if both LLM providers are unreachable: match clauses directly against structured catalog."""
        clauses = _split_order_clauses(query)
        resolved = []
        for clause in clauses:
            qty_m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*x?\s+(.+)$", clause, re.I)
            qty = float(qty_m.group(1)) if qty_m else 1.0
            clean_clause = qty_m.group(2) if qty_m else clause
            matches = self.vector_db.search_structured_items(clean_clause, top_k=1)
            if matches:
                it, sc, reasons = matches[0]
                resolved.append(
                    {
                        "requested_item": clause,
                        "matched_item_id": it.item_id,
                        "matched_item_name": it.item_name,
                        "restaurant": it.restaurant,
                        "category": it.category,
                        "quantity": qty,
                        "unit_macros": {
                            "calories_kcal": it.calories_kcal,
                            "protein_g": it.protein_g,
                            "carbs_g": it.carbs_g,
                            "fat_g": it.fat_g,
                            "fiber_g": it.fiber_g,
                            "serving_size": it.serving_size,
                        },
                        "llm_confidence": sc,
                        "confidence_reasons": reasons,
                        "assumptions_made": ["Matched via deterministic catalog lookup"],
                        "source_file": it.source_file,
                        "source_location": it.source_page_or_section,
                        "doc_modality": it.doc_modality,
                    }
                )
        return {
            "answer_summary": "Matched your order directly against the structured restaurant nutrition catalog.",
            "resolved_items": resolved,
            "overall_confidence_explanation": "Calculated using direct catalog matching and deterministic summation.",
            "assumptions_and_notes": [],
        }
