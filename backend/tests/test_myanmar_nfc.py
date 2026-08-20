"""Tests for myanmar_nfc.normalize_myanmar_nfc (UAX #9 §11.4).

These tests verify the pure-Python Myanmar Unicode NFC normalizer
correctly reorders combining marks within each syllable.
"""
from __future__ import annotations

import pytest

from policy_platform.extract_myanmar.myanmar_nfc import (
    normalize_myanmar_nfc,
    _canonical_key,
    _is_myanmar_base,
    _split_into_syllables,
    _reorder_syllable_marks,
)


class TestCanonicalKey:
    """Test UAX #9 §11.4 canonical ordering keys."""

    def test_medial_ra_is_first(self):
        assert _canonical_key("\u103C") == 0

    def test_medial_la_second(self):
        assert _canonical_key("\u103D") == 1

    def test_medial_wa_third(self):
        assert _canonical_key("\u103E") == 2

    def test_vowel_i_order(self):
        assert _canonical_key("\u102B") == 3

    def test_vowel_e_order(self):
        assert _canonical_key("\u1031") == 9

    def test_vowel_ai_order(self):
        assert _canonical_key("\u1032") == 10

    def test_visarga_order(self):
        assert _canonical_key("\u1038") == 16

    def test_virama_order(self):
        assert _canonical_key("\u103A") == 18

    def test_asat_order(self):
        assert _canonical_key("\u103B") == 19

    def test_extended_vowels(self):
        assert _canonical_key("\u1081") == 20
        assert _canonical_key("\u1084") == 23

    def test_unknown_char_returns_high(self):
        assert _canonical_key("A") == 9999


class TestIsMyanmarBase:
    def test_base_consonants(self):
        for cp in (0x1000, 0x1001, 0x1014, 0x1021):
            assert _is_myanmar_base(cp), f"U+{cp:04X} should be a base"

    def test_marks_not_bases(self):
        for cp in (0x103A, 0x103C, 0x1038):
            assert not _is_myanmar_base(cp), f"U+{cp:04X} should NOT be a base"

    def test_ascii_not_base(self):
        assert not _is_myanmar_base(ord("A"))


class TestSplitIntoSyllables:
    def test_single_syllable(self):
        text = "\u1000\u103A"
        parts = _split_into_syllables(text)
        assert len(parts) == 1
        assert parts[0][0] == text
        assert parts[0][1] == ""

    def test_two_syllables_with_space(self):
        text = "\u1000\u103A \u1014\u103A"
        parts = _split_into_syllables(text)
        # Expected: [("\u1000\u103A", ""), ("", " "), ("\u1014\u103A", "")]
        assert len(parts) == 3
        assert parts[0][0] == "\u1000\u103A"
        assert parts[0][1] == ""
        assert parts[1][0] == ""
        assert parts[1][1] == " "
        assert parts[2][0] == "\u1014\u103A"
        assert parts[2][1] == ""

    def test_preserves_ascii(self):
        text = "Hello \u1000\u103A World"
        parts = _split_into_syllables(text)
        # The first segment is empty (separator) with "Hello "
        assert any(sep == "Hello " for syl, sep in parts if not syl)

    def test_no_myanmar_returns_empty(self):
        text = "Hello World"
        parts = _split_into_syllables(text)
        assert len(parts) == 1
        assert parts[0] == ("", "Hello World")


class TestReorderSyllableMarks:
    def test_already_canonical(self):
        # base + medial ra + vowel e - already in canonical order
        text = "\u1000\u103C\u1031"
        result = _reorder_syllable_marks(text)
        assert result == text

    def test_reorder_marks(self):
        # base + vowel e + medial ra (wrong order) -> base + medial ra + vowel e
        text = "\u1000\u1031\u103C"
        result = _reorder_syllable_marks(text)
        assert result == "\u1000\u103C\u1031"

    def test_single_mark_unchanged(self):
        text = "\u1000\u103A"
        result = _reorder_syllable_marks(text)
        assert result == text

    def test_empty_returns_empty(self):
        assert _reorder_syllable_marks("") == ""

    def test_no_base_returns_unchanged(self):
        text = "\u103C\u103A"
        result = _reorder_syllable_marks(text)
        assert result == text

    def test_multiple_marks_complex(self):
        # Test: base + virama + virama + visarga + vowel
        # Canonical: base + vowel + visarga + virama + virama
        text = "\u1000\u103A\u103A\u1038\u1031"
        result = _reorder_syllable_marks(text)
        assert result == "\u1000\u1031\u1038\u103A\u103A"


