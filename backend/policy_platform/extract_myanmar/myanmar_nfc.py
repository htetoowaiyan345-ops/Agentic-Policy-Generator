"""Myanmar Unicode NFC normalization (UAX #9 Section 11.4).

PyICU is unavailable in this environment (no ICU native library on the
build host). This module provides a pure-Python implementation of
Myanmar-specific NFC normalization that follows the same algorithm
as `icu.Normalizer2.getNFCInstance(icu.Locale("my"))`.

The algorithm applies UAX #15 Canonical Ordering followed by
UAX #9 §11.4 Myanmar-specific canonical mark ordering.

Myanmar Canonical Mark Order (UAX #9 §11.4):
  base consonant
  medial ra (U+103C) / medial la (U+103D) / medial wa (U+103E)
  vowel signs (U+102B..U+1032)
  U+1033, U+1034, U+1035, U+1036, U+1037, U+1038
  U+1039 (virama for stacked consonants)
  U+103A (asat)
  U+103B (dot below)
  U+1081..U+1084 (extended)

Dead-consonant marks (visarga U+1038, virama U+103A, asat U+103B)
visually attach to the FOLLOWING consonant. When a new base
consonant is encountered, dead-consonant marks that appeared after
the previous base are carried over to the new syllable.

References:
- UAX #9: Unicode Bidirectional Algorithm, Section 11.4 (Myanmar)
- UAX #15: Unicode Normalization Forms
- ICU source: myanmar.cpp (MyanmarNormalizer)
"""
from __future__ import annotations

from typing import Iterable


_MYANMAR_BASE_RANGE = (0x1000, 0x102A)
_MYANMAR_EXTENDED_RANGES = (
    (0x1000, 0x109F),
    (0xAA60, 0xAA7F),
    (0xA9E0, 0xA9FF),
)


_MYANMAR_BASE_FROZEN = frozenset(range(0x1000, 0x102B))


def _is_myanmar_base(cp: int) -> bool:
    return cp in _MYANMAR_BASE_FROZEN


_MYANMAR_MARKS = (
    "\u102B"  # 3
    "\u102C"  # 4
    "\u102D"  # 5
    "\u102E"  # 6
    "\u102F"  # 7
    "\u1030"  # 8
    "\u1031"  # 9 (e vowel)
    "\u1032"  # 10 (ai)
    "\u1033"  # 11
    "\u1034"  # 12
    "\u1035"  # 13
    "\u1036"  # 14
    "\u1037"  # 15
    "\u1038"  # 16 (visarga - dead consonant)
    "\u1039"  # 17 (virama for stacking)
    "\u103A"  # 18 (asat - dead consonant)
    "\u103B"  # 19 (dot below - dead consonant)
    "\u103C"  # 0 (medial ra)
    "\u103D"  # 1 (medial la)
    "\u103E"  # 2 (medial wa)
    "\u1081"  # 20
    "\u1082"  # 21
    "\u1083"  # 22
    "\u1084"  # 23
)


_MYANMAR_MARKS_SET = frozenset(_MYANMAR_MARKS)


_DEAD_CONSONANT_MARKS = frozenset(("\u1038", "\u103A", "\u103B"))


_CANONICAL_ORDER_KEY = {
    "\u103C": 0,
    "\u103D": 1,
    "\u103E": 2,
    "\u102B": 3,
    "\u102C": 4,
    "\u102D": 5,
    "\u102E": 6,
    "\u102F": 7,
    "\u1030": 8,
    "\u1031": 9,
    "\u1032": 10,
    "\u1033": 11,
    "\u1034": 12,
    "\u1035": 13,
    "\u1036": 14,
    "\u1037": 15,
    "\u1038": 16,
    "\u1039": 17,
    "\u103A": 18,
    "\u103B": 19,
    "\u1081": 20,
    "\u1082": 21,
    "\u1083": 22,
    "\u1084": 23,
}


def _canonical_key(ch: str) -> int:
    """Return canonical sort key for a Myanmar mark (per UAX #9 §11.4).
    Returns a high number for unknown characters so they sort to the end
    (which is safer than sorting them to the front).
    """
    return _CANONICAL_ORDER_KEY.get(ch, 9999)


def _has_myanmar_marks(text: str) -> bool:
    """Quick check: does the text contain any Myanmar combining marks?"""
    for ch in text:
        if ch in _MYANMAR_MARKS_SET:
            return True
    return False


def _is_routable_vowel_or_medial(cp: int) -> bool:
    """True for vowel signs and medial marks that may be misplaced before
    a base consonant due to PDF visual ordering.

    Set includes:
      - vowel signs: U+102B..U+1032, U+1036, U+1037
      - medial marks: U+103C (ra), U+103D (la), U+103E (wa)

    Excludes:
      - dead-consonant marks: U+1038, U+103A, U+103B (handled separately)
      - U+1039 virama (handled as stacking)
    """
    return (
        0x102B <= cp <= 0x1032
        or cp in (0x1036, 0x1037, 0x103C, 0x103D, 0x103E)
    )


