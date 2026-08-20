"""Glyph-shape based fallback matching for Myanmar text extraction.

When a PDF's embedded TrueType subset uses non-standard glyph names
(e.g., `glyph00510` instead of `uni101F`), the embedded cmap can't
identify the character. Word's MyanmarText PDF output has the additional
problem that /ToUnicode CMap entries are sometimes wrong, so we cannot
trust /ToUnicode alone.

This module matches the unknown glyphs in the embedded subset against
glyphs in a bundled reference font (Noto Sans Myanmar or MyanmarText)
by comparing their visual outline. The match gives us a reliable
Unicode codepoint for each unknown CID.

Comparison strategy (lightweight, no full rasterization):
  1. Normalize both glyphs by translating to origin (bbox-relative).
  2. Resample contour points to a fixed-size 1D vector.
  3. Compute Mean Squared Error between the resampled vectors.
  4. Use bbox dimensions and contour count as hard filters.

Conservative: only accepts matches with high similarity (>0.95).
Falls back to None (no match) for ambiguous cases.

No OCR, no LLM, no PDF rewrite.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fontTools.ttLib import TTFont


_RESAMPLE_SIZE = 64  # number of points sampled per glyph contour
_MIN_CONTOUR_MATCH_RATIO = 0.85  # bbox similarity threshold for filters


@dataclass(frozen=True)
class _GlyphFingerprint:
    """Compact normalized glyph signature."""
    glyph_name: str
    bbox_w: int
    bbox_h: int
    contour_count: int
    point_count: int
    resampled: tuple  # normalized coords resampled to fixed size

    def __hash__(self) -> int:
        return hash((self.glyph_name, self.contour_count, self.point_count,
                     hashlib.md5(str(self.resampled).encode()).hexdigest()))


def _glyph_fingerprint(font: TTFont, glyph_name: str) -> Optional[_GlyphFingerprint]:
    """Compute normalized fingerprint for a glyph in a font."""
    try:
        glyf = font["glyf"]
        if glyph_name not in glyf:
            return None
        glyph = glyf[glyph_name]
        # Skip empty glyphs
        if glyph.numberOfContours == 0 or not hasattr(glyph, "coordinates"):
            return None
        if glyph.coordinates is None:
            return None
        coords = list(glyph.coordinates)
        if not coords:
            return None
        # Normalize: translate so min x,y is at origin
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        min_x, min_y = min(xs), min(ys)
        max_x, max_y = max(xs), max(ys)
        bbox_w = max_x - min_x
        bbox_h = max_y - min_y
        if bbox_w <= 0 or bbox_h <= 0:
            return None
        # Resample contour to fixed-size 1D vector
        # We resample the FIRST contour only (most distinguishing) plus
        # use contour count + total point count for hard filtering.
        end_pts = glyph.endPtsOfContours
        first_contour_end = end_pts[0] + 1 if end_pts else len(coords)
        first_contour = coords[:first_contour_end]
        resampled = _resample_points(first_contour, _RESAMPLE_SIZE)
        # Normalize to unit square: scale by bbox dimensions
        norm = tuple(
            (round((x - min_x) / bbox_w, 3),
             round((y - min_y) / bbox_h, 3))
            for x, y in resampled
        )
        return _GlyphFingerprint(
            glyph_name=glyph_name,
            bbox_w=bbox_w,
            bbox_h=bbox_h,
            contour_count=len(end_pts) if end_pts else 0,
            point_count=len(coords),
            resampled=norm,
        )
    except Exception:
        return None


def _resample_points(coords: list[tuple[int, int]], n: int) -> list[tuple[float, float]]:
    """Resample a contour to N points by uniform arc-length sampling."""
    if len(coords) <= 1:
        return [(float(x), float(y)) for x, y in coords[:n]]
    # Compute cumulative arc length
    arc = [0.0]
    for i in range(1, len(coords)):
        dx = coords[i][0] - coords[i - 1][0]
        dy = coords[i][1] - coords[i - 1][1]
        arc.append(arc[-1] + math.sqrt(dx * dx + dy * dy))
    total = arc[-1]
    if total == 0:
        return [(float(coords[0][0]), float(coords[0][1]))] * n
    out: list[tuple[float, float]] = []
    for i in range(n):
        target = (i / (n - 1)) * total if n > 1 else 0
        # Find segment
        for j in range(1, len(arc)):
            if arc[j] >= target:
                t = (target - arc[j - 1]) / (arc[j] - arc[j - 1]) if arc[j] > arc[j - 1] else 0
                x0, y0 = coords[j - 1]
                x1, y1 = coords[j]
                out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
                break
        else:
            out.append((float(coords[-1][0]), float(coords[-1][1])))
    return out


def _mse(a_vec, b_vec) -> float:
    """Mean squared error between two equal-length vectors."""
    n = min(len(a_vec), len(b_vec))
    if n == 0:
        return 1.0
    total = 0.0
    for i in range(n):
        dx = a_vec[i][0] - b_vec[i][0]
        dy = a_vec[i][1] - b_vec[i][1]
        total += dx * dx + dy * dy
    return total / n


def build_glyph_index(
    reference_font_path: Path,
    unicode_filter: Optional[callable] = None,
) -> dict[str, tuple[cp, fingerprint]]:
    """Build a glyph_name -> (cp, fingerprint) index for a reference font.

    Filters by unicode_filter(cp) -> bool if given (default: accept all Myanmar range).

    Returns dict mapping glyph_name -> (codepoint, fingerprint) so callers
    can match by fingerprint similarity while retrieving the cp.
    """
    try:
        font = TTFont(str(reference_font_path))
    except Exception:
        return {}
    cmap = font.getBestCmap() or {}
    index: dict[str, tuple[int, _GlyphFingerprint]] = {}
    for cp, name in cmap.items():
        if unicode_filter and not unicode_filter(cp):
            continue
        fp = _glyph_fingerprint(font, name)
        if fp is not None:
            index[name] = (cp, fp)
    return index


def match_glyph(
    fp: _GlyphFingerprint,
    reference_index: dict[str, tuple[int, _GlyphFingerprint]],
    bbox_tolerance: float = 0.20,
    contour_tolerance: int = 1,
    point_tolerance: float = 0.25,
) -> Optional[tuple[cp, float]]:
    """Find the best-matching reference glyph for the given fingerprint.

    Returns (codepoint, similarity_score) where similarity_score is in [0, 1]
    (1.0 = perfect match). Returns None if no candidate passes the filters.

    Filters applied:
      - bbox dimensions within ±bbox_tolerance
      - contour count within ±contour_tolerance
      - point count within ±point_tolerance fraction
    """
    if not fp or not reference_index:
        return None
    best_cp: Optional[int] = None
    best_score: float = 0.0
    for ref_name, (cp, ref_fp) in reference_index.items():
        # Hard filter 1: contour count
        if abs(fp.contour_count - ref_fp.contour_count) > contour_tolerance:
            continue
        # Hard filter 2: bbox dimensions
        if ref_fp.bbox_w <= 0 or ref_fp.bbox_h <= 0:
            continue
        w_ratio = abs(fp.bbox_w - ref_fp.bbox_w) / max(fp.bbox_w, ref_fp.bbox_w)
        h_ratio = abs(fp.bbox_h - ref_fp.bbox_h) / max(fp.bbox_h, ref_fp.bbox_h)
        if w_ratio > bbox_tolerance or h_ratio > bbox_tolerance:
            continue
        # Hard filter 3: point count
        if ref_fp.point_count <= 0 or fp.point_count <= 0:
            continue
        p_ratio = abs(fp.point_count - ref_fp.point_count) / max(fp.point_count, ref_fp.point_count)
        if p_ratio > point_tolerance:
            continue
        # Compute similarity
        mse = _mse(fp.resampled, ref_fp.resampled)
        # Convert MSE to similarity in [0, 1]
        # Max expected MSE is ~2 (per-component distance squared). Use exponential decay.
        similarity = math.exp(-2.0 * mse)
        if similarity > best_score:
            best_score = similarity
            best_cp = cp
    if best_cp is None or best_score < 0.95:
        return None
    return best_cp, best_score


def fingerprint_embedded_glyphs(
    embedded_ttf: bytes,
) -> dict[str, _GlyphFingerprint]:
    """Compute fingerprints for all named glyphs in an embedded subset."""
    import io
    try:
        font = TTFont(io.BytesIO(embedded_ttf))
    except Exception:
        return {}
    out: dict[str, _GlyphFingerprint] = {}
    for name in font.getGlyphOrder():
        fp = _glyph_fingerprint(font, name)
        if fp is not None:
            out[name] = fp
    return out


def match_embedded_to_reference(
    embedded_ttf: bytes,
    reference_index: dict[str, tuple[int, _GlyphFingerprint]],
) -> dict[str, int]:
    """For each unknown glyph in the embedded subset, find its Unicode cp.

    Returns dict mapping embedded_glyph_name -> cp. Only includes matches
    with similarity >= 0.95. Names already covered by the embedded font's
    own cmap can be filtered by the caller.
    """
    embedded_fps = fingerprint_embedded_glyphs(embedded_ttf)
    out: dict[str, int] = {}
    for name, fp in embedded_fps.items():
        result = match_glyph(fp, reference_index)
        if result is not None:
            cp, _score = result
            out[name] = cp
    return out


__all__ = [
    "_GlyphFingerprint",
    "build_glyph_index",
    "match_glyph",
    "fingerprint_embedded_glyphs",
    "match_embedded_to_reference",
]