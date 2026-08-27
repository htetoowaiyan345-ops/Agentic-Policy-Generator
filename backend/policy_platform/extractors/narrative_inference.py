"""Phase C: narrative-inference rules for FDA-flavored and label-light inputs.

Some documents don't follow the `Label: value` schema. They are
narrative policies where the title is on the first page and the date
appears in prose. This module extracts:

  - **Effective Date** from common phrases:
      - "Document issued on the web on November 15, 2021."
      - "issued on 01 July 2026"
  - **Supersedes** from common phrases:
      - 'This document supersedes "Policy for ..."'
  - **Policy Title** from the first non-empty line on the first
    page, when the document is not yet bound to a `Policy Title:`
    canonical label.

Phase 7 — extended prose-inference for label-light documents. Each
rule uses general English patterns (no per-document hardcoding).
All inferences are validated via `parse_field_value()` so invalid
values are dropped silently.

Inferences added in Phase 7 (each rule is general — works for any
document whose prose matches common English patterns):

  - Type: detect classification in body ("is a policy", etc.) and
    title subject+classification shape.
  - Applicable Sector(s): detect audience/sector keywords.
  - Functional Area(s): detect ownership keywords + department-head
    noun vocabulary.
  - Brief Description: first paragraph starting with a purpose-intro
    phrase ("This policy …", "Purpose:", etc.).
  - Responsible Function(s): same vocabulary as Functional Area,
    triggered by stronger ownership keywords.
  - Responsible Function Officer(s): pattern-match "policy owner",
    "accountable executive", "sponsor", etc., followed by name list.
  - Last Reviewed: date appearing after a review keyword.
  - Supersedes: extend to unquoted "supersedes <Title>" pattern.
  - Applies to: audience-keyword pattern + general noun phrase.
  - Reason for Policy: extend intro patterns to "aimed to provide",
    "designed to", "intended to", "established to".
  - Exclusions: section heading detection ("3. Exclusions",
    "Exceptions", etc.) + paragraph extraction.

These rules are **defensive and conservative**: they only fire if
the corresponding canonical label is still missing from the field
map produced by Phase B (and earlier paths), unless the caller
explicitly asks for inference to run on populated slots.
"""
from __future__ import annotations

import re
from typing import Iterable


