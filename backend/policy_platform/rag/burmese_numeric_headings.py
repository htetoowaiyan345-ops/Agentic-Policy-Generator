"""Myanmar numeric section mapper.

Generic module that detects Myanmar numbered sections (၁။, ၂။, ၃။, ၄။)
and maps them to Brain slot IDs. Works for any Myanmar policy PDF that uses
Myanmar numeral section markers.

Section-to-slot mapping:
  ၁ (1) → Slot 7 (Purpose)
  ၂ (2) → Slot 8 (Scope & Beneficiaries)
  ၃ (3) → Slot 9 (Exclusions / Benefits combined)
  ၄ (4) → Slot 10 (Award Structure & Payout Tiers)
  ၅ (5) → Slot 12 (Definitions) — if present
  ၆ (6) → Slot 13 (Related Policies) — if present
  ၇ (7) → Slot 14 (History) — if present

Generic: no document-specific strings, no hardcoded links, no defaults.
"""
from __future__ import annotations

import re

# Myanmar digit range U+1040–U+1049
_MM_DIGIT = r"[\u1040-\u1049]"

# Top-level section: Myanmar digit(s) + period/hash, NOT a sub-section.
# Examples: "၁။", "၂#", "၃။ " — but NOT "၁-၁။" (sub-section).
# Negative lookahead (?!\-) prevents matching sub-sections like ၁-၁။.
_TOP_LEVEL_RE = re.compile(
    rf"^\s*({_MM_DIGIT}+)(?!\-)[\u104B.#]\s*"
)

# Sub-section: Myanmar digit + hyphen + Myanmar digit(s) + period/hash
# Examples: "၁-၁။", "၂-၃#", "၁-၁။ "
_SUB_SECTION_RE = re.compile(
    rf"^\s*({_MM_DIGIT})-({_MM_DIGIT}+)[\u104B.#]\s*"
)

# Mapping: Myanmar digit value → Brain slot ID
# Generic: first section → first content slot, etc.
# Based on standard Myanmar policy structure.
_MM_TO_SLOT: dict[int, int] = {
    1: 7,   # Purpose
    2: 8,   # Scope & Beneficiaries
    3: 9,   # Exclusions (combined with Benefits per user decision)
    4: 10,  # Award Structure & Payout Tiers
    5: 12,  # Definitions
    6: 13,  # Related Policies
    7: 14,  # History
}


def _mm_digit_value(ch: str) -> int:
    """Convert a single Myanmar digit character to its integer value.

    Myanmar digits are U+1040 (၀=0) through U+1049 (၉=9).
    """
    return ord(ch) - 0x1040


def _has_myanmar_digits(text: str) -> bool:
    """True if text contains any Myanmar digit."""
    return bool(re.search(_MM_DIGIT, text))


def build_numeric_section_index(
    paragraphs: list[str],
) -> dict[int, int]:
    """Build a mapping from paragraph index to slot ID using Myanmar
    numeric section markers.

    Returns a dict where keys are paragraph indices and values are
    Brain slot IDs. Only paragraphs within a detected section are mapped.
    Paragraphs before any section marker are unmapped.

    The mapper detects:
      - Top-level sections: `၁။`, `၂#`, `၃။` etc.
      - Sub-sections: `၁-၁။`, `၂-၃#` etc. (inherit parent slot)

    Generic: works for any Myanmar policy using numbered sections.
    """
    index: dict[int, int] = {}
    current_slot: int | None = None

    for i, p in enumerate(paragraphs):
        if not p or not p.strip():
            continue
        first_line = p.split("\n")[0].strip() if "\n" in p else p.strip()

        # Check for top-level section
        m_top = _TOP_LEVEL_RE.match(first_line)
        if m_top:
            digit_str = m_top.group(1)
            # Use the LAST digit for multi-digit numbers (rare but possible)
            last_digit = _mm_digit_value(digit_str[-1])
            current_slot = _MM_TO_SLOT.get(last_digit)
            if current_slot is not None:
                index[i] = current_slot
            continue

        # Check for sub-section (inherits parent slot)
        m_sub = _SUB_SECTION_RE.match(first_line)
        if m_sub and current_slot is not None:
            index[i] = current_slot
            continue

        # Body paragraph inherits current slot
        if current_slot is not None:
            index[i] = current_slot

    return index


def has_numeric_sections(paragraphs: list[str]) -> bool:
    """True if the document contains Myanmar numbered section markers.

    Generic detection: checks for any Myanmar digit + period/hash pattern.
    """
    for p in paragraphs[:100]:  # Sample first 100 paragraphs
        if not p:
            continue
        first_line = p.split("\n")[0].strip() if "\n" in p else p.strip()
        if _TOP_LEVEL_RE.match(first_line):
            return True
    return False


def get_slot_for_paragraph(
    para_idx: int,
    numeric_index: dict[int, int],
) -> int | None:
    """Look up the slot ID for a paragraph index from the numeric index."""
    return numeric_index.get(para_idx)
