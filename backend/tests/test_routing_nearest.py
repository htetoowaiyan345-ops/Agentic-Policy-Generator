"""Tests for Phase 3A — nearest-neighbor table routing.

Verifies that:
  * _find_table_section_slot walks both directions and picks the closer
  * distance is reported in paragraphs
  * tie → forward direction wins (slot order is monotonic)
  * backward-only and forward-only fallbacks still behave correctly
  * the pre-existing `test_no_cross_slot_dup_with_section_index` passes
    (this was a Phase 1 baseline failure fixed by Phase 3A)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. Walk both directions; pick closer
# ---------------------------------------------------------------------------

def test_nearest_heading_wins_both_directions():
    from policy_platform.rag.table_routing import (
        _find_table_section_slot_with_distance,
    )
    # section_index: slot 9 heading at paragraph 20, slot 10 at 50.
    # A table appearing at paragraph 45 is 25 paragraphs AFTER slot 9
    # but only 5 paragraphs BEFORE slot 10 — closer to slot 10.
    section_index = {0: 5, 5: 6, 10: 7, 15: 9, 20: 9, 50: 10}
    paragraphs = ["p"] * 60
    # Table appears at paragraph 45 (target_pi = 45)
    result = _find_table_section_slot_with_distance(
        table_idx=0,
        tables=[[]],
        section_index=section_index,
        table_paragraph_indices=[45],
        paragraphs=paragraphs,
    )
    # Closest heading is slot 10 at paragraph 50 (distance 5).
    # Slot 9 at paragraph 20 is distance 25 (farther).
    assert result is not None, result
    slot_id, distance = result
    assert slot_id == 10, f"Expected slot 10 (closer), got {slot_id}"
    assert distance == 5, f"Expected distance 5, got {distance}"


def test_walking_back_only_works_for_one_sided():
    """If no forward heading exists, fall back to backward (pre-Phase 3
    behavior)."""
    from policy_platform.rag.table_routing import (
        _find_table_section_slot_with_distance,
    )
    # Slot 9 at paragraph 20, no slot 10 in this doc.
    section_index = {0: 5, 20: 9}
    paragraphs = ["p"] * 40
    result = _find_table_section_slot_with_distance(
        table_idx=0,
        tables=[[]],
        section_index=section_index,
        table_paragraph_indices=[25],
        paragraphs=paragraphs,
    )
    assert result is not None
    slot_id, distance = result
    assert slot_id == 9
    assert distance == 5


def test_distance_score_tie_break():
    """Equal distance forward and backward → forward wins (slot order
    is monotonic)."""
    from policy_platform.rag.table_routing import (
        _find_table_section_slot_with_distance,
    )
    # Slot 9 at paragraph 10, slot 10 at paragraph 30.
    # Table at paragraph 20 → equidistant (10 back, 10 forward).
    section_index = {10: 9, 30: 10}
    paragraphs = ["p"] * 40
    result = _find_table_section_slot_with_distance(
        table_idx=0,
        tables=[[]],
        section_index=section_index,
        table_paragraph_indices=[20],
        paragraphs=paragraphs,
    )
    assert result is not None
    slot_id, distance = result
    # Tie → forward (slot 10 at paragraph 30) wins.
    assert slot_id == 10, f"Expected slot 10 on tie, got {slot_id}"
    assert distance == 10


# ---------------------------------------------------------------------------
# 2. Pre-existing failure fixed by Phase 3A
# ---------------------------------------------------------------------------

def test_pre_existing_slot9_dedup_passes():
    """The Phase 1 baseline failure
    `test_no_cross_slot_dup_with_section_index` should pass after
    Phase 3A + 3B (header-name hint) fix.

    The test scenario: a "Level / Priority" maintenance table
    appearing in the slot 9 section must NOT leak to slot 10.
    """
    from policy_platform.rag.table_routing import (
        find_table_for_slot_with_context,
    )
    paragraphs = [
        "Introduction: This policy supports safety.",
        "Exclusions:",
        "Some exclusion text here.",
        "Level", "Facility", "Maintenance", "Priority",
        "Level 1", "Critical", "Monthly", "High",
    ]
    section_index = {0: 5, 1: 9, 2: 9}
    table_paragraph_indices = [3]
    facility_table = [
        ["Level", "Facility", "Maintenance", "Priority"],
        ["Level 1", "Critical Care", "Monthly", "High"],
    ]
    # Slot 9 should claim this table.
    result9 = find_table_for_slot_with_context(
        9, [facility_table], section_index, paragraphs,
        table_paragraph_indices=table_paragraph_indices,
    )
    assert result9 is not None, "Slot 9 should claim the facility table"
    # Slot 10 should NOT get this table.
    result10 = find_table_for_slot_with_context(
        10, [facility_table], section_index, paragraphs,
        table_paragraph_indices=table_paragraph_indices,
    )
    assert result10 is None, (
        "Slot 10 should not claim a table in slot 9's section; got "
        f"{result10}"
    )