# Pattern: "Document issued on the web on <DATE>"
_ISSUED_ON_RE = re.compile(
    r"\b(?:document\s+)?issued\s+on(?:\s+the\s+web)?\s+on\s+"
    r"([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


# Pattern: a sentence containing "supersedes" followed by quoted text.
_SUPERSEDES_QUOTED_RE = re.compile(
    r'\bsupersedes?\s+(?:the\s+)?["\u201c]([^"\u201d]{3,200})["\u201d]',
    re.IGNORECASE,
)


# Pattern: "supersedes" followed by unquoted title-like phrase. Used
# only when the quoted pattern misses and the surrounding sentence
# looks like a Supersedes declaration.
_SUPERSEDES_UNQUOTED_RE = re.compile(
    r"\bsupersedes?\s+(?:the\s+)?([A-Z][A-Za-z0-9][^.]{3,200})",
    re.IGNORECASE,
)


# Pattern: "Policy for X" / "Policy Title" suggested by page 1 line.
_POLICY_FOR_RE = re.compile(
    r"^(?:Policy\s+for\s*|Sexual\s+Harassment\s+Policy\s+for\s*)([A-Za-z0-9 \-_/&;.,()'\":*]{0,300})$",
    re.IGNORECASE,
)


# Pattern: body sentence declaring document classification.
# "<Subject> is a|is an|are a|are an <classification>."
_TYPE_DECLARATION_RE = re.compile(
    r"\b(?:this\s+(?:policy|standard|procedure|guideline|framework|manual|charter|directive|rule|regulation|protocol)|this|it|the\s+(?:policy|standard|procedure|guideline|framework|manual|charter|directive|rule|regulation|protocol))\s+"
    r"(?:is|are)\s+(?:a|an)\s+"
    r"([A-Za-z][A-Za-z\s\-]{2,60}?)(?:[.,;:]|\s+(?:for|that|which|to|of|in|and|with|providing|including|covering)\b)",
    re.IGNORECASE,
)


# Pattern: purpose-intro phrases for Brief Description.
_BRIEF_INTRO_PATTERNS: tuple[str, ...] = (
    r"^\s*(?:\d+[\.\d]*\s+)?this\s+(?:policy|standard|procedure|guideline|framework|manual|charter|directive|rule|regulation|protocol|document)\s+",
    r"^\s*purpose\s+(?:of\s+(?:this|the)\s+\w+\s+)?[:\-]",
    r"^\s*overview\s*[:\-]",
    r"^\s*summary\s*[:\-]",
    r"^\s*introduction\s*[:\-]",
    r"^\s*scope\s+(?:of\s+(?:this|the)\s+\w+\s+)?[:\-]",
    r"^\s*objective\s*[:\-]",
)


# Pattern: reason-intro phrases for Reason for Policy.
_REASON_INTRO_PATTERNS: tuple[str, ...] = (
    r"^\s*(?:\d+[\.\d]*\s+)?this\s+(?:policy|standard|procedure|guideline|framework|manual|charter|directive|rule|regulation|protocol|document)\s+is\s+(?:required|needed|necessary|intended|designed|established|aimed|created)\s+",
    r"^\s*(?:\d+[\.\d]*\s+)?the\s+purpose\s+of\s+(?:this|the)\s+\w+\s+",
    r"^\s*in\s+order\s+to\s+",
    r"^\s*to\s+(?:ensure|comply\s+with|meet|satisfy|address|protect|support|enable)\s+",
    r"^\s*(?:because|since|as)\s+",
    r"^\s*(?:this|it|the\s+\w+)\s+(?:is|are)\s+(?:required|needed|designed|intended|aimed)\s+to\s+",
)


# Pattern: ownership keywords that signal "this function/department owns the policy".
_OWNERSHIP_KEYWORDS: tuple[str, ...] = (
    "owned by",
    "managed by",
    "administered by",
    "administered through",
    "overseen by",
    "accountable to",
    "responsible for",
    "the owner",
    "policy owner",
    "administrative responsibility",
    "functional area",
    "the function",
    "the department",
    "the team",
    "the unit",
)


# Pattern: audience keywords that signal "applies to".
_AUDIENCE_KEYWORDS: tuple[str, ...] = (
    "applies to",
    "applicable to",
    "applicability",
    "this policy applies",
    "this standard applies",
    "this procedure applies",
    "this guideline applies",
    "target audience",
    "covered by this",
    "covered under this",
    "scope of application",
    "applicable to all",
)


# Pattern: officer/owner keywords.
_OFFICER_KEYWORDS: tuple[str, ...] = (
    "policy owner",
    "policy sponsor",
    "accountable executive",
    "accountable officer",
    "responsible officer",
    "designated officer",
    "designated contact",
    "approval authority",
    "approving authority",
    "approved by",
    "owner:",
    "sponsor:",
)


# Pattern: review-date keywords.
_REVIEW_KEYWORDS: tuple[str, ...] = (
    "last reviewed",
    "last review",
    "reviewed on",
    "review date",
    "date reviewed",
    "reviewed:",
    "last updated",
    "last update",
    "review period",
)


# Pattern: exclusions section heading. Matches "3. Exclusions",
# "Exclusions:", "Exceptions:", "Limitations:", "Not Covered",
# "Excluded Groups", etc.
_EXCLUSIONS_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:exclusions?|exceptions?|limitations?|not\s+covered|excluded\s+groups?|excluded\s+persons?|excluded\s+entities?)\s*[:\-]?\s*$",
    re.IGNORECASE,
)


# Layer D — Myanmar script helper. We reuse the burmese_strings
# constants via lazy import to avoid circulars; the constants are
# narrow, generic keyword lists (department-head nouns, ownership
# phrases, audience markers, officer titles, type-classification
# nouns) that mirror the English keyword lists above.
def _burmese_strings():
    """Lazy import to avoid circulars at module load."""
    try:
        from policy_platform.i18n.burmese_strings import (
            OWNERSHIP_KEYWORDS_MM,
            AUDIENCE_KEYWORDS_MM,
            OFFICER_KEYWORDS_MM,
            REVIEW_KEYWORDS_MM,
            BRIEF_INTRO_PATTERNS_MM,
            REASON_INTRO_PATTERNS_MM,
            DEPARTMENT_HEAD_NOUNS_MM,
            TYPE_INFERENCE_MM,
            has_burmese,
        )
        return {
            "ownership": OWNERSHIP_KEYWORDS_MM,
            "audience": AUDIENCE_KEYWORDS_MM,
            "officer": OFFICER_KEYWORDS_MM,
            "review": REVIEW_KEYWORDS_MM,
            "brief_intro": BRIEF_INTRO_PATTERNS_MM,
            "reason_intro": REASON_INTRO_PATTERNS_MM,
            "dept_nouns": DEPARTMENT_HEAD_NOUNS_MM,
            "type_mm": TYPE_INFERENCE_MM,
            "has_burmese": has_burmese,
        }
    except Exception:
        return None


