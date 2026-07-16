"""Slot-specific retrieval queries for the 15-slot Brain Framework.

Each slot has a small set of natural-language queries that describe
*what kind of content belongs in that slot*. The retrieval pipeline
runs every query against the document index, merges results, and
picks the best-matching chunk per slot.

These queries are intentionally abstract enough to match many real
policy documents while still being discriminative between slots.
"""
from __future__ import annotations

from typing import Iterable

# 15-slot query dictionary.
# Order matches FROZEN_SECTIONS in framework.section_map.
#
# Query design principles:
# - Use the EXACT heading words the source documents use.
# - Include synonyms for variant styles ("policy statement" vs
#   "statement of policy").
# - Add context to disambiguate similar sections (e.g. "exclusions
#   and limitations" vs "scope").
SLOT_QUERIES: dict[int, list[str]] = {
    1: [
        "policy type and policy title and policy number",
        "applicable sector and functional area header",
    ],
    2: [
        "brief description of the policy purpose",
        "short summary overview of the policy",
    ],
    3: [
        "effective date approved by prepared by responsible function",
        "supersedes last reviewed applies to",
    ],
    4: [
        "reason for policy rationale background",
        "why this policy exists context",
    ],
    5: [
        "introduction to this policy opening paragraph",
        "introduction background preamble overview of the policy",
    ],
    6: [
        "policy statement formal statement of the policy",
        "company position statement of policy",
    ],
    7: [
        "purpose of this policy aim objective",
        "policy intent and what it aims to achieve",
    ],
    8: [
        "scope of this policy who it applies to",
        "scope and beneficiaries applicability",
    ],
    9: [
        "exclusions what is not covered by this policy",
        "exceptions and limitations to this policy",
    ],
    10: [
        "award tier structure payout amount recognition level",
        "award structure and payout tiers benefit level",
        # Flood / disaster relief policies.
        "assistance structure flood relief payout damage level",
        # School / facility / building policies.
        "facility level maintenance frequency priority",
    ],
    11: [
        "policy review note review frequency",
        "when this policy will be reviewed next",
    ],
    12: [
        "definitions of terms glossary defined terms",
        "what does a specific term mean in this policy",
    ],
    13: [
        "related policies associated documents references",
        "related procedures forms guidelines cross references",
        "see also other policies linked companion supplementary",
    ],
    14: [
        "document history version log revision history",
        "change record version history of this policy",
    ],
    15: [],  # logo slot; never matched from text
}


def get_queries_for_slot(slot_id: int) -> list[str]:
    """Return the retrieval queries for the given slot id (1-15)."""
    return list(SLOT_QUERIES.get(slot_id, []))


def all_slots() -> Iterable[int]:
    """Yield slot ids 1-15 in canonical order."""
    return SLOT_QUERIES.keys()
