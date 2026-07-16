"""End-to-end RAG tests on real sample files.

Verifies that the heading-anchor + table-passthrough pipeline picks
the correct content for slots 5, 6, 7, 8, 9, 12, 13, 14 in the
Earthquake and Award samples.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform\backend")
sys.path.insert(0, str(BACKEND))

import pytest

from policy_platform.extractors import dispatch
from policy_platform.rag import RetrievalPipeline
from policy_platform.rag_adapter import build_classification_from_rag


SAMPLES = BACKEND / "data" / "samples"


def _run_on_sample(sample_path: Path, *, timeout: float = 180.0):
    extracted = dispatch(sample_path)
    pipe = RetrievalPipeline(timeout_seconds=timeout)
    rag = pipe.run(
        list(extracted.paragraphs),
        tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
    )
    classified = build_classification_from_rag(rag, source_paragraph_count=len(extracted.paragraphs))
    return classified, rag


@pytest.fixture(scope="module")
def earthquake_classified():
    src = SAMPLES / "Earthquake_Full_Policy_One_Paragraph.pdf"
    if not src.exists():
        pytest.skip(f"sample not found: {src}")
    classified, _rag = _run_on_sample(src)
    return classified


@pytest.fixture(scope="module")
def award_classified():
    src = SAMPLES / "Policy_Template_Award_and_Recognition_Updated.pdf"
    if not src.exists():
        pytest.skip(f"sample not found: {src}")
    classified, _rag = _run_on_sample(src)
    return classified


def test_earthquake_slot_7_purpose(earthquake_classified):
    """Slot 7 (Purpose) must contain the 'Purpose: To provide...' paragraph."""
    slot = earthquake_classified.sections[7]
    assert slot.status == "Found", f"slot 7 status={slot.status}"
    text = " ".join(slot.content_paragraphs).lower()
    assert "purpose" in text
    assert "to provide" in text or "provide" in text


def test_earthquake_slot_8_scope(earthquake_classified):
    """Slot 8 (Scope) must contain the 'Scope and Beneficiaries' paragraph."""
    slot = earthquake_classified.sections[8]
    assert slot.status == "Found", f"slot 8 status={slot.status}"
    text = " ".join(slot.content_paragraphs).lower()
    assert "scope" in text


def test_earthquake_slot_9_exclusions(earthquake_classified):
    """Slot 9 (Exclusions) must contain the 'Exclusions:' paragraph."""
    slot = earthquake_classified.sections[9]
    assert slot.status == "Found", f"slot 9 status={slot.status}"
    text = " ".join(slot.content_paragraphs).lower()
    assert "exclusion" in text


def test_earthquake_slot_12_definitions(earthquake_classified):
    """Slot 12 (Definitions) must contain the 'Definitions:' paragraph."""
    slot = earthquake_classified.sections[12]
    assert slot.status == "Found", f"slot 12 status={slot.status}"
    text = " ".join(slot.content_paragraphs).lower()
    assert "definition" in text


def test_earthquake_slot_14_history(earthquake_classified):
    """Slot 14 (History) must contain the version history line.

    Earthquake has a verbatim "Version FY26-27 Initial Draft
    issued on 03 July 2026." in its source. After the M1 label
    splitter default-on, slot 14 must surface this verbatim (or
    minimum the FY26-27 marker). The previous test was lenient
    (only checked length > 0) and missed regressions where the
    History content was lost to a different tier/table dedup.
    """
    slot = earthquake_classified.sections[14]
    assert slot.status == "Found", f"slot 14 status={slot.status}"
    text = " ".join(slot.content_paragraphs)
    # The version string MUST be present (case-insensitive).
    assert "FY26-27" in text or "fy26-27" in text.lower(), (
        f"slot 14 missing version history line; got: {text!r}"
    )
    # And the issuance metadata (issued / Initial Draft) MUST be present
    # so we know the full version-history line was captured, not a
    # coincidental substring match.
    text_lower = text.lower()
    assert (
        "initial draft" in text_lower
        or "issued on 03 july 2026" in text_lower
        or "issued on" in text_lower
    ), (
        f"slot 14 missing version metadata; got: {text!r}"
    )


def test_earthquake_prose_slots_use_heading_anchor(earthquake_classified):
    """The prose slots that matched should use the heading_anchor rule."""
    for sid in (5, 6, 7, 8, 9, 12, 13, 14):
        slot = earthquake_classified.sections.get(sid)
        if slot is None:
            continue
        if slot.status == "Found":
            assert slot.routing_rule in ("heading_anchor", "table_passthrough", "rag_hybrid", "heading_anchor+table"), \
                f"slot {sid} unexpected rule: {slot.routing_rule}"


def test_award_slot_10_passthrough_if_table_exists(award_classified):
    """Slot 10 (Award Structure) - if the source has a table, it should be passed through."""
    slot = award_classified.sections[10]
    # If the source has a payout table, content_tables should be populated.
    if slot.content_tables:
        assert slot.routing_rule in ("table_passthrough", "heading_anchor", "heading_anchor+table")
        # Table is preserved as-is
        table = slot.content_tables[0]
        assert len(table) >= 2  # at least header + 1 row


def test_award_label_row_slots_unchanged(award_classified):
    """Label-row slots 1, 2, 3, 4, 11 are not handled by RAG (they use field_parser).

    RAG marks them with status "External" to indicate the renderer
    should read from field_map. The actual content comes from the
    field_parser, not from RAG.
    """
    for sid in (1, 2, 3, 4, 11):
        slot = award_classified.sections.get(sid)
        assert slot is not None
        # External = filled by field_parser, not RAG.
        assert slot.status == "External"
        # No content_paragraphs from RAG (renderer will use field_map).
        assert slot.content_paragraphs == []
        # routing_rule marks the source.
        assert slot.routing_rule in ("field_parser", "no_match")
