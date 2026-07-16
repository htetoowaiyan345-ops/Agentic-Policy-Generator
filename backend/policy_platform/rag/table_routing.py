"""Table passthrough for table slots (9, 10, 14).

For slot 9 (Exclusions), 10 (Award Structure & Payout Tiers), and 14
(History), the source document often contains a TABLE rather than
prose. When a matching table is found, the whole table is passed
through as-is - all rows, all columns, all cell text preserved.

Tables must be routed by their DOCUMENT POSITION (which section
they appear in), not just by content keywords. A table that appears
under the Exclusions heading belongs to slot 9, NOT slot 10 even
if the table's content has "tier" or "level" keywords.

The Brain framework is FROZEN - we don't reformat the table to match
the Brain's table shape. The user wants the source table copied
verbatim into the output.
"""
from __future__ import annotations

from typing import List, Optional, Sequence


# Slots where table passthrough is the primary retrieval mechanism.
TABLE_SLOTS: set[int] = {9, 10, 14}

# Keyword signals that identify which table belongs to which slot.
# Used as a FALLBACK when no document-position context is available.
# We require at least MIN_SIGNAL_HITS keywords to match.
#
# IMPORTANT: the slot-10 list is intentionally narrow. It only matches
# tables that are actually about "Award Structure & Payout Tiers".
# Hospital/school maintenance tables, building-tier classifications,
# flood relief tables, exam tables, etc. must NOT be routed to slot 10
# — they belong in their natural sections (slot 8 Scope, slot 9
# Exclusions, etc.) and the user prefers slot 10 to show the canonical
# "Data is not found in source file" marker when the source has no
# real award content.
TABLE_SLOT_SIGNALS: dict[int, list[str]] = {
    9: [
        "exclusion", "exception", "limitation", "not covered",
        "not eligible", "excluded", "ineligible", "does not apply",
        "not include", "excluding", "except for",
    ],
    10: [
        # High-precision multi-word signals (require adjacent match).
        "indicative payout", "amount payable",
        "tier amount", "payout amount",
        "spot award", "excellence award", "leadership award",
        "annual grand award", "long service award",
        "recognition award", "award structure", "award category",
        "award tier", "payout tier", "tier 1", "tier 2", "tier 3", "tier 4",
        # Currencies and tier-specific tokens.
        "mmk", "usd",
        # Single-word signals - kept conservative.
        "award", "payout", "prize", "reward", "bronze", "silver", "gold", "platinum",
        "compensation", "reimbursement", "certificate", "trophy",
    ],
    14: [
        "history", "version", "revision", "date", "change",
        "v1.0", "v1.1", "v2.0", "v0.1", "amend", "issued",
        "initial", "release", "modified", "updated",
    ],
}

# Phase K.5: Generic / "neither slot 9 nor slot 10" signals.
# Tables whose content matches these signals should NOT be routed
# to slot 9 OR slot 10. They belong to a different slot (or no slot
# at all in the Brain schema). When detected, such tables are
# suppressed entirely from the output (no slot gets them).
GENERIC_TABLE_SIGNALS: list[str] = [
    # Maintenance schedule / frequency tables (NOT award tiers).
    "monthly", "quarterly", "annually", "weekly", "daily",
    "frequency", "scheduled", "schedule", "interval",
    # Safety / inspection / cleaning routine tables.
    "safety inspection", "safety inspections", "fire drill",
    "preventive maintenance", "preventative maintenance",
    "cleaning schedule", "cleanliness", "housekeeping",
    # Building / facility maintenance scope tables.
    "maintenance task", "maintenance activity", "maintenance scope",
    "area requirement", "area scope", "area frequency",
    # Inspection / audit tables.
    "audit checklist", "compliance check", "compliance audit",
    "inspection checklist", "inspection report",
]

# Minimum number of signal keywords (case-insensitive substring match)
# that must appear in the table for it to be considered a match.
MIN_SIGNAL_HITS: int = 1
SLOT_10_MIN_SIGNAL_HITS: int = 1


