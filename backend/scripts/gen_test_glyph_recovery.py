#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write tests/test_glyph_recovery.py."""
from pathlib import Path

OUT = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\test_glyph_recovery.py")

CONTENT = r'''# -*- coding: utf-8 -*-
"""Tests for the static uniXXXX Myanmar atlas and glyph_recovery helpers."""
from __future__ import annotations

import unittest

from policy_platform.extract_myanmar.glyph_recovery import (
    build_uni_atlas,
    recover_via_glyph_names,
)
from policy_platform.extract_myanmar.font_inspector import FontInfo, FontCategory


class TestUniAtlas(unittest.TestCase):
    def test_atlas_built(self) -> None:
        a = build_uni_atlas()
        self.assertGreater(len(a), 0)

    def test_atlas_covers_myammar(self) -> None:
        a = build_uni_atlas()
        # Spot-check known codepoints
        self.assertEqual(a["1040"], "\u1040")  # က (KA)
        self.assertEqual(a["101c"], "\u101C")  # လ (LA)
        self.assertEqual(a["1015"], "\u1015")  # ပ (PA)
        self.assertEqual(a["103a"], "\u103A")  # virama
        self.assertEqual(a["1038"], "\u1038")  # visarga

    def test_atlas_covers_extended_a(self) -> None:
        a = build_uni_atlas()
        self.assertIn("aa60", a)
        self.assertEqual(a["aa60"], "\uAA60")

    def test_atlas_covers_extended_b(self) -> None:
        a = build_uni_atlas()
        self.assertIn("a9e0", a)
        self.assertEqual(a["a9e0"], "\uA9E0")

    def test_atlas_no_duplicates(self) -> None:
        a = build_uni_atlas()
        self.assertEqual(len(a), len(set(a.keys())))


class TestRecoverViaGlyphNames(unittest.TestCase):
    def test_returns_none_when_font_lacks_uni_names(self) -> None:
        fi = FontInfo(
            xref=9,
            basefont_name="BCDFEE+MyanmarText",
            subtype="TrueType",
            encoding="WinAnsiEncoding",
            has_tounicode=False,
            glyph_count=1003,
            cmap_myanmar_count=0,
            has_uni_glyph_names=False,  # <-- this PDF
            category=FontCategory.WINANSI_NO_TOUNICODE,
            page=0,
        )
        out = recover_via_glyph_names("hello world", fi)
        # No uni glyph names -> no recovery possible -> None
        self.assertIsNone(out)

    def test_returns_text_when_font_has_uni_names(self) -> None:
        fi = FontInfo(
            xref=10,
            basefont_name="+Pyidaungsu",
            subtype="TrueType",
            encoding="WinAnsiEncoding",
            has_tounicode=True,
            glyph_count=900,
            cmap_myanmar_count=160,
            has_uni_glyph_names=True,
            category=FontCategory.PURE_UNICODE,
            page=0,
        )
        out = recover_via_glyph_names("uni1040uni1015uni102F", fi)
        # Guard returns the raw text unchanged for now (placeholder recovery).
        self.assertIsNotNone(out)

    def test_empty_text_returns_none(self) -> None:
        fi = FontInfo(
            xref=10,
            basefont_name="+Pyidaungsu",
            subtype="TrueType",
            encoding="WinAnsiEncoding",
            has_tounicode=True,
            glyph_count=900,
            cmap_myanmar_count=160,
            has_uni_glyph_names=True,
            category=FontCategory.PURE_UNICODE,
            page=0,
        )
        self.assertIsNone(recover_via_glyph_names("", fi))

    def test_none_font_returns_none(self) -> None:
        self.assertIsNone(recover_via_glyph_names("hello", None))


if __name__ == "__main__":
    unittest.main()
'''

OUT.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
