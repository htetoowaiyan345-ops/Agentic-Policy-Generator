"""Myanmar-aware PDF extraction.

Surface area:
  - `inspect_pdf_fonts(pdf_path)` -> list[FontInfo]
  - `extract_text_smart(pdf_path)` -> TextExtractionResult
  - `compute_corruption_score(text)` -> float
  - `unicode_structural_repair(text)` -> str
  - `recover_via_glyph_names(raw_text, font_info)` -> str | None

Constraints (binding):
  - No OCR
  - No LLM
  - No PDF rewrite / font substitution
"""
from __future__ import annotations

from .corruption_score import compute_corruption_score, indicators_breakdown
from .font_inspector import (
    FontInfo,
    FontCategory,
    PDFQuality,
    inspect_pdf_fonts,
    classify_pdf,
)
from .glyph_recovery import recover_via_glyph_names, build_uni_atlas
from .unicode_repair import unicode_structural_repair, unicode_structural_repair_lines
from .myanmar_extractor import (
    TextExtractionResult,
    extract_text_smart,
    PDF_VERDICT_SAFE,
    PDF_VERDICT_UNSAFE,
    METHOD_PDFPLUMBER,
    METHOD_MYANMAR_RECOVERED,
    METHOD_UNSAFE_HIGH_CORRUPTION,
)

__all__ = [
    "FontInfo",
    "FontCategory",
    "PDFQuality",
    "TextExtractionResult",
    "inspect_pdf_fonts",
    "classify_pdf",
    "recover_via_glyph_names",
    "build_uni_atlas",
    "unicode_structural_repair",
    "unicode_structural_repair_lines",
    "compute_corruption_score",
    "indicators_breakdown",
    "extract_text_smart",
    "PDF_VERDICT_SAFE",
    "PDF_VERDICT_UNSAFE",
    "METHOD_PDFPLUMBER",
    "METHOD_MYANMAR_RECOVERED",
    "METHOD_UNSAFE_HIGH_CORRUPTION",
]