def _flatten_cells(table: Sequence[Sequence[str]]) -> str:
    """Join all cell text of a table into a single lowercase string."""
    parts: list[str] = []
    for row in table:
        for cell in row:
            if cell:
                parts.append(str(cell).lower())
    return " ".join(parts)


def _table_shape_ok(table: Sequence[Sequence[str]]) -> bool:
    """Reject degenerate tables: need at least 1 row and 1 column."""
    if not table:
        return False
    if len(table) < 1:
        return False
    for row in table:
        if not row:
            return False
        for cell in row:
            if cell and str(cell).strip():
                return True
    return False


# Specific labels from the slot-1 label-row schema (see
# framework.brain_fields.BRAIN_HEADER_FIELDS, BRAIN_APPROVAL_FIELDS, etc.)
# If the first column of a table contains MOSTLY these labels, it is
# the slot-1 label-row table and should NOT be matched by slot 9/10/14.
_LABEL_ROW_KEYWORDS: set[str] = {
    "type",
    "policy title",
    "policy number",
    "applicable sector",
    "applicable sector(s)",
    "functional area",
    "functional area(s)",
    "brief description",
    "effective date",
    "effective date/period",
    "approved by",
    "prepared by",
    "responsible function",
    "responsible function(s)",
    "responsible function officer",
    "responsible function officer(s)",
    "supersedes",
    "last reviewed",
    "last reviewed/updated",
    "applies to",
    "reason for policy",
    "policy review note",
}


def _looks_like_label_row_table(table: Sequence[Sequence[str]]) -> bool:
    """Heuristic: this table is the slot-1 label-row table (key-value pairs).

    The slot-1 label-row table is always 2 columns where col 0 is a
    short label and col 1 is the corresponding value. We use this to
    exclude it from slot 9/10/14 matching.

    Hybrid detection:
      1. Structural check (NEW) — works on any label vocabulary. Catches
         tables where labels like 'Reference', 'Policy Category', 'Coverage',
         'Managed By' don't appear in the canonical _LABEL_ROW_KEYWORDS set.
      2. Keyword fallback (LEGACY) — preserved verbatim for backward
         compatibility with documents whose labels match the canonical
         vocabulary (e.g. existing Award / School PDFs).

    A table is a label-row table if EITHER check passes.
    """
    if not table or len(table) < 2:
        return False
    if len(table[0]) != 2:
        return False

    # ---- Check 1: Structural ----
    short_count = 0
    valid_rows = 0
    for row in table[1:]:
        if not row or len(row) < 2:
            continue
        cell0 = (str(row[0]) if row[0] else "").strip()
        cell1 = (str(row[1]) if row[1] else "").strip()
        if not cell0 and not cell1:
            continue
        if not cell0:
            # missing label => not a label-row
            return False
        valid_rows += 1
        if len(cell0.split()) <= 5:
            short_count += 1
    if valid_rows >= 2 and short_count / valid_rows >= 0.5:
        return True

    # ---- Check 2: Keyword fallback (legacy) ----
    label_count = 0
    for row in table[1:]:
        if not row or not row[0]:
            continue
        cell = str(row[0]).strip().lower().rstrip(":")
        if cell in _LABEL_ROW_KEYWORDS:
            label_count += 1
    total_data_rows = sum(1 for row in table[1:] if row and any(c for c in row))
    if total_data_rows == 0:
        return False
    return label_count >= max(2, total_data_rows * 0.5)


def _table_score_for_slot(table: Sequence[Sequence[str]], slot_id: int) -> int:
    """Count signal keywords for a slot. Higher score = better match."""
    if slot_id not in TABLE_SLOT_SIGNALS:
        return 0
    flat = _flatten_cells(table)
    return sum(1 for kw in TABLE_SLOT_SIGNALS[slot_id] if kw in flat)


