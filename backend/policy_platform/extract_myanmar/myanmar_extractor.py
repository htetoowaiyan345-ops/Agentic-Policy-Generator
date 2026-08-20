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

Phase 1 (pdfplumber fallback):
  When the metadata-recovered text shows low Myanmar character density
  (< 30%) OR fewer than 50 Myanmar codepoints (suggesting empty glyphs,
  broken CID mappings, or composite-decomposition failures that produced
  whitespace), re-extract using pdfplumber and use its output if it has
  ≥ 20% higher Myanmar density than the metadata path. Both extractors
  pass through the same Myanmar NFC normalization and corruption-score
  scoring, so the swap is a fair comparison.

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
from .myanmar_nfc import normalize_myanmar_nfc
from .burmese_reorder import reorder_myanmar_syllables  # noqa: F401  (kept for future use)
from .debug_logging import log_checkpoint


METHOD_PDFPLUMBER = "pdfplumber"
METHOD_MYANMAR_RECOVERED = "myanmar_recovered"
METHOD_UNSAFE_HIGH_CORRUPTION = "unsafe_high_corruption"
METHOD_MYANMAR_REPAIR_ATTEMPTED = "myanmar_repair_attempted"
METHOD_METADATA_RECOVERED = "metadata_recovered"
METHOD_PDFPLUMBER_FALLBACK = "pdfplumber_fallback"

PDF_VERDICT_SAFE = "safe"
PDF_VERDICT_UNSAFE = "unsafe"

# --- Phase 1 quality gate --------------------------------------------------
# Myanmar Unicode block U+1000–U+109F (Myanmar, Myanmar Extended-A).
# Anything in this range counts toward "Myanmar character density".
_MYANMAR_BLOCK_LO = 0x1000
_MYANMAR_BLOCK_HI = 0x109F

# Trigger pdfplumber fallback when EITHER:
#   * primary extraction has Burmese density < 30% of the output, OR
#   * primary extraction has fewer than 50 Myanmar codepoints total.
# The absolute floor avoids triggering on tiny snippets where the
# density ratio is meaningless.
_MYANMAR_DENSITY_THRESHOLD = 0.30
_MYANMAR_ABSOLUTE_MIN = 50

# pdfplumber must beat the metadata path by at least 20% on Myanmar
# density (i.e. pdfplumber_density > metadata_density * 1.2) before it
# replaces the metadata result. Otherwise we keep the metadata result
# (which has already been NFC-normalized, structurally repaired, and
# scored).
_DENSITY_IMPROVEMENT_RATIO = 1.2