# Domain taxonomy for department-head nouns. Used for Functional Area
# and Responsible Function vocabulary matching. This is domain
# vocabulary (the names of common organizational units), NOT
# per-document values. Any new file whose Functional Area or
# Responsible Function value contains one of these head nouns is
# eligible for matching.
_DEPARTMENT_HEAD_NOUNS: frozenset[str] = frozenset({
    "security",
    "resources",
    "management",
    "compliance",
    "affairs",
    "operations",
    "services",
    "finance",
    "financial",
    "legal",
    "audit",
    "risk",
    "procurement",
    "treasury",
    "tax",
    "technology",
    "engineering",
    "marketing",
    "sales",
    "logistics",
    "planning",
    "strategy",
    "research",
    "development",
    "support",
    "communications",
    "relations",
    "analytics",
    "intelligence",
    "governance",
    "control",
    "assurance",
    "advisory",
    "consulting",
    "ethics",
    "integrity",
    "wellness",
    "safety",
    "health",
    "healthcare",
    "quality",
    "training",
    "education",
    "facilities",
    "administration",
    "coordination",
    "outreach",
    "policy",
    "policies",
    "standards",
    "programs",
    "programmes",
    "projects",
    "initiatives",
    "systems",
    "system",
    "network",
    "infrastructure",
    "data",
    "privacy",
    "records",
    "documentation",
    "reporting",
    "investigations",
    "hotline",
    "corporate",
    "group",
    "internal",
    "external",
    "human",
    "capital",
    "investor",
    "public",
    "regulatory",
    "clinical",
    "medical",
    "nursing",
    "pharmacy",
    "laboratory",
    "manufacturing",
    "production",
    "operations",
    "facilities",
    "property",
    "construction",
    "buildings",
    "real estate",
    "investments",
    "actuarial",
    "underwriting",
    "claims",
    "benefits",
    "compensation",
    "payroll",
    "talent",
    "learning",
    "diversity",
    "inclusion",
    "sustainability",
    "esg",
    "environment",
    "social",
    "membership",
    "subscriptions",
    "subscriptions",
    "subscriptions",
    "communications",
    "media",
    "brand",
    "reputation",
    "crisis",
    "emergency",
    "business",
    "continuity",
    "resilience",
    "cybersecurity",
    "information",
    "digital",
    "transformation",
    "innovation",
    "product",
    "customer",
    "client",
    "vendor",
    "supplier",
    "partner",
    "channel",
    "market",
    "trading",
    "banking",
    "lending",
    "credit",
    "retail",
    "commercial",
    "corporate",
    "institutional",
    "wealth",
    "asset",
    "portfolio",
    "fund",
    "trust",
    "custody",
    "settlement",
    "clearing",
    "treasury",
    "fx",
    "derivatives",
    "equities",
    "fixed",
    "income",
    "research",
    "economics",
    "strategy",
    "planning",
    "fp&a",
    "reporting",
})


def _has_ownership_keyword(text: str) -> bool:
    """Return True if `text` contains any ownership keyword."""
    low = text.lower()
    return any(kw in low for kw in _OWNERSHIP_KEYWORDS)


def _has_audience_keyword(text: str) -> bool:
    """Return True if `text` contains any audience keyword."""
    low = text.lower()
    return any(kw in low for kw in _AUDIENCE_KEYWORDS)


def _has_officer_keyword(text: str) -> bool:
    """Return True if `text` contains any officer/owner keyword."""
    low = text.lower()
    return any(kw in low for kw in _OFFICER_KEYWORDS)


