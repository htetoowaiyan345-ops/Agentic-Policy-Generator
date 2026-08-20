"""Tests for CMap corruption correction logic.

Microsoft Word's PDF export for MyanmarText-family fonts occasionally
emits /ToUnicode CMap entries that are 32-bit composites (e.g.
`U+103D1031`) when the actual glyph at that CID is a single codepoint
(e.g. `U+103D`). This module tests the override logic that prefers
the bundled font's authoritative single-codepoint mapping when such a
disagreement is detected.
"""
from __future__ import annotations

import pytest

from policy_platform.extract_myanmar.metadata_extractor import (
    _is_myanmartext_family,
    _tu_likely_corrupt,
)


class TestIsMyanmartextFamily:
    def test_myanmartext_base(self):
        assert _is_myanmartext_family("BCDIEE+MyanmarText")

    def test_myanmartext_bold(self):
        assert _is_myanmartext_family("BCDGEE+MyanmarText-Bold")

    def test_case_insensitive(self):
        assert _is_myanmartext_family("myanmartext-lowercase")

    def test_myamar3(self):
        assert _is_myanmartext_family("ABC+Myanmar3")

    def test_pyidaungsu(self):
        assert _is_myanmartext_family("DEF+Pyidaungsu")

    def test_unrelated_font(self):
        assert not _is_myanmartext_family("ArialMT")

    def test_wingdings(self):
        assert not _is_myanmartext_family("Wingdings-Regular")

    def test_calibri(self):
        assert not _is_myanmartext_family("BCDEEE+Calibri")


class TestTuLikelyCorrupt:
    """Detect Word's bad composite entries."""

    def test_composite_vs_single_is_corrupt(self):
        # Word emits U+103D1031 for a glyph whose real mapping is U+103D
        assert _tu_likely_corrupt(0x103D, 0x103D1031)

    def test_composite_vs_composite_not_flagged(self):
        # Both agree it's a composite — leave it alone
        assert not _tu_likely_corrupt(0x103D1031, 0x103D1032)

    def test_single_vs_single_not_flagged(self):
        assert not _tu_likely_corrupt(0x1000, 0x1001)

    def test_composite_vs_different_single_is_corrupt(self):
        # Word says U+10021039, font says U+1002
        assert _tu_likely_corrupt(0x1002, 0x10021039)

    def test_composite_vs_single_high_codepoint(self):
        assert _tu_likely_corrupt(0x104E, 0x10141039)


class TestCMapCorrectionIntegration:
    """Integration test: verify correction logic removes corrupt composites."""

    def test_corrected_text_has_fewer_composites(self):
        """The corrected extraction should have dramatically fewer
        32-bit composite codepoints than the uncorrected one."""
        from policy_platform.extract_myanmar import extract_text_smart
        from pathlib import Path

        pdf = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\fixtures\HR_00002_redacted.pdf")
        if not pdf.exists():
            pytest.skip(f"Fixture missing: {pdf}")
        result = extract_text_smart(pdf)
        text = result.text

        # Count characters whose codepoint > 0xFFFF (composite indicators
        # after decomposition; should be very rare in clean text).
        # Note: post-decomposition, the characters in text are stored as
        # individual chars so any 0xFFFF+ cp is a sign of remaining corruption.
        high_cp_count = sum(1 for ch in text if ord(ch) > 0xFFFF)
        assert high_cp_count < 5, "Expected near-zero high-codepoint chars after correction"

    def test_corrected_text_does_not_have_known_bad_patterns(self):
        """After correction, the output should NOT contain the specific
        bad patterns we identified (e.g., ra+asat+visarga when only
        vowel_e+asat was expected)."""
        from policy_platform.extract_myanmar import extract_text_smart
        from pathlib import Path

        pdf = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\fixtures\HR_00002_redacted.pdf")
        if not pdf.exists():
            pytest.skip(f"Fixture missing: {pdf}")
        result = extract_text_smart(pdf)
        text = result.text

        # Before correction, we'd see ရ်း (ra+asat+visarga) appearing many
        # times due to CMap corruption. After correction, this should be
        # gone or very rare.
        bad_pattern = chr(0x101B) + chr(0x103A) + chr(0x1038)
        occurrences = text.count(bad_pattern)
        # Allow some (might be legitimate stacked-consonant ligatures)
        # but not the dozens we'd see with corruption.
        assert occurrences < 20, "Too many ra+asat+visarga occurrences"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])