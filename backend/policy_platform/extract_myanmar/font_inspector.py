"""PDF font analysis for Myanmar-aware extraction.

For every embedded font in every page of a PDF, capture:
  - font name (BaseFont)
  - encoding (/Encoding)
  - to_unicode presence
  - subtype
  - glyph count
  - cmap coverage of Myanmar Unicode ranges
  - whether glyph names follow uniXXXX convention

Produce a list of FontInfo records and a PDF-level quality verdict.

No OCR. No LLM. No PDF modification.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF

try:
    from fontTools.ttLib import TTFont
except ImportError as e:
    raise RuntimeError(
        "fontTools is required for Myanmar-aware extraction: pip install fonttools"
    ) from e


# Myanmar Unicode ranges
_RANGE_MYANMAR = (0x1000, 0x109F)
_RANGE_MYANMAR_EXT_A = (0xAA60, 0xAA7F)
_RANGE_MYANMAR_EXT_B = (0xA9E0, 0xA9FF)


class FontCategory(str, Enum):
    """How safe a font is for verbatim Unicode extraction."""
    PURE_UNICODE = "PURE_UNICODE"          # has ToUnicode + Myanmar cmap entries
    IDENTITY_H_TOUNICODE = "IDENTITY_H_TOUNICODE"  # Identity-H + ToUnicode
    WINANSI_NO_TOUNICODE = "WINANSI_NO_TOUNICODE"  # WinAnsi + no ToUnicode (unsafe)
    IDENTITY_H_NO_TOUNICODE = "IDENTITY_H_NO_TOUNICODE"  # Identity-H, no ToUnicode (unsafe)
    LATIN = "LATIN"                        # non-Myanmar font, only Latin
    STANDARD_LATIN = "STANDARD_LATIN"      # Calibri / Arial / Helvetica / Times


# Safe categories are those that produce reliable Unicode.
_SAFE_CATEGORIES = {
    FontCategory.PURE_UNICODE,
    FontCategory.IDENTITY_H_TOUNICODE,
    FontCategory.LATIN,
    FontCategory.STANDARD_LATIN,
}


# Known Latin font family names (case-insensitive substring match).
_LATIN_FONT_HINTS = (
    "calibri",
    "arial",
    "helvetica",
    "times",
    "tahoma",
    "verdana",
    "georgia",
    "courier",
    "inter",
    "roboto",
    "segoeui",
    "notosans-regular",
    "notosans",
)


def _is_latin_font(name: str) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in _LATIN_FONT_HINTS)


def _encoding_label(encoding_obj) -> str:
    """Best-effort encoding label from a fitz-supplied /Encoding string."""
    if encoding_obj is None:
        return "UNKNOWN"
    if isinstance(encoding_obj, str):
        return encoding_obj
    # PyMuPDF can return a 3-tuple (place, ordinal, ref)
    if isinstance(encoding_obj, (list, tuple)):
        return " | ".join(str(x) for x in encoding_obj)
    return str(encoding_obj)


@dataclass
class FontInfo:
    """Diagnostic record for one embedded font on one page."""
    xref: int
    basefont_name: str
    subtype: str
    encoding: str
    has_tounicode: bool
    glyph_count: int
    cmap_myanmar_count: int
    has_uni_glyph_names: bool
    category: FontCategory
    page: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        return d


def _classify_font(
    name: str,
    encoding: str,
    has_tounicode: bool,
    cmap_myanmar_count: int,
    has_uni_glyph_names: bool,
) -> FontCategory:
    if _is_latin_font(name):
        return FontCategory.STANDARD_LATIN
    if not has_tounicode:
        if encoding == "WinAnsiEncoding":
            return FontCategory.WINANSI_NO_TOUNICODE
        if encoding == "Identity-H":
            return FontCategory.IDENTITY_H_NO_TOUNICODE
        # Unknown encoding, no ToUnicode - safest assumption: unsafe
        return FontCategory.WINANSI_NO_TOUNICODE
    # has toUnicode
    if encoding == "Identity-H":
        return FontCategory.IDENTITY_H_TOUNICODE
    # If has ToUnicode AND cmap_myanmar_count > 0, definitely pure unicode
    if cmap_myanmar_count > 0:
        return FontCategory.PURE_UNICODE
    # has ToUnicode but no Myanmar cmap entries (e.g. Latin font)
    return FontCategory.LATIN


def _read_embedded_ttf(doc: fitz.Document, xref: int):
    """Return the embedded TTF subset bytes or None on failure."""
    try:
        info = doc.extract_font(xref)
        if isinstance(info, tuple) and len(info) >= 4:
            return info[3]
        # Some versions of PyMuPDF return dict
        if isinstance(info, dict):
            return info.get("content")
    except Exception:
        return None
    return None


def _font_metrics_from_ttf(ttf_bytes: bytes) -> dict:
    """Open a TTF subset and read glyph count + cmap coverage + glyph names."""
    if not ttf_bytes:
        return {
            "glyph_count": 0,
            "cmap_myanmar_count": 0,
            "has_uni_glyph_names": False,
        }
    try:
        font = TTFont(io.BytesIO(ttf_bytes))
    except Exception:
        return {
            "glyph_count": 0,
            "cmap_myanmar_count": 0,
            "has_uni_glyph_names": False,
        }
    cmap = font["cmap"].getBestCmap() or {}
    names = font.getGlyphOrder() or []
    myanmar = sum(
        1
        for cp in cmap
        if (
            _RANGE_MYANMAR[0] <= cp <= _RANGE_MYANMAR[1]
            or _RANGE_MYANMAR_EXT_A[0] <= cp <= _RANGE_MYANMAR_EXT_A[1]
            or _RANGE_MYANMAR_EXT_B[0] <= cp <= _RANGE_MYANMAR_EXT_B[1]
        )
    )
    has_uni = any(n.lower().startswith("uni") for n in names)
    return {
        "glyph_count": len(names),
        "cmap_myanmar_count": myanmar,
        "has_uni_glyph_names": has_uni,
    }


def _xref_has_tounicode(doc: fitz.Document, xref: int) -> bool:
    """Inspect the font dictionary for /ToUnicode by reading the xref dict."""
    try:
        s = doc.xref_object(xref) or ""
        return "/ToUnicode" in s
    except Exception:
        return False


def _font_dict_for_page(page) -> list[tuple]:
    """Return a list of (xref, ext, subtype, basefont, name, encoding) tuples."""
    try:
        return page.get_fonts()
    except Exception:
        return []


def inspect_pdf_fonts(pdf_path: Path) -> list[FontInfo]:
    """Walk every page and inspect every embedded font.

    Returns one FontInfo per (page, font xref) combination.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    out: list[FontInfo] = []
    try:
        doc = fitz.open(str(pdf_path))
        try:
            for pi, page in enumerate(doc):
                fonts = _font_dict_for_page(page)
                for record in fonts:
                    xref = record[0]
                    basefont = record[3] or ""
                    name = record[4] or basefont
                    subtype = record[2] or ""
                    encoding = _encoding_label(record[5])
                    has_tounicode = _xref_has_tounicode(doc, xref)
                    ttf = _read_embedded_ttf(doc, xref)
                    metrics = _font_metrics_from_ttf(ttf) if ttf else {
                        "glyph_count": 0,
                        "cmap_myanmar_count": 0,
                        "has_uni_glyph_names": False,
                    }
                    category = _classify_font(
                        name=name,
                        encoding=encoding,
                        has_tounicode=has_tounicode,
                        cmap_myanmar_count=metrics["cmap_myanmar_count"],
                        has_uni_glyph_names=metrics["has_uni_glyph_names"],
                    )
                    out.append(
                        FontInfo(
                            xref=xref,
                            basefont_name=basefont,
                            subtype=subtype,
                            encoding=encoding,
                            has_tounicode=has_tounicode,
                            glyph_count=metrics["glyph_count"],
                            cmap_myanmar_count=metrics["cmap_myanmar_count"],
                            has_uni_glyph_names=metrics["has_uni_glyph_names"],
                            category=category,
                            page=pi,
                        )
                    )
        finally:
            doc.close()
    except Exception:
        # If PyMuPDF cannot open the file at all, return empty list.
        return []
    return out


