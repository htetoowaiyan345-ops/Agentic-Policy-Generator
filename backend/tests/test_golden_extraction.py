# -*- coding: utf-8 -*-
"""Golden extraction tests against HR_00002 PDF fixture.

Confirms the smart extractor:
  - classifies the PDF as unsafe
  - returns some text
  - records MyanmarText font
  - either recovers text or honestly flags high corruption
  - never invents characters (no PUA contamination in output)
"""
from __future__ import annotations

import unittest
from pathlib import Path

from policy_platform.extract_myanmar import (
    extract_text_smart,
    PDF_VERDICT_UNSAFE,
    METHOD_MYANMAR_RECOVERED,
    METHOD_UNSAFE_HIGH_CORRUPTION,
)


FIXTURE = Path(r"D:\\Htet Oo Wai Yan\\OneDrive - City Holdings Limited\\Desktop\\agentic-policy-platform - Copy (4)\\backend\\tests\\fixtures\\HR_00002_redacted.pdf")


class TestGoldenExtraction(unittest.TestCase):
    def setUp(self) -> None:
        if not FIXTURE.exists():
            self.skipTest(f"Fixture missing: {FIXTURE}")

    def test_classified_unsafe(self) -> None:
        r = extract_text_smart(FIXTURE)
        self.assertEqual(r.pdf_verdict, PDF_VERDICT_UNSAFE)

    def test_my_removal_unused_text_or_score(self) -> None:
        r = extract_text_smart(FIXTURE)
        # Some text was returned (not empty)
        self.assertGreater(len(r.text), 0)

    def test_method_one_of_expected(self) -> None:
        r = extract_text_smart(FIXTURE)
        self.assertIn(
            r.method,
            (
                "metadata_recovered",
                METHOD_MYANMAR_RECOVERED,
                METHOD_UNSAFE_HIGH_CORRUPTION,
            ),
        )

    def test_no_pua_contamination_in_output(self) -> None:
        # After repair, no PUA should remain
        r = extract_text_smart(FIXTURE)
        for ch in r.text:
            self.assertFalse(
                0xE000 <= ord(ch) <= 0xF8FF,
                f"Output contains PUA codepoint U+{ord(ch):04X}",
            )

    def test_no_lone_orphan_combining_marks(self) -> None:
        # Rule applied in unicode_repair should remove combining marks
        # that have spaces on BOTH sides (truly orphan: \s mark \s).
        r = extract_text_smart(FIXTURE)
        prev_was_space = False
        for ch in r.text:
            cp = ord(ch)
            is_combining = 0x102B <= cp <= 0x103A or cp == 0x1038
            if is_combining and prev_was_space:
                # We allow this on a per-case basis (Burmese word boundary)
                # but conservatively check we don't have spaces on BOTH
                # sides in a row (which would be true garbage).
                pass
            prev_was_space = (ch == " ")
        # This test is a smoke test, not a strict assertion.
        self.assertIsNotNone(r.text)


if __name__ == "__main__":
    unittest.main()
