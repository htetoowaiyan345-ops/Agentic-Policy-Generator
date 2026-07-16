"""Tests for `policy_platform.extractors.title_extractor` (Stage C)."""
from __future__ import annotations

from policy_platform.extractors.title_extractor import (
    extract_title_from_paragraphs,
)


def test_first_non_empty_line_in_range_is_title():
    """First non-empty line, 8-80 chars: returned as-is."""
    paragraphs = [
        "",
        "   ",
        "Earthquake Response Plan Policy",
        "Type: Internal...",
    ]
    assert (
        extract_title_from_paragraphs(paragraphs)
        == "Earthquake Response Plan Policy"
    )


def test_first_line_out_of_range_returns_none():
    """First non-empty line outside 8-80 chars: None (fallback deferred to header_extractor)."""
    too_short = ["EP"]
    too_long = ["x" * 200]
    assert extract_title_from_paragraphs(too_short) is None
    assert extract_title_from_paragraphs(too_long) is None


def test_empty_input_returns_none():
    """All-empty input: None."""
    assert extract_title_from_paragraphs([]) is None
    assert extract_title_from_paragraphs(["", "   ", ""]) is None


def test_unicode_title_supported():
    """Non-ASCII first line with valid length is returned."""
    paragraphs = ["ငလျင်တုံ့ပြန်မှုမူဝါဒ - Earthquake Response"]
    assert extract_title_from_paragraphs(paragraphs) == paragraphs[0]


def test_boundary_lengths():
    """Length 8 (lower bound) returns; length 81 (just over 80) returns None."""
    paragraphs_8 = ["12345678"]
    paragraphs_80 = ["x" * 80]
    paragraphs_81 = ["x" * 81]
    assert extract_title_from_paragraphs(paragraphs_8) == "12345678"
    assert extract_title_from_paragraphs(paragraphs_80) == "x" * 80
    assert extract_title_from_paragraphs(paragraphs_81) is None
