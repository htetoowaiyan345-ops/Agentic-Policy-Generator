"""Tests for the generic section-start detector."""
from __future__ import annotations

from policy_platform.rag.section_detector import (
    looks_like_section_heading,
    find_section_starts,
)


def test_all_caps_heading_detected():
    assert looks_like_section_heading("INTRODUCTION")
    assert looks_like_section_heading("DEFINITIONS")
    assert looks_like_section_heading("POLICY STATEMENT")
    assert looks_like_section_heading("AWARD STRUCTURE AND PAYOUT TIERS")


def test_numbered_heading_detected():
    assert looks_like_section_heading("1. Purpose")
    assert looks_like_section_heading("2) Scope")
    assert looks_like_section_heading("I. Introduction")
    assert looks_like_section_heading("IV. Policy")


def test_short_two_word_heading_detected():
    assert looks_like_section_heading("Related Policies")
    assert looks_like_section_heading("Exclusions")
    assert looks_like_section_heading("Definitions")


def test_body_paragraph_not_heading():
    assert not looks_like_section_heading("This is a normal body paragraph with multiple sentences.")
    assert not looks_like_section_heading("Introduction: This policy supports employee engagement by recognizing exceptional performance.")


def test_very_short_not_heading():
    assert not looks_like_section_heading("")
    assert not looks_like_section_heading("Hi")


def test_label_row_not_heading():
    assert not looks_like_section_heading("Type: HR Policy")
    assert not looks_like_section_heading("Policy Number: HR-001")


def test_heading_with_body():
    # Multi-line heading + body.
    assert looks_like_section_heading("Introduction\nThis policy supports employee engagement by recognizing exceptional performance.")


def test_find_section_starts():
    paragraphs = [
        "Header label rows",  # not a heading
        "Introduction",  # heading
        "This is body content.",
        "1. Purpose",  # heading
        "Purpose body here.",
        "DEFINITIONS",  # heading
        "Definition body here.",
    ]
    indices = find_section_starts(paragraphs)
    # Introduction, Purpose, DEFINITIONS
    assert indices == [1, 3, 5]