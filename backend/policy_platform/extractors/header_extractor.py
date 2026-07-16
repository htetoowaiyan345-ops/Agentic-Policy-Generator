"""Extract policy title and version tag from cleaned PDF text.

Heuristic, deliberately simple and robust:

1. **PDF metadata title** (`dc:title` from PDF properties) — when present
   this is by far the most reliable source.
2. **Largest non-empty line on page 1** — chosen by character count and
   not a bullet/running header. Long titles and short titles both work.
3. **First non-empty line** — last-resort fallback when nothing else fits.

The version tag is matched against common patterns:
  - CL&H_02/24
  - FY25-26
  - v1.0 / Version 1.0
  - Rev. 3

Returns a dict with the policy title and version tag (either field may
be None).
"""
from __future__ import annotations

import re
from typing import Any


# Heuristic regex for version labels
_VERSION_PATTERNS = (
    re.compile(r"\b(?:CL&[A-Z]_\d+/\d+)\b"),
    re.compile(r"\b(?:FY\d{2}-\d{2})\b", re.IGNORECASE),
    re.compile(r"\b(?:Rev\.?\s*\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\b(?:v(?:er(?:sion)?)?\.?\s*\d+(?:\.\d+)?)\b", re.IGNORECASE),
)


_BULLET_PREFIX_RE = re.compile(r"^\s*[•\-\*\u2022\u2023\u2043\u204C\u204D]\s*")
_PUNCT_ONLY = re.compile(r"^[\s\W_]+$")


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_PREFIX_RE.match(line))


def _strip_trailing_bracket(line: str) -> str:
    """Strip closing bracket / page marker artifacts from the end of a line."""
    return re.sub(r"\s*\[[^\]]{1,40}\]\s*$", "", line).strip()


def _score_title(line: str, position: int = 0) -> float:
    """Higher score = more likely to be the policy title.

    Scoring factors:
      1. Length: longer is better (titles are typically 20-80 chars).
      2. ALL-CAPS bonus: +60.
      3. **Position bonus**: lines earlier in the document are
         preferred (titles appear at the top). First 5 lines get a
         bonus; later lines get a penalty proportional to position.
      4. Penalty for lines that look like `Label: value` pairs
         (start with a known Brain label followed by a colon).
         These are NOT titles — they're content lines.
      5. Penalty for very long lines (likely content paragraphs).
    """
    s = line.strip()
    if not s:
        return -1.0
    if _PUNCT_ONLY.match(s):
        return -1.0
    if _is_bullet(s):
        return -1.0
    if len(s) < 8 or len(s) > 200:
        return -1.0
    # Penalty: line starts with `Label:` pattern (likely a content line).
    if _looks_like_label_value(s):
        return -1.0
    # Title Case bonus.
    score = len(s)
    if s == s.upper() and any(c.isalpha() for c in s):
        score += 60
    # Penalize very long lines (likely content, not title).
    if len(s) > 120:
        score -= 80
    # Position bonus: prefer lines earlier in the document.
    if position < 3:
        score += 100  # Strong bonus for top 3 lines
    elif position < 6:
        score += 50
    else:
        score -= position * 5  # Decay for later lines
    return score


def _looks_like_label_value(s: str) -> bool:
    """True if `s` looks like `Label: value` (Brain label followed by
    a value with a colon).

    Used to filter out content lines that shouldn't be picked as
    the policy title. Catches both:
      - Label-row labels (Type:, Policy Title:, Effective Date: ...)
        from `BRAIN_LABEL_ROWS`.
      - Section-title labels (Introduction:, Purpose:, Scope and
        Beneficiaries:, Definitions:, History: ...) from
        `SECTION_HEADING_SYNONYMS`.
    """
    import re as _re
    from policy_platform.framework.brain_fields import BRAIN_LABEL_ROWS
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS
    # Build a flat set of known label names (with trailing colon).
    known: set[str] = set()
    for canonical, syns in BRAIN_LABEL_ROWS:
        known.add(canonical.rstrip(":").lower())
        for syn in syns:
            known.add(syn.rstrip(":").lower())
    for sid, syns in SECTION_HEADING_SYNONYMS.items():
        for syn in syns:
            known.add(syn.rstrip(":").lower())
    # Match a label at start, followed by `:` and a value.
    m = _re.match(
        r"^\s*([A-Za-z][A-Za-z0-9 ()/&.,'\-_]*?)\s*[:\t]\s*\S",
        s,
    )
    if not m:
        return False
    label = m.group(1).strip().lower()
    return label in known


def _match_version(text: str) -> str | None:
    for pat in _VERSION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def extract(input_path, pdf_metadata: dict[str, Any] | None = None,
            cleaned_paragraphs: list[str] | None = None) -> dict[str, str | None]:
    """Returns dict with keys: title, version, source.

    Args:
      input_path: Path to the source file. Used for fallback name.
      pdf_metadata: optional dict from PDF parsing — look for /Title.
      cleaned_paragraphs: optional list of cleaned first-page lines.
                         Pass [] when content is empty.
    """
    title: str | None = None
    version: str | None = None
    source: str = "fallback"

    # 1) PDF metadata title
    if pdf_metadata and pdf_metadata.get("title"):
        candidate = str(pdf_metadata["title"]).strip()
        if 8 <= len(candidate) <= 200 and not _PUNCT_ONLY.match(candidate):
            title = candidate
            version = _match_version(candidate)
            source = "pdf_metadata"

    # 2) Largest non-bullet non-punct line in first ~30 cleaned paragraphs
    if title is None and cleaned_paragraphs:
        best = None
        best_score = -1.0
        for idx, line in enumerate(cleaned_paragraphs[:60]):
            s = line.strip()
            if not s:
                continue
            score = _score_title(s, position=idx)
            if score > best_score:
                best = s
                best_score = score
        if best is not None and best_score > 0:
            version = _match_version(best)
            # Strip version from title if present
            cleaned_title = best
            if version:
                cleaned_title = re.sub(re.escape(version), "", cleaned_title).strip()
            cleaned_title = _strip_trailing_bracket(cleaned_title)
            if 4 <= len(cleaned_title) <= 200:
                title = cleaned_title
                source = "first_page_largest"

    # 3) Last-resort fallback: filename stem
    if title is None:
        try:
            stem = input_path.stem.replace("_", " ").replace("-", " ").strip()
            if 4 <= len(stem) <= 200:
                title = stem
                version = _match_version(stem)
                source = "filename"
        except Exception:
            pass

    return {"title": title, "version": version, "source": source}
