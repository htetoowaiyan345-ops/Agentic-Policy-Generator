"""Slot tier classification for required-field handling.

Tier 1: critical framework slots — missing input blocks validation.
Tier 2: soft-required slots — missing input renders placeholder and is
        flagged in the audit.
Tier 3: optional slots — missing input causes the slot body to be
        blank (legacy behavior; Phase 6 leaves this as-is).

Frozen alongside the Brain manifest. Whenever BRAIN_SLOT_RANGES changes,
update this mapping to keep the two in sync.
"""
from __future__ import annotations


# Tier classifications for the 15 Brain slots.
SLOT_TIERS: dict[int, int] = {
    1:  1,   # Header             — critical
    2:  2,   # Brief Description  — soft required
    3:  1,   # Approval           — critical
    4:  2,   # Reason for Policy  — soft required
    5:  3,   # INTRODUCTION       — optional
    6:  3,   # POLICY STATEMENT   — optional
    7:  3,   # Purpose            — optional
    8:  3,   # Scope              — optional
    9:  3,   # Exclusions         — optional
    10: 1,   # Award table        — critical
    11: 2,   # Policy Review Note — soft required
    12: 3,   # DEFINITIONS        — optional
    13: 3,   # RELATED POLICIES   — optional
    14: 1,   # HISTORY table      — critical
    15: 1,   # Logo & Image       — critical (framework brand)
}


def slot_required(sec_id: int) -> bool:
    """Tier 1 and Tier 2 are 'required' — must produce visible content."""
    return SLOT_TIERS.get(sec_id, 3) <= 2


def slot_label(sec_id: int) -> str:
    """Canonical display label for a slot."""
    from .section_map import FROZEN_SECTIONS
    for s in FROZEN_SECTIONS:
        if s.get("id") == sec_id:
            return s.get("title", f"Slot {sec_id}")
    return f"Slot {sec_id}"
