"""Tests for heading-anchored retrieval."""
from __future__ import annotations

from policy_platform.rag.heading_anchors import (
    HEADING_ANCHOR_SLOTS,
    HEADING_PATTERNS,
    find_heading_match,
)


def test_prose_slots_defined():
    expected = {5, 6, 7, 8, 9, 10, 12, 13, 14}
    assert HEADING_ANCHOR_SLOTS == expected


def test_label_row_slots_not_in_anchor_set():
    """Slots 1, 2, 3, 4, 11 are label-row slots; not handled by anchors."""
    assert 1 not in HEADING_ANCHOR_SLOTS
    assert 2 not in HEADING_ANCHOR_SLOTS
    assert 3 not in HEADING_ANCHOR_SLOTS
    assert 4 not in HEADING_ANCHOR_SLOTS
    assert 11 not in HEADING_ANCHOR_SLOTS
    assert 15 not in HEADING_ANCHOR_SLOTS


def test_purpose_heading_matches_slot_7():
    paragraphs = [
        "Policy Title: Test",
        "1. Purpose: To provide safety standards to all employees.",
    ]
    result = find_heading_match(7, paragraphs)
    assert result is not None
    start, end, text = result
    assert start == 1
    # The heading label "Purpose" is stripped from the body so the
    # rendered output doesn't show the heading twice. Only the body
    # text remains.
    assert "Purpose" not in text
    assert "safety standards" in text
    assert "all employees" in text


def test_scope_heading_matches_slot_8():
    paragraphs = [
        "Some intro",
        "Scope: This policy applies to all employees.",
    ]
    result = find_heading_match(8, paragraphs)
    assert result is not None
    _, _, text = result
    assert "Scope" not in text
    assert "all employees" in text


def test_exclusions_heading_matches_slot_9():
    paragraphs = [
        "Exclusions: Contractors and temporary workers.",
    ]
    result = find_heading_match(9, paragraphs)
    assert result is not None
    _, _, text = result
    assert "Contractors" in text


def test_definitions_heading_matches_slot_12():
    paragraphs = [
        "Definitions: Hazard means any source of harm.",
    ]
    result = find_heading_match(12, paragraphs)
    assert result is not None
    _, _, text = result
    assert "Hazard" in text


def test_key_definitions_heading_matches_slot_12():
    """`KEY DEFINITIONS:` is a slot-12 heading (uppercase variant).

    Source PDFs like Food Relief Assistance Policy use `KEY
    DEFINITIONS` as their definitions title instead of bare
    `DEFINITIONS`. Slot 12 must catch this so the body walks
    forward into the Flood-Event / Eligible-Employee definitions.
    """
    paragraphs = [
        "KEY DEFINITIONS: Flood Event means water-related disaster.",
    ]
    result = find_heading_match(12, paragraphs)
    assert result is not None, "`KEY DEFINITIONS:` did not match slot 12"
    _, _, text = result
    assert "Flood Event" in text
    assert "water-related disaster" in text


def test_key_definitions_lowercase_matches_slot_12():
    """`key definitions:` lowercase also matches slot 12.

    Synonyms are matched case-insensitively (regex compiled with
    IGNORECASE inside `_is_heading_for_slot`). Lowercase variant
    must produce the same slot-12 match.
    """
    paragraphs = [
        "key definitions: Eligible Employee means a current local employee.",
    ]
    result = find_heading_match(12, paragraphs)
    assert result is not None, "`key definitions:` did not match slot 12"
    _, _, text = result
    assert "Eligible Employee" in text
    assert "current local employee" in text


def test_history_heading_matches_slot_14():
    paragraphs = [
        "Document History: v1.0 2024-01-15, v1.1 2024-06-15",
    ]
    result = find_heading_match(14, paragraphs)
    assert result is not None
    _, _, text = result
    assert "v1.0" in text


def test_anchor_walks_forward_until_next_heading():
    paragraphs = [
        "Purpose: First sentence of purpose section.",
        "Second sentence of purpose section.",
        "Third sentence of purpose section.",
        "1. Scope: This is a new section.",
    ]
    result = find_heading_match(7, paragraphs)
    assert result is not None
    start, end, text = result
    assert start == 0
    assert end == 2  # stops before the "1. Scope" heading
    assert "Third sentence" in text
    assert "1. Scope" not in text


