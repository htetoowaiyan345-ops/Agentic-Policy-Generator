"""Smart Myanmar-aware PDF text extraction.

Decides whether to use plain Unicode extraction (safe PDFs) or to run
the Myanmar-aware recovery path (unsafe PDFs).

Constraints (binding):
  - No OCR
  - No LLM
  - No PDF rewrite / font substitution

Recovery priority (added Phase C):
  1. /ToUnicode CMap (Type0 fonts) — authoritative, used by the bulk of
     Burmese documents.
  2. Embedded TrueType subset's post->cmap bridge with bundled
     MyanmarText.ttf fallback for `uniXXXX` glyph names.
  3. Structural Unicode repair on what PyMuPDF/pdfplumber emit (the
     legacy fallback).

Diagnostic surface (returned as JSON in /api/upload response):
  - extraction_method
  - corruption_score
  - myanmar_fonts (list of font descriptors)
  - pdf_verdict (safe | unsafe)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from .corruption_score import compute_corruption_score
from .font_inspector import (
    FontInfo,
    inspect_pdf_fonts,
    classify_pdf,
    PDFQuality,
)
from .glyph_recovery import recover_via_glyph_names
from .metadata_extractor import extract_text_via_metadata, is_extractable
from .unicode_repair import unicode_structural_repair
from .burmese_pipeline import normalize_burmese_extraction


METHOD_PDFPLUMBER = "pdfplumber"
METHOD_MYANMAR_RECOVERED = "myanmar_recovered"
METHOD_UNSAFE_HIGH_CORRUPTION = "unsafe_high_corruption"
METHOD_MYANMAR_REPAIR_ATTEMPTED = "myanmar_repair_attempted"
METHOD_METADATA_RECOVERED = "metadata_recovered"

PDF_VERDICT_SAFE = "safe"
PDF_VERDICT_UNSAFE = "unsafe"

# Default location of bundled full MyanmarText.ttf (Phase C).
_DEFAULT_FULL_FONT_CANDIDATES = [
    Path(__file__).resolve().parent.parent.parent
    / "assets"
    / "fonts"
    / "MyanmarText.ttf",
]

# Score above which we mark extraction as unsafe high corruption.
# Even above this threshold we still return the repaired text (with
# the diagnostic flag), per Decision 1(a).
_CORRUPTION_THRESHOLD = 0.30
_CORRUPTION_HIGH_FLAG = 0.50


@dataclass
class TextExtractionResult:
    """Result of `extract_text_smart`.

    `text` is the final Unicode string. It is always non-None for
    a readable PDF; for unreadable PDFs the field is an empty string.

    `method` is one of METHOD_* constants.

    `pdf_verdict` is PDF_VERDICT_SAFE or PDF_VERDICT_UNSAFE.
    """
    text: str = ""
    method: str = METHOD_PDFPLUMBER
    score: float = 0.0
    pdf_verdict: str = PDF_VERDICT_SAFE
    fonts: list[FontInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fonts"] = [f.to_dict() if hasattr(f, "to_dict") else f for f in self.fonts]
        return d


def _extract_with_pymupdf(pdf_path: Path) -> str:
    """Run PyMuPDF text extraction (preserves page boundaries as newlines)."""
    import fitz
    parts: list[str] = []
    try:
        doc = fitz.open(str(pdf_path))
        try:
            for page in doc:
                txt = page.get_text("text") or ""
                if txt:
                    parts.append(txt)
        finally:
            doc.close()
    except Exception:
        return ""
    return "\n".join(parts)


def _extract_with_pdfplumber(pdf_path: Path) -> str:
    """Run pdfplumber extraction as a fallback."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    parts: list[str] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                if txt:
                    parts.append(txt)
    except Exception:
        return ""
    return "\n".join(parts)


def _baseline_extract(pdf_path: Path) -> str:
    """Use whichever extractor is available; PyMuPDF first."""
    return _extract_with_pymupdf(pdf_path) or _extract_with_pdfplumber(pdf_path)


def _myanmar_unsafe_fonts(fonts: Iterable[FontInfo]) -> list[FontInfo]:
    """Filter the font list to only the Myanmar-related unsafe fonts."""
    return [f for f in fonts if f.category.value in (
        "WINANSI_NO_TOUNICODE",
        "IDENTITY_H_NO_TOUNICODE",
    )]


def _choose_recovery_font(
    raw_text: str, fonts: list[FontInfo]
) -> FontInfo | None:
    """Pick the first Myanmar-capable unsafe font that allows recovery."""
    unsafe_my = _myanmar_unsafe_fonts(fonts)
    for fi in unsafe_my:
        if fi.has_uni_glyph_names:
            return fi
    return None


