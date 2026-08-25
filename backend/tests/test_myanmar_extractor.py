# -*- coding: utf-8 -*-
"""Tests for myanmar_extractor.extract_text_smart end-to-end."""
from __future__ import annotations

import unittest
from pathlib import Path

import pytest

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


@pytest.mark.slow
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
        # Per user direction, the Burmese pipeline is OCR-first, so
        # tesseract_ocr is the canonical method when Tesseract is
        # available with the mya lang pack. metadata_recovered is the
        # fallback when OCR is unavailable.
        self.assertIn(
            r.method,
            (
                "metadata_recovered",
                METHOD_MYANMAR_RECOVERED,
                METHOD_UNSAFE_HIGH_CORRUPTION,
                "pdfplumber_fallback",
                "tesseract_ocr",
            ),
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


class TestMyanmarStatsHelpers(unittest.TestCase):
    """Phase 1: cover the helper functions in isolation."""

    def test_empty_text_returns_zero(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _myanmar_stats,
        )
        self.assertEqual(_myanmar_stats(""), (0, 0.0))

    def test_myanmar_codepoints_counted(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _myanmar_stats,
        )
        # Four chars in the Myanmar block out of four total
        text = "\u1000\u1001\u102c\u103a"
        count, density = _myanmar_stats(text)
        self.assertEqual(count, 4)
        self.assertAlmostEqual(density, 1.0)

    def test_mixed_counts_only_myanmar_block(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _myanmar_stats,
        )
        # 2 Myanmar chars out of 5 total (3 English)
        text = "A\u1000B\u1001C"
        count, density = _myanmar_stats(text)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(density, 2 / 5, places=6)

    def test_needs_fallback_below_density_threshold(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _needs_pdfplumber_fallback,
        )
        # 10 Myanmar out of 100 chars (10% density) -> needs fallback
        text = ("\u1000" * 10) + ("x" * 90)
        self.assertTrue(_needs_pdfplumber_fallback(text))

    def test_no_fallback_above_density_threshold(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _needs_pdfplumber_fallback,
        )
        # 60 Myanmar out of 100 chars (60% density) -> no fallback
        text = ("\u1000" * 60) + ("x" * 40)
        self.assertFalse(_needs_pdfplumber_fallback(text))

    def test_no_fallback_when_count_meets_absolute_min(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _needs_pdfplumber_fallback,
            _MYANMAR_ABSOLUTE_MIN,
        )
        # 50 Myanmar chars total, but density is 50/100 = 50% (above 30%)
        # Both gates pass -> no fallback
        text = ("\u1000" * _MYANMAR_ABSOLUTE_MIN) + ("x" * _MYANMAR_ABSOLUTE_MIN)
        self.assertFalse(_needs_pdfplumber_fallback(text))

    def test_needs_fallback_when_count_below_absolute_min(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _needs_pdfplumber_fallback,
            _MYANMAR_ABSOLUTE_MIN,
        )
        # Only 30 Myanmar chars (below 50-char floor) even if density is high
        text = "\u1000" * 30
        self.assertTrue(_needs_pdfplumber_fallback(text))


class TestUnsafePathRouting(unittest.TestCase):
    """Burmese pipeline is OCR-first; metadata is only used as a final
    fallback when OCR is unavailable.
    """

    def setUp(self) -> None:
        from policy_platform.extract_myanmar import myanmar_extractor as me
        self._me = me
        self._orig = {
            "extract_text_via_metadata": me.extract_text_via_metadata,
            "resolve_full_font_path": me._resolve_full_font_path,
            "is_extractable": me.is_extractable,
            "is_tesseract_available": me.is_tesseract_available,
            "extract_text_via_ocr": me.extract_text_via_ocr,
            "extract_with_pdfplumber": me._extract_with_pdfplumber,
        }

    def tearDown(self) -> None:
        me = self._me
        for k, v in self._orig.items():
            setattr(me, k, v)

    def _invoke_safe(self, primary_text: str | None, ocr_text: str | None, fonts):
        """Run _unsafe_extract with metadata + OCR stubbed."""
        me = self._me

        def fake_extract(path, full_font):
            return [primary_text or ""]

        me.extract_text_via_metadata = fake_extract
        me._resolve_full_font_path = lambda: None
        me.is_extractable = lambda p: True
        me.is_tesseract_available = lambda: ocr_text is not None
        me.extract_text_via_ocr = lambda p, **kw: (ocr_text or "")

        from pathlib import Path
        return me._unsafe_extract(Path("dummy.pdf"), fonts)

    def test_ocr_used_when_available(self) -> None:
        # OCR present -> tesseract_ocr wins regardless of metadata
        primary = ("\u1000" * 60) + ("x" * 40)
        ocr_text = ("\u1000" * 50) + ("y" * 50)
        result = self._invoke_safe(primary, ocr_text, fonts=[])
        self.assertEqual(result.method, "tesseract_ocr")
        self.assertEqual(result.text, ocr_text)

    def test_metadata_used_when_ocr_unavailable(self) -> None:
        # OCR unavailable AND metadata non-empty -> metadata_recovered
        primary = ("\u1000" * 60) + ("x" * 40)
        result = self._invoke_safe(primary, ocr_text=None, fonts=[])
        self.assertEqual(result.method, "metadata_recovered")
        self.assertEqual(result.text, primary)

    def test_empty_when_ocr_unavailable_and_metadata_empty(self) -> None:
        # Both unavailable -> falls through to pdfplumber baseline
        result = self._invoke_safe(primary_text=None, ocr_text=None, fonts=[])
        # Either returns metadata-recovered or pdfplumber text, both
        # are acceptable metadata-path outcomes (we just check it
        # didn't crash and method is one of the known unsafe methods).
        self.assertIn(
            result.method,
            ("metadata_recovered", "pdfplumber", "myanmar_repair_attempted"),
        )

    def test_ocr_overrides_sparse_metadata(self) -> None:
        # Even when metadata has very low density, OCR wins
        primary = ("\u1000" * 5) + ("x" * 95)  # 5% density
        ocr_text = ("\u1000" * 80) + ("y" * 20)  # 80% density
        result = self._invoke_safe(primary, ocr_text, fonts=[])
        self.assertEqual(result.method, "tesseract_ocr")
        self.assertEqual(result.text, ocr_text)


if __name__ == "__main__":
    unittest.main()
