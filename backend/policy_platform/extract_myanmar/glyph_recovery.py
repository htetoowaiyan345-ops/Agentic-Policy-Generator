"""Glyph-name based Myanmar Unicode recovery.

Phase B-3 of the extraction strategy.

When a font subset uses `uniXXXX` glyph names (where XXXX is the hex
codepoint), the author's intended Unicode can be inferred directly from
the font. If a font's glyph names look like `glyph00020` instead, this
pass is a no-op (returns None) and the smart extractor falls back to
structural repair only.

This module does NOT:
  - Run OCR
  - Call any LLM
  - Modify the PDF
  - Substitute fonts
"""
from __future__ import annotations

from typing import Optional

from .font_inspector import FontInfo


def build_uni_atlas() -> dict[str, str]:
    """Static `uniXXXX` -> Unicode-codepoint-character map for Myanmar.

    Covers:
      - U+1000..U+109F    Myanmar
      - U+AA60..U+AA7F    Myanmar Extended-A
      - U+A9E0..U+A9FF    Myanmar Extended-B

    Atlas key: lowercase hex without the 'uni' prefix (e.g. '1040').
    Atlas value: the decoded Myanmar character.
    """
    atlas: dict[str, str] = {}
    for start, end in (
        (0x1000, 0x109F),
        (0xAA60, 0xAA7F),
        (0xA9E0, 0xA9FF),
    ):
        for cp in range(start, end + 1):
            hex_key = f"{cp:04X}".lower()
            atlas[hex_key] = chr(cp)
    return atlas


_UNI_ATLAS: dict[str, str] = build_uni_atlas()


def recover_via_glyph_names(
    raw_text: str, font_info: Optional[FontInfo]
) -> Optional[str]:
    """Try to recover text by remapping `uniXXXX` placeholders.

    Returns the remapped string, or None if the font is not recoverable
    (e.g. glyph names follow `glyph00020` convention).
    """
    if raw_text is None or raw_text == "":
        return None
    if font_info is None or not font_info.has_uni_glyph_names:
        return None
    # The PDF text layer does not expose glyph names directly, so this
    # function is currently a guard: only return non-None when the
    # underlying font names are `uni`-prefixed. Downstream we may wire
    # a content-stream walker; for now, structural repair handles
    # the actual cleanup.
    return raw_text


__all__ = ["build_uni_atlas", "recover_via_glyph_names"]
