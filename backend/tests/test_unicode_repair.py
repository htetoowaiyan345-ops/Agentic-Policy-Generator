# -*- coding: utf-8 -*-
"""Tests for unicode_repair.unicode_structural_repair."""
from __future__ import annotations

import unittest

from policy_platform.extract_myanmar.unicode_repair import (
    unicode_structural_repair,
    unicode_structural_repair_lines,
)


VIRAMA = "\u103A"
VISARGA = "\u1038"


class TestStripPUA(unittest.TestCase):
    def test_pua_chars_stripped(self) -> None:
        out = unicode_structural_repair("hello\uE001world")
        self.assertNotIn("\uE001", out)
        self.assertIn("hello", out)
        self.assertIn("world", out)

    def test_multiple_pua_stripped(self) -> None:
        out = unicode_structural_repair("\uE000\uE123\uE234\uF8FF")
        # No PUA codepoint should remain
        for ch in out:
            self.assertFalse(0xE000 <= ord(ch) <= 0xF8FF)


class TestStripControls(unittest.TestCase):
    def test_zero_width_space_stripped(self) -> None:
        out = unicode_structural_repair("hello\u200Bworld")
        self.assertNotIn("\u200B", out)
        self.assertEqual(out, "helloworld")

    def test_lre_rle_stripped(self) -> None:
        out = unicode_structural_repair("x\u202A y\u202E z")
        self.assertNotIn("\u202A", out)
        self.assertNotIn("\u202E", out)


class TestCollapseVirama(unittest.TestCase):
    def test_triple_virama_collapsed_to_single(self) -> None:
        out = unicode_structural_repair("a\u103A\u103A\u103Ab")
        self.assertEqual(out.count(VIRAMA), 1)

    def test_double_virama_collapsed_to_single(self) -> None:
        out = unicode_structural_repair("a\u103A\u103Ab")
        self.assertEqual(out.count(VIRAMA), 1)


class TestReattachCombining(unittest.TestCase):
    def test_combining_with_surrounding_spaces(self) -> None:
        out = unicode_structural_repair("x \u103A y")
        self.assertEqual(out, "x\u103Ay")

    def test_combining_preceded_by_space(self) -> None:
        out = unicode_structural_repair("a \u102Cb")
        self.assertNotIn(" \u102C", out)

    def test_combining_followed_by_space(self) -> None:
        out = unicode_structural_repair("a\u102C b")
        self.assertNotIn("\u102C ", out)


class TestCollapseSpaces(unittest.TestCase):
    def test_double_space_collapsed(self) -> None:
        out = unicode_structural_repair("hello\uE001\uE002  world")
        self.assertNotIn("  ", out)


class TestTrimTrailingVirama(unittest.TestCase):
    def test_trailing_virama_before_space_preserved(self) -> None:
        # Virama at end of syllable before whitespace IS valid Myanmar
        # (word boundary); this rule is disabled.
        out = unicode_structural_repair("hello\u103A world")
        # Virama should still be present.
        self.assertEqual(out.count(VIRAMA), 1)


class TestRealWorldCorruption(unittest.TestCase):
    def test_golden_sentence_repair(self) -> None:
        corrupted = "\u101C\u1015 \u102F\u103A\u103A\u103A\u1004\u1014\u1038\u1005\u102F"
        out = unicode_structural_repair(corrupted)
        self.assertEqual(out.count(VIRAMA), 1)
        self.assertNotIn("  ", out)
        self.assertTrue(out.startswith("\u101C"))

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(unicode_structural_repair(""), "")

    def test_latin_string_unchanged(self) -> None:
        self.assertEqual(unicode_structural_repair("hello world"), "hello world")

    def test_idempotent(self) -> None:
        a = unicode_structural_repair("hello\u103A\u103A world \u102C x")
        b = unicode_structural_repair(a)
        self.assertEqual(a, b)


class TestRepairLines(unittest.TestCase):
    def test_repair_lines(self) -> None:
        lines = ["a\u103A\u103Ab", "hello\uE001"]
        out = unicode_structural_repair_lines(lines)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].count(VIRAMA), 1)
        # PUA should be stripped from line 2
        self.assertNotIn("\uE001", out[1])


if __name__ == "__main__":
    unittest.main()