def _safe_extract(pdf_path: Path, quality: PDFQuality) -> TextExtractionResult:
    """Path for SAFE PDFs: return the verbatim Unicode text."""
    text = _baseline_extract(pdf_path)
    return TextExtractionResult(
        text=text,
        method=METHOD_PDFPLUMBER,
        score=0.0,
        pdf_verdict=PDF_VERDICT_SAFE,
        fonts=[],
    )


def _resolve_full_font_path() -> Path | None:
    """Return the first existing path from _DEFAULT_FULL_FONT_CANDIDATES,
    or None if no bundled MyanmarText.ttf is present.
    """
    env = os.environ.get("MYANMAR_FULL_FONT_PATH")
    if env:
        p = Path(env)
        if p.exists():
            return p
    for cand in _DEFAULT_FULL_FONT_CANDIDATES:
        if cand.exists():
            return cand
    return None


def _unsafe_extract(
    pdf_path: Path, fonts: list[FontInfo]
) -> TextExtractionResult:
    """Path for UNSAFE PDFs.

    Recovery priority:
      1. metadata_extractor (ToUnicode + embedded cmap bridge) — best.
      2. B-3 (glyph-name recovery) + B-4 (structural repair) — fallback.
    """
    full_font = _resolve_full_font_path()

    # Try metadata-based recovery first (Phase C)
    if is_extractable(pdf_path):
        try:
            pages = extract_text_via_metadata(pdf_path, full_font)
            joined = "\n".join(p for p in pages if p)
            if joined.strip():
                # Strip soft-hyphens / NBSPs introduced by the source PDF's
                # text-show operators before structural repair.
                cleaned = normalize_burmese_extraction(joined)
                repaired = unicode_structural_repair(cleaned)
                score = compute_corruption_score(repaired)
                return TextExtractionResult(
                    text=repaired,
                    method=METHOD_METADATA_RECOVERED,
                    score=score,
                    pdf_verdict=PDF_VERDICT_UNSAFE,
                    fonts=fonts,
                )
        except Exception:
            pass

    raw = _baseline_extract(pdf_path)
    if not raw:
        return TextExtractionResult(
            text="",
            method=METHOD_PDFPLUMBER,
            score=1.0,
            pdf_verdict=PDF_VERDICT_UNSAFE,
            fonts=fonts,
        )

    # B-3
    candidate_font = _choose_recovery_font(raw, fonts)
    candidate_text = recover_via_glyph_names(raw, candidate_font)
    pre_repair_text = candidate_text if candidate_text is not None else raw
    pre_method = (
        METHOD_MYANMAR_RECOVERED
        if candidate_text is not None
        else METHOD_MYANMAR_REPAIR_ATTEMPTED
    )

    # B-4 always runs on unsafe PDFs.
    repaired = unicode_structural_repair(pre_repair_text)
    score = compute_corruption_score(repaired)

    # Revert to the unrepaired raw if repair made it worse.
    raw_score = compute_corruption_score(raw)
    if score > raw_score + 0.05:
        repaired = raw
        score = raw_score
        pre_method = METHOD_PDFPLUMBER

    # Final method selection
    if score >= _CORRUPTION_HIGH_FLAG:
        method = METHOD_UNSAFE_HIGH_CORRUPTION
    elif score < _CORRUPTION_THRESHOLD:
        method = pre_method
    else:
        method = (
            METHOD_MYANMAR_RECOVERED
            if candidate_text is not None
            else METHOD_MYANMAR_REPAIR_ATTEMPTED
        )

    return TextExtractionResult(
        text=repaired,
        method=method,
        score=score,
        pdf_verdict=PDF_VERDICT_UNSAFE,
        fonts=fonts,
    )


def extract_text_smart(pdf_path: Path) -> TextExtractionResult:
    """Decide safe vs. unsafe path based on the PDF's font inventory.

    - SAFE  -> verbatim Unicode via existing extractor (pdfplumber/PyMuPDF)
    - UNSAFE -> structural repair + honest flagging
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return TextExtractionResult(
            text="",
            method=METHOD_PDFPLUMBER,
            score=1.0,
            pdf_verdict=PDF_VERDICT_SAFE,
            fonts=[],
        )

    fonts = inspect_pdf_fonts(pdf_path)
    quality = classify_pdf(fonts)

    if quality.verdict == "safe":
        result = _safe_extract(pdf_path, quality)
        # Even safe PDFs may carry their full font list for diagnostics.
        result.fonts = fonts
        return result

    return _unsafe_extract(pdf_path, fonts)
