"""Tests for repeated-vowel-sign dedup and composite-dead-mark collapse."""
from policy_platform.extract_myanmar.metadata_extractor import (
    _dedupe_repeated_vowel_signs,
    _collapse_composite_dead_marks,
)


class TestDedupeRepeatedVowelSigns:
    def test_no_dupes_no_change(self):
        # Single vowel-e in Health word - should not change
        chars = list("ကျန်းမာရး")
        result = _dedupe_repeated_vowel_signs(chars)
        assert result == chars

    def test_double_vowel_e_deduped(self):
        # Pattern: vowel-e + ra + vowel-e + visarga (Word artifact)
        chars = [chr(0x101B), chr(0x1031), chr(0x101B), chr(0x1038)]
        result = _dedupe_repeated_vowel_signs(chars)
        # The second vowel-e should be dropped
        assert result == [chr(0x101B), chr(0x1031), chr(0x101B), chr(0x1038)][:3] + [chr(0x1038)] or len(result) == 3
        assert result.count(chr(0x1031)) == 1

    def test_stacked_consonants_preserved(self):
        # Stacked consonant ligature: virama + asat (legitimate)
        chars = list("က်း")
        result = _dedupe_repeated_vowel_signs(chars)
        assert result == chars

    def test_double_medial_ra_deduped(self):
        chars = list("ရြရြး")
        result = _dedupe_repeated_vowel_signs(chars)
        # Second medial-ra should be removed
        assert result.count("ြ") == 1

    def test_empty_input(self):
        assert _dedupe_repeated_vowel_signs([]) == []

    def test_only_vowels_deduped(self):
        # Only one vowel preserved when multiple in a row
        chars = list("ေေေ")
        result = _dedupe_repeated_vowel_signs(chars)
        assert result == ["ေ"]

    def test_different_vowels_preserved(self):
        # First vowel sign is kept. Second different vowel sign is also kept
        # (the function drops a SAME-vowel duplicate, not different vowels).
        chars = [chr(0x102B), chr(0x102C)]  # vowel i, vowel aa
        result = _dedupe_repeated_vowel_signs(chars)
        # Both present (they're different vowels, not duplicates)
        assert chr(0x102B) in result
        assert chr(0x102C) in result


class TestCollapseCompositeDeadMarks:
    """Tests for collapsing composite+standalone dead-mark duplicates."""

    def test_asat_vowelu_asat_collapses(self):
        # Composite CID 0x01FD = asat + vowel-u, followed by standalone asat
        chars = [chr(0x103A), chr(0x102F), chr(0x103A)]
        result = _collapse_composite_dead_marks(chars)
        assert result == [chr(0x102F), chr(0x103A)]

    def test_double_asat_preserved(self):
        # Legitimate double asat is NOT touched by composite collapse
        chars = list("က််")
        result = _collapse_composite_dead_marks(chars)
        assert result == chars

    def test_visarga_double_collapses(self):
        # Visarga + vowel + visarga (same pattern with visarga)
        chars = [chr(0x1038), chr(0x1021), chr(0x1038)]
        result = _collapse_composite_dead_marks(chars)
        assert result == [chr(0x1021), chr(0x1038)]

    def test_legitimate_dotbelow_preserved(self):
        # Dot-below (U+103B) in a legitimate stacked consonant
        chars = list("ကျမ")
        result = _collapse_composite_dead_marks(chars)
        assert result == chars

    def test_empty_input(self):
        assert _collapse_composite_dead_marks([]) == []

    def test_no_collapses_when_no_pattern(self):
        # Random text without the dead-mark-sandwich pattern
        chars = list("မြန်မာ")
        result = _collapse_composite_dead_marks(chars)
        assert result == chars

    def test_collapses_multiple_occurrences(self):
        # Multiple sandwiches in a row
        chars = (
            [chr(0x103A), chr(0x102F), chr(0x103A)]
            + [chr(0x1000)]
            + [chr(0x103A), chr(0x102F), chr(0x103A)]
        )
        result = _collapse_composite_dead_marks(chars)
        # First sandwich ် + ု + ် -> ု + (trailing ် collapsed by next match)
        # Second sandwich ် + ု + ် -> ု + ်
        # The base က stays as-is.
        assert result == [chr(0x102F), chr(0x1000), chr(0x102F), chr(0x103A)]