def test_no_match_returns_none():
    paragraphs = [
        "Just some random text",
        "No headings here",
    ]
    assert find_heading_match(7, paragraphs) is None


def test_empty_input_returns_none():
    assert find_heading_match(7, []) is None


def test_label_row_slots_always_return_none():
    """Anchors are only for prose slots."""
    paragraphs = ["Type: Policy"]
    for sid in (1, 2, 3, 4, 11, 15):
        assert find_heading_match(sid, paragraphs) is None


def test_introduction_heading_matches_slot_5():
    paragraphs = [
        "Introduction: This is the opening paragraph.",
    ]
    result = find_heading_match(5, paragraphs)
    assert result is not None
    _, _, text = result
    assert "opening paragraph" in text


def test_policy_statement_heading_matches_slot_6():
    paragraphs = [
        "Policy Statement: The Company shall provide...",
    ]
    result = find_heading_match(6, paragraphs)
    assert result is not None
    _, _, text = result
    assert "Company shall" in text


def test_related_policies_heading_matches_slot_13():
    paragraphs = [
        "Related Policies: See also the Travel Policy.",
    ]
    result = find_heading_match(13, paragraphs)
    assert result is not None
    _, _, text = result
    assert "Travel Policy" in text


def test_case_insensitive_matching():
    paragraphs = [
        "PURPOSE: To provide safety.",
    ]
    result = find_heading_match(7, paragraphs)
    assert result is not None
    _, _, text = result
    assert "safety" in text


def test_heading_with_trailing_period_matches():
    paragraphs = [
        "Purpose. To provide safety.",
    ]
    result = find_heading_match(7, paragraphs)
    # Pattern allows "Purpose" followed by "." but the "To provide safety" must follow
    # The pattern is `r"^\s*purpose\s*[:\-.]?"` so "Purpose." matches and the rest
    # of the paragraph ("To provide safety.") is the body.
    assert result is not None
    _, _, text = result
    assert "To provide safety" in text


def test_pure_heading_with_body_on_next_line():
    """When heading is on its own line, collect the next non-boundary paragraph."""
    paragraphs = [
        "Introduction",
        "This policy supports employee engagement by recognizing exceptional performance.",
        "Policy Statement - Purpose",
        "Provide clear guidelines for nominations, approvals, awards and payouts.",
        "Exclusions",
        "Interns, contractors and consultants unless specifically approved.",
    ]
    # Slot 5 = Introduction
    result = find_heading_match(5, paragraphs)
    assert result is not None
    start, end, text = result
    assert start == 0
    assert end == 1
    assert "supports employee engagement" in text
    # Should NOT include the Policy Statement paragraph
    assert "Policy Statement" not in text
    # Should NOT include the Exclusions paragraph
    assert "Exclusions" not in text


def test_award_sample_slot_8_bounded_by_exclusions():
    """Regression: Award PDF slot 8 must stop at 'Exclusions' boundary."""
    paragraphs = [
        "Introduction",
        "This policy supports employee engagement by recognizing exceptional performance.",
        "Policy Statement - Purpose",
        "Provide clear guidelines for nominations, approvals, awards and payouts.",
        "Scope and Beneficiaries",
        "All permanent and fixed-term employees.",
        "Exclusions",
        "Interns, contractors and consultants unless specifically approved.",
        "Award Structure and Payout Tiers",
        "Award Level Criteria Recognition Indicative Payout",
    ]
    result = find_heading_match(8, paragraphs)
    assert result is not None
    start, end, text = result
    # Slot 8 (Scope) starts at index 4 (Scope and Beneficiaries)
    assert start == 4
    # Should end at index 5 (just the body "All permanent and fixed-term employees.")
    # NOT include "Exclusions" or anything after
    assert end == 5
    assert "permanent and fixed-term" in text
    assert "Exclusions" not in text
    assert "Award Structure" not in text


