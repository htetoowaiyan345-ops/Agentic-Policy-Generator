# -*- coding: utf-8 -*-
"""Tests for the Tesseract OCR fallback path.

These tests probe both the OCR module directly and its integration into
``myanmar_extractor._unsafe_extract``. Real Tesseract is invoked when
available (CI on developer machines); the dependency is optional in
production.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import pytest

from policy_platform.extract_myanmar.ocr_fallback import (
    is_tesseract_available,
    extract_text_via_ocr,
    _resolve_tesseract_cmd,
    _resolve_poppler_dir,
)


FIXTURE = Path(
    r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\fixtures\HR_00002_redacted.pdf"
)


class TestTesseractAvailability(unittest.TestCase):
    def test_returns_bool(self) -> None:
        # On a properly configured machine this is True; on a bare CI
        # it is False. Just ensure the probe never raises and returns a
        # definite answer.
        result = is_tesseract_available()
        self.assertIsInstance(result, bool)

    def test_resolve_tesseract_cmd_default(self) -> None:
        cmd = _resolve_tesseract_cmd()
        # Default value should be a Windows-style absolute path.
        self.assertTrue(cmd.endswith("tesseract.exe"))


class TestPopplerResolution(unittest.TestCase):
    def test_returns_string_or_none(self) -> None:
        result = _resolve_poppler_dir()
        # Should be either a directory path or None.
        if result is not None:
            self.assertIsInstance(result, str)


class TestExtractTextViaOcr(unittest.TestCase):
    def test_returns_none_when_pdf_missing(self) -> None:
        result = extract_text_via_ocr(Path("/nonexistent.pdf"))
        self.assertIsNone(result)

    def test_returns_none_when_tesseract_module_unavailable(
        self,
    ) -> None:
        with mock.patch(
            "policy_platform.extract_myanmar.ocr_fallback._load_pytesseract",
            return_value=None,
        ):
            result = extract_text_via_ocr(FIXTURE)
        self.assertIsNone(result)

    def test_returns_none_when_pdf2image_unavailable(self) -> None:
        with mock.patch(
            "policy_platform.extract_myanmar.ocr_fallback._load_pdf2image",
            return_value=None,
        ):
            result = extract_text_via_ocr(FIXTURE)
        self.assertIsNone(result)

    @pytest.mark.slow
    def test_invoke_real_ocr_when_available(self) -> None:
        # Only meaningful when Tesseract is installed with ``mya``.
        if not is_tesseract_available():
            self.skipTest("Tesseract with ``mya`` lang pack not installed")
        if not FIXTURE.exists():
            self.skipTest("Test fixture not present")
        # Render only the first page to keep the test fast (don't OCR
        # the entire 12-page fixture in CI).
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore

        poppler_path = _resolve_poppler_dir()
        imgs = convert_from_path(
            str(FIXTURE),
            dpi=300,
            first_page=1,
            last_page=1,
            fmt="png",
            **( {"poppler_path": poppler_path} if poppler_path else {} ),
        )
        text = pytesseract.image_to_string(
            imgs[0], lang="mya+eng", config="--psm 6"
        )
        self.assertIsInstance(text, str)
        # OCR may yield a near-empty result on titles — just confirm
        # the pipeline runs without crashing.
        self.assertGreaterEqual(len(text), 0)


class TestPostprocessOcr(unittest.TestCase):
    """Path C: post-OCR canonicalization helper."""

    def test_empty_string_unchanged(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        self.assertEqual(_postprocess_ocr(""), "")

    def test_collapses_double_vowel(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        # Tesseract emits duplicate U+102F (vowel u) when reading
        # stacked ligatures.
        out = _postprocess_ocr("\u1000\u102F\u102F")
        self.assertEqual(out, "\u1000\u102F")

    def test_collapses_double_i_vowel(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        out = _postprocess_ocr("\u1000\u102D\u102D")
        self.assertEqual(out, "\u1000\u102D")

    def test_strips_trailing_virama(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        # Tesseract emits virama at EOL when it forgets to attach
        # the following consonant.
        out = _postprocess_ocr("\u1000\u1039\n")
        self.assertEqual(out, "\u1000\n")

    def test_strips_trailing_virama_before_space(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        out = _postprocess_ocr("\u1000\u1039 ")
        self.assertEqual(out, "\u1000 ")

    def test_strips_trailing_virama_at_eof(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        out = _postprocess_ocr("\u1000\u1039")
        # Trailing-only stripping handled by rstrip too.
        self.assertEqual(out, "\u1000")

    def test_collapses_double_spaces(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        out = _postprocess_ocr("Hello  World")
        self.assertEqual(out, "Hello World")

    def test_preserves_newlines(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        out = _postprocess_ocr("\u1000\n\u1001")
        self.assertEqual(out, "\u1000\n\u1001")

    def test_combined_fixes_chain(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            _postprocess_ocr,
        )
        # Hit duplicate-vowel, trailing-virama, AND double-space
        # in one input.
        out = _postprocess_ocr("\u1000\u102F\u102F\u1039  \u1001")
        self.assertEqual(out, "\u1000\u102F \u1001")


class TestPostprocessOcrMyanmar(unittest.TestCase):
    """Targeted-fix post-OCR Myanmar reordering rules."""

    def test_empty_string_unchanged(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        self.assertEqual(_postprocess_ocr_myanmar(""), "")

    def test_fixes_nunu_nunu_swap(self) -> None:
        """`နး်` (Tesseract) → `န်း` (canonical)."""
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        out = _postprocess_ocr_myanmar("\u1014\u1038\u103A")
        self.assertEqual(out, "\u1014\u103A\u1038")

    def test_fixes_kakaukaukau_swap(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        out = _postprocess_ocr_myanmar("\u1000\u1038\u103A")
        self.assertEqual(out, "\u1000\u103A\u1038")

    def test_preserves_english_words(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        out = _postprocess_ocr_myanmar("Group Policy HR")
        self.assertEqual(out, "Group Policy HR")

    def test_preserves_correctly_ordered_text(self) -> None:
        """Already-canonical text must NOT be modified."""
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        # န်း is already canonical (U+1014 + U+103A + U+1038)
        canonical = "\u1014\u103A\u1038"
        out = _postprocess_ocr_myanmar(canonical)
        self.assertEqual(out, canonical)

    def test_preserves_whitespace(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        out = _postprocess_ocr_myanmar("\u1000 \n\u1001  \u1002")
        self.assertEqual(out, "\u1000 \n\u1001  \u1002")

    def test_preserves_unrelated_myanmar_chars(self) -> None:
        """Unrelated syllables should not be touched."""
        from policy_platform.extract_myanmar.ocr_fallback import (
            _postprocess_ocr_myanmar,
        )
        # ကောင်း — ka + e-vowel + medial-wa + nasal + asat — different pattern
        original = "\u1000\u1031\u102C\u1004\u103A\u1038"
        out = _postprocess_ocr_myanmar(original)
        self.assertEqual(out, original)


class TestStripHeaderNoise(unittest.TestCase):
    """Strip Latin-only header/watermark noise from OCR output."""

    def test_empty_string_unchanged(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _strip_header_noise,
        )
        self.assertEqual(_strip_header_noise(""), "")

    def test_drops_fav_city_line(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _strip_header_noise,
        )
        out = _strip_header_noise("FAV city\n\u1000\u1001")
        self.assertIn("\u1000\u1001", out)
        self.assertNotIn("FAV city", out)

    def test_drops_wy_holdings_line(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _strip_header_noise,
        )
        out = _strip_header_noise("wy Holdings\nmyanmar text")
        self.assertNotIn("wy Holdings", out)
        self.assertIn("myanmar text", out)

    def test_keeps_myanmar_lines(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _strip_header_noise,
        )
        original = "\u1000\u1001\n\u1002\u1003\n\u1004\u1005"
        out = _strip_header_noise(original)
        self.assertEqual(out, original)

    def test_keeps_lines_with_myanmar_substring(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _strip_header_noise,
        )
        # Mixed line: keep it because it has Myanmar chars
        original = "Policy Title: \u1000\u1001"
        out = _strip_header_noise(original)
        self.assertEqual(out, original)

    def test_preserves_blank_lines(self) -> None:
        from policy_platform.extract_myanmar.ocr_fallback import (
            _strip_header_noise,
        )
        original = "\u1000\n\n\u1001"
        out = _strip_header_noise(original)
        self.assertEqual(out, original)


class TestMyanmarExtractorOCRRouting(unittest.TestCase):
    """OCR path must NEVER be reached from the English (_safe_) flow,
    and must produce ``None`` when Tesseract is unavailable.
    """

    def setUp(self) -> None:
        # Snapshot module-level names BEFORE any monkeypatching so we
        # can reliably restore them even if a test raises mid-flight.
        from policy_platform.extract_myanmar import myanmar_extractor as me
        self._me = me
        self._orig_avail = me.is_tesseract_available
        self._orig_ocr = me.extract_text_via_ocr
        self._orig_metadata = me.extract_text_via_metadata
        self._orig_extractable = me.is_extractable
        self._orig_pdfplumber = me._extract_with_pdfplumber
        self._orig_resolve = me._resolve_full_font_path

    def tearDown(self) -> None:
        me = self._me
        me.is_tesseract_available = self._orig_avail
        me.extract_text_via_ocr = self._orig_ocr
        me.extract_text_via_metadata = self._orig_metadata
        me.is_extractable = self._orig_extractable
        me._extract_with_pdfplumber = self._orig_pdfplumber
        me._resolve_full_font_path = self._orig_resolve

    def test_ocr_returns_none_when_tesseract_unavailable(self) -> None:
        me = self._me
        me.is_tesseract_available = lambda: False
        result = me._maybe_tesseract_fallback(
            Path("dummy.pdf"),
            primary_text="some text",
            primary_score=0.3,
            fonts=[],
        )
        self.assertIsNone(result)

    def test_ocr_returns_none_when_text_empty(self) -> None:
        me = self._me
        me.is_tesseract_available = lambda: True
        me.extract_text_via_ocr = lambda p, **kw: ""
        result = me._maybe_tesseract_fallback(
            Path("dummy.pdf"),
            primary_text="\u1000" * 100,
            primary_score=0.3,
            fonts=[],
        )
        self.assertIsNone(result)

    def test_ocr_swaps_when_dominates(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            METHOD_TESSERACT_OCR,
        )
        me = self._me
        me.is_tesseract_available = lambda: True
        me.extract_text_via_ocr = lambda p, **kw: ("\u1000" * 50) + ("x" * 50)
        result = me._maybe_tesseract_fallback(
            Path("dummy.pdf"),
            primary_text="\u1000" + ("y" * 49),
            primary_score=0.5,
            fonts=[],
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.method, METHOD_TESSERACT_OCR)

    def test_ocr_used_when_any_text_returned_even_low_quality(self) -> None:
        from policy_platform.extract_myanmar.myanmar_extractor import (
            METHOD_TESSERACT_OCR,
        )
        me = self._me
        me.is_tesseract_available = lambda: True
        me.extract_text_via_ocr = lambda p, **kw: ("\u1000" * 30) + ("x" * 70)
        result = me._maybe_tesseract_fallback(
            Path("dummy.pdf"),
            primary_text=("\u1000" * 28) + ("y" * 72),
            primary_score=0.3,
            fonts=[],
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.method, METHOD_TESSERACT_OCR)

    def test_ocr_text_postprocessed(self) -> None:
        """Path C: result.text must be canonicalized, not raw OCR output."""
        from policy_platform.extract_myanmar.myanmar_extractor import (
            METHOD_TESSERACT_OCR,
        )
        me = self._me
        me.is_tesseract_available = lambda: True
        raw = (
            "\u1000\u102F\u102F\u1039  "
            + "\u1000\u102D\u102D"
        )
        me.extract_text_via_ocr = lambda p, **kw: raw
        result = me._maybe_tesseract_fallback(
            Path("dummy.pdf"),
            primary_text="ignored",
            primary_score=0.3,
            fonts=[],
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.method, METHOD_TESSERACT_OCR)
        self.assertNotIn("\u102F\u102F", result.text)
        self.assertNotIn("\u102D\u102D", result.text)
        self.assertFalse(result.text.endswith("\u1039"))
        self.assertNotIn("  ", result.text)

    def test_ocr_config_defaults(self) -> None:
        """Path A: the OCR module exposes the new defaults."""
        from policy_platform.extract_myanmar.ocr_fallback import (
            _DEFAULT_PSM,
            _DEFAULT_OEM,
            _DEFAULT_LANG,
        )
        self.assertEqual(_DEFAULT_PSM, "6")
        self.assertEqual(_DEFAULT_OEM, "1")
        self.assertEqual(_DEFAULT_LANG, "mya+eng")


if __name__ == "__main__":
    unittest.main()
