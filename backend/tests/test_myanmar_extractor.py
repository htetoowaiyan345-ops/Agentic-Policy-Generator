# -*- coding: utf-8 -*-
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
            (
                "metadata_recovered",
                METHOD_MYANMAR_RECOVERED,
                METHOD_UNSAFE_HIGH_CORRUPTION,
                "pdfplumber_fallback",
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


class TestPdfplumberFallback(unittest.TestCase):
    """Phase 1: cover the pdfplumber fallback swap behaviour."""

    def _invoke_safe(self, primary_text: str, fallback_text: str, fonts):
        """Run _unsafe_extract with primary + fallback stubbed."""
        from policy_platform.extract_myanmar import myanmar_extractor as me
        from policy_platform.extract_myanmar.myanmar_extractor import (
            METHOD_METADATA_RECOVERED,
            METHOD_PDFPLUMBER_FALLBACK,
            _DENSITY_IMPROVEMENT_RATIO,
        )

        real_extract = me.extract_text_via_metadata
        real_pdfplumber = me._extract_with_pdfplumber

        def fake_extract(path, full_font):
            return [primary_text]

        def fake_pdfplumber(path):
            return fallback_text

        me.extract_text_via_metadata = fake_extract
        me._extract_with_pdfplumber = fake_pdfplumber
        me._resolve_full_font_path = lambda: None
        me.is_extractable = lambda p: True
        try:
            from pathlib import Path
            return me._unsafe_extract(Path("dummy.pdf"), fonts)
        finally:
            me.extract_text_via_metadata = real_extract
            me._extract_with_pdfplumber = real_pdfplumber

    def test_healthy_metadata_skips_fallback(self) -> None:
        # 60% Myanmar, >50 chars -> metadata result kept
        primary = ("\u1000" * 60) + ("x" * 40)
        fallback = ("\u1000" * 5) + ("y" * 95)  # extremely sparse
        result = self._invoke_safe(primary, fallback, fonts=[])
        self.assertEqual(result.method, "metadata_recovered")
        self.assertEqual(result.text, primary)

    def test_sparse_metadata_triggers_swap_to_fallback(self) -> None:
        # primary: 30 Myanmar out of 1000 chars (3%, sparse)
        primary = ("\u1000" * 30) + ("x" * 970)
        # fallback: 500 Myanmar out of 1000 chars (50%, dense)
        fallback = ("\u1000" * 500) + ("y" * 500)
        result = self._invoke_safe(primary, fallback, fonts=[])
        self.assertEqual(result.method, "pdfplumber_fallback")
        self.assertEqual(result.text, fallback)

    def test_fallback_must_beat_metadata_by_20_percent(self) -> None:
        # primary: 22% Myanmar -> density=0.22
        # fallback: 25% Myanmar -> ratio = 25/22 ≈ 1.14 < 1.20
        primary = ("\u1000" * 22) + ("x" * 78)
        fallback = ("\u1000" * 25) + ("y" * 75)
        result = self._invoke_safe(primary, fallback, fonts=[])
        # Primary wins because fallback did not clear the 1.2× bar
        self.assertEqual(result.method, "metadata_recovered")
        self.assertEqual(result.text, primary)

    def test_fallback_domination_swaps_result(self) -> None:
        # primary: 8% Myanmar -> density=0.08
        # fallback: 50% Myanmar -> ratio ≈ 6.25 >> 1.2
        primary = ("\u1000" * 8) + ("x" * 92)
        fallback = ("\u1000" * 50) + ("y" * 50)
        result = self._invoke_safe(primary, fallback, fonts=[])
        self.assertEqual(result.method, "pdfplumber_fallback")
        self.assertEqual(result.text, fallback)


if __name__ == "__main__":
    unittest.main()