def _table_is_generic_non_award(table: Sequence[Sequence[str]]) -> bool:
    """Phase K.5: True if the table is a generic non-award/non-exclusion table.

    A table is generic when its content is dominated by maintenance
    schedule / frequency / safety-inspection / routine-task signals
    and does NOT have award-tier or exclusion signals. Such tables
    are suppressed entirely (no slot 9, no slot 10, no slot 14).
    """
    flat = _flatten_cells(table)
    if not flat:
        return False
    generic_hits = sum(1 for kw in GENERIC_TABLE_SIGNALS if kw in flat)
    slot9_hits = sum(1 for kw in TABLE_SLOT_SIGNALS[9] if kw in flat)
    slot10_hits = sum(1 for kw in TABLE_SLOT_SIGNALS[10] if kw in flat)
    return generic_hits >= 2 and slot9_hits == 0 and slot10_hits == 0


def _classify_table_by_content(
    table: Sequence[Sequence[str]],
) -> int:
    """Phase K.5/K.6: Signal-based classification of a table to a slot.

    Returns:
        9  - the table matches slot-9 (Exclusions) signals
        10 - the table matches slot-10 (Award/Payout/Tier) signals
        -1 - the table matches neither (should be suppressed)
        0  - the table matches both or neither clearly (ambiguous;
             caller should fall back to position-based routing)

    Decision logic:
    - Label-row tables (slot-1 schema) are returned as 0 — their
      content matches are accidental (keywords in brief description
      cells) and must NOT drive slot assignment.
    - Slot 9 wins if slot9_hits > slot10_hits AND slot9_hits >= 1.
    - Slot 10 wins if slot10_hits > slot9_hits AND slot10_hits >= 1.
    - If both have hits, the larger one wins.
    - If neither has hits AND generic hits >= 2, return -1.
    - Otherwise return 0 (ambiguous - let caller decide).
    """
    flat = _flatten_cells(table)
    if not flat:
        return 0
    # Phase K.6: label-row tables must not be content-classified by
    # their cell text (which contains incidental keyword matches
    # like "payout" in the brief description cell).
    if _looks_like_label_row_table(table):
        return 0
    slot9_hits = sum(1 for kw in TABLE_SLOT_SIGNALS[9] if kw in flat)
    slot10_hits = sum(1 for kw in TABLE_SLOT_SIGNALS[10] if kw in flat)
    generic_hits = sum(1 for kw in GENERIC_TABLE_SIGNALS if kw in flat)
    if slot9_hits > 0 and slot9_hits > slot10_hits:
        return 9
    if slot10_hits > 0 and slot10_hits > slot9_hits:
        return 10
    if slot9_hits > 0 and slot9_hits == slot10_hits:
        return 0
    if generic_hits >= 2:
        return -1
    return 0


def find_table_for_slot(
    slot_id: int,
    tables: List[List[List[str]]],
    claimed_tables: Optional[set[int]] = None,
) -> Optional[List[List[str]]]:
    """Return the first non-label-row table whose content matches the slot's signals.

    This is the FALLBACK routing when no document position context
    is available. The caller (retrieval_pipeline.run) should use
    `find_table_for_slot_with_context()` first; this function is
    only used when context is unavailable.
    """
    if slot_id not in TABLE_SLOTS:
        return None
    if not tables:
        return None

    signals = TABLE_SLOT_SIGNALS.get(slot_id, [])
    if not signals:
        return None

    min_hits = SLOT_10_MIN_SIGNAL_HITS if slot_id == 10 else MIN_SIGNAL_HITS

    for table_idx, table in enumerate(tables):
        if claimed_tables and table_idx in claimed_tables:
            continue
        if not _table_shape_ok(table):
            continue
        if _looks_like_label_row_table(table):
            continue
        flat = _flatten_cells(table)
        hits = sum(1 for kw in signals if kw in flat)
        if hits >= min_hits:
            return [list(row) for row in table]
    return None


