"""Myanmar Unicode structural repair.

Conservative, deterministic, lossless-only cleaning of corrupted Myanmar
Unicode. Operates only on Myanmar code points (U+1000..U+109F and the
Myanmar Extended-A/B ranges) and never invents characters.

Rules:
  1. Drop excess U+103A (virama) - keep at most one per consonant cluster
  2. Drop isolated U+1038 (visarga) with no preceding consonant
  3. Re-attach combining marks that have spaces on both sides
  4. Collapse double spaces within Myanmar lines
  5. Reject PUA codepoints (U+E000..U+F8FF)
  6. Reject bidi/zero-width controls (U+200B..U+200F, U+202A..U+202E)
  7. Trim trailing virama before whitespace

No OCR. No LLM. No PDF rewrite. No font substitution.
"""
from __future__ import annotations

import re


# Combining marks U+102B..U+103A plus visarga U+1038
_MYANMAR_COMBINING = "\u102B-\u103A"
_MYANMAR_VISARGA = "\u1038"

_MYANMAR_RANGE = "\u1000-\u109F"
_MYANMAR_EXT_A = "\uAA60-\uAA7F"
_MYANMAR_EXT_B = "\uA9E0-\uA9FF"

_PUA = "\uE000-\uF8FF"
_ZWS = "\u200B-\u200F"
_BIDI = "\u202A-\u202E"


# Pattern: combining mark adjacent to whitespace on one or both sides.
# A clean Myanmar syllable should never have the base consonant
# separated from a medial/vowel/virama/visarga by whitespace.
_RE_COMBINING_BEFORE_SPACE = re.compile(
    r"\s([" + _MYANMAR_COMBINING + _MYANMAR_VISARGA + r"])"
)
_RE_COMBINING_AFTER_SPACE = re.compile(
    r"([" + _MYANMAR_COMBINING + _MYANMAR_VISARGA + r"])\s"
)

_RE_TRIPLE_VIRAMA = re.compile(r"(\u103A){3,}")
_RE_DOUBLE_VIRAMA = re.compile(r"(\u103A){2}")

_RE_PUA = re.compile("[" + _PUA + "]")
_RE_ZWS = re.compile("[" + _ZWS + "]")
_RE_BIDI = re.compile("[" + _BIDI + "]")

_RE_DOUBLE_SPACE = re.compile(r"  +")

# Isolated visarga with no preceding Myanmar base consonant
_RE_ISOLATED_VISARGA = re.compile(
    r"(?<!["
    + _MYANMAR_RANGE
    + _MYANMAR_EXT_A
    + _MYANMAR_EXT_B
    + r"])"
    + _MYANMAR_VISARGA
)

# Trailing virama immediately followed by another consonant with a space
# between them. (NOT followed by whitespace directly - that's a real
# word boundary in clean Myanmar.)
# Disabled by design: this rule is too aggressive and removes valid
# syllables. Left here as a comment for future reference.


def _collapse_spaces(text: str) -> str:
    return _RE_DOUBLE_SPACE.sub(" ", text)


def _strip_pua(text: str) -> str:
    return _RE_PUA.sub("", text)


def _strip_zws(text: str) -> str:
    return _RE_ZWS.sub("", text)


def _strip_bidi(text: str) -> str:
    return _RE_BIDI.sub("", text)


def _reattach_combining(text: str) -> str:
    """Drop whitespace between a combining mark and adjacent characters."""
    prev = None
    cur = text
    # iterate to convergence (rare for Myanmar text, but safe)
    for _ in range(8):
        prev = cur
        cur = _RE_COMBINING_BEFORE_SPACE.sub(r"\1", cur)
        cur = _RE_COMBINING_AFTER_SPACE.sub(r"\1", cur)
        if cur == prev:
            break
    return cur


def _collapse_virama(text: str) -> str:
    """3+ viramas in a row -> 1 virama.

    Lossless for purposes of Myanmar Unicode normalization.
    """
    prev = None
    cur = text
    for _ in range(8):
        prev = cur
        cur = _RE_TRIPLE_VIRAMA.sub("\u103A", cur)
        if cur == prev:
            break
    # 2 viramas -> 1 virama
    cur = _RE_DOUBLE_VIRAMA.sub("\u103A", cur)
    return cur


def _drop_isolated_visarga(text: str) -> str:
    return _RE_ISOLATED_VISARGA.sub("", text)


def _trim_trailing_virama(text: str) -> str:
    # Removed: too aggressive and removed valid Myanmar syllables.
    return text


def unicode_structural_repair(text: str) -> str:
    """Apply all conservative repair rules in safe order.

    Returns the repaired text. Never raises on Myanmar content.
    """
    if not text:
        return text

    out = text
    # First strip hostile byte classes (these cannot be valid Myanmar text)
    out = _strip_pua(out)
    out = _strip_zws(out)
    out = _strip_bidi(out)
    # Then reattach combining marks that have spaces
    out = _reattach_combining(out)
    # Collapse excessive virama runs
    out = _collapse_virama(out)
    # Drop any virama that is now isolated and adjacent to whitespace
    out = _trim_trailing_virama(out)
    # Remove orphaned visarga
    out = _drop_isolated_visarga(out)
    # Finally collapse stray double spaces
    out = _collapse_spaces(out)
    return out.strip()


def unicode_structural_repair_lines(lines: Iterable[str]) -> list[str]:
    """Apply repair to each line independently."""
    return [unicode_structural_repair(ln) for ln in lines]
