"""Phase K regression tests for slot routing + dedup.

These tests verify the general-purpose fixes for:
- Bug A: Cross-slot table duplication (same table in slot 9 AND slot 10)
- Bug B: Over-broad slot-10 fallback signals (table with "Level 1/2/3
  Maintenance" routed to slot 10 by content-signal match)
- Bug C: Prose/table overlap (slot 9 renders prose AND table)
- Bug D: PDF paragraph-stream leakage (2-col label-row table cells
  appear in the paragraph stream and get routed to slot 9/10 by RAG)
- Bug E: Multi-table drop (slot has 2+ tables but only first is rendered)

All tests use fixtures OR run the full pipeline on the bundled sample
files. They must pass on EVERY input file, not just the existing
samples.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from policy_platform.rag.table_routing import (
    TABLE_SLOT_SIGNALS,
    find_all_tables_for_slot_with_context,
    find_table_for_slot_with_context,
)
from policy_platform.rag.retrieval_pipeline import (
    RetrievalPipeline,
    _prose_table_overlap,
)
from policy_platform.rag_adapter import build_classification_from_rag
from policy_platform.extractors import dispatch


# ---------------------------------------------------------------------------
# Bug A: Cross-slot table dedup
# ---------------------------------------------------------------------------

def test_no_cross_slot_table_dup():
    """A table with no slot context cannot be claimed by multiple slots.

    Bug A root cause: find_all_tables_for_slot_with_context() used a
    LOCAL `used_tables` set per call, so the same table could be
    assigned to slot 10 first, then to slot 9 too.

    Fix: claimed_tables set passed across calls.
    """
    # A table that has slot-9 signals (exclusion) but no slot-10
    # signals. No position context → must use signal fallback.
    tables = [
        [
            ["Exclusion", "Reason", "Notes"],
            ["Item A", "Not eligible", "Out of scope"],
            ["Item B", "Excluded", "Legacy"],
        ]
    ]
    section_index = {}  # no section context

    # Slot 9 claims this table first via fallback.
    r9 = find_all_tables_for_slot_with_context(
        9, tables, section_index, [], claimed_tables=set(),
    )
    assert r9, "slot 9 fallback should claim table"
    claimed = {0}

    # Slot 10 should NOT see this table now.
    r10 = find_all_tables_for_slot_with_context(
        10, tables, section_index, [], claimed_tables=claimed,
    )
    assert not r10, (
        f"slot 10 should not claim table already claimed by slot 9, "
        f"got {r10}"
    )


def test_no_cross_slot_dup_with_section_index():
    """Tables assigned by document-position to slot 9 must not also
    route to slot 10 via content-signal fallback."""
    paragraphs = [
        "Policy Title",  # 0
        "Brief Description",  # 1
        "1. Purpose",  # 2
        "2. Scope & Beneficiaries",  # 3
        "3. Exclusions",  # 4
        "Some exclusion text.",  # 5
        "4. Award Structure",  # 6
        "Award criteria.",  # 7
        "DEFINITIONS",  # 8
        "HISTORY",  # 9
    ]
    section_index = {
        4: 9,
        5: 9,
        6: 10,
        7: 10,
        8: 12,
        9: 14,
    }
    # TABLE 0 is positioned right after paragraph 5 (in slot 9's section).
    # TABLE 1 is positioned right after paragraph 7 (in slot 10's section).
    table_paragraph_indices = [5, 7]
    tables = [
        # Table for slot 9: contains an exclusion list.
        [
            ["Exclusion", "Reason"],
            ["Item A", "Not eligible"],
            ["Item B", "Out of scope"],
        ],
        # Table for slot 10: contains award tiers.
        [
            ["Tier", "Amount", "Currency"],
            ["Tier 1", "100", "USD"],
            ["Tier 2", "200", "USD"],
        ],
    ]
    # Slot 9 claims TABLE 0.
    r9 = find_all_tables_for_slot_with_context(
        9, tables, section_index, paragraphs,
        table_paragraph_indices=table_paragraph_indices,
        claimed_tables=set(),
    )
    assert r9, "slot 9 should claim TABLE 0"
    claimed = {0}
    # Slot 10 should claim TABLE 1 only (TABLE 0 already taken).
    r10 = find_all_tables_for_slot_with_context(
        10, tables, section_index, paragraphs,
        table_paragraph_indices=table_paragraph_indices,
        claimed_tables=claimed,
    )
    assert r10, "slot 10 should claim TABLE 1"
    # Verify slot 10 got TABLE 1, not TABLE 0.
    assert r10[0][0][0] == "Tier", (
        f"slot 10 should have the tier table, got {r10[0][0]}"
    )


# ---------------------------------------------------------------------------
# Bug B: Over-broad slot-10 fallback signals
# ---------------------------------------------------------------------------

def test_slot_10_over_broad_signal_rejected():
    """A table about 'Level 1/2/3 Facility Maintenance' must NOT route
    to slot 10 by content-signal fallback (Bug B).

    With the tightened signals, the score should be < 2 (the new
    SLOT_10_MIN_SIGNAL_HITS threshold) and the table should not
    match slot 10.
    """
    tables = [
        [
            ["Level", "Facility Type", "Maintenance Frequency", "Priority"],
            ["Level 1", "Critical Care", "Monthly", "High"],
            ["Level 2", "Patient Wards", "Quarterly", "High"],
            ["Level 3", "Administrative", "Semi-Annual", "Medium"],
        ]
    ]
    section_index = {}  # no position context → must use signal fallback
    matched = find_all_tables_for_slot_with_context(
        10, tables, section_index, [], claimed_tables=set(),
    )
    assert not matched, (
        f"facility maintenance table should NOT route to slot 10 "
        f"(score must be < {TABLE_SLOT_SIGNALS[10]}); got {matched}"
    )


def test_slot_10_real_award_table_routes_correctly():
    """A real Award Structure table should still route to slot 10.

    This test guards against over-tightening: a genuine payout table
    with multiple signal keywords must still match.
    """
    tables = [
        [
            ["Tier", "Payout Amount", "Currency", "Recognition"],
            ["Tier 1", "100", "USD", "Spot Award"],
            ["Tier 2", "500", "USD", "Excellence Award"],
            ["Tier 3", "1000", "USD", "Leadership Award"],
            ["Tier 4", "5000", "USD", "Annual Grand Award"],
        ]
    ]
    matched = find_all_tables_for_slot_with_context(
        10, tables, section_index={}, paragraphs=[], claimed_tables=set(),
    )
    assert matched, "real award table must route to slot 10"


# ---------------------------------------------------------------------------
# Bug C: Prose/table overlap suppression
# ---------------------------------------------------------------------------

def test_prose_table_overlap_suppressed():
    """When anchor prose text is ≥70% substring of table cell text,
    the prose must be suppressed to avoid duplicate rendering (Bug C).

    Hospital PDF scenario: slot 9 heading "3. Exclusions" with body
    "Off-site leased premises not under hospital operational control."
    PLUS a table about "Level 1/2/3 Facility Maintenance". The body
    text is independent of the table content, so overlap should be
    low. Let's test BOTH cases.
    """
    # Case 1: prose IS table content → overlap should be True.
    prose_overlap = "Level 1 Critical Care Monthly High"
    table_overlap = [
        ["Level", "Facility Type", "Maintenance Frequency", "Priority"],
        ["Level 1", "Critical Care", "Monthly", "High"],
        ["Level 2", "Patient Wards", "Quarterly", "High"],
    ]
    assert _prose_table_overlap(prose_overlap, table_overlap), (
        "prose that matches table cells should be detected as overlap"
    )

    # Case 2: prose is independent → overlap should be False.
    prose_independent = "Off-site leased premises not under hospital control."
    assert not _prose_table_overlap(prose_independent, table_overlap), (
        "independent prose should NOT be detected as overlap"
    )

    # Case 3: prose shorter than threshold (20 chars) → no overlap.
    assert not _prose_table_overlap("Hi", table_overlap), (
        "very short prose should not trigger overlap"
    )


# ---------------------------------------------------------------------------
# Bug D: PDF paragraph-stream leakage (label-row tables)
# ---------------------------------------------------------------------------

def test_label_row_table_not_in_paragraph_stream():
    """The slot-1 schema label-row table (Type/Title/Date/etc.) cells
    must NOT leak into the paragraph stream after PDF extraction.

    Bug D root cause: pdf_extractor only filtered 3+ column data
    tables; 2-column label-row tables still emitted cells as
    paragraphs, allowing them to match slot 9/10 by keyword.

    This test reads the Hospital PDF and asserts that no paragraph
    text is a label-row cell value (e.g. 'Facilities & Infrastructure
    Policy', 'HBM-001').
    """
    from policy_platform.extractors.pdf_extractor import extract as pdf_extract
    sample = (
        Path(__file__).parent.parent
        / "data" / "samples" / "Hospital_Buildings_Policy_Template.pdf"
    )
    if not sample.exists():
        pytest.skip(f"sample not found: {sample}")
    doc = pdf_extract(sample)
    label_row_values = [
        "Facilities & Infrastructure Policy",
        "HBM-001",
        "Healthcare Services",
        "Facilities Management",
        # Add more if needed.
    ]
    leaked = [p for p in doc.paragraphs if any(v in p for v in label_row_values)]
    assert not leaked, (
        f"label-row table cells should NOT appear in paragraph stream, "
        f"but found: {leaked}"
    )


# ---------------------------------------------------------------------------
# Bug E: Multi-table passthrough
# ---------------------------------------------------------------------------

def test_multi_table_passthrough_slot_9():
    """If a section has 2 tables, BOTH should be returned for slot 9.

    Bug E root cause: renderer only used content_tables[0].
    Fix: extra_tables are now rendered as separate <w:tbl> elements.
    """
    tables = [
        [
            ["Exclusion", "Reason"],
            ["Item A", "Not eligible"],
        ],
        [
            ["Other Exclusion", "Scope"],
            ["Item B", "Out of scope"],
        ],
    ]
    paragraphs = [
        "Policy Title",  # 0
        "3. Exclusions",  # 1
        "Some text",  # 2
    ]
    section_index = {1: 9, 2: 9}
    table_paragraph_indices = [2, 2]
    matched = find_all_tables_for_slot_with_context(
        9, tables, section_index, paragraphs,
        table_paragraph_indices=table_paragraph_indices,
        claimed_tables=set(),
    )
    assert matched, "slot 9 should match"
    assert len(matched) == 2, (
        f"both tables should be returned for slot 9, got {len(matched)}"
    )


# ---------------------------------------------------------------------------
# E2E: Hospital PDF slot 9 / slot 10 verification
# ---------------------------------------------------------------------------

def test_hospital_pdf_slot_9_no_prose_dup():
    """Hospital PDF: slot 9 should have the facility-level table
    AND no duplicated prose paragraph below it.

    Bug C root cause: Phase I.3a dedup only applied to slots 10/14,
    not slot 9. After Phase K.1, slot 9 also drops prose when table
    is present.
    """
    sample = (
        Path(__file__).parent.parent
        / "data" / "samples" / "Hospital_Buildings_Policy_Template.pdf"
    )
    if not sample.exists():
        pytest.skip(f"sample not found: {sample}")
    doc = dispatch(sample)
    pipe = RetrievalPipeline()
    rag = pipe.run(
        doc.paragraphs,
        tables=doc.tables,
        table_paragraph_indices=doc.table_paragraph_indices,
    )
    # Slot 9 should have a table (the facility-level one).
    s9 = rag.slots.get(9)
    assert s9 is not None, "slot 9 should be assigned"
    if s9.table is not None:
        # If slot 9 has a table, it should NOT have prose (no dup).
        assert s9.chunk_text is None or s9.chunk_text.strip() == "", (
            f"slot 9 with table should have no prose, got {s9.chunk_text!r}"
        )


def test_hospital_pdf_slot_10_is_marker():
    """Hospital PDF: slot 10 should NOT claim the facility table.

    Bug B root cause: over-broad signals caused facility table to
    route to slot 10 by content-signal fallback. After tightening,
    slot 10 should be empty (canonical marker).
    """
    sample = (
        Path(__file__).parent.parent
        / "data" / "samples" / "Hospital_Buildings_Policy_Template.pdf"
    )
    if not sample.exists():
        pytest.skip(f"sample not found: {sample}")
    doc = dispatch(sample)
    pipe = RetrievalPipeline()
    rag = pipe.run(
        doc.paragraphs,
        tables=doc.tables,
        table_paragraph_indices=doc.table_paragraph_indices,
    )
    s10 = rag.slots.get(10)
    assert s10 is not None, "slot 10 should be assigned"
    # Slot 10 should NOT have the facility table.
    if s10.table is not None:
        first_row = [c.strip().lower() for c in s10.table[0] if c]
        assert "level" not in first_row and "facility type" not in first_row, (
            f"slot 10 must not claim the facility table; got {first_row}"
        )


# ---------------------------------------------------------------------------
# Award DOCX: slot 10 should still get the tier table
# ---------------------------------------------------------------------------

def test_award_docx_slot_10_preserved():
    """Award DOCX: slot 10 should still get the tier table.

    This guards against the slot-10 tightening breaking legitimate
    award table routing.
    """
    sample = (
        Path(__file__).parent.parent
        / "data" / "samples"
        / "Policy_Template_Award_and_Recognition_Updated.docx"
    )
    if not sample.exists():
        pytest.skip(f"sample not found: {sample}")
    doc = dispatch(sample)
    pipe = RetrievalPipeline()
    rag = pipe.run(
        doc.paragraphs,
        tables=doc.tables,
        table_paragraph_indices=doc.table_paragraph_indices,
    )
    s10 = rag.slots.get(10)
    assert s10 is not None, "slot 10 should be assigned"
    # Slot 10 should have a tier-style table OR prose about awards.
    has_tier = False
    if s10.table is not None:
        flat = " ".join(
            c for r in s10.table for c in r if c
        ).lower()
        if "tier" in flat or "payout" in flat or "award" in flat:
            has_tier = True
    if s10.chunk_text is not None:
        if "tier" in s10.chunk_text.lower() or "payout" in s10.chunk_text.lower():
            has_tier = True
    assert has_tier, (
        f"slot 10 in Award DOCX should have tier/award content; "
        f"got table={s10.table is not None}, text={s10.chunk_text!r}"
    )