def find_table_for_slot_with_context(
    slot_id: int,
    tables: List[List[List[str]]],
    section_index: dict,
    paragraphs: List[str],
    table_paragraph_indices: Optional[List[int]] = None,
    claimed_tables: Optional[set[int]] = None,
) -> Optional[List[List[str]]]:
    """Route a table to a slot based on document position.

    PRIMARY table routing function. Uses section_index (paragraph_idx
    -> slot_id) to determine which section the table appears in.

    Phase K.6: content-signal classification can override position.
    Generic tables that aren't in slot-9 position are suppressed.
    Tables assigned to one slot don't leak to another via fallback.
    """
    if slot_id not in TABLE_SLOTS:
        return None
    if not tables:
        return None

    used_tables: set[int] = set()
    tables_assigned_to_other_slot: set[int] = set()

    for table_idx, table in enumerate(tables):
        if claimed_tables and table_idx in claimed_tables:
            tables_assigned_to_other_slot.add(table_idx)
            continue
        if table_idx in used_tables:
            continue
        if not _table_shape_ok(table):
            continue
        if _looks_like_label_row_table(table):
            continue
        table_slot = _find_table_section_slot(
            table_idx, tables, section_index, table_paragraph_indices, paragraphs
        )
        content_class = _classify_table_by_content(table)
        is_generic = _table_is_generic_non_award(table)
        if is_generic and table_slot != 9:
            tables_assigned_to_other_slot.add(table_idx)
            continue
        if content_class == slot_id:
            used_tables.add(table_idx)
            if claimed_tables is not None:
                claimed_tables.add(table_idx)
            return [list(row) for row in table]
        if content_class > 0 and content_class != slot_id:
            tables_assigned_to_other_slot.add(table_idx)
            continue
        if content_class == -1:
            tables_assigned_to_other_slot.add(table_idx)
            continue
        if table_slot == slot_id:
            used_tables.add(table_idx)
            if claimed_tables is not None:
                claimed_tables.add(table_idx)
            return [list(row) for row in table]
        if table_slot is not None and table_slot != slot_id:
            tables_assigned_to_other_slot.add(table_idx)
            if claimed_tables is not None:
                claimed_tables.add(table_idx)
            continue

    # Second pass: content-signal fallback.
    best_table_idx = -1
    best_score = 0
    min_hits = SLOT_10_MIN_SIGNAL_HITS if slot_id == 10 else MIN_SIGNAL_HITS
    for table_idx, table in enumerate(tables):
        if claimed_tables and table_idx in claimed_tables:
            continue
        if table_idx in used_tables:
            continue
        if table_idx in tables_assigned_to_other_slot:
            continue
        if not _table_shape_ok(table):
            continue
        if _looks_like_label_row_table(table):
            continue
        if _table_is_generic_non_award(table) and slot_id != 9:
            continue
        score = _table_score_for_slot(table, slot_id)
        if score > best_score and score >= min_hits:
            best_score = score
            best_table_idx = table_idx

    if best_table_idx >= 0:
        used_tables.add(best_table_idx)
        if claimed_tables is not None:
            claimed_tables.add(best_table_idx)
        return [list(row) for row in tables[best_table_idx]]
    return None