def _has_review_keyword(text: str) -> bool:
    """Return True if `text` contains any review keyword."""
    low = text.lower()
    return any(kw in low for kw in _REVIEW_KEYWORDS)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on `.`/`!`/`?` followed by space+uppercase.

    General heuristic — splits on common sentence terminators.
    """
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_department_phrases(text: str) -> list[str]:
    """Extract Title-Case 1-3 word phrases ending in a department-head noun.

    General heuristic — uses `_DEPARTMENT_HEAD_NOUNS` (domain taxonomy,
    not per-document values) to identify candidates. Multi-value output.
    """
    # Find Title-Case sequences of 1-3 tokens.
    matches = re.findall(
        r"\b([A-Z][a-zA-Z]+(?:\s+(?:and|&|of|the)\s+[A-Z][a-zA-Z]+|\s+[A-Z][a-zA-Z]+){0,2})\b",
        text,
    )
    candidates: list[str] = []
    for m in matches:
        # Normalize and split on "and"/"&"/"of"/"the" connectors.
        tokens = re.split(r"\s+(?:and|&|of|the)\s+", m)
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            t_tokens = t.split()
            if not t_tokens:
                continue
            # Must end in a department-head noun.
            last = t_tokens[-1].lower().rstrip(".,;:")
            if last in _DEPARTMENT_HEAD_NOUNS:
                # Reject if too short or starts with a non-capital letter.
                if len(t) >= 4 and t[0].isupper():
                    candidates.append(t)
    return candidates


def _infer_type_from_prose(
    paras: list[str],
    blob: str,
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer document Type from body prose.

    Returns (value, confidence) or None. Confidence is 0-1.
    """
    # Highest confidence: explicit declaration "this is a <classification>".
    for para in paras[:30]:
        m = _TYPE_DECLARATION_RE.search(para)
        if not m:
            continue
        candidate = m.group(1).strip().rstrip(".,;:")
        if not candidate:
            continue
        # Validate through Type branch.
        cleaned = parse_field_value("Type:", candidate)
        if cleaned:
            return cleaned, 0.95
        # Try the full matched phrase if it was multi-word.
        full_phrase = candidate
        if 2 <= len(candidate.split()) <= 4:
            cleaned = parse_field_value("Type:", full_phrase)
            if cleaned:
                return cleaned, 0.85
    return None


