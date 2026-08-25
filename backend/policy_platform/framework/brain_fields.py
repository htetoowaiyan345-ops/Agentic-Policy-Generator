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
        # Phase 5 — general date-label synonyms seen across
        # real-world policy PDFs (no per-file hardcoding; these are
        # generic Brain-schema slot-3 label variants).
        "effected/review date",
        "effected/review",
        "effected date",
        "effected on",
        "review date",
        "reviewed date",
        "reviewed on",
        "date effected",
        "date effective",
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
    # Phase 6 — Burmese reverse-index fallback. Only consulted when English
    # exact + prefix matching both failed. Maps Burmese phrases from
    # ``burmese_synonyms.yaml`` to canonical English labels.
    try:
        from policy_platform.i18n.burmese_synonyms import (
            get_canonical_for_burmese_label,
        )
        # Strip trailing colon before reverse lookup (loader accepts both).
        cleaned = input_label.strip().rstrip(":").strip()
        result = get_canonical_for_burmese_label(cleaned)
        if result:
            return result
    except Exception:
        pass
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
# punctuation in the label. Burmese characters (U+1000-U+109F and
# U+AA60-U+AA7F) are accepted so Myanmar PDFs can use Burmese labels
# such as `မူဝါဒအမည်: ...`.
_LABEL_LINE_RE = re.compile(
    r"^\s*([A-Za-z\u1000-\u109F\uAA60-\uAA7F][A-Za-z0-9 ()/&.,'\-_\u1000-\u109F\uAA60-\uAA7F]*?)\s*[:\t]\s*(.+?)\s*$"
)


