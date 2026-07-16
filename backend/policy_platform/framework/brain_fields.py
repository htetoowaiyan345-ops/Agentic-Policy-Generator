"""Brain label-value schema (Phase 7).

The Brain template uses label-value pairs in five places:
1. Slot 1 (Header): rows like `Type:`, `Policy Title:`, `Policy Number:`,
   `Applicable Sector(s):`, `Functional Area(s):`.
2. Slot 2 (Brief Description): a single label-row `Brief Description:[value]`.
3. Slot 3 (Approval & Governance): rows like `Effective Date/Period:`,
   `Approved by:`, `Prepared by:`, `Responsible Function(s):`,
   `Supersedes:`, `Last Reviewed:`, `Applies to:`.
4. Slot 4 (Reason for Policy): a single label-row `Reason for Policy:[value]`.
5. Slot 11 (Policy Review Note): a single label-row `Policy Review Note:[value]`.

This module:
- Defines the canonical ordered lists of Brain labels.
- Provides synonym matching so input labels like `Policy Type:` map to
  Brain's `Type:`.
- Provides `field_map` helper to build a dict from input paragraphs.
- Provides `missing_field_placeholder` to render `<Label> Data not found in source file`.
"""
from __future__ import annotations

import re
from typing import Iterable


# Slot 1: Header label-rows in order.
BRAIN_HEADER_FIELDS: list[tuple[str, list[str]]] = [
    ("Type:", [
        "type",
        "policy type",
        "document type",
        "category",
        "classification",
        "policy category",
    ]),
    ("Policy Title:", [
        "policy title",
        "title",
        "policy name",
        "name",
        "policy name:",
    ]),
    ("Policy Number:", [
        "policy number",
        "number",
        "code",
        "policy code",
        "policy no",
        "policy no.",
        "policy no:",
        "policy no.:",
        "document number",
        "doc no",
        "doc number",
        "reference",
        "reference number",
        "ref",
        "ref no",
    ]),
    ("Applicable Sector(s):", [
        "applicable sector",
        "applicable sector(s)",
        "sector",
        "sectors",
        "applicable sectors",
        "applicable to sector",
        "sector(s)",
    ]),
    ("Functional Area(s):", [
        "functional area",
        "functional area(s)",
        "function",
        "area",
        "functional areas",
        "area(s)",
        "departments",
        "department",
    ]),
]


# Slot 2: Brief Description label-row.
BRAIN_BRIEF_DESCRIPTION_FIELDS: list[tuple[str, list[str]]] = [
    ("Brief Description:", [
        "brief description",
        "description",
        "summary",
        "brief",
        "overview",
        "policy description",
    ]),
]


# Slot 3: Approval & Governance label-rows in order.
BRAIN_APPROVAL_FIELDS: list[tuple[str, list[str]]] = [
    ("Effective Date/Period:", [
        "effective date",
        "effective date/period",
        "effective date period",
        "date of issue",
        "issued on",
        "date issued",
        "effective from",
        "effective",
        "effective period",
        "valid from",
        "validity",
    ]),
    ("Approved by:", [
        "approved by",
        "approver",
        "approved",
        "approval",
        "approving authority",
        "approving officer",
    ]),
    ("Prepared by:", [
        "prepared by",
        "preparer",
        "author",
        "prepared",
        "drafted by",
        "drafter",
    ]),
    ("Responsible Function(s):", [
        "responsible function",
        "responsible function(s)",
        "responsible functions",
        "owner",
        "function",
        "responsible",
    ]),
    # Brain also has a separate `Responsible Function Officer(s):` row.
    ("Responsible Function Officer(s):", [
        "responsible function officer(s)",
        "responsible function officer",
        "responsible officer",
        "responsible officers",
        "officer",
        "officer(s)",
        "responsible function officers",
    ]),
    ("Supersedes:", [
        "supersedes",
        "replaces",
        "superseded by",
        "superseded",
        "previous policy",
        "predecessor",
    ]),
    ("Last Reviewed:", [
        "last reviewed",
        "review date",
        "last review",
        "last reviewed/updates",
        "last reviewed/updates:",
        "last reviewed/updated",
        "last reviewed/update",
        "reviewed date",
        "review",
        "reviewed on",
        "review period",
        "last update",
    ]),
    ("Applies to:", [
        "applies to",
        "applied to",
        "applies to:",
        "applicable to",
        "applicability",
        "application",
    ]),
]


# Slot 4: Reason for Policy label-row.
BRAIN_REASON_FIELDS: list[tuple[str, list[str]]] = [
    ("Reason for Policy:", [
        "reason for policy",
        "rationale",
        "reason",
        "policy rationale",
        "background",
        "context",
    ]),
]


# Slot 11: Policy Review Note label-row.
BRAIN_REVIEW_NOTE_FIELDS: list[tuple[str, list[str]]] = [
    ("Policy Review Note:", [
        "policy review note",
        "review note",
        "review",
        "note",
        "review notes",
        "policy notes",
    ]),
]