# Default location of bundled reference fonts for Myanmar extraction.
# Prefer Noto Sans Myanmar (Google Fonts, modern Unicode-compliant cmap)
# over MyanmarText (older Windows system font with mixed-up ligature
# mappings that propagate through Word's PDF /ToUnicode CMap generation).
_DEFAULT_FULL_FONT_CANDIDATES = [
    Path(__file__).resolve().parent.parent.parent
    / "assets"
    / "fonts"
    / "NotoSansMyanmar.ttf",
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


def _myanmar_stats(text: str) -> tuple[int, float]:
    """Count codepoints in the Myanmar Unicode block and the density.

    Returns ``(myanmar_char_count, density)`` where density is
    ``myanmar_char_count / max(len(text), 1)``. Empty input yields
    ``(0, 0.0)``.
    """
    if not text:
        return 0, 0.0
    lo = _MYANMAR_BLOCK_LO
    hi = _MYANMAR_BLOCK_HI
    count = sum(1 for ch in text if lo <= ord(ch) <= hi)
    return count, count / len(text)


def _needs_pdfplumber_fallback(text: str) -> bool:
    """Decide whether the metadata extraction looks too sparse.

    Triggers when density is below the threshold *or* the absolute
    Myanmar character count is too small to be meaningful.
    """
    count, density = _myanmar_stats(text)
    return density < _MYANMAR_DENSITY_THRESHOLD or count < _MYANMAR_ABSOLUTE_MIN


def _maybe_pdfplumber_fallback(
    pdf_path: Path,
    primary_text: str,
    primary_method: str,
    primary_score: float,
) -> TextExtractionResult:
    """Phase 1 fallback: invoke pdfplumber when metadata path is sparse.

    Returns the pdfplumber ``TextExtractionResult`` only if it has ≥ 20%
    higher Myanmar density than the metadata path's ``primary_text``.
    Otherwise returns a ``TextExtractionResult`` echoing the primary
    values (no method field changed).
    """
    fallback_text = _extract_with_pdfplumber(pdf_path)
    if not fallback_text:
        return TextExtractionResult(
            text=primary_text,
            method=primary_method,
            score=primary_score,
            pdf_verdict=PDF_VERDICT_SAFE,
            fonts=[],
        )

    fallback_text = normalize_myanmar_nfc(fallback_text)
    fb_count, fb_density = _myanmar_stats(fallback_text)
    pri_count, pri_density = _myanmar_stats(primary_text)

    fb_score = compute_corruption_score(fallback_text)

    if fb_density > pri_density * _DENSITY_IMPROVEMENT_RATIO:
        return TextExtractionResult(
            text=fallback_text,
            method=METHOD_PDFPLUMBER_FALLBACK,
            score=fb_score,
            pdf_verdict=PDF_VERDICT_UNSAFE,
            fonts=[],
        )

    # Fallback didn't beat the metadata path; keep the metadata result.
    return TextExtractionResult(
        text=primary_text,
        method=primary_method,
        score=primary_score,
        pdf_verdict=PDF_VERDICT_UNSAFE,
        fonts=[],
    )


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
      2. pdfplumber fallback (Phase 1) when the metadata result is
         sparse in Myanmar characters.
      3. B-3 (glyph-name recovery) + B-4 (structural repair) — last
         resort.
    """
    full_font = _resolve_full_font_path()

    # Try metadata-based recovery first (Phase C)
    if is_extractable(pdf_path):
        try:
            pages = extract_text_via_metadata(pdf_path, full_font)
            joined = "\n".join(p for p in pages if p)
            if joined.strip():
                log_checkpoint("after_normalize", joined)
                # Strip soft-hyphens / NBSPs introduced by the source PDF's
                # text-show operators before structural repair.
                cleaned = normalize_burmese_extraction(joined)
                repaired = unicode_structural_repair(cleaned)
                # Apply Myanmar Unicode canonical reordering (UAX #9 §11.4).
                # PDF content streams concatenate hex blobs in visual TJ
                # order, not Unicode semantic order. The pure-Python
                # NFC normalizer sorts combining marks within each
                # syllable into the canonical position.
                repaired = normalize_myanmar_nfc(repaired)
                log_checkpoint("after_repair", repaired)
                score = compute_corruption_score(repaired)
                primary = TextExtractionResult(
                    text=repaired,
                    method=METHOD_METADATA_RECOVERED,
                    score=score,
                    pdf_verdict=PDF_VERDICT_UNSAFE,
                    fonts=fonts,
                )
                # Phase 1: if the metadata result has suspiciously low
                # Myanmar density, give pdfplumber a chance — only swap
                # if its Myanmar density beats the metadata path by ≥ 20%.
                if _needs_pdfplumber_fallback(repaired):
                    candidate = _maybe_pdfplumber_fallback(
                        pdf_path,
                        repaired,
                        METHOD_METADATA_RECOVERED,
                        score,
                    )
                    # Preserve font diagnostics from the metadata scan.
                    candidate.fonts = fonts
                    return candidate
                return primary
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
    # Apply Myanmar Unicode canonical reordering (UAX #9 §11.4).
    repaired = normalize_myanmar_nfc(repaired)
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
