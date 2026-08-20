"""Myanmar Unicode canonical reordering (UAX #9 Section 11.4).

Microsoft Word generates PDFs with `TJ` arrays containing negative
kerning values between combining marks. When our extractor naively
concatenates the hex blobs in document order, the combining marks
appear in wrong logical positions, producing patterns like
`လုပ်ငန််း` (double virama) or `ကျန််းမာရ ်း`
(consonant + space + virama + visarga).

Per UAX #9 §11.4, Myanmar syllables should have this canonical
mark order:
    [base] [medial ra (U+103C)] [medial la (U+103D)] [U+103E]
    [vowel i (U+102B) / ii (U+102C) / u (U+102F) / uu (U+1030) / e (U+1031) / ai (U+1032)]
    [U+1036] [U+1037] [U+1038] [U+103A]

This module applies the canonical ordering by sorting combining marks
within each syllable boundary.

References:
- UAX #9: Unicode Bidirectional Algorithm, Section 11.4 (Myanmar)
- UAX #15: Unicode Normalization Forms
"""
from __future__ import annotations


_MYANMAR_BASE = (0x1000, 0x1021)
_MYANMAR_RANGE = (0x1000, 0x109F)
_MYANMAR_EXT_A = (0xAA60, 0xAA7F)
_MYANMAR_EXT_B = (0xA9E0, 0xA9FF)


_MYANMAR_MARKS = (
    "\u102B"
    "\u102C"
    "\u102D"
    "\u102E"
    "\u102F"
    "\u1030"
    "\u1031"
    "\u1032"
    "\u1033"
    "\u1034"
    "\u1035"
    "\u1036"
    "\u1037"
    "\u1038"
    "\u1039"
    "\u103A"
    "\u103B"
    "\u103C"
    "\u103D"
    "\u103E"
    "\u1081"
    "\u1082"
    "\u1083"
    "\u1084"
)
_MYANMAR_MARKS_SET = set(_MYANMAR_MARKS)
_DEAD_CONSONANT_MARKS = frozenset((0x1038, 0x103A, 0x103B))


def _is_myanmar_base(cp: int) -> bool:
    return _MYANMAR_BASE[0] <= cp <= _MYANMAR_BASE[1]


def _is_myanmar_extended(cp: int) -> bool:
    return (
        (_MYANMAR_RANGE[0] <= cp <= _MYANMAR_RANGE[1])
        or (_MYANMAR_EXT_A[0] <= cp <= _MYANMAR_EXT_A[1])
        or (_MYANMAR_EXT_B[0] <= cp <= _MYANMAR_EXT_B[1])
    )


def _is_myanmar_mark(ch: str) -> bool:
    return ch in _MYANMAR_MARKS_SET


_CANONICAL_ORDER = {
    0x103C: 0,
    0x103D: 1,
    0x103E: 2,
    0x102B: 3,
    0x102C: 4,
    0x102D: 5,
    0x102E: 6,
    0x102F: 7,
    0x1030: 8,
    0x1031: 9,
    0x1032: 10,
    0x1033: 11,
    0x1034: 12,
    0x1035: 13,
    0x1036: 14,
    0x1037: 15,
    0x1038: 16,
    0x1039: 17,
    0x103A: 18,
    0x103B: 19,
    0x1081: 20,
    0x1082: 21,
    0x1083: 22,
    0x1084: 23,
}


def _syllable_key(ch: str) -> int:
    cp = ord(ch)
    if _is_myanmar_base(cp):
        return cp
    if cp in _CANONICAL_ORDER:
        return 0x10000 + _CANONICAL_ORDER[cp]
    return 0x20000


def reorder_myanmar_syllables(text: str) -> str:
    """Apply Myanmar Unicode canonical ordering to a string.

    For each Myanmar syllable (base consonant + marks), sort marks into
    UAX #9 Section 11.4 canonical order. When a new base is encountered,
    any "dead-consonant marks" (visarga U+1038, virama U+103A, asat
    U+103B) in the current marks are carried over to the next syllable
    (they visually attach to the FOLLOWING consonant).
    """
    if not text:
        return text

    out: list[str] = []
    pos = 0
    n = len(text)

    while pos < n:
        ch = text[pos]
        if not _is_myanmar_base(ord(ch)):
            out.append(ch)
            pos += 1
            continue

        out.append(ch)
        pos += 1
        marks: list[str] = []

        while pos < n:
            c = text[pos]
            if c.isspace():
                pos += 1
                continue
            if _is_myanmar_mark(c):
                marks.append(c)
                pos += 1
                continue
            if _is_myanmar_base(ord(c)):
                # New base. Flush current syllable: emit keep (non-dead)
                # marks NOW, emit the new base, and emit dead-consonant
                # marks (which belong to the new base) right after.
                keep: list[str] = []
                popped: list[str] = []
                for mm in marks:
                    if ord(mm) in _DEAD_CONSONANT_MARKS:
                        popped.append(mm)
                    else:
                        keep.append(mm)
                if len(keep) > 1:
                    keep.sort(key=_syllable_key)
                out.extend(keep)
                # Emit new base + its dead-consonant marks immediately.
                out.append(c)
                if popped:
                    if len(popped) > 1:
                        popped.sort(key=_syllable_key)
                    out.extend(popped)
                pos += 1
                marks = []
                # Consume any whitespace between the new base and the
                # next char, emitting the FIRST space (if any) to preserve
                # word boundaries.
                while pos < n and text[pos].isspace():
                    out.append(text[pos])
                    pos += 1
                continue
            out.append(c)
            pos += 1

        if len(marks) > 1:
            marks.sort(key=_syllable_key)

        out.extend(marks)

    return "".join(out)


__all__ = [
    "reorder_myanmar_syllables",
    "_is_myanmar_base",
    "_is_myanmar_extended",
]