# All label rows, in order: slot 1, 2, 3, 4, 11.
BRAIN_LABEL_ROWS: list[tuple[str, list[str]]] = (
    BRAIN_HEADER_FIELDS
    + BRAIN_BRIEF_DESCRIPTION_FIELDS
    + BRAIN_APPROVAL_FIELDS
    + BRAIN_REASON_FIELDS
    + BRAIN_REVIEW_NOTE_FIELDS
)


_LABEL_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _LABEL_NORM_RE.sub("", s.casefold())


def canonical_label(input_label: str) -> str | None:
    """Given any user-supplied label (e.g., 'Policy Type:' or
    'Last Reviewed/Updates:'), return the canonical Brain label
    (e.g., 'Type:' or 'Last Reviewed:'). Returns None if no match.

    Strategy:
      1. Exact canonical or synonym match.
      2. Longest-prefix match against the canonical labels — so Brain
         paragraphs like `Responsible Function Officer(s):` resolve to
         `Responsible Function Officer(s):` (the longer canonical),
         not the shorter `Responsible Function(s):`. This handles
         Brain headings that extend a canonical with extra words.
    """
    target = _norm(input_label)
    # Exact match first (canonical or synonym).
    for canonical, syns in BRAIN_LABEL_ROWS:
        if _norm(canonical) == target:
            return canonical
        for syn in syns:
            if _norm(syn) == target:
                return canonical
    # Longest-prefix match second.
    best: tuple[int, str] | None = None
    for canonical, _ in BRAIN_LABEL_ROWS:
        ncan = _norm(canonical)
        if ncan and target.startswith(ncan):
            if best is None or len(ncan) > best[0]:
                best = (len(ncan), canonical)
    if best is not None:
        return best[1]
    return None


def is_exact_label_or_synonym(text: str) -> str | None:
    """True if `text` is an EXACT canonical label or synonym (not a
    prefix-match).

    Unlike `canonical_label()`, which accepts prefix matches like
    "Brief Description Framework governing" (because it starts with
    "brief description"), this function only returns the canonical
    when the input is an exact canonical or synonym. Use this when
    you need to avoid the prefix-match behavior — e.g., when deciding
    where to split a line on a label boundary.

    The colon is optional in `text` (e.g., both "Type" and "Type:"
    match).
    """
    target = _norm(text.rstrip(":").rstrip())
    if not target:
        return None
    for canonical, syns in BRAIN_LABEL_ROWS:
        if _norm(canonical.rstrip(":")) == target:
            return canonical
        for syn in syns:
            if _norm(syn.rstrip(":")) == target:
                return canonical
    return None


# Regex matching any `Label: value` line. Allows letters, digits, spaces,
# punctuation in the label.
_LABEL_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ()/&.,'\-_]*?)\s*[:\t]\s*(.+?)\s*$")


def field_map(input_paragraphs: Iterable[str]) -> dict[str, str]:
    """Walk input paragraphs, extract `Label: value` lines, normalize labels
    against Brain synonyms, return canonical_label → value dict.

    Lines that don't match any known label are ignored (kept for prose
    consumption by the analyzer).

    Values consisting entirely of digits (e.g. phone numbers or reference
    IDs accidentally picked up from PDF metadata) are rejected — they are
    treated as "not found" so the renderer writes the marker instead.
    """
    out: dict[str, str] = {}
    for line in input_paragraphs:
        if not line or not line.strip():
            continue
        m = _LABEL_LINE_RE.match(line)
        if not m:
            continue
        input_label = m.group(1).strip()
        value = m.group(2).strip()
        # Reject pure-numeric values — they are almost certainly
        # extraction artifacts (page numbers, phone numbers, reference
        # IDs) rather than legitimate label values.
        if value and re.fullmatch(r"\d+", value):
            continue
        canonical = canonical_label(input_label + ":")
        if canonical is None:
            # Try also matching `Label` without colon (in case regex is
            # strict). Try the normalized form.
            continue
        if canonical in out:
            # Multi-occurrence input: prefer first non-empty value.
            if not out[canonical] and value:
                out[canonical] = value
        else:
            out[canonical] = value
    return out


def missing_field_placeholder(label: str) -> str:
    """Return the canonical marker text the renderer writes when no
    input value is supplied: `<label> Data is not found in source file`.

    Plain body styling (no italic, no gray) — caller renders the label
    portion bold; the marker text is plain Calibri 10pt.

    Wording is exact per the user's directive:
        "Data is not found in source file"
    """
    return f"{label} Data is not found in source file"


def iter_brain_labels() -> Iterable[tuple[str, list[str]]]:
    """Yield (canonical_label, synonyms) tuples in order."""
    return iter(BRAIN_LABEL_ROWS)
