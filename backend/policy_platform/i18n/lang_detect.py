"""Per-paragraph language detection.

A paragraph is classified as ``en`` (English/Latin-script), ``my``
(Burmese/Myanmar script), or ``mixed`` (both scripts present in
significant proportion).

Detection is Unicode-block-based: we count characters in the Myanmar
script blocks (U+1000-U+109F, U+AA60-U+AA7F, U+A9E0-U+A9FF) vs the
Basic Latin block (U+0041-U+005A, U+0061-U+007A) and apply thresholds.

The function is pure, side-effect free, and safe to call on empty
strings or non-string inputs.
"""
from __future__ import annotations

import re
from typing import Iterable


_MYANMAR_RANGES = (
    (0x1000, 0x109F),  # Myanmar
    (0xAA60, 0xAA7F),  # Myanmar Extended-A
    (0xA9E0, 0xA9FF),  # Myanmar Extended-B
)


def _is_myanmar(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _MYANMAR_RANGES)


_LATIN_RE = re.compile(r"[A-Za-z]")
_MYANMAR_RE = re.compile(r"[\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF]")


def detect_paragraph_lang(text: str) -> str:
    """Return ``"my"`` | ``"en"`` | ``"mixed"`` for the given paragraph.

    Thresholds:
      - >=30% Myanmar chars -> ``"my"``
      - >=10% Myanmar with rest Latin -> ``"mixed"``
      - otherwise -> ``"en"``

    Empty / non-string inputs return ``"en"``.
    """
    if not text or not isinstance(text, str):
        return "en"

    myanmar = len(_MYANMAR_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    # Total meaningful chars for ratio: union (avoid double-counting)
    total = myanmar + latin
    if total == 0:
        return "en"

    myanmar_ratio = myanmar / total

    if myanmar_ratio >= 0.30:
        return "my"
    if myanmar_ratio >= 0.10:
        return "mixed"
    return "en"


def detect_document_lang(paragraphs: Iterable[str]) -> str:
    """Compute the dominant language of a document.

    Returns the dominant per-paragraph language, with ``"mixed"`` if
    no single language clearly dominates. Used to populate
    ``runs.source_lang`` for audit/diagnostic purposes.
    """
    counts = {"en": 0, "my": 0, "mixed": 0}
    total = 0
    for p in paragraphs:
        lang = detect_paragraph_lang(p)
        counts[lang] += 1
        total += 1
    if total == 0:
        return "en"

    # If both en and my have >=10% of paragraphs, treat as bilingual.
    en_share = counts["en"] / total
    my_share = counts["my"] / total
    if en_share >= 0.10 and my_share >= 0.10:
        return "en-my"
    # Else return the dominant one.
    return max(counts, key=counts.get)