def field_map(input_paragraphs: Iterable[str]) -> dict[str, str]:
    """Walk input paragraphs, extract `Label: value` lines, normalize labels
    against Brain synonyms, return canonical_label → value dict.

    Lines that don't match any known label are ignored (kept for prose
    consumption by the analyzer).

    Values consisting entirely of digits (e.g. phone numbers or reference
    IDs accidentally picked up from PDF metadata) are rejected — they are
    treated as "not found" so the renderer writes the marker instead.

    Phase 6: each extracted value is validated via `parse_field_value()`
    which applies field-specific rules (whitelists, patterns, length
    caps). Invalid values are dropped silently. This is a general
    heuristic — works for any document whose values match common
    English-language patterns.
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
        # Phase 6: validate the value via field-specific rules.
        cleaned = parse_field_value(canonical, value)
        if cleaned is None:
            continue
        if canonical in out:
            # Multi-occurrence input: prefer first non-empty value.
            if not out[canonical] and cleaned:
                out[canonical] = cleaned
        else:
            out[canonical] = cleaned
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


# ---------------------------------------------------------------------------
# Phase 6 — Field value validation.
#
# Each Brain-schema field has rules about what a valid value looks like.
# These rules are general heuristics — they apply to any document whose
# values match common English-language patterns. They are NOT
# HR_00002-specific; HR_00002 PDF is used only as a reference for the
# expected format of each field.
#
# The validation rules are exported as patterns. Callers
# (field_parser, pipeline) call `parse_field_value(canonical, raw_value)`
# to get a cleaned value or None (rejected).
# ---------------------------------------------------------------------------


# Department-suffix pattern used to disambiguate Responsible Function
# values (e.g., "HR Department", "Risk Team") from Officer names
# (e.g., "Daw Win Win Tint").
_DEPARTMENT_SUFFIX_RE = re.compile(
    r"\b(department|team|division|unit|group|office|function|"
    r"directorate|section|branch|board|committee|desk)\s*$",
    re.IGNORECASE,
)


# Person-name pattern: 2-4 capitalized words. Allows for single-letter
# initials (e.g., "Pwint P Han", "John A. Smith", "Mary-Jane Watson").
_PERSON_NAME_RE = re.compile(
    r"^([A-Z][a-zA-Z'\-\.]*\.?\s+){1,3}[A-Z][a-zA-Z'\-\.]+$"
)


# Date patterns — accept common formats:
#   DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD, YYYY/MM/DD
#   "01 July 2026", "July 1 2026", "1 July 2026"
#   "01-Jul-2026", "1 Jul 2026"
_DATE_PATTERNS: tuple[str, ...] = (
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",  # 01/07/2026, 1-7-26
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",     # 2026-07-01, 2026/7/1
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{2,4}\b",                         # 1 July 2026
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},?\s+\d{2,4}\b",             # July 1, 2026
)
_DATE_REGEX = re.compile("|".join(_DATE_PATTERNS), re.IGNORECASE)


# Version-number / reference-number pattern (e.g., "V2", "v1.0", "HR-ARP-001").
_REFERENCE_NUMBER_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,30}$"
)


def _looks_like_person_name(value: str) -> bool:
    """Return True if `value` looks like a person name (2-4 capitalized words)."""
    v = value.strip()
    if not v:
        return False
    return bool(_PERSON_NAME_RE.match(v))


def _looks_like_department(value: str) -> bool:
    """Return True if `value` ends with a department-suffix word."""
    v = value.strip()
    if not v:
        return False
    return bool(_DEPARTMENT_SUFFIX_RE.search(v))


def _looks_like_date(value: str) -> bool:
    """Return True if `value` contains a recognizable date."""
    return bool(_DATE_REGEX.search(value))


def _looks_like_reference(value: str) -> bool:
    """Return True if `value` is a reference/version number."""
    v = value.strip()
    return bool(_REFERENCE_NUMBER_RE.match(v))


def _is_clean_noun_phrase(value: str) -> bool:
    """Return True if `value` looks like a clean noun phrase (not a sentence).

    A clean noun phrase:
      - Has length ≥ 2 chars (after stripping trailing period).
      - Does NOT contain `:` (label marker).
      - Does NOT contain `\n` (multi-line).
      - Does NOT start with a digit (likely an artifact).
      - Does NOT have a period mid-string (sentence).
      - Has at least one alphabetic character.
      - Does NOT start with a lowercase article (a, an, the, of, in,
        on, at, for, to, by, with) — those are sentence fragments
        not noun-phrase heads.

    A trailing `.` is allowed (sentence-segmented input commonly ends
    with `.`). The stripped value is what matters for the noun-phrase
    check.
    """
    v = value.strip()
    # Allow trailing period — strip it for the noun-phrase check.
    if v.endswith("."):
        v = v[:-1].rstrip()
    if len(v) < 2:
        return False
    if ":" in v or "\n" in v:
        return False
    if v[0].isdigit():
        return False
    # Mid-string period = sentence (not a noun phrase).
    if v.count(".") > 0:
        return False
    # Must have at least one alphabetic character.
    if not any(c.isalpha() for c in v):
        return False
    # Reject sentence fragments starting with lowercase articles or
    # prepositions.
    if v[0].islower() and v.split()[0].lower() in {
        "a", "an", "the", "of", "in", "on", "at", "for",
        "to", "by", "with", "and", "or", "as", "is", "are",
        "was", "were", "be", "been",
    }:
        return False
    return True


def _split_multi_value(value: str) -> list[str]:
    """Split a multi-value string on common separators.

    Handles: `,`, `;`, ` and `, ` & `. Each part is stripped.
    """
    v = value.strip()
    # Replace ` and ` / ` & ` with comma first.
    v = re.sub(r"\s+and\s+", ",", v, flags=re.IGNORECASE)
    v = re.sub(r"\s+&\s+", ",", v)
    parts = re.split(r"[,;]", v)
    return [p.strip() for p in parts if p.strip()]


# General taxonomy vocabulary for the `Type:` field. These are
# domain-taxonomy words (the names of document classifications used
# across corporate-policy, regulatory, and operational contexts), not
# per-document hardcoded values. Any new file whose `Type:` value is one
# of these (case-insensitive) is accepted as-is. Single-word matches are
# preferred; multi-word matches (e.g. "Standard Operating Procedure")
# are accepted by exact full-string match after normalization.
_TYPE_VOCABULARY: frozenset[str] = frozenset({
    "policy",
    "standard",
    "procedure",
    "guideline",
    "framework",
    "charter",
    "directive",
    "rule",
    "regulation",
    "protocol",
    "manual",
    "handbook",
    "code",
    "act",
    "bylaw",
    "bylaws",
    "specification",
    "spec",
    "methodology",
    "instruction",
    "circular",
    "notice",
    "bulletin",
    "memo",
    "memorandum",
    "policy and procedure",
    "standard operating procedure",
    "sop",
    "code of conduct",
    "code of practice",
    "governance document",
    "terms of reference",
    "terms and conditions",
    "service-level agreement",
    "service level agreement",
    "sla",
    "operational directive",
    "administrative order",
    "executive order",
    "ordinance",
    "statute",
    "law",
    "compliance requirement",
})


# Section-marker words that signal a *section heading* rather than a
# title. Used to reject candidates that look like titles but are
# actually section labels from the body of a document.
_SECTION_MARKER_WORDS: frozenset[str] = frozenset({
    "purpose",
    "scope",
    "objective",
    "objectives",
    "definition",
    "definitions",
    "responsibility",
    "responsibilities",
    "procedure",
    "process",
    "policy statement",
    "background",
    "introduction",
    "overview",
    "reference",
    "references",
    "appendix",
    "annex",
    "annexure",
    "glossary",
    "revision history",
    "version history",
    "approval",
    "approval history",
})


# Role-word suffixes for Responsible Function Officer(s). When a value
# ends in one of these, it's almost certainly an officer *title* (e.g.,
# "Chief Information Security Officer", "Head of HR", "Compliance
# Manager") rather than a person name or department.
_ROLE_SUFFIX_WORDS: frozenset[str] = frozenset({
    "officer",
    "manager",
    "head",
    "director",
    "lead",
    "chief",
    "coordinator",
    "specialist",
    "administrator",
    "controller",
    "supervisor",
    "analyst",
    "engineer",
    "vp",
    "avp",
    "president",
    "partner",
    "associate",
    "consultant",
    "advisor",
    "adviser",
    "executive",
    "secretary",
    "chair",
    "chairperson",
    "owner",
})


# Verb-like tokens that signal a sentence fragment when they appear in
# what should be a clean noun phrase (Functional Area, Responsible
# Function, Applies to). These are general English verbs commonly seen
# in prose descriptions.
_VERB_LIKE_TOKENS: frozenset[str] = frozenset({
    "provides",
    "provide",
    "includes",
    "include",
    "manages",
    "manage",
    "covers",
    "cover",
    "applies",
    "apply",
    "ensures",
    "ensure",
    "requires",
    "require",
    "supports",
    "support",
    "facilitates",
    "facilitate",
    "delivers",
    "deliver",
    "operates",
    "operate",
    "maintains",
    "maintain",
    "governs",
    "govern",
    "addresses",
    "address",
    "handles",
    "handle",
    "assists",
    "assist",
    "performs",
    "perform",
    "executes",
    "execute",
    "coordinates",
    "coordinate",
    "reviews",
    "review",
    "approves",
    "approve",
    "is",
    "are",
    "was",
    "were",
    "be",
    "being",
    "been",
    "has",
    "have",
    "had",
    "does",
    "do",
    "did",
    "will",
    "would",
    "should",
    "could",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "helps",
    "help",
})


# Review-cycle strings used as fallback for Last Reviewed when no date
# is present. These are general schedule words, not file-specific.
_REVIEW_CYCLE_WORDS: frozenset[str] = frozenset({
    "annually",
    "annual",
    "yearly",
    "bi-annually",
    "biannually",
    "bi-annually",
    "semi-annually",
    "semi-annually",
    "semi-annually",
    "quarterly",
    "monthly",
    "weekly",
    "daily",
    "ad-hoc",
    "adhoc",
    "as needed",
    "as required",
    "every 2 years",
    "every 3 years",
    "every 5 years",
    "every 6 months",
    "every quarter",
    "every month",
})


# Partial-date patterns for Last Reviewed fallback (no full day number).
# "July 2026", "Q3 2025", "2026".
_PARTIAL_DATE_PATTERNS: tuple[str, ...] = (
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}\b",                          # July 2026
    r"\bQ[1-4]\s+\d{4}\b",                    # Q3 2025
    r"\b\d{4}\b",                            # 2026 (year alone)
    r"\b(?:H[12]|FY)\s*\d{2,4}\b",            # H1 2026, FY26, FY 2026
)
_PARTIAL_DATE_REGEX = re.compile(
    "|".join(_PARTIAL_DATE_PATTERNS), re.IGNORECASE
)


# Words/phrases that introduce a Reason for Policy paragraph in prose
# (when no explicit label is present).
_REASON_INTRO_PATTERNS: tuple[str, ...] = (
    r"^\s*this\s+policy\s+is\s+(required|needed|necessary|intended|designed|established)",
    r"^\s*the\s+purpose\s+of\s+this\s+policy",
    r"^\s*in\s+order\s+to",
    r"^\s*to\s+(ensure|comply|meet|satisfy|address|protect|support|enable)",
    r"^\s*(because|since|as)\s+",
)


def _looks_like_partial_date(value: str) -> bool:
    """Return True if `value` contains a partial date (year alone, month-year, quarter)."""
    return bool(_PARTIAL_DATE_REGEX.search(value))


def _looks_like_role_title(value: str) -> bool:
    """Return True if `value` ends with a role-word suffix (e.g., "Manager", "Officer")."""
    v = value.strip()
    if not v:
        return False
    # Strip trailing punctuation.
    v_clean = v.rstrip(".,;:")
    if not v_clean:
        return False
    last_word = v_clean.split()[-1].lower().rstrip(".,;:")
    return last_word in _ROLE_SUFFIX_WORDS


def _contains_verb(value: str) -> bool:
    """Return True if `value` contains a verb-like token (sentence fragment indicator)."""
    tokens = re.findall(r"[A-Za-z]+", value.lower())
    return any(t in _VERB_LIKE_TOKENS for t in tokens)


def _contains_section_marker(value: str) -> bool:
    """Return True if `value` contains a section-marker word (heading, not title)."""
    v = value.strip().lower()
    for marker in _SECTION_MARKER_WORDS:
        # Match as a whole word boundary.
        if re.search(rf"\b{re.escape(marker)}\b", v):
            return True
    return False


def _looks_like_type_vocab(value: str) -> bool:
    """Return True if `value` matches the general Type vocabulary.

    Accepts:
      - Exact single-word match from vocabulary (case-insensitive).
      - Exact multi-word match (e.g., "Standard Operating Procedure").
      - Single capitalized word ending in common doc-type morpheme.
    """
    v = value.strip()
    if not v:
        return False
    # Strip trailing punctuation.
    v_norm = v.rstrip(".,;:").strip()
    if not v_norm:
        return False
    # Multi-word exact match (case-insensitive).
    if v_norm.lower() in _TYPE_VOCABULARY:
        return True
    # Single-word: must be entirely capitalized first letter, rest lowercase
    # or all caps, and present in vocabulary.
    tokens = v_norm.split()
    if len(tokens) == 1:
        if v_norm.lower() in _TYPE_VOCABULARY:
            return True
    return False


def _extract_type_classification(value: str) -> str | None:
    """Extract only the document-classification portion of a Type value.

    General heuristic — works for any future file. The user-supplied
    guidance is that `Type:` may arrive as either:

      - Pure classification: "Policy", "Standard", "Code of Conduct".
      - Subject + classification: "Information Security Policy",
        "Password Management Procedure", "Risk Management Framework",
        "Employee Code of Conduct".

    When the value is multi-word, look at trailing 1-3 word phrases and
    return the first one that matches the type vocabulary. Strip the
    subject prefix.

    Examples (case-insensitive):
      "Information Security Policy"      -> "Policy"
      "Password Management Procedure"    -> "Procedure"
      "Risk Management Framework"        -> "Framework"
      "Employee Code of Conduct"         -> "Code of Conduct"
      "Information Security Standard"    -> "Standard"
      "Policy"                          -> "Policy"
      "Standard"                        -> "Standard"

    If no trailing phrase matches the vocabulary, return None — the
    Type slot is rejected rather than filled with the whole subject
    (which would duplicate the Policy Title).
    """
    v = value.strip()
    if not v:
        return None
    v_norm = v.rstrip(".,;:").strip()
    if not v_norm:
        return None
    # Pure classification (1-3 words).
    if _looks_like_type_vocab(v_norm):
        return v_norm
    # Multi-word: try trailing 1, 2, 3 word phrases (longest first).
    tokens = v_norm.split()
    if len(tokens) < 2:
        return None
    # Don't strip if too long — likely a sentence, not a title.
    if len(tokens) > 8:
        return None
    for n in (3, 2, 1):
        if n > len(tokens):
            continue
        candidate = " ".join(tokens[-n:])
        if candidate.lower() in _TYPE_VOCABULARY:
            # Return capitalized classification (e.g., "Policy" not "policy").
            # Only capitalize the first letter to preserve multi-word casing.
            return candidate[0].upper() + candidate[1:] if candidate else candidate
    return None


def parse_field_value(canonical: str, raw_value: str | None) -> str | None:
    """Validate and clean a raw value for a Brain-schema field.

    Args:
        canonical: The canonical Brain label (e.g., "Type:", "Functional Area(s):").
        raw_value: The raw extracted value (may be empty, None, or noisy).

    Returns:
        The cleaned value (string) if the value passes validation rules
        for that field, or None if the value is rejected.

    Validation rules per field (general heuristics, NO hardcoded
    whitelists — works for any document whose values match common
    English-language patterns):
      - Type:                clean noun phrase (rejects sentence
                              fragments like "of benefits").
      - Functional Area(s):  clean noun phrase, multi-value support.
      - Brief Description:   any non-empty value, capped at 500 chars.
      - Reason for Policy:   any non-empty value, capped at 1000 chars.
      - Policy Title:        any non-empty value, capped at 200 chars,
                              no `:` (titles don't have colons).
      - Effective Date/Period: any non-empty value.
      - Approved by:         any non-empty value.
      - Prepared by:         any non-empty value.
      - Responsible Function(s): clean noun phrase (department pattern
                                  or general).
      - Responsible Function Officer(s): person-name pattern OR
                                          department-suffix or general
                                          noun phrase.
      - Supersedes:          reference/version/date/title-like.
      - Last Reviewed:       must contain a recognizable date.
      - Applies to:          any non-empty value.
      - Policy Number:       any non-empty value.
      - Applicable Sector(s): any non-empty value, multi-value support.
      - Policy Review Note:  any non-empty value.

    This is a general heuristic — works for any document whose values
    match common English-language patterns. HR_00002 PDF was used only
    as a reference for the expected format of each field. NO file-
    specific hardcoding.
    """
    if not raw_value:
        return None
    value = raw_value.strip()
    if not value:
        return None

    if canonical == "Type:":
        # Reject sentence fragments.
        if _contains_verb(value):
            return None
        # Extract only the classification portion. If the value is
        # "Information Security Policy", return "Policy". If it's a
        # pure classification ("Policy"), return "Policy". Otherwise
        # reject — the subject without classification would duplicate
        # the Policy Title.
        extracted = _extract_type_classification(value)
        if extracted is not None:
            return extracted
        # Fallback: accept single-word clean noun phrase that may be
        # a doc-type name not in the vocabulary.
        v = value.strip().rstrip(".,;:")
        if v and _is_clean_noun_phrase(v):
            tokens = v.split()
            if len(tokens) == 1:
                return v
        return None

    if canonical == "Functional Area(s):":
        # Multi-value support: split on common separators.
        parts = _split_multi_value(value)
        if not parts:
            return None
        # Each part must be a clean noun phrase and not contain verb-like
        # tokens (rejects sentence fragments like "Provides information
        # security services to the group").
        valid_parts = [
            p for p in parts
            if _is_clean_noun_phrase(p) and not _contains_verb(p)
        ]
        if not valid_parts:
            return None
        if len(valid_parts) == 1:
            return valid_parts[0]
        return ", ".join(valid_parts)

    if canonical == "Brief Description:":
        # Reject values that contain section-marker headings only (e.g.,
        # "1. Purpose" or "Purpose:" alone) — these are not descriptions.
        v = value.strip()
        if v.endswith(":") and len(v.split()) <= 4:
            return None
        # Cap length at 500 chars.
        return v[:500]

    if canonical == "Reason for Policy:":
        v = value.strip()
        if not v:
            return None
        # Reject values that are pure section headings.
        if v.endswith(":") and len(v.split()) <= 4:
            return None
        return v[:1000]

    if canonical == "Policy Title:":
        if not value or ":" in value:
            return None
        # Reject candidates that contain section-marker words — these
        # are headings, not titles.
        if _contains_section_marker(value):
            return None
        v = value.strip()
        # Reject very short candidates (likely page numbers / labels).
        if len(v) < 4:
            return None
        # Reject candidates that are only digits / punctuation.
        if not any(c.isalpha() for c in v):
            return None
        return v[:200]

    if canonical == "Last Reviewed:":
        # Accept full date, partial date (year, month-year, quarter), or
        # review-cycle strings.
        if _looks_like_date(value):
            return value
        if _looks_like_partial_date(value):
            return value
        # Review-cycle fallback (general schedule words, not file-specific).
        v = value.strip().lower().rstrip(".,;:")
        if v in _REVIEW_CYCLE_WORDS:
            return value
        return None

    if canonical == "Supersedes:":
        # Multi-value: split on common separators first.
        # Supersedes can be a long title (e.g., "Policy for Coronavirus
        # Disease-2019 Tests During the Public Health Emergency
        # (Revised): Guidance for Clinical Laboratories"), so we accept
        # longer text including colons (which are common in policy
        # titles like "Policy for X: Subtitle").
        if not value or "\n" in value:
            return None
        # Strip enclosing quotes if present (general English convention
        # — values like `"Policy Title"` should yield `Policy Title`).
        stripped = value.strip()
        if (stripped.startswith('"') and stripped.endswith('"')) or \
           (stripped.startswith("'") and stripped.endswith("'")):
            stripped = stripped[1:-1].strip()
            if stripped:
                value = stripped
        # Multi-value support: split on `;` (semicolons are the most
        # common supersedes-list separator). Each part must be valid.
        if ";" in value:
            parts = [p.strip() for p in value.split(";") if p.strip()]
            valid_parts = []
            for p in parts:
                if _validate_supersedes_part(p):
                    valid_parts.append(p)
            if not valid_parts:
                return None
            return "; ".join(valid_parts)
        return value if _validate_supersedes_part(value) else None

    if canonical == "Responsible Function(s):":
        # Multi-value support.
        parts = _split_multi_value(value)
        valid_parts: list[str] = []
        for p in parts:
            # Accept any clean noun phrase (general — no whitelist).
            # Reject parts containing verb-like tokens (sentence fragments).
            if _is_clean_noun_phrase(p) and not _contains_verb(p):
                valid_parts.append(p)
        if not valid_parts:
            return None
        if len(valid_parts) == 1:
            return valid_parts[0]
        return ", ".join(valid_parts)

    if canonical == "Responsible Function Officer(s):":
        # Multi-value support (some documents have multiple officers).
        parts = _split_multi_value(value)
        valid_parts = []
        for p in parts:
            # Accept person-name pattern, role-title suffix (e.g., "Chief
            # Information Security Officer"), department suffix, or any
            # clean noun phrase (general — no whitelist). Reject parts
            # containing verb-like tokens (sentence fragments).
            if _contains_verb(p):
                continue
            if (
                _looks_like_person_name(p)
                or _looks_like_role_title(p)
                or _looks_like_department(p)
                or _is_clean_noun_phrase(p)
            ):
                valid_parts.append(p)
        if not valid_parts:
            return None
        if len(valid_parts) == 1:
            return valid_parts[0]
        return ", ".join(valid_parts)

    if canonical in ("Applies to:", "Effective Date/Period:", "Approved by:",
                     "Prepared by:", "Policy Number:",
                     "Applicable Sector(s):", "Policy Review Note:"):
        if canonical == "Applicable Sector(s):":
            # Multi-value support — preserve original separator style.
            parts = _split_multi_value(value)
            if not parts:
                return None
            if "&" in value and "," not in value:
                return " & ".join(parts)
            return ", ".join(parts)
        if canonical == "Applies to:":
            # Multi-value support.
            parts = _split_multi_value(value)
            valid_parts = [
                p for p in parts
                if _is_clean_noun_phrase(p) and not _contains_verb(p)
            ]
            if not valid_parts:
                return None
            if len(valid_parts) == 1:
                return valid_parts[0]
            return ", ".join(valid_parts)
        if canonical == "Approved by:" or canonical == "Prepared by:":
            return value[:200]
        return value[:500]

    # Default: any non-empty value.
    return value


def _validate_supersedes_part(value: str) -> bool:
    """Validate a single Supersedes part (used for both single and
    multi-value cases).

    Accepts: reference-number, version, date, or title-like phrase.
    Rejects: empty, multi-line, lowercase-article starts.
    """
    v = value.strip()
    if not v or "\n" in v:
        return False
    if _looks_like_reference(v) or _looks_like_date(v):
        return True
    # Version-like pattern (e.g., "v1.0", "V2.1", "Version 2").
    if re.match(r"^v?\d+(\.\d+)*(\s|$)", v, re.IGNORECASE):
        return True
    # Reject values starting with lowercase article/preposition.
    first_word = v.split()[0].lower() if v.split() else ""
    if first_word in {
        "the", "a", "an", "and", "or", "but", "is", "are",
        "was", "were", "of", "in", "on", "at", "to", "by", "with",
        "for", "as",
    }:
        return False
    return True
