"""Tests for Phase 3B — header-name → slot synonym matching.

Verifies that:
  * a Tier/Amount header table scores higher for slot 10
  * a Department/Manager header table does NOT match slot 10
  * both detection modes (short_cell_length + first_row) are consulted
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. Tier table headers match slot 10
# ---------------------------------------------------------------------------

def test_tier_table_headers_match_slot_10():
    """A table with headers ['Tier', 'Amount'] should have ≥2 slot 10
    header-hits (Tier matches 'tier' tokens in slot 10 synonyms; Amount
    matches 'amount' tokens)."""
    from policy_platform.rag.table_routing import _header_slot_hits
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS

    table = [
        ["Tier", "Amount"],
        ["1", "100"],
    ]
    hits = _header_slot_hits(
        table,
        {10: SECTION_HEADING_SYNONYMS.get(10, [])},
    )
    assert 10 in hits, f"slot 10 should have header hits, got {hits}"
    assert hits[10] >= 2, f"Expected ≥2 header hits for slot 10, got {hits[10]}"


# ---------------------------------------------------------------------------
# 2. Building/Funding headers do NOT match slot 10
# (these are unrelated to award tiers)
# ---------------------------------------------------------------------------

def test_unrelated_headers_no_match():
    from policy_platform.rag.table_routing import _header_slot_hits
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS

    table = [
        ["Department", "Manager"],
        ["HR", "Alice"],
    ]
    hits = _header_slot_hits(
        table,
        {10: SECTION_HEADING_SYNONYMS.get(10, [])},
    )
    assert 10 not in hits or hits[10] == 0, (
        f"Unrelated headers should not match slot 10, got {hits}"
    )


# ---------------------------------------------------------------------------
# 3. Both header detection modes (short_cell_length + first_row)
# ---------------------------------------------------------------------------

def test_both_header_detection_modes():
    """When the table has headers that are NOT the shortest row, the
    `short_cell_length` heuristic might pick the wrong row. The
    Phase 3B routing falls back to `first_row` when synonyms
    disagree, ensuring correct header detection.
    """
    from policy_platform.rag.table_routing import (
        _detect_header_row_for_routing,
        _header_slot_hits,
    )
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS

    # Table where data row is shorter than header row.
    # Row 0 = ['Tier', 'Amount'] (longer, the header)
    # Row 1 = ['1', '100']    (shorter, data)
    # short_cell_length alone would pick row 1 (avg=2 < avg=5).
    # Phase 3B fallback should pick row 0 (Tier/Amount).
    table = [
        ["Tier", "Amount"],
        ["1", "100"],
    ]
    headers = _detect_header_row_for_routing(table)
    assert headers == ["Tier", "Amount"], (
        f"Expected ['Tier', 'Amount'], got {headers}. short_cell_length "
        "would pick the data row; the routing helper must override."
    )
    hits = _header_slot_hits(
        table,
        {10: SECTION_HEADING_SYNONYMS.get(10, [])},
    )
    assert 10 in hits, f"slot 10 should match 'Tier'/'Amount', got {hits}"
    assert hits[10] >= 2