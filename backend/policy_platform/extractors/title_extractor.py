"""Stage C: simple line-1 fallback for policy title.

When `header_extractor` cannot confidently pick a title (PDF metadata
missing, page-1 largest-line too long or too short), this module
provides a deterministic fallback: take the first non-empty input
paragraph if it has 8-80 characters.

This is deliberately simple:
- It does not try to score lines.
- It does not parse PDF metadata.
- It exists so that single-paragraph inputs (e.g., Earthquake's
  one-paragraph dense input) have a third option between
  "PDF metadata" and "filename fallback".

Behavior:
  - empty input -> None
  - first non-empty line in 8-80 chars -> return that line
  - first non-empty line out of range -> return None
"""
from __future__ import annotations

_MIN_LEN = 8
_MAX_LEN = 80


def extract_title_from_paragraphs(input_paragraphs: list[str]) -> str | None:
    """Take the first non-empty line if its length is 8-80 chars.

    Args:
        input_paragraphs: A sequence of paragraphs (one per visual line
            of cleaned PDF text).

    Returns:
        The first non-empty line if its length is within range, else None.
    """
    for line in input_paragraphs:
        if not line or not line.strip():
            continue
        s = line.strip()
        if _MIN_LEN <= len(s) <= _MAX_LEN:
            return s
        return None
    return None
