#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write tests/test_myanmar_extractor.py."""
from pathlib import Path

OUT = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\test_myanmar_extractor.py")

CONTENT = r'''# -*- coding: utf-8 -*-
"""Tests for myanmar_extractor.extract_text_smart end-to-end."""
from __future__ import annotations

import unittest
from pathlib import Path

from policy_platform.extract_myanmar import (
    extract_text_smart,
    TextExtractionResult,
    METHOD_PDFPLUMBER,
    METHOD_MYANMAR_RECOVERED,
    METHOD_UNSAFE_HIGH_CORRUPTION,
    PDF_VERDICT_SAFE,
    PDF_VERDICT_UNSAFE,
)


USER_PDF = Path(r"C:\\Users\\htetoowaiyan\\Downloads\\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
FIXTURE_PDF = Path(r"D:\\Htet Oo Wai Yan\\OneDrive - City Holdings Limited\\Desktop\\agentic-policy-platform - Copy (4)\\backend\\tests\\fixtures\\HR_00002_redacted.pdf")


def _pick_pdf() -> Path | None:
    if FIXTURE_PDF.exists():
        return FIXTURE_PDF
    if USER_PDF.exists():
        return USER_PDF
    return None


class TestExtractTextSmartMissing(unittest.TestCase):
    def test_missing_file(self) -> None:
        r = extract_text_smart(Path("/nonexistent.pdf"))
        self.assertIsInstance(r, TextExtractionResult)
        self.assertEqual(r.text, "")
        self.assertEqual(r.method, METHOD_PDFPLUMBER)


class TestExtractTextSmartUserPDF(unittest.TestCase):
    def setUp(self) -> None:
        self.pdf = _pick_pdf()
        if self.pdf is None:
            self.skipTest("No PDF fixture available")

    def test_returns_text_extraction_result(self) -> None:
        r = extract_text_smart(self.pdf)
        self.assertIsInstance(r, TextExtractionResult)
        self.assertIsInstance(r.text, str)
        self.assertGreater(len(r.text), 0)

    def test_user_pdf_classified_unsafe(self) -> None:
        r = extract_text_smart(self.pdf)
        self.assertEqual(r.pdf_verdict, PDF_VERDICT_UNSAFE)
        self.assertIn(
            r.method,
            (METHOD_MYANMAR_RECOVERED, METHOD_UNSAFE_HIGH_CORRUPTION),
        )

    def test_user_pdf_fonts_recorded(self) -> None:
        r = extract_text_smart(self.pdf)
        self.assertGreater(len(r.fonts), 0)
        # At least one font should be Myanmar+unsafe
        my_unsafe = [f for f in r.fonts if f.category.value == "WINANSI_NO_TOUNICODE"]
        self.assertGreater(len(my_unsafe), 0)

    def test_user_pdf_returns_score(self) -> None:
        r = extract_text_smart(self.pdf)
        self.assertGreaterEqual(r.score, 0.0)
        self.assertLessEqual(r.score, 1.0)


class TestExtractTextSmartDiagnosticFields(unittest.TestCase):
    def test_to_dict_has_required_keys(self) -> None:
        r = TextExtractionResult(
            text="hello",
            method=METHOD_PDFPLUMBER,
            score=0.0,
            pdf_verdict=PDF_VERDICT_SAFE,
            fonts=[],
        )
        d = r.to_dict()
        for k in ("text", "method", "score", "pdf_verdict", "fonts"):
            self.assertIn(k, d)


if __name__ == "__main__":
    unittest.main()
'''

OUT.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
