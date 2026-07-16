"""Tests for `policy_platform.extractors.spacy_extractor`.

All tests are guarded at the function level: if spaCy or the
`en_core_web_sm` model is not installed, each test skips without
failing — production environments without spaCy keep working.
"""
from __future__ import annotations

import pytest

from policy_platform.extractors.spacy_extractor import (
    SpaCyUnavailable,
    extract_field_map,
    is_available,
)


def _spacy_runtime_ready(monkeypatch) -> bool:
    """Same logic `is_available` uses: env var=1 + spaCy + model."""
    monkeypatch.setenv("AGENTIC_POLICY_USE_SPACY", "1")
    return is_available()


def test_extracts_type_label(monkeypatch):
    if not _spacy_runtime_ready(monkeypatch):
        pytest.skip("spaCy or en_core_web_sm not installed")
    paragraphs = ["Type: Policy."]
    field_map, path = extract_field_map(paragraphs)
    # spaCy keeps the trailing period; downstream consumers strip it.
    assert "Type:" in field_map
    assert field_map["Type:"].rstrip(".!?").strip() == "Policy"
    assert path == "spacy"


def test_extracts_effective_date(monkeypatch):
    if not _spacy_runtime_ready(monkeypatch):
        pytest.skip("spaCy or en_core_web_sm not installed")
    paragraphs = ["Effective Date: 01 July 2026."]
    field_map, _ = extract_field_map(paragraphs)
    assert "Effective Date/Period:" in field_map
    assert "01 July 2026" in field_map["Effective Date/Period:"]


def test_extracts_no_label_sentence(monkeypatch):
    if not _spacy_runtime_ready(monkeypatch):
        pytest.skip("spaCy or en_core_web_sm not installed")
    paragraphs = ["Some random prose with no label.", "Just text here."]
    field_map, path = extract_field_map(paragraphs)
    assert field_map == {}
    assert path == "spacy-fallback"


def test_handles_one_paragraph_dense_input(monkeypatch):
    if not _spacy_runtime_ready(monkeypatch):
        pytest.skip("spaCy or en_core_web_sm not installed")
    paragraph = (
        "Type: Internal. Policy Title: Earthquake Response Plan. "
        "Policy Number: ERP-2026-01. "
        "Effective Date: 01 July 2026. "
        "Approved by: Group CEO."
    )
    field_map, path = extract_field_map([paragraph])
    assert path == "spacy"
    assert "Type:" in field_map
    assert "Policy Title:" in field_map
    assert "Policy Number:" in field_map
    assert "Approved by:" in field_map


def test_handles_decimal_numbers(monkeypatch):
    if not _spacy_runtime_ready(monkeypatch):
        pytest.skip("spaCy or en_core_web_sm not installed")
    paragraphs = ["Award amount: MMK 1,000,000.00 for first place."]
    field_map, _ = extract_field_map(paragraphs)
    assert isinstance(field_map, dict)


def test_handles_abbreviations(monkeypatch):
    if not _spacy_runtime_ready(monkeypatch):
        pytest.skip("spaCy or en_core_web_sm not installed")
    paragraphs = [
        "Approved by: Dr. U Win Myint Aung, Corporate Affairs Director, "
        "City Holdings e.g. the parent group."
    ]
    field_map, _ = extract_field_map(paragraphs)
    assert "Approved by:" in field_map


def test_spaCy_unavailable_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("AGENTIC_POLICY_USE_SPACY", raising=False)
    monkeypatch.setenv("AGENTIC_POLICY_USE_SPACY", "0")
    assert is_available() is False