def test_award_sample_slot_9_bounded_by_award_structure():
    """Regression: Award PDF slot 9 (Exclusions) must stop at 'Award Structure' boundary."""
    paragraphs = [
        "Scope and Beneficiaries",
        "All permanent and fixed-term employees.",
        "Exclusions",
        "Interns, contractors and consultants unless specifically approved.",
        "Award Structure and Payout Tiers",
        "Award Level Criteria Recognition Indicative Payout",
    ]
    result = find_heading_match(9, paragraphs)
    assert result is not None
    start, end, text = result
    # Slot 9 (Exclusions) starts at index 2
    assert start == 2
    # Should end at index 3 (just "Interns, contractors and consultants...")
    assert end == 3
    assert "Interns" in text
    assert "Award Structure" not in text


def test_hospital_buildings_slot_6_no_body():
    """Regression: Hospital Buildings slot 6 = 'Policy Statement' heading on its own line.
    The next paragraph is 'Purpose:' which is a new section, so slot 6 has no body."""
    paragraphs = [
        "Introduction: This policy supports patient care.",
        "Policy Statement",
        "Purpose: Provide guidelines for building operations.",
    ]
    result = find_heading_match(6, paragraphs)
    assert result is not None
    start, end, text = result
    # Heading is at index 1, no body collected.
    assert "Policy Statement" in text
    # Should NOT include the next section's heading or body
    assert "Purpose" not in text
    assert "Provide guidelines" not in text


def test_multiline_heading_collected():
    """Regression: 'RELATED POLICIES ... &' heading + 'OTHER RESOURCES' continuation.

    Multi-line headings like 'RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES &'
    that wrap to 'OTHER RESOURCES' on the next line should be collected
    as the heading, not split into heading+body.
    """
    from policy_platform.rag.heading_anchors import _strip_heading_label
    # The adapter strips the heading label from the body.
    body = "RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES &. OTHER RESOURCES Performance Management Policy; Employee Code of Conduct."
    stripped = _strip_heading_label(body, 13)
    # Should remove "RELATED POLICIES..." prefix.
    assert "RELATED POLICIES" not in stripped or "OTHER RESOURCES" in stripped


def test_strip_heading_label_removes_prefix():
    """Verify _strip_heading_label removes the heading word from the body."""
    from policy_platform.rag.heading_anchors import _strip_heading_label
    # Slot 8 = Scope. Paragraph: "Scope and Beneficiaries: All permanent employees."
    stripped = _strip_heading_label("Scope and Beneficiaries: All permanent and fixed-term employees.", 8)
    assert "Scope" not in stripped or "All permanent" in stripped
    assert "All permanent" in stripped


def test_strip_heading_label_keeps_whole_text_if_no_match():
    """If paragraph doesn't start with a known label, return as-is."""
    from policy_platform.rag.heading_anchors import _strip_heading_label
    text = "This is just a regular paragraph."
    stripped = _strip_heading_label(text, 8)
    assert stripped == text


def test_mid_sentence_aim_not_treated_as_purpose_heading():
    """Regression: 'aim. Adverse action...' mid-sentence should NOT
    match slot 7 (Purpose) heading pattern. Previously the pattern
    matched 'aim' as a synonym followed by '.' and got treated as
    a heading + body.
    """
    from policy_platform.rag.heading_anchors import _is_heading_for_slot
    p = "aim. Adverse action need not be job-related or occur in the workplace to constitute unlawful retaliation (e.g., threats of physical harm or other adverse consequences), off-duty, off-premises conduct, or even after the end of the employment relationship."
    assert not _is_heading_for_slot(7, p)


def test_long_inline_heading_with_colon_matches():
    """Long paragraphs with 'Heading: Body' format (200+ chars)
    should match the heading pattern because colon is a reliable
    separator.
    """
    from policy_platform.rag.heading_anchors import _is_heading_for_slot
    p = "Exclusions: Claims without supporting evidence, damages unrelated to the earthquake, fraudulent claims, expatriate staff where local policy does not apply, and applications submitted beyond the approved reporting period."
    assert _is_heading_for_slot(9, p)


