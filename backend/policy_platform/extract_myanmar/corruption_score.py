"""Myanmar Unicode corruption scoring.

Deterministic heuristic scoring of how likely a Unicode string is to be
fabricated / corrupt Myanmar output from a font fallback path (e.g.
Microsoft Word's `MyanmarText` font with no /ToUnicode cmap).

No OCR. No LLM. No rewrite. Pure measurement.

Used by the smart extractor to decide whether to flag an extraction
as `unsafe_high_corruption` before downstream processing.
"""
from __future__ import annotations

import re
from typing import Iterable


# Myanmar Unicode ranges (conservative subset)
_MYANMAR_BASE = (0x1000, 0x102A)  # Myanmar consonants
_MYANMAR_INDIC_VOWELS = (0x102B, 0x103A)  # U+102B..U+103A medial/vowel/virama
_MYANMAR_SIGN_VISARGA = 0x1038
_PUA_MIN = 0xE000
_PUA_MAX = 0xF8FF

_VIRAMA = "\u103A"
_VISARGA = "\u1038"


def _myanmar_consonant_count(text: str) -> int:
    """Count Myanmar base consonants (U+1000..U+102A)."""
    return sum(1 for ch in text if _MYANMAR_BASE[0] <= ord(ch) <= _MYANMAR_BASE[1])


def _virama_count(text: str) -> int:
    return text.count(_VIRAMA)


def _visarga_count(text: str) -> int:
    return text.count(_VISARGA)


def _excess_virama_count(text: str) -> int:
    """Count occurrences of 2+ viramas in a row."""
    return len(re.findall(r"\u103A{2,}", text))


def _combining_with_spaces_count(text: str) -> int:
    """Count combining marks adjacent to whitespace on one or both sides.

    A clean Myanmar syllable should never have spaces between the base
    consonant and a combining medial/vowel/virama.
    """
    # mark between two spaces
    pat1 = re.compile(r"\s[\u102B-\u103A]\s")
    # mark immediately preceded or followed by whitespace
    pat2 = re.compile(r"\s[\u102B-\u103A]|[\u102B-\u103A]\s")
    return len(set(pat1.findall(text)) | set(pat2.findall(text)))


def _pua_count(text: str) -> int:
    return sum(1 for ch in text if _PUA_MIN <= ord(ch) <= _PUA_MAX)


def _indicator_excess_virama(text: str) -> float:
    """Score for abnormally frequent U+103A. Caps at 1.0."""
    if not text:
        return 0.0
    n_virama = _virama_count(text)
    # > 5 viramas per 100 chars is suspicious
    per_100 = (n_virama * 100.0) / max(1, len(text))
    if per_100 <= 2.0:
        return 0.0
    # saturate at 12 per 100
    return min(1.0, (per_100 - 2.0) / 10.0)


def _indicator_double_virama(text: str) -> float:
    """Score for 2+ viramas in a row (invalid Myanmar)."""
    if not text:
        return 0.0
    n_doubles = _excess_virama_count(text)
    # saturate at 10 occurrences
    return min(1.0, n_doubles / 10.0)


def _indicator_combining_with_space(text: str) -> float:
    if not text:
        return 0.0
    n = _combining_with_spaces_count(text)
    # saturate at 12 occurrences
    return min(1.0, n / 12.0)


def _indicator_virama_to_base_ratio(text: str) -> float:
    """Score for virama_count / consonant_count > 0.5."""
    if not text:
        return 0.0
    n_virama = _virama_count(text)
    n_consonants = _myanmar_consonant_count(text)
    if n_consonants == 0:
        return 0.0
    ratio = n_virama / n_consonants
    if ratio <= 0.5:
        return 0.0
    # saturate at ratio == 1.5
    return min(1.0, (ratio - 0.5) / 1.0)


def _indicator_pua_contamination(text: str) -> float:
    if not text:
        return 0.0
    n_pua = _pua_count(text)
    # any PUA bytes are suspicious
    if n_pua == 0:
        return 0.0
    return min(1.0, n_pua / 20.0)


# weights
_W_EXCESS_VIRAMA = 0.30
_W_DOUBLE_VIRAMA = 0.20
_W_COMBINING_SPACE = 0.25
_W_VIRAMA_RATIO = 0.15
_W_PUA = 0.10


def compute_corruption_score(text: str) -> float:
    """Return a float in [0.0, 1.0] estimating Myanmar Unicode corruption.

    Score 0.0 means clean Myanmar Unicode.
    Score 1.0 means severely over-emitted virama / clearly fabricated.

    Threshold 0.5 is the recommended "route to flag" cutoff.
    """
    if not text:
        return 0.0
    s1 = _indicator_excess_virama(text) * _W_EXCESS_VIRAMA
    s2 = _indicator_double_virama(text) * _W_DOUBLE_VIRAMA
    s3 = _indicator_combining_with_space(text) * _W_COMBINING_SPACE
    s4 = _indicator_virama_to_base_ratio(text) * _W_VIRAMA_RATIO
    s5 = _indicator_pua_contamination(text) * _W_PUA
    return min(1.0, s1 + s2 + s3 + s4 + s5)


def indicators_breakdown(text: str) -> dict:
    """Return raw indicator counts and per-indicator scores.

    Useful for diagnostics; tests, debug endpoints, and reports.
    """
    return {
        "length": len(text),
        "virama_count": _virama_count(text),
        "visarga_count": _visarga_count(text),
        "consonant_count": _myanmar_consonant_count(text),
        "excess_virama_sequences": _excess_virama_count(text),
        "combining_with_space_count": _combining_with_spaces_count(text),
        "pua_count": _pua_count(text),
        "scores": {
            "excess_virama": _indicator_excess_virama(text),
            "double_virama": _indicator_double_virama(text),
            "combining_space": _indicator_combining_with_space(text),
            "virama_ratio": _indicator_virama_to_base_ratio(text),
            "pua": _indicator_pua_contamination(text),
        },
        "weighted_sum": compute_corruption_score(text),
    }
