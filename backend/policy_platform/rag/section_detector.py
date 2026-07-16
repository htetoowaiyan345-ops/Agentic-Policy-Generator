"""Generic section-start detector for paragraphs.

This complements the slot-specific heading patterns in
`heading_anchors.py`. When the slot-specific patterns miss a real
section heading (because the heading uses an unexpected word or
format), this detector identifies likely section starts based on
generic structural signals:

- Short, ALL-CAPS lines (e.g. "INTRODUCTION" or "DEFINITIONS")
- Lines starting with a number/letter followed by a period
  (e.g. "1. Purpose" or "I. Introduction")
- Lines ending without sentence punctuation
- Lines that look like a heading (capitalized, short, no body)

The detector returns TRUE for paragraphs that LOOK like section
starts but don't match any known slot heading. The caller can use
this to identify candidate section boundaries even for unknown
sections.

This is intentionally NOT used by the heading-anchor pipeline
directly - it's a fallback signal for the RAG tier when no
slot-specific heading matches.
"""
from __future__ import annotations

import re
from typing import Optional


def looks_like_section_heading(paragraph: str) -> bool:
    """True if the paragraph looks like a section heading (any section).

    Returns True for paragraphs that are LIKELY a heading for some
    section, even if the heading word isn't in our known synonyms.

    The detector is intentionally STRICT - false negatives are
    acceptable, false positives pollute RAG candidates. Only headings
    that match multiple structural signals pass.
    """
    if not paragraph:
        return False
    text = paragraph.strip()
    if not text:
        return False
    first_line = text.split("\n")[0].strip()

    # Very short paragraphs (< 3 chars) are not headings.
    if len(first_line) < 3:
        return False

    # Check 1: ALL-CAPS line (no body punctuation). Must have at
    # least 3 uppercase letters to avoid matching single-letter
    # paragraphs.
    if re.match(r"^[A-Z][A-Z][A-Z][A-Z\s]*$", first_line):
        return True

    # Check 2: Numbered heading "1. Title" / "1) Title" / "I. Title".
    if re.match(r"^\s*(\d+\.\s+|\d+\)\s+|[IVX]+\.\s+)[A-Z]", first_line):
        return True

    # Check 3: Single or two-word heading from the known slot words.
    KNOWN_HEADINGS = {
        # Single-word headings
        "introduction", "background", "preamble", "overview",
        "purpose", "aim", "objectives",
        "scope", "applicability", "application",
        "exclusions", "exceptions", "limitations",
        "definitions", "definition", "glossary",
        "history",
        "references",
        "eligibility",
        # Two-word headings
        "related policies",
        "related documents",
        "policy statement",
        "policy review",
        "review note",
        "review notes",
        "scope and beneficiaries",
        "scope & beneficiaries",
        "policy review note",
        "policy overview",
        "policy summary",
        "version history",
        "revision history",
        "change log",
        "document history",
        "award structure",
        "payout structure",
        "policy rationale",
        "policy background",
    }
    cleaned = first_line.rstrip(":.-\ufffd").rstrip()
    if cleaned.lower() in KNOWN_HEADINGS and ":" not in first_line:
        return True

    # Check 4: Multi-line: heading on first line, body on subsequent lines.
    # Requires:
    # - first line <= 50 chars (heading is short)
    # - subsequent lines are longer (real body content)
    # - first line does NOT end with period (not a sentence)
    # - first line has no colon-body pattern (those are heading_anchor)
    lines = text.split("\n")
    if len(lines) > 1:
        first = lines[0].strip()
        rest = "\n".join(lines[1:]).strip()
        if (
            first
            and rest
            and 3 <= len(first) <= 50
            and len(rest) > len(first) * 3  # body is significantly longer
            and first[0].isupper()
            and ":" not in first
            and not first.endswith(".")
        ):
            # Heading + body pattern.
            # Reject if first line contains a verb or a complete sentence.
            words = first.lower().split()
            # Common function words that suggest a complete sentence.
            sentence_markers = {"is", "are", "was", "were", "has", "have", "had", "will", "would", "should", "could", "may", "might", "must", "can", "the", "a", "an"}
            sentence_marker_count = sum(1 for w in words if w in sentence_markers)
            # Headings have very few sentence markers.
            if sentence_marker_count <= 1 and 1 <= len(words) <= 8:
                return True
    return False


def find_section_starts(paragraphs: list[str]) -> list[int]:
    """Return indices of paragraphs that look like section starts.

    This is the list of "candidate section boundary" positions in
    the document. Combined with the slot-specific heading-anchor,
    it provides robust section detection for new doc styles.
    """
    return [i for i, p in enumerate(paragraphs) if looks_like_section_heading(p)]