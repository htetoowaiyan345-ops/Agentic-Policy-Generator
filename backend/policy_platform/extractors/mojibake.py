"""Stage D: targeted mojibake repair for cleaned text.

PDF text extraction sometimes emits the Unicode replacement character
`` (U+FFFD) where the original encoding had a smart punctuation
or an en-dash. This module converts `` to a sensible replacement
based on context:

  1. `` followed by a letter or at the start of a word -> `'`
     (smart apostrophe).
  2. `` surrounded by digits or after a space -> `–` (en-dash).
  3. Otherwise -> `?` (last-resort placeholder).

The original `` (U+FFFD) is also passed through these rules.
The function is deliberately conservative; it never overwrites real
characters or breaks tokens.
"""
from __future__ import annotations

import re


# U+FFFD = Replacement Character produced by CP1252->UTF-8 misreads.
_REPLACEMENT_CHAR = "\ufffd"


# Pattern A: a replacement character followed by a letter -> apostrophe.
# E.g. `Don` + `t worry` -> `Don't worry` (with smart apostrophe).
_PRECEDING_LETTER_RE = re.compile(
    r"(?<=[A-Za-z])" + _REPLACEMENT_CHAR + r"(?=[A-Za-z])"
)


# Pattern B: a replacement character surrounded by digits or spaces -> en-dash.
# E.g. `2026` + ` -` + `Type: Policy` -> `2026 – Type: Policy`.
# We specifically exclude the preceding-letter case so we don't conflict with rule 1.
_DIGITS_OR_SPACE_RE = re.compile(
    r"(?<![A-Za-z])" + _REPLACEMENT_CHAR + r"(?![A-Za-z])"
)


def normalize_mojibake(text: str) -> str:
    """Replace `` with `'`, `–`, or `?` based on context.

    Args:
        text: A cleaned paragraph.

    Returns:
        The same paragraph with `` repaired where applicable.
    """
    if not text:
        return text
    if _REPLACEMENT_CHAR not in text:
        return text
    out = _PRECEDING_LETTER_RE.sub("\u2019", text)
    out = _DIGITS_OR_SPACE_RE.sub("\u2013", out)
    out = out.replace(_REPLACEMENT_CHAR, "?")
    return out
