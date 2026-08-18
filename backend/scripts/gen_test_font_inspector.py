#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write tests/test_font_inspector.py."""
from pathlib import Path

OUT = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\test_font_inspector.py")

# Real fixture PDF (sanitized/redacted copy lives in tests/fixtures/).
# We allow the test to fall back to the user's source PDF if no
# sanitized fixture is present yet.
USER_PDF = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
FIXTURE_PDF = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\fixtures\HR_00002_redacted.pdf")

CONTENT = r'''# -*- coding: utf-8 -*-
"""Tests for font_inspector.inspect_pdf_fonts and classify_pdf.

These tests use the user's real PDF (or a sanitized copy committed
to `tests/fixtures/`) to verify that font categories are computed
correctly. They are skipped when no fixture is available.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from policy_platform.extract_myanmar.font_inspector import (
    FontInfo,
    FontCategory,
    inspect_pdf_fonts,
    classify_pdf,
    PDFQuality,
)


USER_PDF = Path(r"C:\\Users\\htetoowaiyan\\Downloads\\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
FIXTURE_PDF = Path(r"D:\\Htet Oo Wai Yan\\OneDrive - City Holdings Limited\\Desktop\\agentic-policy-platform - Copy (4)\\backend\\tests\\fixtures\\HR_00002_redacted.pdf")


def _pick_pdf() -> Path | None:
    if FIXTURE_PDF.exists():
        return FIXTURE_PDF
    if USER_PDF.exists():
        return USER_PDF
    return None


class TestInspectPDF(unittest.TestCase):
    def setUp(self) -> None:
        self.pdf = _pick_pdf()
        if self.pdf is None:
            self.skipTest("No PDF fixture available")

    def test_inspect_returns_list_of_fontinfo(self) -> None:
        out = inspect_pdf_fonts(self.pdf)
        self.assertGreater(len(out), 0)
        for fi in out:
            self.assertIsInstance(fi, FontInfo)
            self.assertIsInstance(fi.category, FontCategory)

    def test_user_pdf_detects_myanmartext_no_tounicode(self) -> None:
        out = inspect_pdf_fonts(self.pdf)
        # The user's PDF was confirmed via audit to contain MyanmarText
        # with WinAnsiEncoding and no /ToUnicode.
        unsafe_myanmar = [
            f for f in out
            if f.category == FontCategory.WINANSI_NO_TOUNICODE
            and "myanmar" in (f.basefont_name or "").lower()
        ]
        self.assertGreater(
            len(unsafe_myanmar),
            0,
            f"Expected at least one MyanmarText+WinAnsi+no-ToUnicode font; got {out}",
        )

    def test_user_pdf_classifies_unsafe(self) -> None:
        fonts = inspect_pdf_fonts(self.pdf)
        q = classify_pdf(fonts)
        self.assertEqual(q.verdict, "unsafe")
        self.assertTrue(q.has_my_unsafe_fonts)


class TestClassifyPDF(unittest.TestCase):
    def test_safe_only(self) -> None:
        fonts = [
            FontInfo(
                xref=1,
                basefont_name="+Calibri",
                subtype="TrueType",
                encoding="WinAnsiEncoding",
                has_tounicode=True,
                glyph_count=400,
                cmap_myanmar_count=0,
                has_uni_glyph_names=False,
                category=FontCategory.STANDARD_LATIN,
                page=0,
            ),
        ]
        q = classify_pdf(fonts)
        self.assertEqual(q.verdict, "safe")
        self.assertFalse(q.has_my_unsafe_fonts)

    def test_unsafe_when_myanmartext_present(self) -> None:
        fonts = [
            FontInfo(
                xref=1,
                basefont_name="+Calibri",
                subtype="TrueType",
                encoding="WinAnsiEncoding",
                has_tounicode=True,
                glyph_count=400,
                cmap_myanmar_count=0,
                has_uni_glyph_names=False,
                category=FontCategory.STANDARD_LATIN,
                page=0,
            ),
            FontInfo(
                xref=2,
                basefont_name="+MyanmarText",
                subtype="TrueType",
                encoding="WinAnsiEncoding",
                has_tounicode=False,
                glyph_count=1003,
                cmap_myanmar_count=0,
                has_uni_glyph_names=False,
                category=FontCategory.WINANSI_NO_TOUNICODE,
                page=0,
            ),
        ]
        q = classify_pdf(fonts)
        self.assertEqual(q.verdict, "unsafe")
        self.assertTrue(q.has_my_unsafe_fonts)

    def test_safe_when_pure_unicode(self) -> None:
        fonts = [
            FontInfo(
                xref=1,
                basefont_name="+Pyidaungsu",
                subtype="TrueType",
                encoding="Identity-H",
                has_tounicode=True,
                glyph_count=900,
                cmap_myanmar_count=160,
                has_uni_glyph_names=True,
                category=FontCategory.PURE_UNICODE,
                page=0,
            ),
        ]
        q = classify_pdf(fonts)
        self.assertEqual(q.verdict, "safe")
        self.assertTrue(q.has_my_safe_fonts)


class TestFontInfoSerialization(unittest.TestCase):
    def test_to_dict(self) -> None:
        fi = FontInfo(
            xref=1,
            basefont_name="+MyanmarText",
            subtype="TrueType",
            encoding="WinAnsiEncoding",
            has_tounicode=False,
            glyph_count=1003,
            cmap_myanmar_count=0,
            has_uni_glyph_names=False,
            category=FontCategory.WINANSI_NO_TOUNICODE,
            page=0,
        )
        d = fi.to_dict()
        self.assertEqual(d["category"], "WINANSI_NO_TOUNICODE")
        self.assertEqual(d["encoding"], "WinAnsiEncoding")
        self.assertFalse(d["has_tounicode"])


if __name__ == "__main__":
    unittest.main()
'''

OUT.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