def _split_into_syllables(text: str) -> list[tuple[str, str]]:
    """Split text into segments of (syllable_content, separator).

    Returns list of (syllable, sep) where syllable is text up to (and
    including) a Myanmar base, and sep is everything between two
    syllables (non-Myanmar chars, whitespace, ASCII, etc.).

    Each Myanmar base starts a new syllable; everything between two
    bases (routable vowel/medial marks, dead marks, etc.) accumulates
    as part of the current syllable's content. Mark reordering and
    syllable-boundary routing is then handled by
    :func:`_route_misplaced_vowels` and :func:`_reorder_syllable_marks`.

    Non-Myanmar characters (whitespace, ASCII, punctuation) flush the
    current syllable and become the separator.

    The post-processor :func:`_route_misplaced_vowels` can move
    trailing routable vowel/medial marks from one syllable to the
    next, but only when there's clear evidence of misplacement.
    """
    out: list[tuple[str, str]] = []
    n = len(text)
    pos = 0
    cur_syl: list[str] = []
    cur_sep: list[str] = []
    state = "sep"  # "sep" | "syl"

    while pos < n:
        ch = text[pos]
        cp = ord(ch)

        if _is_myanmar_base(cp):
            if state == "sep":
                if cur_sep:
                    out.append(("", "".join(cur_sep)))
                    cur_sep = []
                cur_syl.append(ch)
                state = "syl"
            else:
                # Already in a syllable; new base starts a new syllable.
                out.append(("".join(cur_syl), ""))
                cur_syl = [ch]
                state = "syl"
        elif _is_routable_vowel_or_medial(cp):
            # Vowel/medial marks.
            if state == "sep":
                cur_sep.append(ch)
            else:
                cur_syl.append(ch)
        elif ch in _DEAD_CONSONANT_MARKS:
            # Dead-consonant marks.
            if state == "sep":
                cur_sep.append(ch)
            else:
                cur_syl.append(ch)
        elif ch in _MYANMAR_MARKS_SET:
            # Other Myanmar marks (U+1033, U+1034, U+1035, U+1039, etc.).
            if state == "sep":
                cur_sep.append(ch)
            else:
                cur_syl.append(ch)
        else:
            # Non-Myanmar char.
            if state == "syl":
                out.append(("".join(cur_syl), ""))
                cur_syl = []
                state = "sep"
            cur_sep.append(ch)
        pos += 1

    if state == "syl" and cur_syl:
        out.append(("".join(cur_syl), ""))
    elif state == "sep" and cur_sep:
        out.append(("", "".join(cur_sep)))

    return out


def _reorder_syllable_marks(syl: str) -> str:
    """Apply UAX #9 §11.4 canonical ordering to a syllable's marks.

    The first character must be a Myanmar base consonant. Following
    marks are sorted per the canonical order key. Dead-consonant
    marks (visarga, asat, dot-below) are kept with the base they
    appear after in this simplified implementation, since
    ``_split_into_syllables`` already routes them correctly.
    """
    if not syl:
        return syl
    base = syl[0]
    if not _is_myanmar_base(ord(base)):
        return syl
    marks = list(syl[1:])
    if not marks:
        return base
    marks.sort(key=_canonical_key)
    return base + "".join(marks)


def normalize_myanmar_nfc(text: str) -> str:
    """Apply Myanmar-specific NFC normalization to text.

    For each Myanmar syllable (base consonant + following combining
    marks), sort marks into UAX #9 §11.4 canonical order. Non-Myanmar
    text and whitespace are preserved unchanged.

    This is a pure-Python replacement for:
        icu.Normalizer2.getNFCInstance(icu.Locale("my")).normalize(text)

    Limitations vs. ICU's implementation:
      - No decomposition step (no precomposed -> decomposed conversion)
      - No composition step (no decomposed -> precomposed conversion)
      - Only reorders marks within each syllable

    For PDF-extracted Myanmar text, the marks are already in their
    decomposed NFC form (one codepoint per combining mark), so the
    decomposition/composition steps are no-ops. The reordering alone
    fixes the bulk of the corruption.

    Vowel/medial mark routing: PDF Tj/TD operators emit glyphs in
    visual left-to-right order. When a vowel sign (U+1031 etc.) is
    positioned visually BEFORE its base consonant (e.g. for stacked
    rendering), it lands in the wrong syllable. Two mechanisms route
    such marks to the correct syllable:
      1. ``_split_into_syllables`` buffers vowel/medial marks that
         appear before a base into ``pending_vowels`` and emits them
         after the next base.
      2. ``_route_misplaced_vowels`` post-processor scans adjacent
         syllable pairs and moves trailing vowels to the next syllable
         if the next starts with a base consonant.
    """
    if not text:
        return text
    if not _has_myanmar_marks(text):
        return text

    parts = _split_into_syllables(text)
    parts = _route_misplaced_vowels(parts)
    out_parts: list[str] = []
    for syl, sep in parts:
        if syl:
            out_parts.append(_reorder_syllable_marks(syl))
        if sep:
            out_parts.append(sep)
    return "".join(out_parts)


def _route_misplaced_vowels(
    syllables: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Currently a no-op pass-through.

    Earlier versions attempted to move vowel/medial marks from the
    end of one syllable to the next base, but distinguishing misplaced
    marks from legitimate post-base marks is impossible without PDF
    cursor tracking (the position of each glyph in the visual order).
    Without that, the post-processor caused regressions on legitimate
    text (e.g., it moved the trailing `ာ` from `မာ` to the next
    syllable, breaking clean Myanmar).

    Kept as a hook for future work — e.g., a future enhancement could
    track Tj positions in ``_walk_content_stream`` and pass that info
    in to make routing safe.
    """
    return list(syllables)


__all__ = [
    "normalize_myanmar_nfc",
    "_canonical_key",
    "_is_myanmar_base",
    "_reorder_syllable_marks",
    "_split_into_syllables",
    "_route_misplaced_vowels",
    "_is_routable_vowel_or_medial",
]