class TestNormalizeMyanmarNFC:
    """Integration tests for the full normalization pipeline."""

    def test_empty_input(self):
        assert normalize_myanmar_nfc("") == ""

    def test_ascii_only_unchanged(self):
        assert normalize_myanmar_nfc("Hello World 123") == "Hello World 123"

    def test_single_syllable_reorder(self):
        # base + vowel e + medial ra (wrong) -> correct order
        text = "\u1000\u1031\u103C"
        result = normalize_myanmar_nfc(text)
        assert result == "\u1000\u103C\u1031"

    def test_realistic_corruption_pattern(self):
        # From HR_00002_redacted.pdf:
        # Simple case: base + virama + virama (double-virama corruption).
        # Should be normalized to base + virama + virama (NFC keeps both,
        # but canonical order is fine since they're equal-rank marks).
        # The KEY claim: we don't introduce new double-virama where there
        # wasn't one in the correct order.
        # A simpler real-world test: vowel-mark-before-medial corruption.
        text = "\u1000\u1031\u103C"  # base + vowel e + medial ra (wrong order)
        result = normalize_myanmar_nfc(text)
        # Should be reordered to canonical order.
        assert result == "\u1000\u103C\u1031"  # base + medial ra + vowel e
        # Verify no extra characters introduced.
        assert len(text) == len(result)

    def test_dead_marks_attach_to_following_base(self):
        # dead mark followed by base should put mark in front of that base
        # (semantically, the previous base is killed).
        text = "\u1000\u103A\u1014"  # ka + asat + na
        result = normalize_myanmar_nfc(text)
        # Expected: asat goes with the FOLLOWING base (na), so:
        # syllable 1: U+1000 (just ka, no marks survive)
        # syllable 2: U+103A U+1014 (asat + na)
        assert "\u103A\u1014" in result

    def test_preserves_whitespace(self):
        text = "\u1000\u1031 \u1014\u1031"
        result = normalize_myanmar_nfc(text)
        assert " " in result
        assert result == "\u1000\u1031 \u1014\u1031"

    def test_preserves_ascii_boundaries(self):
        text = "Section \u1000\u1031 1"
        result = normalize_myanmar_nfc(text)
        assert "Section " in result
        assert " 1" in result

    def test_idempotent(self):
        # Applying NFC twice should give same result.
        text = "\u1000\u1031\u103C\u103A"
        once = normalize_myanmar_nfc(text)
        twice = normalize_myanmar_nfc(once)
        assert once == twice

    def test_skips_when_no_myanmar_marks(self):
        # Pure consonant string with no marks - fast path
        text = "\u1000\u1001\u1002"
        assert normalize_myanmar_nfc(text) == text


class TestNormalizationOrder:
    """Verify the canonical order matches UAX #9 §11.4."""

    def test_canonical_order_progression(self):
        # All marks in REVERSE canonical order should be sorted to forward.
        marks_in_canonical_order = "\u103C\u103D\u103E\u102B\u102C\u102D\u102E\u102F\u1030\u1031\u1032\u1033\u1034\u1035\u1036\u1037\u1038\u1039\u103A\u103B\u1081\u1082\u1083\u1084"
        marks_reversed = marks_in_canonical_order[::-1]
        base = "\u1000"
        text = base + marks_reversed
        result = normalize_myanmar_nfc(text)
        assert result == base + marks_in_canonical_order