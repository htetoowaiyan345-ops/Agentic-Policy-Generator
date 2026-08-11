"""Optional spaCy-based label extraction (opt-in via AGENTIC_POLICY_USE_SPACY=1).

When the environment variable `AGENTIC_POLICY_USE_SPACY=1` is set AND spaCy
is importable, `extract_field_map` runs sentence segmentation on the input
paragraphs, then matches each sentence against the Brain synonym dict.

When spaCy is absent or the env var is unset, `is_available()` returns False
and `extract_field_map` raises `SpaCyUnavailable`. Callers (the field_parser
in Stage 5) must check `is_available()` first and fall back to the regex path.

This module deliberately depends on `policy_platform.framework.brain_fields`
for the label/synonym table so that:
  - The spaCy path uses the SAME dictionary as the regex path.
  - Stage 2 (synonym additions) automatically benefits both paths.
"""
from __future__ import annotations

import os
from typing import Iterable

from policy_platform.framework.brain_fields import (
    BRAIN_LABEL_ROWS,
    canonical_label,
    parse_field_value,
)


def is_available() -> bool:
    """Return True iff the env var is set AND spaCy imports cleanly.

    The env var is a deliberate opt-in so that production environments
    without spaCy installed keep working unchanged via the regex path.
    """
    if os.environ.get("AGENTIC_POLICY_USE_SPACY", "0") != "1":
        return False
    try:
        import spacy  # noqa: F401  (probe-only; we re-import in extract_field_map)
    except ImportError:
        return False
    try:
        import spacy  # noqa: F401

        spacy.load("en_core_web_sm")
    except Exception:
        return False
    return True


class SpaCyUnavailable(RuntimeError):
    """Raised when spaCy is requested but not available."""


_NLP = None  # type: ignore[var-annotated]


def _get_nlp():
    """Lazy-load the spaCy pipeline once per process."""
    global _NLP
    if _NLP is None:
        import spacy

        _NLP = spacy.load("en_core_web_sm")
    return _NLP


def _build_norm_lookup() -> dict[str, str]:
    """Build {normalized_label_text: canonical_label} for fast lookup.

    Includes both canonical labels and all synonyms, all normalized
    via the same `_norm`-style algorithm used by `canonical_label()`.
    Without synonyms, the matcher would miss user variants like
    `Policy Type:` or `Effective Date:`.
    """
    import re as _re

    norm_re = _re.compile(r"[^a-z0-9]+")
    out: dict[str, str] = {}

    def _norm(s: str) -> str:
        return norm_re.sub("", s.casefold()).rstrip(":")

    for canonical, syns in BRAIN_LABEL_ROWS:
        key = _norm(canonical)
        if key:
            out[key] = canonical
        for syn in syns:
            s_key = _norm(syn)
            if s_key and s_key not in out:
                out[s_key] = canonical
    return out


_NORM_LOOKUP: dict[str, str] | None = None


def _get_norm_lookup() -> dict[str, str]:
    global _NORM_LOOKUP
    if _NORM_LOOKUP is None:
        _NORM_LOOKUP = _build_norm_lookup()
    return _NORM_LOOKUP


def _split_label_value(sentence: str) -> tuple[str, str] | None:
    """Given a sentence, return (canonical_label, value) or None.

    Strategy:
      1. Split on first `:` or `\t` or ` -` followed by something.
      2. If the LHS, normalized, matches a synonym, return canonical_label.
         The value is the RHS stripped.
      3. Otherwise return None — fall back to regex path.
    """
    import re as _re

    m = _re.match(r"^\s*([^:\t][^:\t]{0,80}?)\s*[:\t]\s*(.+?)\s*$", sentence)
    if not m:
        return None
    lhs = m.group(1).strip()
    rhs = m.group(2).strip()
    if not lhs or not rhs:
        return None
    canon = canonical_label(lhs + ":")
    if canon is None:
        return None
    return canon, rhs


def extract_field_map(
    input_paragraphs: Iterable[str],
) -> tuple[dict[str, str], str]:
    """Run spaCy sentence segmentation and return (field_map, extraction_path).

    Args:
        input_paragraphs: A sequence of paragraphs (one PDF page may emit
            one string per visual line).

    Returns:
        A 2-tuple (field_map, extraction_path) where:
          - field_map is {canonical_label: value}
          - extraction_path is one of `'spacy'` (full run) or
            `'spacy-fallback'` (spaCy loaded but matched zero sentences).

    Raises:
        SpaCyUnavailable: if spaCy is missing or the env var is unset.
    """
    if not is_available():
        raise SpaCyUnavailable(
            "spaCy extraction requested but unavailable. "
            "Set AGENTIC_POLICY_USE_SPACY=1 and `pip install spacy` "
            "+ `python -m spacy download en_core_web_sm`."
        )

    nlp = _get_nlp()
    paragraphs = [p for p in input_paragraphs if p and p.strip()]
    if not paragraphs:
        return {}, "spacy-fallback"

    text = "\n\n".join(paragraphs)
    doc = nlp(text)

    field_map: dict[str, str] = {}
    matched = 0

    for sent in doc.sents:
        sentence_text = sent.text.strip()
        if not sentence_text:
            continue
        split = _split_label_value(sentence_text)
        if split is None:
            continue
        canonical, value = split
        # Phase 6: validate the value via field-specific rules.
        cleaned = parse_field_value(canonical, value)
        if cleaned is None:
            continue
        matched += 1
        if canonical not in field_map or (
            not field_map[canonical] and cleaned
        ):
            field_map[canonical] = cleaned

    extraction_path = "spacy" if matched > 0 else "spacy-fallback"
    return field_map, extraction_path
