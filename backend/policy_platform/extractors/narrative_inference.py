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

These rules are **defensive and conservative**: they only fire if
the corresponding canonical label is still missing from the field
map produced by Phase B (and earlier paths).
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
# Conservative: only when the next clause is quoted.
_SUPERSEDES_QUOTED_RE = re.compile(
    r'\bsupersedes?\s+(?:the\s+)?["\u201c]([^"\u201d]{3,200})["\u201d]',
    re.IGNORECASE,
)


# Pattern: "Policy for X" / "Policy Title" suggested by page 1 line.
# Apply only when there's no other Policy Title: candidate already.
# Allows: literal "Policy for" / "Sexual Harassment Policy for" with
# either (a) trailing title text or (b) no trailing text (multi-line
# title continues on next paragraph).
_POLICY_FOR_RE = re.compile(
    r"^(?:Policy\s+for\s*|Sexual\s+Harassment\s+Policy\s+for\s*)([A-Za-z0-9 \-_/&;.,()'\":*]{0,300})$",
    re.IGNORECASE,
)


def infer_narrative_fields(
    paragraphs: Iterable[str],
    existing_field_map: dict[str, str],
) -> dict[str, str]:
    """Return additional canonical labels inferable from narrative prose.

    Args:
        paragraphs: A sequence of paragraphs (cleaned text lines).
        existing_field_map: The FieldMap produced by Phase B
            (and earlier paths). Already-resolved keys are not
            overwritten.

    Returns:
        A dict of NEW canonical-label -> inferred value. Only fires
        for labels where `existing_field_map` is missing a value.
    """
    out: dict[str, str] = {}
    paras = [p for p in paragraphs if p and p.strip()]
    if not paras:
        return out

    # Combine the first ~60 paragraphs into one big string for prose search.
    # This is necessary because "Document issued on..." might span lines.
    blob = "\n".join(paras[:200])

    # Rule 1: Effective Date from "issued on ... <date>".
    if "Effective Date/Period:" not in existing_field_map:
        m = _ISSUED_ON_RE.search(blob)
        if m:
            out["Effective Date/Period:"] = m.group(1).strip().rstrip(".,")

    # Rule 2: Supersedes from "supersedes" + quoted previous title.
    if "Supersedes:" not in existing_field_map:
        m = _SUPERSEDES_QUOTED_RE.search(blob)
        if m:
            out["Supersedes:"] = m.group(1).strip().rstrip(".,")

    # Rule 3: Policy Title from page 1 if it starts with "Policy for"
    # or "Sexual Harassment Policy for" and there's no Policy Title: yet.
    # Some titles span 2-3 lines on page 1 — so we look at a 1-2 line window.
    if "Policy Title:" not in existing_field_map:
        for i, line in enumerate(paras[:200]):
            s = line.strip()
            if not s:
                continue
            m = _POLICY_FOR_RE.match(s)
            if not m:
                continue
            # Group 1 is the trailing text after "Policy for". If it's
            # empty (only 0-or-few chars after the prefix), this is a
            # multi-line title — join with the next non-empty line.
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
                out["Policy Title:"] = combined.rstrip(".,*").strip()
                break
            # Single-line case.
            out["Policy Title:"] = s.rstrip(".,*").strip()
            break

    return out
