#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write tests/test_corruption_score.py with proper UTF-8 encoding."""
import io
from pathlib import Path

OUT = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\test_corruption_score.py")

CONTENT = '''# -*- coding: utf-8 -*-
"""Tests for corruption_score.compute_corruption_score.

Targets the heuristics that detect fabricated Myanmar Unicode from
font-fallback extraction paths (MyanmarText no-ToUnicode case).
"""
from __future__ import annotations

import unittest

from policy_platform.extract_myanmar.corruption_score import (
    compute_corruption_score,
    indicators_breakdown,
)


class TestCorruptionScoreEnglish(unittest.TestCase):
    def test_clean_ascii_returns_zero(self) -> None:
        self.assertEqual(compute_corruption_score("hello world"), 0.0)

    def test_empty_string_returns_zero(self) -> None:
        self.assertEqual(compute_corruption_score(""), 0.0)

    def test_clean_latin_punctuation_returns_zero(self) -> None:
        self.assertEqual(compute_corruption_score("Group Policy (2025) - v3"), 0.0)


class TestCorruptionScoreCleanMyanmar(unittest.TestCase):
    def test_clean_pure_unicode_returns_zero(self) -> None:
        # "group policy" in real Unicode Myanmar
        clean = "\\u101C\\u102F\\u1015\\u1004\\u1014\\u1038\\u1005\\u102F \\u1019\\u1030\\u101D\\u102B\\u1012\\u1012"
        self.assertEqual(compute_corruption_score(clean), 0.0)


class TestCorruptionScoreCorrupted(unittest.TestCase):
    def test_excess_virama_increases_score(self) -> None:
        # Triple virama definitely not valid
        bad = "a\\u103A\\u103A\\u103Ab" * 50
        s = compute_corruption_score(bad)
        self.assertGreater(s, 0.2)

    def test_combining_with_spaces_increases_score(self) -> None:
        bad = "x \\u103A y \\u102C z \\u1038 a"
        s = compute_corruption_score(bad)
        self.assertGreater(s, 0.05)

    def test_pua_contamination_increases_score(self) -> None:
        bad = "hello\\uE001world\\uE002test"
        s = compute_corruption_score(bad)
        self.assertGreater(s, 0.05)

    def test_real_world_corruption_example(self) -> None:
        # "လုပ်ငန်းစု မူဝါဒ" with fabricated intersyllable virama:
        bad = "\\u101C\\u1015\\u102F\\u103A \\u1004\\u1014\\u103A\\u103A\\u1038\\u1005\\u102F \\u1019\\u1030\\u101D\\u102B\\u1012"
        s = compute_corruption_score(bad)
        self.assertGreater(s, 0.3)


class TestCorruptionScoreThreshold(unittest.TestCase):
    def test_score_in_unit_interval(self) -> None:
        for s in [
            "",
            "abc",
            "hello world",
            "\\u101C\\u1015\\u102F",
            "\\u101C\\u103A\\u103A\\u103A\\u103A\\u103A\\u103A",
            "x" * 1000,
            "\\uE000" * 5,
        ]:
            v = compute_corruption_score(s)
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)


class TestIndicatorsBreakdown(unittest.TestCase):
    def test_breakdown_keys_present(self) -> None:
        b = indicators_breakdown("hello")
        self.assertIn("virama_count", b)
        self.assertIn("consonant_count", b)
        self.assertIn("scores", b)
        self.assertIn("weighted_sum", b)
        self.assertEqual(b["virama_count"], 0)
        self.assertEqual(b["consonant_count"], 0)

    def test_breakdown_detects_corruption(self) -> None:
        bad = "x \\u103A y \\u1038 z" * 20
        b = indicators_breakdown(bad)
        self.assertGreater(b["virama_count"], 0)
        self.assertGreater(b["weighted_sum"], 0.1)


if __name__ == "__main__":
    unittest.main()
'''

OUT.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