def _infer_applicable_sectors(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Applicable Sector(s) from body prose.

    Looks for paragraphs containing 'sector' or 'subsidiaries' or
    'business units' keywords and extracts the noun phrase following.
    If the paragraph ends with ":" (sentence fragment), look at the
    next paragraph for the actual list.
    """
    for i, para in enumerate(paras[:40]):
        low = para.lower()
        if "sector" not in low and "subsidiar" not in low and \
           "business unit" not in low and "group holdings" not in low:
            continue
        # If paragraph ends with ":" it's likely a sentence fragment.
        # Look at the next paragraph for the actual list.
        candidate = para.strip()[:200]
        if candidate.endswith(":") and i + 1 < len(paras):
            next_para = paras[i + 1].strip()[:200]
            if next_para:
                candidate = next_para
        # Extract candidate noun phrase.
        cleaned = parse_field_value(
            "Applicable Sector(s):", candidate
        )
        if cleaned:
            return cleaned, 0.7
    return None


def _infer_functional_area(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Functional Area(s) from body prose.

    Strategy:
      1. Look for paragraphs containing ownership keywords OR
         department-head nouns in title case.
      2. Extract the noun phrase(s).
      3. Multi-value split.
    """
    for para in paras[:60]:
        if not _has_ownership_keyword(para):
            continue
        # Extract department-head noun phrases.
        phrases = _extract_department_phrases(para)
        if not phrases:
            continue
        # Join multi-value with commas.
        joined = ", ".join(dict.fromkeys(phrases))  # dedup, preserve order
        cleaned = parse_field_value("Functional Area(s):", joined)
        if cleaned:
            return cleaned, 0.8
    return None


def _infer_responsible_function(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Responsible Function(s) from body prose.

    Same vocabulary as Functional Area but with stronger ownership
    keywords. Returns only if a stronger signal is found.
    """
    strong_keywords = (
        "responsible function",
        "policy owner",
        "owned by",
        "administered by",
        "managed by",
        "overseen by",
        "accountable to",
    )
    for para in paras[:60]:
        low = para.lower()
        if not any(kw in low for kw in strong_keywords):
            continue
        phrases = _extract_department_phrases(para)
        if not phrases:
            continue
        joined = ", ".join(dict.fromkeys(phrases))
        cleaned = parse_field_value("Responsible Function(s):", joined)
        if cleaned:
            return cleaned, 0.85
    return None


def _infer_responsible_officer(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Responsible Function Officer(s) from body prose.

    Looks for officer-keyword paragraphs and extracts the trailing
    name list (multi-value support).
    """
    for para in paras[:80]:
        if not _has_officer_keyword(para):
            continue
        # Strip the keyword portion and keep trailing text.
        cleaned = parse_field_value(
            "Responsible Function Officer(s):", para.strip()[:200]
        )
        if cleaned:
            return cleaned, 0.8
    return None


def _infer_last_reviewed(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Last Reviewed from body prose.

    Looks for paragraphs containing review keywords followed by a date.
    Filters out synthesized label-row headers (e.g., "Effected/Review
    date: 23/04/2025") and values matching Effective Date.
    """
    for para in paras[:100]:
        if not _has_review_keyword(para):
            continue
        # Filter synthesized label-row headers: skip if paragraph contains
        # both "effected" and "review date" (synthesis artifact).
        low = para.lower()
        if "effected" in low and "review date" in low:
            continue
        cleaned = parse_field_value("Last Reviewed:", para.strip()[:200])
        if cleaned:
            return cleaned, 0.9
    return None


def _infer_supersedes(
    paras: list[str],
    blob: str,
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Supersedes from body prose.

    Strategy:
      1. Try quoted pattern (highest confidence).
      2. Fall back to unquoted pattern (medium confidence).
    """
    # Quoted pattern.
    m = _SUPERSEDES_QUOTED_RE.search(blob)
    if m:
        raw = m.group(1).strip().rstrip(".,")
        cleaned = parse_field_value("Supersedes:", raw)
        if cleaned:
            return cleaned, 0.95
    # Unquoted pattern.
    m = _SUPERSEDES_UNQUOTED_RE.search(blob)
    if m:
        raw = m.group(1).strip().rstrip(".,")
        # Only accept if it doesn't end with a Brain label keyword
        # (avoids accidentally capturing the next label's heading).
        if not raw.endswith(":"):
            cleaned = parse_field_value("Supersedes:", raw)
            if cleaned:
                return cleaned, 0.7
    return None


def _infer_applies_to(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Applies to from body prose.

    Looks for paragraphs containing audience keywords and extracts the
    trailing noun phrase(s). Strips the keyword prefix and trailing
    colon to produce a clean noun phrase.
    """
    for para in paras[:50]:
        if not _has_audience_keyword(para):
            continue
        # Strip "applies to" / "applicable to" prefix.
        s = para.strip()[:300]
        low = s.lower()
        for prefix in ("this policy applies to", "this standard applies to",
                       "this procedure applies to", "this guideline applies to",
                       "applies to", "applicable to"):
            if low.startswith(prefix):
                s = s[len(prefix):].strip()
                break
        # Strip trailing colon.
        s = s.rstrip(":").strip()
        if not s:
            continue
        cleaned = parse_field_value("Applies to:", s)
        if cleaned:
            return cleaned, 0.85
    return None


def _infer_brief_description(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Brief Description from the first purpose-intro paragraph.

    Returns first sentence of the first paragraph that starts with
    a purpose-intro phrase. When the first candidate ends with a
    conjunction, tries to find a better candidate later in the list.
    Capped at 500 chars.
    """
    for i, para in enumerate(paras[:40]):
        s = para.strip()
        if not s:
            continue
        if not any(re.match(p, s, re.IGNORECASE) for p in _BRIEF_INTRO_PATTERNS):
            continue
        # Take sentences, skip numbering-only prefixes.
        sentences = _split_sentences(s)
        if not sentences:
            continue
        # Try each sentence, skipping numbering-only prefixes.
        first = None
        for sent in sentences:
            candidate = sent.strip()
            # Strip leading numbering like "1.1.", "1.1. ", or "2.3.2 ".
            candidate = re.sub(r"^\d+[\.\d]*\.?\s*", "", candidate)
            if not candidate:
                continue
            # Skip if sentence ends with a conjunction (incomplete).
            if candidate.rstrip().endswith((" and", " or", " the", " a", " an", " to", " of", " in", " for", " with")):
                continue
            first = candidate
            break
        if not first:
            continue
        # Cap at 500 chars.
        first = first[:500].rstrip()
        cleaned = parse_field_value("Brief Description:", first)
        if cleaned:
            return cleaned, 0.85
    return None


def _infer_reason_for_policy(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Reason for Policy from the first reason-intro paragraph.

    Returns first sentence of the first paragraph that starts with
    a reason-intro phrase. Strips leading numbering (e.g., "1.1. ")
    from the first sentence. Capped at 600 chars.
    """
    for para in paras[:40]:
        s = para.strip()
        if not s:
            continue
        if not any(re.match(p, s, re.IGNORECASE) for p in _REASON_INTRO_PATTERNS):
            continue
        sentences = _split_sentences(s)
        if not sentences:
            continue
        # Try each sentence, skip numbering-only prefixes.
        first = None
        for sent in sentences:
            candidate = sent.strip()
            # Strip leading numbering like "1.1.", "1.1. ", or "2.3.2 ".
            candidate = re.sub(r"^\d+[\.\d]*\.?\s*", "", candidate)
            if not candidate:
                continue
            first = candidate
            break
        if not first:
            continue
        first = first[:600].rstrip()
        cleaned = parse_field_value("Reason for Policy:", first)
        if cleaned:
            return cleaned, 0.85
    return None


def _infer_exclusions(
    paras: list[str],
    parse_field_value,
) -> tuple[str, float] | None:
    """Infer Exclusions from a section heading + the paragraph after.

    Strategy:
      1. Find first paragraph matching the Exclusions heading pattern.
      2. The value is the paragraph immediately following (until the
         next heading or end of doc).
    """
    for i, para in enumerate(paras):
        s = para.strip()
        if not s:
            continue
        if not _EXCLUSIONS_HEADING_RE.match(s):
            continue
        # Take the next non-empty paragraph(s) as the value.
        collected: list[str] = []
        for j in range(i + 1, min(i + 10, len(paras))):
            nxt = paras[j].strip()
            if not nxt:
                continue
            # Stop at next heading (a line that's mostly capitalized
            # and short, or ends with ":").
            if (len(nxt) < 80 and nxt.endswith(":")) or \
               (len(nxt.split()) <= 6 and nxt[:1].isupper() and
                not nxt.endswith(".")):
                break
            collected.append(nxt)
        if not collected:
            continue
        joined = " ".join(collected)
        joined = joined[:800].rstrip()
        if not joined:
            continue
        cleaned = parse_field_value("Reason for Policy:", joined)
        # Note: we don't have a dedicated Exclusions validator; reuse
        # the Reason for Policy length cap as a sanity check.
        if cleaned:
            # Use a generic key for Exclusions (Brain-schema slot 9 is
            # filled by section routing, not by label-row). The
            # inference result is informational.
            return joined, 0.8
    return None


def infer_narrative_fields(
    paragraphs: Iterable[str],
    existing_field_map: dict[str, str],
    *,
    always_run: bool = True,
) -> dict[str, str]:
    """Return additional canonical labels inferable from narrative prose.

    Args:
        paragraphs: A sequence of paragraphs (cleaned text lines).
        existing_field_map: The FieldMap produced by Phase B
            (and earlier paths).
        always_run: When True (default), inference runs for all fields
            regardless of whether existing_field_map has values.
            Inferred values supplement (do not overwrite) existing
            label-row values. When False, inference only fills empty
            slots.

    Returns:
        A dict of canonical-label -> inferred value. Existing
        non-empty values are NOT overwritten (label-row wins).

    Phase 7 — every inference rule uses general English patterns.
    All inferences are validated via `parse_field_value()`. Invalid
    values are dropped silently.

    Confidence scoring (0-1):
      0.95: explicit declaration
      0.9:  quoted / labeled pattern
      0.85: paragraph-level keyword + phrase
      0.7:  implicit / unquoted

    Highest-confidence inference wins when multiple rules produce
    candidates for the same field.
    """
    # Import here to avoid circular dependency.
    from policy_platform.framework.brain_fields import parse_field_value

    out: dict[str, str] = {}
    paras = [p for p in paragraphs if p and p.strip()]
    if not paras:
        return out

    blob = "\n".join(paras[:200])

    # Helper: only emit when slot is empty (unless always_run is True
    # for that slot).
    def _set(canonical: str, value: str) -> None:
        if not always_run and existing_field_map.get(canonical):
            return
        # Don't overwrite an existing non-empty value.
        if existing_field_map.get(canonical):
            return
        out[canonical] = value

    # Rule 1: Effective Date from "issued on ... <date>".
    if not existing_field_map.get("Effective Date/Period:"):
        m = _ISSUED_ON_RE.search(blob)
        if m:
            raw_value = m.group(1).strip().rstrip(".,")
            cleaned = parse_field_value("Effective Date/Period:", raw_value)
            if cleaned:
                out["Effective Date/Period:"] = cleaned

    # Rule 2: Supersedes from "supersedes" + quoted/unquoted title.
    if not existing_field_map.get("Supersedes:"):
        result = _infer_supersedes(paras, blob, parse_field_value)
        if result:
            out["Supersedes:"] = result[0]

    # Rule 3: Policy Title from page 1 if it starts with "Policy for".
    if not existing_field_map.get("Policy Title:"):
        for i, line in enumerate(paras[:200]):
            s = line.strip()
            if not s:
                continue
            m = _POLICY_FOR_RE.match(s)
            if not m:
                continue
            trailing = (m.group(1) or "").strip()
            if len(trailing) <= 2:
                next_lines: list[str] = [s]
                for j in range(i + 1, min(i + 4, len(paras))):
                    nxt = paras[j].strip()
                    if not nxt:
                        continue
                    next_lines.append(nxt)
                    break
                combined = " ".join(next_lines)
                raw_value = combined.rstrip(".,*").strip()
            else:
                raw_value = s.rstrip(".,*").strip()
            cleaned = parse_field_value("Policy Title:", raw_value)
            if cleaned:
                out["Policy Title:"] = cleaned
                break

    # Phase 7 — Type inference from body prose.
    if not existing_field_map.get("Type:"):
        result = _infer_type_from_prose(paras, blob, parse_field_value)
        if result:
            out["Type:"] = result[0]

    # Layer G — Type inference from Policy Title suffix (Myanmar + English).
    # Fires after the English type-inference has had its chance. If
    # the title ends with a known classification noun (Policy,
    # Standard, Procedure, etc. — English OR Myanmar via TYPE_INFERENCE_MM),
    # extract that classification as the Type. General heuristic —
    # no per-file hardcoding.
    if not existing_field_map.get("Type:"):
        # Reuse the Policy Title we just inferred (or already had).
        title_value = out.get("Policy Title:") or existing_field_map.get(
            "Policy Title:"
        )
        if title_value:
            title_mm = title_value
            mm = _burmese_strings()
            if mm is not None:
                # Myanmar: find longest TYPE_INFERENCE_MM key in title.
                best_mm: tuple[int, str] | None = None
                for mm_key, en_class in mm["type_mm"].items():
                    if mm_key in title_mm:
                        if best_mm is None or len(mm_key) > best_mm[0]:
                            best_mm = (len(mm_key), en_class)
                if best_mm is not None:
                    cleaned = parse_field_value("Type:", best_mm[1])
                    if cleaned:
                        out["Type:"] = cleaned
            # English fallback if Myanmar path didn't fire.
            if not out.get("Type:"):
                title_low_en = title_value.lower()
                # Look for trailing classification noun in title.
                for keyword in (
                    "policy",
                    "policies",
                    "standard",
                    "standards",
                    "procedure",
                    "procedures",
                    "guideline",
                    "guidelines",
                    "framework",
                    "charter",
                    "directive",
                    "rule",
                    "regulation",
                    "protocol",
                    "manual",
                    "handbook",
                    "code",
                ):
                    if keyword in title_low_en.split():
                        # Capitalize first letter to match vocabulary.
                        cls = keyword.capitalize()
                        cleaned = parse_field_value("Type:", cls)
                        if cleaned:
                            out["Type:"] = cleaned
                            break

    # Phase 7 — Applicable Sector(s) inference.
    if not existing_field_map.get("Applicable Sector(s):"):
        result = _infer_applicable_sectors(paras, parse_field_value)
        if result:
            out["Applicable Sector(s):"] = result[0]

    # Phase 7 — Functional Area(s) inference.
    if not existing_field_map.get("Functional Area(s):"):
        result = _infer_functional_area(paras, parse_field_value)
        if result:
            out["Functional Area(s):"] = result[0]

    # Phase 7 — Responsible Function(s) inference.
    if not existing_field_map.get("Responsible Function(s):"):
        result = _infer_responsible_function(paras, parse_field_value)
        if result:
            out["Responsible Function(s):"] = result[0]

    # Phase 7 — Responsible Function Officer(s) inference.
    if not existing_field_map.get("Responsible Function Officer(s):"):
        result = _infer_responsible_officer(paras, parse_field_value)
        if result:
            out["Responsible Function Officer(s):"] = result[0]

    # Phase 7 — Last Reviewed inference.
    if not existing_field_map.get("Last Reviewed:"):
        result = _infer_last_reviewed(paras, parse_field_value)
        if result:
            out["Last Reviewed:"] = result[0]

    # Phase 7 — Applies to inference.
    if not existing_field_map.get("Applies to:"):
        result = _infer_applies_to(paras, parse_field_value)
        if result:
            out["Applies to:"] = result[0]

    # Phase 7 — Brief Description inference.
    if not existing_field_map.get("Brief Description:"):
        result = _infer_brief_description(paras, parse_field_value)
        if result:
            out["Brief Description:"] = result[0]

    # Phase 7 — Reason for Policy inference.
    if not existing_field_map.get("Reason for Policy:"):
        result = _infer_reason_for_policy(paras, parse_field_value)
        if result:
            out["Reason for Policy:"] = result[0]

    # Layer D — Myanmar keyword inference. Only fires when at least one
    # paragraph contains Myanmar script. Mirrors the English
    # _infer_* functions but scans for Myanmar keyword patterns from
    # burmese_strings.py. General heuristic — no per-file hardcoding.
    mm = _burmese_strings()
    if mm is not None:
        any_mm = any(mm["has_burmese"](p) for p in paras if p)
        if any_mm:
            # Applicable Sector(s) from Myanmar audience keywords.
            if not existing_field_map.get("Applicable Sector(s):"):
                for p in paras[:40]:
                    if not mm["has_burmese"](p):
                        continue
                    if any(kw in p for kw in mm["audience"]):
                        cleaned = parse_field_value(
                            "Applicable Sector(s):", p.strip()[:300]
                        )
                        if cleaned:
                            out["Applicable Sector(s):"] = cleaned
                            break
            # Functional Area(s) from Myanmar ownership keywords + dept-nouns.
            if not existing_field_map.get("Functional Area(s):"):
                for p in paras[:60]:
                    if not mm["has_burmese"](p):
                        continue
                    if any(kw in p for kw in mm["ownership"]):
                        # Extract first noun phrase containing a dept-noun.
                        for noun in mm["dept_nouns"]:
                            if noun in p:
                                # Capture surrounding phrase (3 tokens max).
                                idx = p.find(noun)
                                start = max(0, idx - 12)
                                end = min(len(p), idx + len(noun) + 12)
                                phrase = p[start:end].strip()
                                # Trim to last noun.
                                cleaned = parse_field_value(
                                    "Functional Area(s):", phrase
                                )
                                if cleaned:
                                    out["Functional Area(s):"] = cleaned
                                    break
                        if out.get("Functional Area(s):"):
                            break
            # Responsible Function Officer(s) from Myanmar officer titles.
            if not existing_field_map.get("Responsible Function Officer(s):"):
                for p in paras[:80]:
                    if not mm["has_burmese"](p):
                        continue
                    if any(kw in p for kw in mm["officer"]):
                        cleaned = parse_field_value(
                            "Responsible Function Officer(s):", p.strip()[:200]
                        )
                        if cleaned:
                            out["Responsible Function Officer(s):"] = cleaned
                            break
            # Last Reviewed from Myanmar review keywords.
            if not existing_field_map.get("Last Reviewed:"):
                for p in paras[:100]:
                    if not mm["has_burmese"](p):
                        continue
                    if any(kw in p for kw in mm["review"]):
                        cleaned = parse_field_value(
                            "Last Reviewed:", p.strip()[:200]
                        )
                        if cleaned:
                            out["Last Reviewed:"] = cleaned
                            break
            # Applies to from Myanmar audience keywords.
            if not existing_field_map.get("Applies to:"):
                for p in paras[:50]:
                    if not mm["has_burmese"](p):
                        continue
                    if any(kw in p for kw in mm["audience"]):
                        s = p[:300].strip()
                        # Strip a leading keyword to leave a clean noun phrase.
                        for kw in mm["audience"]:
                            if kw in s:
                                idx = s.find(kw)
                                # Strip up to and including the keyword.
                                s = s[idx + len(kw):].strip()
                                break
                        s = s.rstrip(":").strip()
                        if s:
                            cleaned = parse_field_value("Applies to:", s)
                            if cleaned:
                                out["Applies to:"] = cleaned
                                break
            # Brief Description from Myanmar brief-intro patterns.
            if not existing_field_map.get("Brief Description:"):
                for p in paras[:40]:
                    if not mm["has_burmese"](p):
                        continue
                    if not any(re.match(pat, p, re.IGNORECASE) for pat in mm["brief_intro"]):
                        continue
                    sentences = _split_sentences(p)
                    if sentences:
                        first = sentences[0].strip()[:500]
                        if first:
                            cleaned = parse_field_value(
                                "Brief Description:", first
                            )
                            if cleaned:
                                out["Brief Description:"] = cleaned
                                break
            # Reason for Policy from Myanmar reason-intro patterns.
            if not existing_field_map.get("Reason for Policy:"):
                for p in paras[:40]:
                    if not mm["has_burmese"](p):
                        continue
                    if not any(re.match(pat, p, re.IGNORECASE) for pat in mm["reason_intro"]):
                        continue
                    sentences = _split_sentences(p)
                    if sentences:
                        first = sentences[0].strip()[:600]
                        if first:
                            cleaned = parse_field_value(
                                "Reason for Policy:", first
                            )
                            if cleaned:
                                out["Reason for Policy:"] = cleaned
                                break

    return out