@dataclass
class PDFQuality:
    """Verdict for the whole PDF based on its font inventory."""
    verdict: str  # "safe" | "unsafe"
    safe_categories: list[FontCategory] = field(default_factory=list)
    unsafe_categories: list[FontCategory] = field(default_factory=list)
    has_my_safe_fonts: bool = False
    has_my_unsafe_fonts: bool = False


def classify_pdf(fonts: Iterable[FontInfo]) -> PDFQuality:
    """Classify PDF as safe/unsafe based on font inventory."""
    safe: list[FontCategory] = []
    unsafe: list[FontCategory] = []
    has_my_safe = False
    has_my_unsafe = False
    for fi in fonts:
        if fi.category in _SAFE_CATEGORIES:
            safe.append(fi.category)
            # Myanmar-capable safe fonts are still Myanmar-relevant
            if fi.category in (
                FontCategory.PURE_UNICODE,
                FontCategory.IDENTITY_H_TOUNICODE,
            ):
                has_my_safe = True
        else:
            unsafe.append(fi.category)
            has_my_unsafe = True
    verdict = "safe" if not has_my_unsafe else "unsafe"
    return PDFQuality(
        verdict=verdict,
        safe_categories=sorted({c.value for c in safe}),
        unsafe_categories=sorted({c.value for c in unsafe}),
        has_my_safe_fonts=has_my_safe,
        has_my_unsafe_fonts=has_my_unsafe,
    )