def find_all_tables_for_slot_with_context(
    slot_id: int,
    tables: List[List[List[str]]],
    section_index: dict,
    paragraphs: List[str],
    table_paragraph_indices: Optional[List[int]] = None,
    claimed_tables: Optional[set[int]] = None,
) -> List[List[List[str]]]:
    """Route ALL tables that belong to a slot.

    Unlike `find_table_for_slot_with_context` which returns ONE table
    per slot, this returns ALL tables whose position-context indicates
    they belong to the slot.

    Phase K.6: tables assigned to a different slot in the main loop
    are tracked so the content-signal fallback doesn't pick them up
    for the wrong slot. Label-row tables are filtered. Generic
    tables are suppressed unless they're in slot 9's section.

    Phase K.6 position-fallback: when content_class == -1 (no slot
    signals match) but the table IS positioned in the slot we're
    routing, allow the position-based routing to claim it. This
    handles the Hospital PDF case where the Level/Facility Type
    table sits in slot 9's section but has no slot-9/slot-10
    keywords.
    """
    if slot_id not in TABLE_SLOTS:
        return []
    if not tables:
        return []

    matched: List[List[List[str]]] = []
    tables_assigned_to_other_slot: set[int] = set()

    for table_idx, table in enumerate(tables):
        if claimed_tables and table_idx in claimed_tables:
            continue
        if not _table_shape_ok(table):
            continue
        if _looks_like_label_row_table(table):
            continue
        table_slot = _find_table_section_slot(
            table_idx, tables, section_index, table_paragraph_indices, paragraphs
        )
        content_class = _classify_table_by_content(table)
        is_generic = _table_is_generic_non_award(table)
        if is_generic and table_slot != 9:
            tables_assigned_to_other_slot.add(table_idx)
            continue
        if content_class == slot_id:
            if claimed_tables is not None:
                claimed_tables.add(table_idx)
            matched.append([list(row) for row in table])
            continue
        if content_class > 0 and content_class != slot_id:
            tables_assigned_to_other_slot.add(table_idx)
            continue
        # Phase K.6: position fallback when content_class == -1.
        if content_class == -1:
            if table_slot == slot_id:
                if claimed_tables is not None:
                    claimed_tables.add(table_idx)
                matched.append([list(row) for row in table])
            continue
        if table_slot == slot_id:
            if claimed_tables is not None:
                claimed_tables.add(table_idx)
            matched.append([list(row) for row in table])

    if matched:
        return matched

    # Fallback: content-signal matching for tables without position
    # context. Phase K.6: skip tables already assigned to other slots.
    min_hits = SLOT_10_MIN_SIGNAL_HITS if slot_id == 10 else MIN_SIGNAL_HITS
    for table_idx, table in enumerate(tables):
        if claimed_tables and table_idx in claimed_tables:
            continue
        if _looks_like_label_row_table(table):
            continue
        if _table_is_generic_non_award(table) and slot_id != 9:
            continue
        if table_idx in tables_assigned_to_other_slot:
            continue
        score = _table_score_for_slot(table, slot_id)
        if score >= min_hits:
            if claimed_tables is not None:
                claimed_tables.add(table_idx)
            matched.append([list(row) for row in table])

    return matched


def _find_table_section_slot(
    table_idx: int,
    tables: List[List[List[str]]],
    section_index: dict,
    table_paragraph_indices: Optional[List[int]] = None,
    paragraphs: Optional[List[str]] = None,
) -> Optional[int]:
    """Determine which slot a table belongs to based on document position.

    Uses `table_paragraph_indices` from ExtractedDocument if available:
    this tells us the paragraph index AFTER which the table appeared.
    We then look up the slot of that paragraph in section_index.

    Falls back to even-distribution approximation if the mapping
    is not available.
    """
    if not section_index:
        return None

    if (
        table_paragraph_indices
        and table_idx < len(table_paragraph_indices)
        and table_paragraph_indices[table_idx] is not None
    ):
        # Phase K.6: try walking back from target_pi FIRST. If the
        # paragraph at target_pi itself is a section heading, the
        # table belongs to that section. This fixes the case where
        # the table appears directly after a heading paragraph (the
        # most common pattern in the test suite).
        target_pi = table_paragraph_indices[table_idx]
        if target_pi in section_index:
            return section_index[target_pi]
        # Otherwise walk back further to find the nearest preceding
        # section heading. This handles cases where target_pi is
        # past the last paragraph or doesn't directly map to a
        # heading.
        candidate_slot = None
        for pi in range(target_pi - 1, -1, -1):
            if pi in section_index:
                candidate_slot = section_index[pi]
                break
        if candidate_slot is None and paragraphs is not None:
            for pi in range(target_pi + 1, len(paragraphs)):
                if pi in section_index:
                    candidate_slot = section_index[pi]
                    break
        return candidate_slot

    max_pi = max(section_index.keys()) if section_index else -1
    if max_pi < 0:
        return None
    n_tables = len(tables)
    n_paras = max_pi + 1
    if n_tables == 0:
        return None
    approx_pos = (table_idx * n_paras) // n_tables
    nearest_slot = None
    nearest_dist = float("inf")
    for pi, slot in section_index.items():
        if pi <= approx_pos:
            dist = approx_pos - pi
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_slot = slot
    if nearest_slot is None:
        for pi, slot in section_index.items():
            if pi > approx_pos:
                dist = pi - approx_pos
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_slot = slot
    return nearest_slot