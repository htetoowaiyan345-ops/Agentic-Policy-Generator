"""Burmese heading-aware paragraph pipeline.

This module holds the Burmese-specific heading and section-detection
logic that was previously inlined in the English rag pipeline
(``heading_anchors.py`` / ``section_detector.py`` /
``retrieval_pipeline.py`` / ``extractors/__init__.py``). It is split
out so the English pipeline runs unchanged for ``lang="en"`` docs.

Surface area:
  - ``is_burmese_lang(lang)`` : detect Burmese-active lang codes.
  - ``looks_like_burmese_heading(line)`` : heuristic Burmese section
    heading detector.
  - ``split_paragraphs_on_burmese_headings(lines)`` : split dense
    paragraphs at inline Burmese section headings.
  - ``get_burmese_heading_patterns(slot_id, *, lang="en")`` : augmented
    heading-anchor patterns for Burmese/mixed docs.
  - ``compile_heading_patterns_for_lang(slot_id, base_patterns, lang)`` :
    augment English patterns with Burmese patterns when lang is
    Burmese-active.
  - ``_has_burmese_slot_synonym_in_first_line(paragraphs, slot_name)`` :
    complement to English section-marker regexes.
  - ``burmese_section_marker_check(paragraphs, slot_name)`` : dispatch
    helper for the 5 Burmese section marker functions.
"""
from __future__ import annotations

import re
from typing import Iterable


# ---------------------------------------------------------------------------
# Lang detection helper
# ---------------------------------------------------------------------------
def is_burmese_lang(lang: str | None) -> bool:
    """True if the document language is Burmese or mixed.

    ``detect_document_lang`` may return ``"my"``, ``"mixed"``,
    ``"en-my"``, or ``"my-en"``. We treat any of these as
    Burmese-active so Burmese heading patterns and section markers
    apply.
    """
    if not lang:
        return False
    s = lang.lower()
    if "my" in s:
        return True
    return "burmese" in s or "mixed" in s


# ---------------------------------------------------------------------------
# Burmese heading character helpers
# ---------------------------------------------------------------------------
def is_burmese_char(ch: str) -> bool:
    """True if ``ch`` is a Burmese Unicode character (U+1000-U+109F)."""
    if not ch:
        return False
    return "\u1000" <= ch <= "\u109F"


