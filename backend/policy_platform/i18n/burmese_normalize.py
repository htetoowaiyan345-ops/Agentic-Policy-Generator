"""Light Burmese Unicode normalization.

Goal: ensure that two visually identical Burmese strings have the same
code-point sequence so BM25 tokenization and embedder indexing see them
as the same token.

Operations performed:
  1. NFC normalization (canonical composition).
  2. Strip ZWNJ (U+200C) - typing artifact, not part of Burmese syllable
     structure.
  3. Strip invisible formatting chars (U+200B zero-width space,
     U+200E/U+200F LTR/RTL marks, U+202A-U+202E directional
     formatting). These frequently sneak in from PDF extraction.
  4. PRESERVE ZWJ (U+200D) - it IS part of Burmese syllable structure
     (e.g. stacked consonants, kinzi). Stripping it would break the
     visual identity of common Burmese words.

This is intentionally light. We do NOT attempt Burmese syllable
reordering or kinzi composition - those require either a dedicated
library or carefully tuned rules and are out of scope for the
pass-through architecture.
"""
from __future__ import annotations

import unicodedata


_INVISIBLE_CHARS = (
    "\u200B"  # zero-width space
    "\u200C"  # zero-width non-joiner (stripped)
    "\u200E"  # left-to-right mark
    "\u200F"  # right-to-left mark
    "\u202A"  # left-to-right embedding
    "\u202B"  # right-to-left embedding
    "\u202C"  # pop directional formatting
    "\u202D"  # left-to-right override
    "\u202E"  # right-to-left override
    "\uFEFF"  # byte-order mark / zero-width no-break space
)

# Build a translation table once at import time.
_STRIP_TABLE = str.maketrans("", "", _INVISIBLE_CHARS)


_MYANMAR_RE = __import__("re").compile(
    r"[\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF]"
)


def normalize_burmese(text: str) -> str:
    """Apply light Burmese Unicode normalization.

    Idempotent. Safe to call on non-Burmese text (the regex won't match
    so it returns the input unchanged except for NFC + invisible-char
    strip, which are harmless on Latin text).
    """
    if not text or not isinstance(text, str):
        return text or ""
    # Step 1: NFC.
    text = unicodedata.normalize("NFC", text)
    # Step 2: strip invisible chars (including ZWNJ).
    text = text.translate(_STRIP_TABLE)
    return text


def is_burmese_text(text: str) -> bool:
    """True if the text contains at least one Myanmar-script character."""
    if not text or not isinstance(text, str):
        return False
    return bool(_MYANMAR_RE.search(text))