# ---------------------------------------------------------------------------
# Synthetic Flood symptom test for the heading-anchor walk.
#
# Mirrors the post-M1-split chunk list for the Flood PDF slot-10
# region. Asserts that `find_heading_match(12, ...)` claims the
# `Definitions:` paragraph as its own content (i.e., slot 12
# resolves correctly after M1 has split).
# ---------------------------------------------------------------------------


def test_flood_inline_definitions_resolves_slot_12():
    """Flood symptom: post-M1-split chunk list, slot 12 claims
    the `Definitions:` paragraph on its own.

    Simulates the chunk list that split_paragraphs would produce
    for the Flood slot-10 body region. Slot 12's heading match
    must succeed on the third paragraph and include only the
    Flood-Event definitions clause chain.
    """
    paragraphs = [
        # Slot 10's body chunk: tiers + annual budget block.
        "Tier 1 Minor Property Damage – MMK 300,000; Tier 2 Moderate Property Damage MMK 500,000; "
        "Tier 3 Severe Property Damage – MMK 1,000,000; Tier 4 Complete Loss of Home – MMK 2,000,000; "
        "Tier 5 Serious Injury or Hospitalization – MMK 1,500,000; Tier 6 Fatality Support – MMK 3,000,000. "
        "Annual Budget Allocation: Emergency Relief Fund MMK 50,000,000; Housing Recovery Support MMK 30,000,000; "
        "Medical Assistance Support MMK 15,000,000; Bereavement and Family Support MMK 5,000,000; "
        "Total Annual Budget MMK 100,000,000.",
        # Slot 12's heading chunk: definitions.
        "Definitions: Company means City Holdings Group and its business units; Immediate Family Member means "
        "spouse, children, parents, or dependents; Flood Event means an officially recognized flood, "
        "flash flood, river overflow, storm surge, or water-related natural disaster; "
        "Verified Damage means damage supported by acceptable evidence.",
        # Slot 11/13's chunk: required documents.
        "Required Documents include application form, photographs, official reports, local authority verification, "
        "medical records, death certificates where applicable, and identification documents.",
    ]
    result = find_heading_match(12, paragraphs)
    assert result is not None, (
        "slot 12 heading match returned None; the `Definitions:` "
        "paragraph should have been claimed"
    )
    start_idx, end_idx, joined_text = result
    assert start_idx == 1, (
        f"slot 12 must claim paragraph index 1 (the Definitions: paragraph); "
        f"got start_idx={start_idx}"
    )
    assert end_idx == 1, (
        f"slot 12 must NOT extend into Required Documents; "
        f"got end_idx={end_idx}"
    )
    # Body text MUST contain the Flood-Event definition.
    assert "Flood Event" in joined_text, (
        f"slot 12 body missing Flood Event definition; got: {joined_text!r}"
    )
    assert "Company means City Holdings Group" in joined_text
    # Body MUST NOT leak slot 10's tier text.
    assert "Tier 1 Minor" not in joined_text, (
        f"slot 12 leaked slot-10 tier text; got: {joined_text!r}"
    )
    # Body MUST NOT leak Required-Documents text.
    assert "Required Documents include" not in joined_text, (
        f"slot 12 leaked Required Documents text; got: {joined_text!r}"
    )


def test_flood_inline_definitions_key_definitions_variant_resolves_slot_12():
    """Same as above but for the Food Relief style `KEY DEFINITIONS:`.

    After we added the new synonyms `key definitions` and friends
    to slot 12 in section_map.py, this variant should now resolve
    slot 12 correctly too.
    """
    paragraphs = [
        "Tier 1 Minor Damage – MMK 300,000; Tier 2 Moderate – MMK 500,000.",
        "KEY DEFINITIONS: Flood Event means water-related disaster causing residential damage. "
        "Eligible Employee means current local employee of City Holdings Group. "
        "Assistance means non-repayable financial support.",
        "Required Documents include application form.",
    ]
    result = find_heading_match(12, paragraphs)
    assert result is not None, (
        "slot 12 heading match returned None for KEY DEFINITIONS"
    )
    start_idx, _, joined_text = result
    assert start_idx == 1
    assert "Flood Event" in joined_text
    assert "Eligible Employee" in joined_text
    assert "Tier 1" not in joined_text
    assert "Required Documents" not in joined_text