def looks_like_mid_sentence(text: str) -> bool:
    """Heuristic: True if text looks like the middle of a sentence.

    Used to reject Burmese false positives where the heading word
    appears in the middle of a sentence rather than at the start of a
    heading. Signals: long length, mid-sentence punctuation, no
    Burmese period.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) > 200:
        return True
    if re.search(r"\.\s+[A-Za-z\u1000-\u109F]", stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# Burmese heading detector
# ---------------------------------------------------------------------------
def looks_like_burmese_heading(line: str) -> bool:
    """Heuristic Burmese section heading detector.

    Returns True for lines that look like Burmese section headings:
    - Line starts with Myanmar digit(s) + Burmese/ASCII period + Burmese
      text (e.g. ``"၁။ နိ�ါန်း"``, ``"၂-၂� အကျိုးခံစားခွင့်"``).
    - Short Burmese-only line (<= 60 chars, no English/digit/colon/period).
    - Line matches a known Burmese heading synonym exactly.
    """
    if not line:
        return False
    stripped = line.strip()
    if not stripped:
        return False
    # Myanmar digit prefix + Burmese text. Accept `။` (U+104B), `.`, or `#`
    # (Tesseract OCR often mis-recognizes the tiny Burmese period as
    # `#` when it immediately follows Myanmar digits). The `#` fallback
    # is gated on Myanmar content present elsewhere in the document;
    # English-only docs use `.` or `:` not `#` for section markers.
    if re.match(r"^\s*[\u1040-\u1049]+[\u104B.#]\s*[\u1000-\u109F]", stripped):
        return True
    # Short Burmese-only line, no body punctuation
    has_burmese = any("\u1000" <= ch <= "\u109F" for ch in stripped)
    if not has_burmese:
        return False
    if len(stripped) > 60:
        return False
    body_chars = [ch for ch in stripped if not ch.isspace()]
    if any(
        ch.isascii() and (ch.isalnum() or ch in ":-.,()#") and ch != "\u104B"
        for ch in body_chars
    ):
        return False
    # Known Burmese heading synonyms — loaded dynamically from the
    # burmese_synonyms YAML so the hardcoded set stays in sync with the
    # canonical synonym list. Falls back to the legacy hardcoded set
    # if the loader is unavailable.
    try:
        from ..i18n.burmese_synonyms import get_all_burmese_synonyms
        all_syns = get_all_burmese_synonyms()
        flat = []
        for syns in all_syns.values():
            flat.extend(syns)
        if stripped in flat:
            return True
    except Exception:
        pass
    # Legacy hardcoded set (kept as a safety net; the YAML loader
    # may be unavailable in some test contexts).
    BURMESE_HEADINGS = {
        "နိဒါန်း", "မူဝါဒ", "ရည်ရွယ်ချက်", "ရည်ရွယ်",
        "နယ်ပယ်", "အကျိုးခံစားခွင့်", "ချွင်းချက်များ", "ချွင်းချက်",
        "အဓိပ္ပါယ်", "အဓိပ္ပါယ်ဖော်ပြချက်", "ဆက်စပ်မူဝါဒများ",
        "ဆက်စပ်မူဝါဒ", "သမိုင်း", "မူဝါဒပြန်လည့်စစ်ဆေးခြင်း",
        "ပြန်လည့်စစ်ဆေးခြင်း",
    }
    if stripped in BURMESE_HEADINGS:
        return True
    return False


# ---------------------------------------------------------------------------
# Burmese heading patterns (heading-anchor augmentation)
# ---------------------------------------------------------------------------
_BURMESE_COMPILED_HEADINGS_CACHE: dict[int, list[re.Pattern]] = {}


def get_burmese_heading_patterns(slot_id: int) -> list[re.Pattern]:
    """Build heading patterns from Burmese synonyms for ``slot_id``.

    Patterns include an optional Myanmar digit prefix. Used for
    Burmese/mixed-lang docs to enable heading-anchor matching of
    section headings like ``"၁။ �ိဒါန်း"``.
    """
    if slot_id in _BURMESE_COMPILED_HEADINGS_CACHE:
        return _BURMESE_COMPILED_HEADINGS_CACHE[slot_id]
    patterns: list[str] = []
    try:
        from ..i18n.burmese_synonyms import get_burmese_synonyms
        syns = get_burmese_synonyms(slot_id)
    except Exception:
        syns = []
    # Separator accepts `။` (U+104B), `.`, `:`, `-`, or `#` (Tesseract
    # OCR may mis-read the Burmese period as ASCII hash when it follows
    # Myanmar digits).
    sep = r"(?:[:\-.\ufffd#]|\s|$|[\u1000-\u109F])"
    prefix_en = r"(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*)?"
    # Myanmar digit prefix accepts `။` (U+104B), `.`, or `#` as the
    # period character following the digits.
    prefix_my = r"(?:[\u1040-\u1049]+[\u104B.#]\s*)?"
    seen: set[str] = set()
    for syn in syns:
        norm = syn.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        patterns.append(r"^\s*" + prefix_en + prefix_my + r"\s*" + re.escape(norm) + sep)
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    _BURMESE_COMPILED_HEADINGS_CACHE[slot_id] = compiled
    return compiled


def compile_heading_patterns_for_lang(
    slot_id: int,
    base_patterns: Iterable[re.Pattern],
    lang: str = "en",
) -> list[re.Pattern]:
    """Return heading patterns for ``slot_id``, augmented for Burmese/mixed docs.

    English docs (``lang="en"``) get the base patterns unchanged.
    Burmese-active docs get the base patterns plus Burmese heading
    patterns from ``burmese_synonyms.yaml``.
    """
    base = list(base_patterns)
    if is_burmese_lang(lang):
        burmese = get_burmese_heading_patterns(slot_id)
        seen = {p.pattern for p in base}
        for p in burmese:
            if p.pattern not in seen:
                base.append(p)
                seen.add(p.pattern)
    return base


# ---------------------------------------------------------------------------
# Burmese slot-name -> slot_id map (for section marker complements)
# ---------------------------------------------------------------------------
_BURMESE_SLOT_NAME_TO_ID: dict[str, int] = {
    "introduction": 5,
    "policy_statement": 6,
    "purpose": 7,
    "scope": 8,
    "exclusions": 9,
    "definitions": 12,
    "related": 13,
    "history": 14,
}


_BURMESE_SLOT_SYNONYMS_CACHE: dict[str, list[str]] = {}


def get_burmese_slot_synonyms(slot_name: str) -> list[str]:
    """Return Burmese heading synonyms for the given English slot name.

    Loaded from ``burmese_synonyms.yaml`` at the slot-heading level.
    Cached per slot name. Returns an empty list if the YAML file is
    missing or has no synonyms for the slot.
    """
    if slot_name in _BURMESE_SLOT_SYNONYMS_CACHE:
        return _BURMESE_SLOT_SYNONYMS_CACHE[slot_name]
    slot_id = _BURMESE_SLOT_NAME_TO_ID.get(slot_name)
    if slot_id is None:
        _BURMESE_SLOT_SYNONYMS_CACHE[slot_name] = []
        return []
    try:
        from ..i18n.burmese_synonyms import get_burmese_synonyms
        syns = get_burmese_synonyms(slot_id)
    except Exception:
        syns = []
    _BURMESE_SLOT_SYNONYMS_CACHE[slot_name] = list(syns)
    return _BURMESE_SLOT_SYNONYMS_CACHE[slot_name]


def has_burmese_slot_synonym_in_first_line(
    paragraphs: Iterable[str],
    slot_name: str,
) -> bool:
    """True if any paragraph's first line starts with a Burmese heading
    synonym for the given slot.

    Optional Myanmar digit prefix is accepted. Used as a Burmese-aware
    complement to the English section-marker regexes.
    """
    if not paragraphs:
        return False
    syns = get_burmese_slot_synonyms(slot_name)
    if not syns:
        return False
    escaped = [re.escape(s) for s in syns if s]
    if not escaped:
        return False
    # Myanmar digit prefix accepts `။` (U+104B), `.`, or `#` as the
    # period character (Tesseract OCR fallback).
    pattern = re.compile(
        r"^\s*(?:[\u1040-\u1049]+[\u104B.#]\s*)?(?:"
        + "|".join(escaped)
        + r")\s*[:\-.]?\s*$",
    )
    for p in paragraphs:
        first_line = p.split("\n")[0].strip() if p else ""
        if pattern.match(first_line):
            return True
    return False


def burmese_section_marker_check(paragraphs: Iterable[str], slot_name: str) -> bool:
    """Dispatch helper: returns True if the document contains a Burmese
    heading for the given slot name. Callers use this as a complement
    to the English section-marker regex. No-op for non-Burmese slot
    names.
    """
    return has_burmese_slot_synonym_in_first_line(paragraphs, slot_name)


# ---------------------------------------------------------------------------
# Burmese paragraph splitter
# ---------------------------------------------------------------------------
_BURMESE_SPLITTER_SYNONYMS_CACHE: list[str] | None = None


def load_burmese_splitter_synonyms() -> list[str]:
    """Load all Burmese heading synonyms (slots 5-14) for the inline splitter.

    Returns the union of ``policy_platform.i18n.burmese_synonyms``
    heading synonyms across all slots. Cached after first load.
    """
    global _BURMESE_SPLITTER_SYNONYMS_CACHE
    if _BURMESE_SPLITTER_SYNONYMS_CACHE is not None:
        return _BURMESE_SPLITTER_SYNONYMS_CACHE
    out: list[str] = []
    try:
        from ..i18n.burmese_synonyms import get_all_burmese_synonyms
        all_syns = get_all_burmese_synonyms()
        for syns in all_syns.values():
            for syn in syns:
                if syn and syn not in out:
                    out.append(syn)
    except Exception:
        pass
    _BURMESE_SPLITTER_SYNONYMS_CACHE = sorted(out, key=len, reverse=True)
    return _BURMESE_SPLITTER_SYNONYMS_CACHE


def split_paragraphs_on_burmese_headings(lines: list[str]) -> list[str]:
    """Split lines at inline Burmese section headings.

    Burmese policy PDFs commonly encode section headings inline within
    a paragraph (e.g. ``"၁။ ရည်ရွ�်ချက်၁-၁။ �မူဝါဒ..."``), unlike English docs
    which use separate lines. This splitter finds Myanmar digit prefix
    + Burmese heading synonym followed by Burmese body and splits the
    paragraph at that boundary.

    Metadata-based Burmese extraction produces text with garbled
    mid-word characters (replacement chars U+FFFD). To handle this,
    the splitter uses a 4-char prefix of each synonym and matches
    greedily on the FIRST 4-char prefix that occurs after Myanmar
    digits. This still requires exact matches on a known heading's
    first 4 chars, preserving specificity (no false positives on body
    text), but tolerates corruption in trailing chars.

    Pre-filters paragraphs without Myanmar digits so English-only text
    passes through unchanged.
    """
    syns = load_burmese_splitter_synonyms()
    if not syns:
        return list(lines)
    prefixes = sorted({s[:4] for s in syns if len(s) >= 4}, key=len, reverse=True)
    if not prefixes:
        return list(lines)
    prefix_alt = "|".join(re.escape(p) for p in prefixes)
    # Myanmar digit prefix accepts `။` (U+104B), `.`, or `#` as the
    # period character. The `#` fallback is gated on the prefix-Myanmar-
    # digit constraint so English text with hash characters (room #5,
    # tag #foo) is preserved unchanged.
    pattern = re.compile(
        r"(?:^|(?<=\s))"
        r"(?:[\u1040-\u1049]+[\u104B.#]\s*)"
        r"(?:" + prefix_alt + r")",
    )
    out: list[str] = []
    for line in lines:
        if not line:
            out.append(line)
            continue
        if not any("\u1040" <= ch <= "\u1049" for ch in line):
            out.append(line)
            continue
        m = pattern.search(line)
        if m is None:
            out.append(line)
            continue
        split_pos = m.start()
        head_end = m.end()
        matched_prefix = m.group()[len(m.group()) - 4:]
        for syn in syns:
            if syn.startswith(matched_prefix):
                rest = syn[4:]
                if rest and line[head_end:head_end + len(rest)] == rest:
                    head_end += len(rest)
                    break
                if not rest:
                    break
        head = line[:split_pos].rstrip()
        heading = line[split_pos:head_end].rstrip()
        body = line[head_end:].strip()
        if head and heading and body:
            out.append(head)
            out.append(heading)
            out.append(body)
        elif heading and body:
            out.append(heading)
            out.append(body)
        else:
            out.append(line)
    return out


__all__ = [
    "is_burmese_lang",
    "is_burmese_char",
    "looks_like_mid_sentence",
    "looks_like_burmese_heading",
    "get_burmese_heading_patterns",
    "compile_heading_patterns_for_lang",
    "get_burmese_slot_synonyms",
    "has_burmese_slot_synonym_in_first_line",
    "burmese_section_marker_check",
    "load_burmese_splitter_synonyms",
    "split_paragraphs_on_burmese_headings",
    "normalize_burmese_extraction",
    "normalize_burmese_lines",
    "find_burmese_heading_match",
    "apply_burmese_heading_anchors",
    "apply_burmese_label_row_overrides",
]


# ---------------------------------------------------------------------------
# Burmese heading-anchor matching (Phase 6)
#
# Provides a self-contained heading-anchor matcher that operates ONLY on
# Burmese patterns loaded from burmese_synonyms.yaml. Used to override
# slot results that the English RAG pipeline marked as
# ``no_*_section`` when a Burmese heading is present.
# ---------------------------------------------------------------------------
from typing import Iterable as _Iterable


_HEADING_PATTERN_SLOTS = frozenset({5, 6, 7, 8, 9, 10, 12, 13, 14})


def find_burmese_heading_match(
    slot_id: int,
    paragraphs: list[str],
    *,
    reserved_paragraphs: set[int] | None = None,
    numeric_index: dict[int, int] | None = None,
) -> tuple[int, int, str] | None:
    """Find a Burmese heading-anchored section for ``slot_id``.

    Mirrors the English ``find_heading_match`` contract: returns
    ``(start_idx, end_idx, joined_text)`` on success, ``None`` otherwise.

    When ``numeric_index`` is provided (Myanmar numbered sections detected),
    uses numeric position mapping instead of synonym-based matching.
    Numeric mapping is more reliable for documents with explicit section
    numbers (၁, ၂, ၃, ၄).

    When ``numeric_index`` is None, falls back to synonym-based matching
    via ``get_burmese_heading_patterns``.
    """
    if slot_id not in _HEADING_PATTERN_SLOTS:
        return None
    if not paragraphs:
        return None
    if reserved_paragraphs is None:
        reserved_paragraphs = set()

    # Phase 3: Numeric section mapper — override synonym matching
    # when Myanmar numbered sections are detected.
    if numeric_index is not None:
        # Find all paragraph indices mapped to this slot
        slot_indices = [
            idx for idx, sid in numeric_index.items()
            if sid == slot_id and idx not in reserved_paragraphs
        ]
        if not slot_indices:
            return None
        start_idx = min(slot_indices)
        end_idx = max(slot_indices)
        # Collect body text
        body_paragraphs = [
            p.strip()
            for idx in range(start_idx, end_idx + 1)
            for p in [paragraphs[idx]]
            if p and p.strip() and idx not in reserved_paragraphs
        ]
        # Sub-section separator injection
        _SUB_SECTION_RE = re.compile(
            r"(?:^|(?<=\s))([\u1040-\u1049]+-[\u1040-\u1049]+[\u104B.#]\s*)"
        )
        joined_parts: list[str] = []
        for piece in body_paragraphs:
            clean = piece.strip()
            if not clean:
                continue
            m = _SUB_SECTION_RE.match(clean)
            if m and joined_parts:
                joined_parts.append("\n\n" + clean)
            else:
                joined_parts.append(clean)
        joined = " ".join(joined_parts).strip()
        if not joined:
            return None
        return (start_idx, end_idx, joined)

    # Fallback: synonym-based matching
    patterns = get_burmese_heading_patterns(slot_id)
    if not patterns:
        return None

    start_idx: int | None = None
    for i, p in enumerate(paragraphs):
        if i in reserved_paragraphs:
            continue
        if not p:
            continue
        first_line = p.split("\n")[0].strip() if "\n" in p else p.strip()
        if not first_line:
            continue
        if not any(pat.search(first_line) for pat in patterns):
            continue
        start_idx = i
        break
    if start_idx is None:
        return None

    # Walk forward collecting body paragraphs until the next heading
    end_idx = len(paragraphs) - 1
    for j in range(start_idx + 1, len(paragraphs)):
        next_p = paragraphs[j]
        if not next_p:
            continue
        next_first = next_p.split("\n")[0].strip() if "\n" in next_p else next_p.strip()
        if looks_like_burmese_heading(next_first):
            end_idx = j - 1
            break

    body_paragraphs = [
        p.strip()
        for k, p in enumerate(paragraphs[start_idx + 1: end_idx + 1], start=start_idx + 1)
        if p and p.strip() and k not in reserved_paragraphs
    ]
    heading_para = paragraphs[start_idx]
    heading_first = heading_para.split("\n")[0]
    inline_body = ""
    for pat in patterns:
        m = pat.search(heading_first)
        if m is not None:
            inline_body = heading_first[m.end():].strip()
            break
    pieces: list[str] = []
    if inline_body:
        pieces.append(inline_body)
    pieces.extend(body_paragraphs)
    _SUB_SECTION_RE = re.compile(
        r"(?:^|(?<=\s))([\u1040-\u1049]+-[\u1040-\u1049]+[\u104B.#]\s*)"
    )
    joined_parts: list[str] = []
    for piece in pieces:
        clean = piece.strip()
        if not clean:
            continue
        m = _SUB_SECTION_RE.match(clean)
        if m and joined_parts:
            joined_parts.append("\n\n" + clean)
        else:
            joined_parts.append(clean)
    joined = " ".join(joined_parts).strip()
    if not joined:
        joined = heading_first.strip()
    return (start_idx, end_idx, joined)


def apply_burmese_heading_anchors(
    paragraphs: list[str],
    rag_result: object,
    tables: list | None = None,
    table_paragraph_indices: list[int] | None = None,
) -> int:
    """Override slots in ``rag_result`` for Burmese documents.

    For each prose slot in {5, 6, 7, 8, 9, 10, 12, 13, 14} when the
    document contains Burmese text, this function:

      1. Tries :func:`find_burmese_heading_match`. If a Burmese heading
         is found, overrides the slot assignment with
         ``backend='burmese_heading_anchor'`` and the matched body text.
      2. If NO Burmese heading is found, replaces any RAG-based or
         fallback backend (``rag:*``, ``low_confidence``,
         ``all_reserved``, ``position_fallback``, ``fallback_position``,
         or any of the ``no_*_section`` / ``no_*_markers`` markers)
         with ``backend='no_burmese_heading'`` and the standard
         "Data is not found" placeholder. This enforces the user's
         rule: Burmese docs never fall back to RAG; if no Burmese
         heading is present, the slot reports "Data is not found".
      3. Preserves English-only matches (e.g. ``heading_anchor`` for
         sections that happen to use English in a Burmese doc).

    Non-Burmese documents pass through unchanged.

    When ``tables`` is provided, detected tables are routed to the
    matching slot (typically slot 10 - Award Structure) based on
    paragraph position.

    Returns the number of slots that were overridden.
    """
    if not paragraphs:
        return 0
    # Decide whether this is a Burmese document by quick paragraph sampling.
    sample = "\n".join(p for p in paragraphs[:50] if p)
    has_burmese = any("\u1000" <= ch <= "\u109F" for ch in sample)
    if not has_burmese:
        return 0

    # Phase 3: Build numeric section index when Myanmar numbered sections
    # are detected. Numeric mapping overrides synonym-based matching.
    numeric_index = None
    try:
        from ..rag.burmese_numeric_headings import (
            has_numeric_sections,
            build_numeric_section_index,
        )
        if has_numeric_sections(paragraphs):
            numeric_index = build_numeric_section_index(paragraphs)
    except ImportError:
        pass

    slots = getattr(rag_result, "slots", None)
    if not isinstance(slots, dict):
        return 0
    reserved: set[int] = set()
    overridden = 0
    data_not_found = _burmese_data_not_found_placeholder()
    for sid, sa in list(slots.items()):
        if sid not in _HEADING_PATTERN_SLOTS:
            continue
        match = find_burmese_heading_match(
            sid, paragraphs, reserved_paragraphs=reserved,
            numeric_index=numeric_index,
        )
        if match is not None:
            s_idx, e_idx, text = match
            reserved.update(range(s_idx, e_idx + 1))
            sa.chunk_text = text
            sa.source_idx = s_idx
            sa.score = 1.0
            sa.backend = "burmese_heading_anchor"
            overridden += 1
            continue
        backend = getattr(sa, "backend", "") or ""
        if _is_rag_or_fallback_backend(backend):
            sa.chunk_text = data_not_found
            sa.source_idx = None
            sa.score = 0.0
            sa.backend = "no_burmese_heading"
            overridden += 1

    # Phase 7 Step 4: Route detected tables to slots.
    # Tables extracted from OCR heuristics or pdfplumber are routed to
    # the matching slot based on paragraph position. If a table falls
    # within a slot's heading-anchor paragraph range, attach it.
    # User-confirmed: tables belong in Award Structure (slot 10).
    if tables and numeric_index is not None:
        _route_burmese_tables(
            slots, tables, paragraphs, numeric_index,
            table_paragraph_indices=table_paragraph_indices,
        )
    return overridden


_RAG_OR_FALLBACK_BACKENDS: frozenset[str] = frozenset({
    "no_introduction_section",
    "no_policy_statement_section",
    "no_exclusions_section",
    "no_related_policies_section",
    "no_history_markers",
    "no_chunks",
    "no_queries",
    "no_embed",
    "embed_failed",
    "oob_idx",
    "all_reserved",
    "low_confidence",
    "fallback_position",
    "position_fallback",
    "mm_position_fallback",
    "timeout",
    "empty_input",
})


def _is_rag_or_fallback_backend(backend: str) -> bool:
    """True if the backend indicates an English RAG or fallback attempt."""
    if not backend:
        return True
    if backend in _RAG_OR_FALLBACK_BACKENDS:
        return True
    if backend.startswith("rag:") or backend.startswith("rag_"):
        return True
    return False


def _route_burmese_tables(
    slots: dict,
    tables: list,
    paragraphs: list[str],
    numeric_index: dict[int, int],
    table_paragraph_indices: list[int] | None = None,
) -> int:
    """Phase 7 Step 4: Route detected tables to matching slots.

    Uses content-based detection to classify tables and route them:
      - Header tables (2-5 rows × 5 cols, contains "Policy"/"Version")
        → skip (already in slot 1 via field_parser).
      - Award Structure tables (contains "Rank", "Health Care", "Amount")
        → slot 10 (Award Structure & Payout Tiers).
      - Other data tables → slot 10 (fallback per user decision).

    Note: table_paragraph_indices from pdfplumber may not align with
    OCR paragraphs. We use content-based classification instead.

    Args:
        slots: rag_result.slots dict (mutated in-place).
        tables: list of detected tables (each is list[list[str]]).
        paragraphs: list of paragraph strings (for range lookup).
        numeric_index: dict mapping paragraph_idx → slot_id.
        table_paragraph_indices: optional (used for reference only).

    Returns the number of tables routed.
    """
    if not tables:
        return 0
    routed = 0
    _HEADER_KEYWORDS = frozenset({
        "policy no", "version", "approved by", "prepared by",
        "effective", "review date", "ဤမူဝါဒ", "policy_title",
    })
    _AWARD_KEYWORDS = frozenset({
        "rank", "health care", "hospital care", "amount",
        "limit", "annual", "monthly", "per visit", "benefit",
        "eligibility", "tier", "payout", "ကာယကံရှ",
        "ထတ်ု်ယူ", "ပမာဏ", "တစ်နစ်", "တစ်လ",
    })

    def _table_text(table: list) -> str:
        """Flatten table cells to lowercase text for keyword matching."""
        return " ".join(
            " ".join(str(c).lower() for c in row)
            for row in table if row
        )

    def _is_header_table(table: list) -> bool:
        """Header table: 2-5 rows, 5 columns, has label-row keywords."""
        rows = len(table)
        cols = max((len(r) for r in table if r), default=0)
        if rows < 2 or rows > 5 or cols < 4:
            return False
        text = _table_text(table)
        hits = sum(1 for kw in _HEADER_KEYWORDS if kw in text)
        return hits >= 2

    def _is_award_table(table: list) -> bool:
        """Award Structure table: has Rank, Health/Hospital Care, amount."""
        text = _table_text(table)
        hits = sum(1 for kw in _AWARD_KEYWORDS if kw in text)
        return hits >= 3

    # Classify and route each table
    award_tables: list[list] = []
    other_tables: list[list] = []
    for table in tables:
        if _is_header_table(table):
            continue  # Skip — already in slot 1 via field_parser
        if _is_award_table(table):
            award_tables.append(table)
        else:
            other_tables.append(table)

    # Route award tables to slot 10 (Award Structure)
    if 10 in slots:
        sa = slots[10]
        if award_tables:
            sa.table = award_tables[0]
            sa.extra_tables = award_tables[1:] + other_tables
            routed = len(award_tables) + len(other_tables)
        elif other_tables:
            sa.table = other_tables[0]
            sa.extra_tables = other_tables[1:]
            routed = len(other_tables)

    return routed


def _burmese_data_not_found_placeholder() -> str:
    """Return the localized "Data is not found" string for Burmese."""
    try:
        from ...i18n.burmese_strings import DATA_NOT_FOUND_MY
        return DATA_NOT_FOUND_MY
    except Exception:
        return "Data is not found in source file."


# Slot ids filled from PDF label rows (header table) instead of prose
# heading-anchor matching. These are populated by `field_parser.py`
# from a 2-column "Label: value" table that precedes the body prose.
_LABEL_ROW_SLOTS: frozenset[int] = frozenset({1, 2, 3, 4, 11})


def apply_burmese_label_row_overrides(
    paragraphs: list[str],
    rag_result: object,
) -> int:
    """Fill empty label-row slots (1, 2, 3, 4, 11) with the
    "Data is not found" placeholder for Burmese documents.

    Label-row slots are normally populated by ``field_parser`` from
    the PDF's 2-column label table. After the dispatch routes to
    ``extract_text_smart`` (clean text), ``field_parser`` will extract
    any labels present. Slots that are still empty after field parsing
    get this placeholder so the user sees "Data is not found in
    source file" instead of nothing or a mis-inferred body chunk.

    Skips slots that already have a non-empty ``chunk_text``. Non-Burmese
    documents pass through unchanged.

    Returns the number of label-row slots that were overridden.
    """
    if not paragraphs:
        return 0
    sample = "\n".join(p for p in paragraphs[:50] if p)
    if not any("\u1000" <= ch <= "\u109F" for ch in sample):
        return 0

    slots = getattr(rag_result, "slots", None)
    if not isinstance(slots, dict):
        return 0

    placeholder = "Data is not found in source file."
    overridden = 0
    for sid, sa in slots.items():
        if sid not in _LABEL_ROW_SLOTS:
            continue
        existing = getattr(sa, "chunk_text", None)
        if existing is not None and existing.strip():
            continue
        sa.chunk_text = placeholder
        sa.source_idx = None
        sa.score = 0.0
        sa.backend = "no_burmese_field"
        sa.table = None
        sa.extra_tables = []
        overridden += 1
    return overridden


# ---------------------------------------------------------------------------
# Post-extraction normalization (Phase 4)
#
# Metadata-based Burmese extraction produces text with garbled control
# characters (U+00AD soft hyphens, U+00A0 non-breaking spaces) and
# occasionally a soft-hyphen used as a section-number separator. This
# function normalizes these artifacts so downstream heading-anchor
# matching works against clean text.
# ---------------------------------------------------------------------------
_SOFT_HYPHEN = "\xad"
_NBSP = "\xa0"
_MYANMAR_DIGIT_RANGE = range(0x1040, 0x104A)


def normalize_burmese_extraction(text: str) -> str:
    """Normalize garbled artifacts in metadata-extracted Burmese text.

    Operations (in order):
      1. Replace U+00A0 non-breaking spaces with U+0020 regular spaces.
      2. Replace U+00AD soft-hyphen between two Myanmar digits with U+002D
         hyphen (the source PDFs use the soft-hyphen as a section-number
         separator like ``1­1.`` to mean ``1-1.``).
      3. Strip remaining U+00AD soft hyphens (cosmetic line breaks).
      4. Collapse runs of more than 2 ASCII spaces into a single space.

    Pure function. Safe for non-Burmese text (Burmese Unicode block
    ranges are used to gate the digit-hyphen substitution).
    """
    if not text:
        return text
    out = text.replace(_NBSP, " ")
    out = out.replace(_SOFT_HYPHEN, "")
    return out


def normalize_burmese_lines(lines: list[str]) -> list[str]:
    """Apply ``normalize_burmese_extraction`` to each line in ``lines``."""
    return [normalize_burmese_extraction(line) for line in lines]
