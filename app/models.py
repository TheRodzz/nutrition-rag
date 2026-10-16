from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class MacroBreakdown(BaseModel):
    calories_kcal: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fiber_g: Optional[float] = None
    serving_size: Optional[str] = None


class StructuredMenuItem(BaseModel):
    item_id: str
    restaurant: str
    category: str
    item_name: str
    variant_or_size: Optional[str] = None
    calories_kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: Optional[float] = None
    serving_size: Optional[str] = None
    allergens: List[str] = Field(default_factory=list)
    dietary_tags: List[str] = Field(default_factory=list)
    source_file: str
    source_page_or_section: str
    doc_modality: str = "tabular"  # "tabular" or "non_tabular"
    raw_text: str = ""


class DocumentChunk(BaseModel):
    chunk_id: str
    source_file: str
    restaurant: str
    page_or_section: str
    doc_modality: str  # "tabular" or "non_tabular"
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentDetectionReport(BaseModel):
    filename: str
    file_type: str
    file_size_bytes: int
    sha256: str
    detected_modality: str  # "tabular", "non_tabular", or "hybrid"
    tabular_confidence: float
    detection_reasoning: str
    restaurant_detected: str
    total_pages_or_sections: int
    tabular_sections_count: int
    prose_sections_count: int
    extracted_items_count: int
    created_chunks_count: int
    indexed_at: str


class ResolvedOrderItem(BaseModel):
    requested_item: str
    matched_item_name: str
    restaurant: str
    category: Optional[str] = None
    quantity: float = 1.0
    unit_macros: MacroBreakdown
    total_macros: MacroBreakdown
    confidence_score: float = Field(description="0 to 100 confidence score for this item")
    confidence_level: str = Field(description="High, Medium, or Low")
    confidence_reasons: List[str] = Field(default_factory=list)
    assumptions_made: List[str] = Field(default_factory=list)
    source_file: str
    source_location: str
    doc_modality: str


class QueryRequest(BaseModel):
    query: str
    restaurant_filter: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    top_k: int = 12


class SourceCitation(BaseModel):
    chunk_id: str
    source_file: str
    restaurant: str
    page_or_section: str
    doc_modality: str
    retrieval_score: float
    snippet: str


class QueryResponse(BaseModel):
    query: str
    answer_summary: str
    items: List[ResolvedOrderItem] = Field(default_factory=list)
    total_macros: MacroBreakdown
    overall_confidence_score: float = Field(description="0 to 100 overall confidence score")
    overall_confidence_level: str
    confidence_explanation: str
    assumptions_and_notes: List[str] = Field(default_factory=list)
    citations: List[SourceCitation] = Field(default_factory=list)
    llm_provider_used: str
    llm_model_used: str
    index_version: int
    latency_ms: int
