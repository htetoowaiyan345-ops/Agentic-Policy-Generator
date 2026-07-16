"""Tests for Phase C narrative-inference rules.

Exercises the FDA-style / label-light document cases where the
input doesn't follow the `Label: value` schema but DOES contain
language from which we can defensively infer some labels:
  - "Document issued on the web on <date>" -> Effective Date/Period
  - 'supersedes "Previous Policy Title"'  -> Supersedes
  - First-page "Policy for X" line         -> Policy Title
"""
from __future__ import annotations

from policy_platform.extractors.narrative_inference import infer_narrative_fields
from policy_platform.extractors.field_parser import parse


def test_issued_on_phrase_sets_effective_date():
    """`Document issued on the web on November 15, 2021.` -> Effective Date/Period."""
    paragraphs = [
        "Contains Nonbinding Recommendations",
        "Policy for Coronavirus Disease-2019 Tests",
        "Document issued on the web on November 15, 2021.",
    ]
    out = infer_narrative_fields(paragraphs, existing_field_map={})
    assert "Effective Date/Period:" in out
    assert "November 15, 2021" in out["Effective Date/Period:"]


def test_supersedes_phrase_sets_supersedes():
    """`This document supersedes "Previous Policy..."` -> Supersedes."""
    paragraphs = [
        "This document supersedes \"Policy for Coronavirus Disease-2019 Tests "
        "During the Public Health Emergency (Revised): Guidance for Clinical "
        'Laboratories" issued May 11, 2020.',
    ]
    out = infer_narrative_fields(paragraphs, existing_field_map={})
    assert "Supersedes:" in out
    assert "Policy for Coronavirus Disease-2019" in out["Supersedes:"]


def test_policy_for_first_line_sets_title():
    """First page "Policy for X" line -> Policy Title."""
    paragraphs = [
        "Policy for Coronavirus Disease-2019 Tests During the Public Health Emergency (Revised)*",
        "Guidance for Developers and Food and Drug Administration Staff",
    ]
    out = infer_narrative_fields(paragraphs, existing_field_map={})
    assert "Policy Title:" in out
    assert "Coronavirus" in out["Policy Title:"]


def test_sexual_harassment_first_line_title():
    """`Sexual Harassment Policy for <audience>` -> Policy Title."""
    paragraphs = [
        "Sexual Harassment Policy for All Employers in New York State",
    ]
    out = infer_narrative_fields(paragraphs, existing_field_map={})
    assert "Policy Title:" in out
    assert "Sexual Harassment" in out["Policy Title:"]


def test_multiline_title_with_trailing_space_only():
    """A line ending with "Policy for " (no trailing text) merges with next line."""
    paragraphs = [
        "Sexual Harassment Policy for  ",
        "All Employers in New York State ",
    ]
    out = infer_narrative_fields(paragraphs, existing_field_map={})
    assert "Policy Title:" in out
    assert "Sexual Harassment" in out["Policy Title:"]
    assert "All Employers" in out["Policy Title:"]


def test_existing_label_is_not_overwritten():
    """If Effective Date already exists, infer_narrative does NOT overwrite."""
    paragraphs = ["Document issued on the web on November 15, 2021."]
    existing = {"Effective Date/Period:": "01 July 2026"}
    out = infer_narrative_fields(paragraphs, existing_field_map=existing)
    assert "Effective Date/Period:" not in out  # did not overwrite


def test_no_rules_match_returns_empty():
    """Input that has nothing matching returns empty dict."""
    paragraphs = ["Random paragraph.", "Another random paragraph."]
    out = infer_narrative_fields(paragraphs, existing_field_map={})
    assert out == {}


def test_empty_paragraphs_returns_empty():
    out = infer_narrative_fields([], existing_field_map={})
    assert out == {}


def test_parse_picks_up_narrative_labels():
    """`parse()` runs narrative phase and adds them to the field map."""
    paragraphs = [
        "Policy for Coronavirus Disease-2019 Tests During the Public Health Emergency (Revised)*",
        "Document issued on the web on November 15, 2021.",
        'This document supersedes "Policy for Coronavirus Disease-2019 Tests During '
        'the Public Health Emergency (Revised): Guidance for Clinical Laboratories, '
        'Commercial Manufacturers, and Food and Drug Administration Staff" issued '
        "May 11, 2020.",
    ]
    fm = parse(paragraphs)
    assert "Policy Title:" in fm
    assert "Coronavirus" in fm["Policy Title:"]
    assert "Effective Date/Period:" in fm
    assert "November 15, 2021" in fm["Effective Date/Period:"]
    assert "Supersedes:" in fm
