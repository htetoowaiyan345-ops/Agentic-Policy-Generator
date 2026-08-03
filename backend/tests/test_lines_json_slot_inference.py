"""test_lines_json_slot_inference.py

Automated tests for Stage 4.15 - publish-time slot inference.

Validates that when the reviewer's saved `lines_json` has `slot=0`
for every paragraph (the legacy free-typing path), the
`infer_anchor_slots` + `preserve_editor_anchor_slot` helpers route the
content into the correct Brain framework slots so the published .docx
contains the reviewer's actual Result instead of the brain template
scaffold.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures: load the lines_json_extractor without triggering
# policy_platform.__init__.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def extractor_mod(backend_root):
    """Load `api.lines_json_extractor` directly.

    This module does NOT touch the broken `policy_platform.__init__.py`,
    so we can import it normally.
    """
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from api.lines_json_extractor import (
        infer_anchor_slots,
        preserve_editor_anchor_slot,
        normalise_lines_json,
    )
    return {
        "infer_anchor_slots": infer_anchor_slots,
        "preserve_editor_anchor_slot": preserve_editor_anchor_slot,
        "normalise_lines_json": normalise_lines_json,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_infer_metadata_field_slots(extractor_mod):
    """Slot-1 metadata fields are routed to slot 1."""
    lines_json = [
        ["p", {"slot": 0, "text": "Type: HR Policy"}],
        ["p", {"slot": 0, "text": "Policy Title: AWARD POLICY"}],
        ["p", {"slot": 0, "text": "Policy Number: HR-001"}],
        ["p", {"slot": 0, "text": "Applicable Sector(s): Corporate"}],
        ["p", {"slot": 0, "text": "Functional Area(s): HR"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    slots = [p[1].get("slot") for p in out]
    assert slots == [1, 1, 1, 1, 1], f"expected all slot 1, got {slots}"


def test_infer_slot_2_brief_description(extractor_mod):
    """Brief Description metadata is routed to slot 2."""
    lines_json = [
        ["p", {"slot": 0, "text": "Brief Description: This policy governs..."}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 2


def test_infer_slot_3_governance_fields(extractor_mod):
    """Slot-3 governance metadata fields are routed to slot 3."""
    fields = [
        "Effective Date/Period: 01 July 2026",
        "Approved by: Htet Oo",
        "Prepared by: Htet Oo Wai Yan",
        "Responsible Function(s): HR",
        "Supersedes: Version 0.9",
        "Last Reviewed: 05 July 2026",
        "Applies to: All employees",
    ]
    lines_json = [["p", {"slot": 0, "text": t}] for t in fields]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    slots = [p[1].get("slot") for p in out]
    assert slots == [3] * len(fields), f"expected all slot 3, got {slots}"


def test_infer_slot_4_reason_for_policy(extractor_mod):
    """Reason for Policy is routed to slot 4."""
    lines_json = [
        ["p", {"slot": 0, "text": "Reason for Policy: To establish..."}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 4


def test_infer_section_headings(extractor_mod):
    """Section headings are routed to their respective slots."""
    cases = [
        ("INTRODUCTION", 5),
        ("POLICY STATEMENT", 6),
        ("1. Purpose", 7),
        ("2. Scope & Beneficiaries", 8),
        ("3. Exclusions", 9),
        ("4. Award Structure & Payout Tiers", 10),
        ("Policy Review Note: Reviewed annually.", 11),
        ("DEFINITIONS", 12),
        ("RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES", 13),
        ("HISTORY", 14),
    ]
    for text, expected_slot in cases:
        lines_json = [["p", {"slot": 0, "text": text}]]
        out = extractor_mod["infer_anchor_slots"](lines_json)
        assert out[0][1]["slot"] == expected_slot, (
            f"{text!r} should map to slot {expected_slot}, got {out[0][1].get('slot')}"
        )


def test_section_body_inherits_slot(extractor_mod):
    """Body paragraphs after a section heading inherit that section's slot."""
    lines_json = [
        ["p", {"slot": 0, "text": "INTRODUCTION"}],
        ["p", {"slot": 0, "text": "This policy supports employee engagement."}],
        ["p", {"slot": 0, "text": "More content under intro."}],
        ["p", {"slot": 0, "text": "POLICY STATEMENT"}],
        ["p", {"slot": 0, "text": "Clear guidelines for nominations."}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    slots = [p[1].get("slot") for p in out]
    assert slots == [5, 5, 5, 6, 6], f"expected [5,5,5,6,6], got {slots}"


def test_existing_non_zero_slot_preserved(extractor_mod):
    """Paragraphs with explicit non-zero slots are not rewritten."""
    lines_json = [
        ["p", {"slot": 7, "text": "explicit purpose"}],
        ["p", {"slot": 0, "text": "INTRODUCTION"}],
        ["p", {"slot": 0, "text": "Inherits slot 5 (intro), not slot 7."}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    slots = [p[1].get("slot") for p in out]
    assert slots == [7, 5, 5], f"expected [7,5,5], got {slots}"


def test_full_user_scenario(extractor_mod):
    """End-to-end: the user's actual published-file content is routed to
    the correct slots when slot inference runs at publish-time."""
    lines_json = [
        ["p", {"slot": 0, "text": "Type: HR Policy"}],
        ["p", {"slot": 0, "text": "Policy Title: POLICY TEMPLATE - AWARD AND RECOGNITION"}],
        ["p", {"slot": 0, "text": "Policy Number: HR-ARP-001"}],
        ["p", {"slot": 0, "text": "Applicable Sector(s): Corporate Services & Operations"}],
        ["p", {"slot": 0, "text": "Functional Area(s): Human Resources"}],
        ["p", {"slot": 0, "text": "Brief Description: Framework governing employee awards..."}],
        ["p", {"slot": 0, "text": "Effective Date/Period: 01 July 2026 - 30 June 2027"}],
        ["p", {"slot": 0, "text": "Approved by: Htet Oo"}],
        ["p", {"slot": 0, "text": "Prepared by: Htet Oo Wai Yan"}],
        ["p", {"slot": 0, "text": "Reason for Policy: To establish a fair..."}],
        ["p", {"slot": 0, "text": "INTRODUCTION"}],
        ["p", {"slot": 0, "text": "This policy supports employee engagement."}],
        ["p", {"slot": 0, "text": "POLICY STATEMENT"}],
        ["p", {"slot": 0, "text": "Provide clear guidelines for nominations."}],
        ["p", {"slot": 0, "text": "1. Purpose"}],
        ["p", {"slot": 0, "text": "To establish a fair, transparent framework."}],
        ["p", {"slot": 0, "text": "DEFINITIONS"}],
        ["p", {"slot": 0, "text": "Award: formal recognition. Payout: monetary reward."}],
        ["p", {"slot": 0, "text": "RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES"}],
        ["p", {"slot": 0, "text": "Performance Management Policy; Code of Conduct."}],
        ["p", {"slot": 0, "text": "HISTORY"}],
        ["p", {"slot": 0, "text": "Version 1.0"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    # Build {slot: [texts]} map.
    bucket: dict = {}
    for _kind, p in out:
        slot = p.get("slot", 0)
        bucket.setdefault(slot, []).append(p.get("text", ""))

    # Slot 1 must contain the header metadata.
    assert "Type: HR Policy" in bucket.get(1, []), "Type missing from slot 1"
    assert "Policy Title: POLICY TEMPLATE - AWARD AND RECOGNITION" in bucket.get(1, [])

    # Slot 2 = Brief Description.
    assert any("Brief Description" in t for t in bucket.get(2, []))

    # Slot 3 = governance fields.
    assert any("Effective Date" in t for t in bucket.get(3, []))
    assert any("Approved by" in t for t in bucket.get(3, []))

    # Slot 4 = Reason for Policy.
    assert any("Reason for Policy" in t for t in bucket.get(4, []))

    # Slots 5-14 = section content.
    assert any("INTRODUCTION" in t for t in bucket.get(5, []))
    assert any("This policy supports" in t for t in bucket.get(5, []))
    assert any("POLICY STATEMENT" in t for t in bucket.get(6, []))
    assert any("1. Purpose" in t for t in bucket.get(7, []))
    assert any("DEFINITIONS" in t for t in bucket.get(12, []))
    assert any("HISTORY" in t for t in bucket.get(14, []))


def test_preserve_editor_anchor_slot(extractor_mod):
    """`anchor_slot` field is propagated to `slot` when `slot == 0`."""
    lines_json = [
        ["p", {"slot": 0, "anchor_slot": 7, "text": "purpose content"}],
        ["p", {"slot": 0, "anchor_slot": 0, "text": "free paragraph"}],
    ]
    out = extractor_mod["preserve_editor_anchor_slot"](lines_json)
    assert out[0][1]["slot"] == 7
    assert out[1][1]["slot"] == 0


def test_preserve_does_not_overwrite_explicit_slot(extractor_mod):
    """If `slot != 0`, `anchor_slot` is ignored (explicit slot wins)."""
    lines_json = [
        ["p", {"slot": 5, "anchor_slot": 7, "text": "explicit slot 5 wins"}],
    ]
    out = extractor_mod["preserve_editor_anchor_slot"](lines_json)
    assert out[0][1]["slot"] == 5


def test_table_slot_inference(extractor_mod):
    """Tables also get slot routing by inheritance."""
    lines_json = [
        ["p", {"slot": 0, "text": "HISTORY"}],
        ["p", {"slot": 0, "text": "Version 1.0"}],
        ["t", {"slot": 0, "rows": [["1.0", "2026-01-01", "Alice"]]}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    # Last paragraph + table both inherit slot 14.
    assert out[1][1]["slot"] == 14
    assert out[2][1]["slot"] == 14


def test_no_mutation_of_input(extractor_mod):
    """infer_anchor_slots returns a new list; the input is untouched."""
    original = [["p", {"slot": 0, "text": "Type: HR"}]]
    out = extractor_mod["infer_anchor_slots"](original)
    assert original[0][1]["slot"] == 0, "input was mutated"
    assert out[0][1]["slot"] == 1
    assert out is not original


def test_empty_text_inherits_last_slot(extractor_mod):
    """Empty paragraphs inherit the preceding slot so they land in the
    right scaffold body region (not the free paragraph zone)."""
    lines_json = [
["p", {"slot": 0, "text": "1. Purpose"}],
        ["p", {"slot": 0, "text": "To establish a fair framework."}],
        ["p", {"slot": 0, "text": ""}],
        ["p", {"slot": 0, "text": "2. Scope & Beneficiaries"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    slots = [p[1].get("slot") for p in out]
    assert slots == [7, 7, 7, 8], f"empty para should inherit slot 7, got {slots}"


def test_heading_with_value_inferred_to_correct_slot(extractor_mod):
    """When the reviewer types the section heading AND value on one line
    (e.g. 'HISTORY Htet Oo Wai Yan'), the heading label is the first word
    and must still match the section prefix. Otherwise the paragraph
    falls into slot=0 (free paragraph zone) and the published .docx
    renders it at the TOP of the body instead of inside the HISTORY
    section — and the brain's scaffold bullets + scaffold table bleed
    through unchanged."""
    lines_json = [
        ["p", {"slot": 0, "text": "HISTORY Htet Oo Wai Yan"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 14, (
        f"HISTORY prefix should infer slot 14, got {out[0][1].get('slot')}"
    )


def test_introduction_with_value_inferred(extractor_mod):
    """Same pattern for INTRODUCTION / DEFINITIONS / etc."""
    lines_json = [
        ["p", {"slot": 0, "text": "INTRODUCTION framework text"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 5


def test_heading_prefix_requires_space(extractor_mod):
    """The prefix matcher requires a trailing space so 'HISTORICAL' (or
    similar) is NOT mis-routed to slot 14."""
    lines_json = [
        ["p", {"slot": 0, "text": "HISTORICAL OVERVIEW"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 0, (
        f"HISTORICAL should NOT match HISTORY prefix, "
        f"got {out[0][1].get('slot')}"
    )


def test_history_table_inferred_to_slot_14(extractor_mod):
    """A table with HISTORY signals (DATE / VERSION / DESCRIPTION OF
    CHANGE / AUTHOR columns) and slot=0 should be re-inferred to slot 14
    via content-signal classification."""
    lines_json = [
        ["t", {"slot": 0, "rows": [
            ["DATE", "VERSION", "DESCRIPTION OF CHANGE", "AUTHOR / REVIEWER"],
            ["05 July 2026", "1.0", "Initial Release", "Htet Oo Wai Yan"],
        ]}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 14, (
        f"HISTORY content table should infer slot 14, "
        f"got {out[0][1].get('slot')}"
    )


def test_award_table_inferred_to_slot_10(extractor_mod):
    """A table with Award Tier signals (TIER / PAYOUT / CRITERIA / etc.)
    and slot=0 should be re-inferred to slot 10."""
    lines_json = [
        ["t", {"slot": 0, "rows": [
            ["AWARD LEVEL", "TIER", "PAYOUT", "CRITERIA"],
            ["Tier 1", "1", "USD 100", "Immediate recognition"],
        ]}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 10, (
        f"Award tier table should infer slot 10, "
        f"got {out[0][1].get('slot')}"
    )


def test_free_paragraph_stays_slot_0(extractor_mod):
    """Random free text with no slot signals stays at slot 0 (free zone)."""
    lines_json = [
        ["p", {"slot": 0, "text": "Random note about anything"}],
    ]
    out = extractor_mod["infer_anchor_slots"](lines_json)
    assert out[0][1]["slot"] == 0, (
        f"Random text should NOT be inferred to a slot, "
        f"got {out[0][1].get('slot')}"
    )
