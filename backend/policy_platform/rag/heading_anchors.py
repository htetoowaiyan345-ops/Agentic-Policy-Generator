"""Heading-anchored retrieval for prose slots (5, 6, 7, 8, 9, 12, 13, 14).

The key insight is that in real policy documents, a section is
structured as:

    Heading line
    Body line 1
    Body line 2
    ...
    [next section's heading or end of doc]

The heading may be:
- On the same line as body: "Purpose: To provide immediate relief..."
- On its own line: "Purpose" / "1. Purpose" / "1. PURPOSE"
- Followed by a colon then body: "Purpose: To provide..."

The body may span multiple paragraphs.

The retrieval function returns a span of paragraphs that constitute
the entire section (heading + all body paragraphs until the next
section's heading).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


# Prose slots that should be matched by heading anchors.
HEADING_ANCHOR_SLOTS: set[int] = {5, 6, 7, 8, 9, 10, 12, 13, 14}

# Heading patterns per slot. The first line of a paragraph is checked
# against these regexes. Patterns are case-insensitive and tolerate
# various heading styles:
#   - "Purpose"
#   - "Purpose:"
#   - "Purpose: To provide..."
#   - "1. Purpose"
#   - "1) Purpose"
#   - "I. Purpose"
#   - "PURPOSE"
#   - "Purpose. To provide..."
#
# The pattern matches if the heading word appears at the start of
# the line, optionally preceded by a number, and is followed by
# either end-of-line or a colon/dash separator.
def _build_slot_patterns(slot_words: list[str]) -> list[str]:
    """Build heading patterns from a list of slot heading words.

    The separator character class accepts standard punctuation
    (: - .) plus the Unicode replacement char (\ufffd) which
    appears in PDFs where the source font's em-dash or other
    glyph was not extractable.

    Patterns are sorted by length (longest first) downstream so
    multi-word synonyms match before single-word ones. The
    `_is_heading_for_slot` validator then checks that the
    remainder of the line is consistent with a heading + body,
    rejecting mid-sentence false positives like "aim. Adverse
    action...".
    """
    # Separator: colon, hyphen, period, replacement char, or whitespace.
    # We use a non-capturing group with alternation.
    patterns = []
    for word in slot_words:
        prefix = r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*|section\s+\d+\s*:?\s*)?"
        suffix = r"(?:[:\-\.\ufffd]|\s|$)"
        patterns.append(prefix + re.escape(word) + suffix)
        if word == "policy statement":
            ps_prefix = r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*)?"
            patterns.append(ps_prefix + r"policy\s+statement(\s*-\s*purpose)?" + suffix)
    return patterns


# Each slot's heading words (lowercase, normalized)
# Pulled from framework.section_map.SECTION_HEADING_SYNONYMS so the
# canonical Brain synonyms are the single source of truth.
def _load_slot_heading_words() -> dict[int, list[str]]:
    """Load slot heading words from the canonical SECTION_HEADING_SYNONYMS.

    Falls back to the Brain slot titles if a slot has no synonyms
    defined (e.g. slot 1 / 3 are label-row slots, not heading-anchored).
    """
    from ..framework.section_map import FROZEN_SECTIONS, SECTION_HEADING_SYNONYMS
    words: dict[int, list[str]] = {}
    for sec in FROZEN_SECTIONS:
        sid = sec["id"]
        syns = SECTION_HEADING_SYNONYMS.get(sid, [])
        if syns:
            words[sid] = list(syns)
        else:
            # Fallback to the Brain title (e.g. "INTRODUCTION" -> "introduction").
            words[sid] = [sec["title"].lower()]
    return words


SLOT_HEADING_WORDS: dict[int, list[str]] = _load_slot_heading_words()

# Build the patterns
HEADING_PATTERNS: dict[int, list[str]] = {
    sid: _build_slot_patterns(words) for sid, words in SLOT_HEADING_WORDS.items()
}

# Add special "bare heading" patterns for headings that are commonly
# seen as just the heading word on a line, e.g. "Policy:". These are
# added per-slot. The pattern is the heading word at the start, followed
# by separator, and NO other words before end-of-line.
#
# CRITICAL: bare heading patterns must NOT match compound titles like
# "POLICY TEMPLATE - AWARD AND RECOGNITION". The pattern requires
# the heading word to be IMMEDIATELY followed by a separator or
# end-of-line, not by other words.
_BARE_HEADING_PATTERNS: dict[int, list[str]] = {
    6: [
        # "Policy:" or "Policy." or "Policy" alone - bare word as section heading.
        # IMPORTANT: must end with separator OR end-of-line, NOT space + other text.
        r"^\s*policy\s*[:\-.\ufffd]?\s*$",
    ],
}

# Add special "bare heading" patterns for headings that are commonly
# seen as just the heading word on a line, e.g. "Policy:". These are
# added per-slot. The pattern is the heading word at the start, followed
# by separator, and NO other words before end-of-line.
_BARE_HEADING_PATTERNS: dict[int, list[str]] = {
    6: [
        # "Policy:" - bare word as section heading
        r"^\s*policy\s*[:\-.\u00a9\ufffd]?\s*$",
    ],
}


def _first_line(text: str) -> str:
    """Return the first non-blank line of a paragraph (as-is)."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# Boundary patterns - the START of a paragraph that signals the END
# of the current section. These should be reasonably conservative -
# they only trigger when the line is clearly a heading, not when
# it just happens to contain a heading word.
#
# Each pattern matches the heading word either:
# - on its own line: "Purpose" or "Purpose:"
# - or with body: "Purpose: To provide..."
# The latter is a "soft" boundary: it signals the START of a new
# section but the body is also part of the new section (handled
# by the slot's own heading-anchor logic).
_BOUNDARY_PATTERNS: list[str] = [
    # Numbered headings: "1. Purpose" / "2) Scope" / "I. Introduction"
    r"^\s*\d+\.\s+[A-Z]",
    r"^\s*\d+\)\s+[A-Z]",
    r"^\s*[IVX]+\.\s+[A-Z]",
    # ALL-CAPS headings: "INTRODUCTION" / "POLICY STATEMENT"
    # Must be 4+ chars to avoid matching single uppercase words.
    r"^\s*[A-Z][A-Z][A-Z][A-Z\s]{3,}\s*[:\-.\ufffd]?\s*$",
]


def _build_boundary_patterns_from_synonyms() -> list[str]:
    """Build boundary patterns dynamically from SECTION_HEADING_SYNONYMS.

    Each synonym becomes a boundary pattern. The pattern matches the
    synonym at the start of a line, optionally preceded by a number,
    followed by a separator (:, -, ., replacement char, or whitespace).
    """
    patterns = []
    seen = set()
    # Pull from the canonical SECTION_HEADING_SYNONYMS so the boundary
    # list stays in sync with the heading detection.
    from ..framework.section_map import SECTION_HEADING_SYNONYMS
    sep = r"(?:[:\-.\ufffd]|\s|$)"
    for sid, syns in SECTION_HEADING_SYNONYMS.items():
        if sid in (1, 3, 11, 15):
            # These slots use label-row parsing, not heading-anchor.
            continue
        for syn in syns:
            norm = syn.strip().lower()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            # Escape and build pattern.
            prefix = r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*)?"
            escaped = re.escape(norm)
            # For multi-word synonyms like "policy statement" the regex
            # already requires the whole phrase to match at the start.
            patterns.append(prefix + escaped + sep)
    return patterns


_BOUNDARY_PATTERNS.extend(_build_boundary_patterns_from_synonyms())


def _compile_patterns(patterns: list[str], *, case_insensitive: bool = True) -> list[re.Pattern]:
    flags = re.IGNORECASE if case_insensitive else 0
    return [re.compile(p, flags) for p in patterns]


_COMPILED_HEADINGS: dict[int, list[re.Pattern]] = {
    sid: _compile_patterns(pats) for sid, pats in HEADING_PATTERNS.items()
}
# Add bare heading patterns.
for sid, pats in _BARE_HEADING_PATTERNS.items():
    _COMPILED_HEADINGS.setdefault(sid, []).extend(
        re.compile(p, re.IGNORECASE) for p in pats
    )
# ALL-CAPS pattern needs to be case-SENSITIVE to avoid matching
# regular prose. Other boundary patterns are case-insensitive.
_COMPILED_BOUNDARIES_CS: list[re.Pattern] = [
    re.compile(p) for p in _BOUNDARY_PATTERNS if r"[A-Z][A-Z][A-Z][A-Z\s]" in p
]
_COMPILED_BOUNDARIES_CI: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in _BOUNDARY_PATTERNS
    if r"[A-Z][A-Z][A-Z][A-Z\s]" not in p
]
_COMPILED_BOUNDARIES: list[re.Pattern] = _COMPILED_BOUNDARIES_CS + _COMPILED_BOUNDARIES_CI


def _first_line(text: str) -> str:
    """Return the first non-blank line of a paragraph (as-is)."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _is_toc_entry(paragraph: str) -> bool:
    """True if the paragraph looks like a table-of-contents entry.

    TOC entries have lots of dots and a page number at the end,
    e.g. "Introduction ............................. 5".
    """
    if not paragraph:
        return False
    # TOC entry has multiple dots followed by a number at the end.
    return bool(re.search(r"\.{4,}\s*\d+\s*$", paragraph))


def _is_heading_for_slot(slot_id: int, paragraph: str) -> bool:
    """True if the first line of `paragraph` matches a heading pattern for slot_id.

    A heading pattern must match either:
    - The heading word followed by `:` or `-` (a clear heading separator),
      with or without body after.
    - The heading word alone on a line (or with newline) followed by end.
    - The heading word followed by "." and short body (heading-style).

    The heading pattern does NOT match when the heading word is at the
    start of a paragraph but the rest of the paragraph is a continuation
    of a previous sentence (e.g. "...with the purpose or aim. Adverse
    action need not be job-related..." — here "aim" is mid-sentence,
    not a heading).

    Patterns are tried in length-descending order so longer/more specific
    synonyms match first. The function returns True if ANY pattern
    matches with a valid remainder.
    """
    first = _first_line(paragraph)
    if not first:
        return False
    # Skip TOC entries - they're not real section headings.
    if _is_toc_entry(paragraph):
        return False
    patterns = sorted(
        _COMPILED_HEADINGS.get(slot_id, []),
        key=lambda p: -len(p.pattern),
    )
    for pattern in patterns:
        if not pattern.match(first):
            continue
        m = pattern.match(first)
        if m is None:
            continue
        end_pos = m.end()
        remainder = first[end_pos:]
        # Determine what separator the pattern matched with. We look
        # at the last char of the matched text (before remainder).
        if not remainder:
            return True
        last_matched = first[end_pos - 1] if end_pos > 0 else ""
        # Colons and dashes are reliable heading separators (not sentence
        # terminators). Accept any body length after them.
        if last_matched in (":", "-", "\ufffd"):
            return True
        if remainder[0] in (":", "-", "\ufffd"):
            return True
        # If the separator was a period ("Purpose."), the matched text
        # ends with ".". Distinguish real heading + body from mid-sentence:
        # - heading + body: "Purpose. To provide safety standards..."
        #   body is usually <= 100 chars after the period.
        # - mid-sentence: "aim. Adverse action need not be job-related..."
        #   body is 200+ chars after the period.
        if last_matched == ".":
            if len(remainder) <= 120:
                return True
            continue
        if remainder[0].isalpha() and remainder[0].isupper():
            if len(remainder.strip()) < 60:
                return True
            continue
        if remainder[0].isdigit() or remainder[0].isspace():
            # Heading + body without a clear separator (e.g. "Aim
            # Statement body content"). Distinguish from mid-sentence
            # by paragraph length.
            if len(first) <= 200:
                return True
            continue
        continue
    return False


def _is_boundary(paragraph: str) -> bool:
    """True if the first line of `paragraph` looks like a major heading boundary.

    This catches three categories:
    1. Known slot-specific boundary patterns (slot 5-9, 12-14 headings).
    2. Generic structural signals (numbered headings, ALL-CAPS lines).
    3. Any paragraph that looks like a section heading per the
       generic section_detector (catches new heading styles without
       requiring us to update the synonym list).
    """
    first = _first_line(paragraph)
    if not first:
        return False
    # Skip TOC entries - they're not real section headings.
    if _is_toc_entry(paragraph):
        return False
    for pattern in _COMPILED_BOUNDARIES:
        if pattern.match(first):
            return True
    # Generic fallback: any paragraph that looks like a section
    # heading is also a boundary. This is strict by design - the
    # section_detector only matches strong heading signals.
    try:
        from .section_detector import looks_like_section_heading
        if looks_like_section_heading(paragraph):
            return True
    except Exception:
        pass
    return False


def _strip_heading_label(text: str, slot_id: int) -> str:
    """Strip the heading label from the start of a paragraph's text.

    For example, if the paragraph is "Scope and Beneficiaries: All
    permanent employees..." and slot_id is 8 (Scope), return
    "All permanent employees..." with the heading label removed.

    Handles leading numbering ("1. Purpose:" / "2) Scope:") and
    multi-word synonyms ("Policy Statement - Purpose:").

    If the text matches the synonym EXACTLY (no body after), the
    function returns the text unchanged — the synonym IS the heading,
    not a prefix to be stripped.

    This prevents the rendered output from showing the heading word
    twice (once as the heading, once as the start of the body).
    """
    if not text:
        return text
    import re as _re
    synonyms = SLOT_HEADING_WORDS.get(slot_id, [])
    sorted_syns = sorted(set(synonyms), key=len, reverse=True)
    text_stripped = text.strip()
    text_lower = text_stripped.lower()
    for syn in sorted_syns:
        syn_lower = syn.lower()
        # Exact match: the text IS the synonym (e.g. "Policy Statement -
        # Purpose" matches synonym "policy statement - purpose"). No
        # body to extract — return unchanged.
        if text_lower == syn_lower:
            return text
        # Prefix match: optional leading numbering + synonym + separator
        # + body content.
        sep_class = r"[:\-.\ufffd]"
        prefix_pattern = (
            r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*|section\s+\d+\s*:?\s*)?"
            + _re.escape(syn_lower)
            + r"\s*"
            + sep_class
            + r"\s*"
            + r"(.+)$"
        )
        m = _re.match(prefix_pattern, text_lower, flags=_re.IGNORECASE | _re.DOTALL)
        if m and m.group(1).strip():
            return text[m.start(1):].lstrip()
    return text


def _extract_inline_body(paragraph: str, slot_id: int) -> str:
    """If the heading paragraph has body on the same line (e.g. "Purpose:
    To provide relief..."), return the body portion after the colon.

    Returns "" if there's no inline body (heading on its own line).

    Only extracts inline body when the synonym is followed by:
    - ":" then space then body (heading + body), OR
    - period + space + body, where body is short (< 100 chars).

    For multi-word synonyms that include separators like "policy
    statement - purpose", the heading IS the synonym itself; we do NOT
    extract trailing fragments as body.
    """
    if not paragraph:
        return ""
    synonyms = SLOT_HEADING_WORDS.get(slot_id, [])
    # Sort by length descending so longer phrases match first.
    sorted_syns = sorted(set(synonyms), key=len, reverse=True)
    text = paragraph.strip()
    text_lower = text.lower()
    for syn in sorted_syns:
        syn_lower = syn.lower()
        # Case 1: synonym ends with ":" - "Purpose: body"
        prefix_colon = syn_lower + ":"
        if text_lower.startswith(prefix_colon):
            body = text[len(prefix_colon):].lstrip()
            if body:
                return body
        # Case 2: synonym matches the FULL paragraph (no body after).
        # If the paragraph is exactly the synonym (case-insensitive),
        # there's no inline body.
        if text_lower == syn_lower:
            return ""
        # Case 3: synonym matches with period and short body.
        # "Purpose. To provide safety." - accept if body <= 100 chars.
        prefix_period = syn_lower + "."
        if text_lower.startswith(prefix_period):
            rest = text[len(prefix_period):].lstrip()
            if rest and len(rest) <= 100:
                return rest
        # Case 4: synonym with " - " separator and body.
        # "Purpose - body content" - the heading is "Purpose" only,
        # the body is "body content".
        prefix_dash = syn_lower + " - "
        if text_lower.startswith(prefix_dash):
            # Only treat as inline body if the rest is body content,
            # not another heading word. Check if the rest is short
            # (< 100 chars) and doesn't look like a heading.
            rest = text[len(prefix_dash):].lstrip()
            if rest and len(rest) <= 100 and not _looks_like_heading(rest):
                return rest
    return ""


def _looks_like_heading(text: str) -> bool:
    """True if text looks like a heading (short, capitalized, no body)."""
    if not text:
        return False
    if len(text) > 60:
        return False
    # Short text starting with uppercase = likely heading.
    return text[0].isupper()


def find_heading_match(
    slot_id: int,
    paragraphs: List[str],
    reserved_paragraphs: Optional[set] = None,
) -> Optional[Tuple[int, int, str]]:
    """Find the heading-anchored section for `slot_id` in `paragraphs`.

    Returns:
        None if no heading match is found.
        (start_idx, end_idx, joined_text) if a heading match is found,
        where paragraphs[start_idx..end_idx] (inclusive) are the body
        of the section.

    The section includes the heading line itself and all body
    paragraphs until the next section's heading boundary. If the
    heading is on its own line (no body on same line), the FIRST
    following paragraph that is NOT a boundary is collected as
    the body. This handles "Introduction" / "1. Purpose" / "PURPOSE"
    styles where the heading sits alone.

    Multi-line headings (e.g. "RELATED POLICIES, PROCEDURES, FORMS,
    GUIDELINES &" / "OTHER RESOURCES" on next line) are handled by
    collecting continuation lines that look like heading text.

    If `reserved_paragraphs` is provided, those paragraph indices are
    treated as "already owned" by an earlier slot. The anchor search
    will SKIP reserved indices when picking the start position, and the
    end-of-section walk will treat a reserved index as a boundary so we
    don't accidentally extend this slot's body into another slot's
    territory. This is the Fix-3 guard for the "slot 7 eats slot 6 body"
    bug where the same heading line ended up matching two adjacent
    slots.
    """
    if slot_id not in HEADING_ANCHOR_SLOTS:
        return None
    if not paragraphs:
        return None
    if reserved_paragraphs is None:
        reserved_paragraphs = set()

    start_idx: Optional[int] = None
    for i, p in enumerate(paragraphs):
        if i in reserved_paragraphs:
            continue
        if _is_heading_for_slot(slot_id, p):
            start_idx = i
            break
    if start_idx is None:
        return None

    # Check if the heading line has body content on the same line.
    # A heading is "pure" if its first line ends at the heading word
    # (no body after the colon/dash) AND has no more than 8 words
    # (a heading is usually short).
    first_line = _first_line(paragraphs[start_idx])
    is_pure_heading = bool(
        re.match(
            r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*)?"
            r"\S+(\s+\S+){0,7}\s*[:\-.\ufffd]?\s*$",
            first_line,
        )
    )

    # Detect multi-line headings: a heading line that ends with "&"
    # (continuation indicator) or with "," or that doesn't end with
    # a sentence terminator should include the next line as part of
    # the heading.
    #
    # Continuation detection must be CONSERVATIVE — body content that
    # happens to start with an uppercase word (e.g. "Performance
    # Management Policy; Employee Code of Conduct; ...") should NOT
    # be treated as a heading continuation. Real heading continuations
    # are short (<= 40 chars), have no body punctuation (`;` `,`), and
    # read like a title fragment.
    ends_with_continuation = first_line.rstrip().endswith(("&", ",", "/"))
    heading_continuation_indices: List[int] = [start_idx]
    if ends_with_continuation:
        for j in range(start_idx + 1, len(paragraphs)):
            next_line = _first_line(paragraphs[j])
            if not next_line:
                continue
            stripped = next_line.rstrip(":.-\ufffd").rstrip()
            # Stricter continuation check:
            # - short (<= 60 chars after stripping separators)
            # - no body punctuation (`,` `;` `:`) — those are body
            #   indicators, not heading-continuation indicators
            # - ALL CAPS or Title Case (real heading style)
            # - does not end with "." (period is sentence terminator)
            if not stripped:
                continue
            has_body_punct = any(c in stripped for c in (",", ";", ":"))
            if (
                stripped
                and not has_body_punct
                and stripped[0].isupper()
                and not stripped.endswith(".")
                and len(stripped) <= 60
            ):
                heading_continuation_indices.append(j)
            else:
                break

    # Walk forward from start_idx+1 to find the next boundary.
    end_idx = len(paragraphs) - 1
    next_idx = heading_continuation_indices[-1] + 1

    # Slot 14 (HISTORY) is special: it accepts ALL paragraphs after the
    # heading, including short ALL-CAPS words that are table headers
    # (e.g. "DATE", "VERSION"). These would otherwise be classified as
    # section boundaries by _is_boundary. The HISTORY section typically
    # ends at end-of-document or the next major section heading.
    HISTORY_SLOT = 14

    if is_pure_heading:
        # Heading on its own line. The next non-empty paragraph
        # that is NOT a boundary is the start of the body.
        body_start = None
        for j in range(next_idx, len(paragraphs)):
            p = paragraphs[j]
            # Reserved-by-another-slot paragraphs act as a hard
            # boundary for THIS slot's body. This prevents slot 7
            # from extending into a paragraph that slot 6 (or any
            # earlier slot) already owns. Heading lines themselves
            # (heading_continuation_indices) are excluded.
            if (
                j in reserved_paragraphs
                and j not in heading_continuation_indices
                and slot_id != HISTORY_SLOT
            ):
                end_idx = j - 1
                break
            if _is_boundary(p) and slot_id != HISTORY_SLOT:
                end_idx = j - 1
                break
            # Cross-slot boundary (M2 fix): if this paragraph's
            # first line starts with a section-heading label for
            # ANY OTHER slot (5-14), treat it as a stop boundary.
            # Slot 14 (HISTORY) is exempt so it can keep absorbing
            # content after its own heading.
            if (
                _is_cross_slot_boundary(p)
                and slot_id != HISTORY_SLOT
                and j not in heading_continuation_indices
            ):
                end_idx = j - 1
                break
            # Inline clause break inside body paragraph. When the
            # paragraph TEXT contains `. {structural_label}` (with
            # the slot-10/12/14 ownership filter), the body for THIS
            # slot ends before that paragraph. The paragraph's
            # pre-break portion is included via the body-clipped path
            # below; the post-break portion becomes the next slot's
            # territory.
            if (
                slot_id != HISTORY_SLOT
                and j not in heading_continuation_indices
                and _find_inline_clause_break(p, slot_id=slot_id)
                is not None
            ):
                # Stop BEFORE the paragraph; the body-clipped portion
                # of this paragraph will be appended via the
                # `body_only` collector below.
                end_idx = j - 1
                break
            if body_start is None and p.strip():
                body_start = j
            if body_start is not None and j >= body_start + 30:
                end_idx = j - 1
                break
        if body_start is None:
            end_idx = start_idx
    else:
        # Heading has body on the same line. Continue collecting
        # until the next boundary.
        for j in range(next_idx, len(paragraphs)):
            if (
                j in reserved_paragraphs
                and j not in heading_continuation_indices
                and slot_id != HISTORY_SLOT
            ):
                end_idx = j - 1
                break
            if _is_boundary(paragraphs[j]) and slot_id != HISTORY_SLOT:
                end_idx = j - 1
                break
            # Cross-slot boundary (M2 fix) — same logic as above
            # for the inline-heading branch.
            if (
                _is_cross_slot_boundary(paragraphs[j])
                and slot_id != HISTORY_SLOT
                and j not in heading_continuation_indices
            ):
                end_idx = j - 1
                break
        end_idx = min(end_idx, start_idx + 30)

    if end_idx < start_idx:
        end_idx = start_idx

    # The heading paragraph itself (at start_idx) and any multi-line
    # continuation lines are NOT part of the body — they are the heading.
    # Only paragraphs strictly AFTER the heading are body. This prevents
    # the rendered output from showing the heading label twice (once as
    # the heading itself, once as the start of the body).
    body_start_idx = heading_continuation_indices[-1] + 1
    body_paragraphs_all = paragraphs[body_start_idx:end_idx + 1]
    # Exclude label-row paragraphs from the body — these are slot-1/3/11
    # placeholders ("Policy Review Note: Data is not found in source
    # file") that should NOT bleed into slot 9/10/12 prose content.
    from .chunker import is_label_row_paragraph
    body_only: list[str] = []
    clause_break_hit = False
    for p in body_paragraphs_all:
        if not p or not p.strip():
            continue
        if is_label_row_paragraph(p):
            continue
        if _is_metadata_marker(p):
            continue
        # Inline clause-break detector: when a body paragraph contains
        # a `. {structural_label}` boundary mid-text, clip it at the
        # terminator. Subsequent paragraphs after the clipped chunk
        # may still be claimed by THIS slot's walk but the body
        # itself stops here. The slot_id-aware variant excludes
        # labels that "belong" to this slot (e.g. `annual budget`
        # when walking slot 10) so the chunk is not clipped
        # mid-list.
        clipped = _clip_chunk_at_clause_break(p.strip(), slot_id=slot_id)
        if clipped != p.strip():
            # The paragraph contained a clause break. Append only the
            # portion before the break, and stop accumulating.
            if clipped:
                body_only.append(clipped)
            clause_break_hit = True
            break
        body_only.append(p.strip())
    # If the heading line has body on the same line (e.g. "Purpose: To
    # provide relief..."), pull the body portion after the colon.
    heading_para = paragraphs[start_idx]
    inline_body = _extract_inline_body(heading_para, slot_id)
    pieces = []
    if inline_body:
        pieces.append(inline_body.strip())
    pieces.extend(body_only)
    joined = " ".join(pieces).strip()
    if not joined:
        # Fallback: when there's no body after the heading, try to
        # extract the body portion from the heading paragraph itself
        # by stripping the heading label. For "1. Purpose: To provide
        # safety standards..." this returns "To provide safety standards..."
        # (without the "1. Purpose:" prefix).
        stripped = _strip_heading_label(heading_para, slot_id)
        if stripped != heading_para and stripped.strip():
            joined = stripped.strip()
        else:
            # Last resort: return the heading text so the slot has
            # some content (better than an empty slot).
            joined = _first_line(heading_para).strip()
    if not joined:
        return None
    return (start_idx, end_idx, joined)


# ---------------------------------------------------------------------------
# Cross-slot boundary detection (the M2 fix for slot-bleed on dense
# single-paragraph sources like Earthquake PDF).
#
# When the body-walk for slot X (e.g. slot 12 = Definitions) extends
# forward looking for `_is_boundary()`, it only knows about boundaries
# defined for THAT slot. Cross-slot boundaries (paragraphs whose first
# line matches a heading-anchor synonym for ANY slot 5-14, e.g.
# `History:` or `Related Forms:` or `Governance:`) are missed, so
# slot X keeps claiming the other slot's content.
#
# `_is_cross_slot_boundary()` returns True if the first line of the
# paragraph starts with ANY section-heading synonym (slot 5-14).
# Slot 14 (HISTORY) is intentionally excluded because HISTORY
# absorbs everything after its own heading.
#
# Gated by env var `AGENTIC_POLICY_CROSS_SLOT_BOUNDARY` (default ON).
# When ON, the body-walk honors cross-slot boundaries. When OFF,
# legacy behavior is preserved.
# ---------------------------------------------------------------------------

_CROSS_SLOT_LABEL_REGEX_CACHE: Optional["re.Pattern[str]"] = None


def _cross_slot_label_regex() -> "re.Pattern[str]":
    """Build a regex that matches the FIRST LINE of a paragraph when it
    starts with any section-heading synonym (slot 5-14). Case-
    insensitive. Cached after first build.
    """
    global _CROSS_SLOT_LABEL_REGEX_CACHE
    if _CROSS_SLOT_LABEL_REGEX_CACHE is not None:
        return _CROSS_SLOT_LABEL_REGEX_CACHE
    from ..framework.section_map import SECTION_HEADING_SYNONYMS

    opts: list[str] = []
    # Skip label-row slots (1, 3, 11) and image slot (15); they are
    # not body-walk-boundary candidates — their "headings" live in
    # label rows, not the prose paragraph stream.
    for sid, syns in SECTION_HEADING_SYNONYMS.items():
        if sid in (1, 3, 11, 15):
            continue
        for syn in syns:
            s = syn.strip()
            if not s:
                continue
            opts.append(re.escape(s))
            if not s.endswith(":"):
                opts.append(re.escape(s + ":"))
    # Sort longest-first so multi-word phrases (e.g.
    # "scope and beneficiaries") win over sub-phrases ("scope").
    joined = "|".join(sorted(set(opts), key=len, reverse=True))
    # Match label at the START of a line, optionally preceded by a
    # number ("1. Purpose"), followed by a separator (colon, dash,
    # period, whitespace, or EOL). Anchored to ^ so mid-paragraph
    # mentions of "History:" do NOT trip it.
    _CROSS_SLOT_LABEL_REGEX_CACHE = re.compile(
        r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*)?"
        r"(?P<lab>(" + joined + r"))"
        r"(?:[:\-.\ufffd]|\s|$)",
        re.IGNORECASE,
    )
    return _CROSS_SLOT_LABEL_REGEX_CACHE


def _is_cross_slot_boundary(paragraph: str) -> bool:
    """True if the FIRST LINE of `paragraph` starts with any section-
    heading synonym (slot 5-14) OR with a structural body-break label
    (e.g. `Required Documents`, `Annual Budget Allocation`, `Tier 1`)
    that acts as a natural boundary even though it's not a Brain-
    section-heading title.

    Honors the `AGENTIC_POLICY_CROSS_SLOT_BOUNDARY` env var (default
    ON). When the env var is "0", always returns False (legacy
    behavior — only same-slot boundaries stop the body-walk).
    """
    if not paragraph:
        return False
    # Honor the gate. Default ON so the fix ships by default. Set to
    # "0" for legacy behavior.
    import os
    if os.environ.get("AGENTIC_POLICY_CROSS_SLOT_BOUNDARY", "1") == "0":
        return False
    first = _first_line(paragraph)
    if not first:
        return False
    # HISTORY is intentionally allowed to extend past everything (see
    # HISTORY_SLOT handling in find_heading_match) — don't flag its
    # own heading line as a boundary for other slots that come
    # BEFORE it. For OTHER slots walking past a History heading, treat
    # it as a normal cross-slot boundary so they stop. The check
    # below is only relevant if someone is walking from BEFORE
    # History forward (e.g. slot 12->13 case) — that's exactly the
    # behavior we want, so we DO flag History: as a boundary for
    # other slots. The HISTORY_SLOT exception inside
    # find_heading_match() handles the opposite case (the History
    # slot itself extending forward).
    if _cross_slot_label_regex().match(first):
        return True
    # Structural body-break labels. These aren't Brain-section
    # heading synonyms (so the regex above doesn't catch them) but
    # they DO appear as the start of a paragraph in real policy PDFs
    # and clearly delimit a body region. Treating them as cross-slot
    # boundaries stops the slot-walk before it leaks into the next
    # paragraph.
    return bool(_structural_body_break_regex().match(first))


# ---------------------------------------------------------------------------
# Structural body-break labels (additive on top of cross-slot regex).
#
# These are NOT slot-bound synonyms (their home is in label-row slot 11,
# which is intentionally excluded from the cross-slot regex). But they
# appear as start-of-paragraph markers in real policy PDFs (Flood,
# Earthquake, etc.) and naturally delimit body regions. Recognising
# them as cross-slot boundaries prevents the slot-walk from leaking
# into the next paragraph.
#
# Static allow-list (not env-driven) so the contract is auditable from
# this one location.
# ---------------------------------------------------------------------------
_STRUCTURAL_BODY_BREAK_LABELS: tuple[str, ...] = (
    "required documents",
    "required documentation",
    "documents required",
    "documentation required",
    "required forms",
    "required attachments",
    "annual budget allocation",
    "annual budget",
    "budget allocation",
    "funding allocation",
    "resource allocation",
    "financial allocation",
    # Found as inline body breaks on Flood PDF — slot 12 territory
    # (definitions clause list) and slot 14 territory (history line).
    # Tier 1..9 and Award/Compensation/Payout Tier variants are
    # intentionally excluded — they are SLOT 10's own labels and
    # would falsely trigger a break inside slot 10's tier enumeration.
    "definitions",
    "definition",
    "key definitions",
    "main definitions",
    "core definitions",
    # Definitions clause-list patterns: a paragraph whose text
    # starts with `<subject> means <clause-list>` (e.g. "Company
    # means City Holdings Group; Immediate Family Member ...")
    # is slot-12 territory even when the explicit "Definitions:"
    # heading line is missing (as in the Flood PDF). We match the
    # opener phrase `<word> means` to detect.
    "company means",
    "flood event means",
    "earthquake event means",
    "means city holdings group",
    "history",
)


_STRUCTURAL_BODY_BREAK_REGEX_CACHE: Optional["re.Pattern[str]"] = None


def _structural_body_break_regex() -> "re.Pattern[str]":
    """Build a regex matching any of `_STRUCTURAL_BODY_BREAK_LABELS`
    at the start of a paragraph's first line. Case-insensitive.
    Cached after first build.
    """
    global _STRUCTURAL_BODY_BREAK_REGEX_CACHE
    if _STRUCTURAL_BODY_BREAK_REGEX_CACHE is not None:
        return _STRUCTURAL_BODY_BREAK_REGEX_CACHE
    import re as _re
    # Sort longest-first so multi-word phrases win over sub-phrases.
    joined = "|".join(
        sorted(set(_STRUCTURAL_BODY_BREAK_LABELS), key=len, reverse=True)
    )
    # Match label at the START of a line, optionally preceded by a
    # number ("1. Tier 1"), followed by a separator (colon, dash,
    # period, whitespace, or EOL).
    _STRUCTURAL_BODY_BREAK_REGEX_CACHE = _re.compile(
        r"^\s*(?:\d+\.\s*|\d+\)\s*|[IVX]+\.\s*)?"
        r"(?:" + joined + r")"
        r"(?:[:\-.\ufffd]|\s|$)",
        _re.IGNORECASE,
    )
    return _STRUCTURAL_BODY_BREAK_REGEX_CACHE


# ---------------------------------------------------------------------------
# Inline clause-break detector (separate from first-line check).
#
# Some PDFs (Flood Emergency Assistance Policy) emit the slot-10/12
# body region across multiple extractor paragraphs joined visually
# but split into rows by the PDF column-wrap. The slot-10 body chunk
# ends and slot-12 begins ONLY when a new paragraph starts with
# "Company means City Holdings Group..." — but neither paragraph's
# first line begins with the literal "Definitions:" label (the label
# is dropped or run-in elsewhere).
#
# To handle this, scan the chunk TEXT itself for `. {structural_label}`
# boundaries. When found, treat the chunk as a stop signal AND clip
# the body to end at that boundary (before the clause).
# ---------------------------------------------------------------------------
_INLINE_CLAUSE_BREAK_PER_SLOT_CACHE: dict = {}


def _clip_chunk_at_clause_break(p: str, slot_id: Optional[int] = None) -> str:
    """Clip `p` at the first inline clause-break boundary. Returns
    the original `p` unchanged when no break is found.

    `slot_id` (if provided) filters the structural-list considered:
    labels that "belong" to that slot are excluded from the break
    list so the chunk is NOT clipped mid-tier. Per-slot ownership:
      * slot 10 owns: payout tiers, annual budget, payout tiers,
        tier N labels. None of these trigger a break.
      * slot 11 owns: required documents, documentation required,
        etc. (label-row slots are still subject to bleed-over
        triggers from other slots' labels).
    """
    idx = _find_inline_clause_break(p, slot_id=slot_id)
    if idx is None:
        return p
    return p[:idx].rstrip()


def _find_inline_clause_break(
    p: str, slot_id: Optional[int] = None
) -> Optional[int]:
    """Return the index of the FIRST inline clause break inside `p`,
    or None if no break is found.

    When `slot_id` is provided, structural labels that "belong" to
    that slot are filtered from the regex so they do NOT trigger
    a body-clip.
    """
    rx = _inline_clause_break_regex(slot_id=slot_id)
    m = rx.search(p)
    if m is None:
        return None
    return m.start()


def _inline_clause_break_regex(
    slot_id: Optional[int] = None,
) -> "re.Pattern[str]":
    """Build a regex matching `. {label}{sep}` boundaries. When
    `slot_id` is provided, labels in `_STRUCTURAL_BODY_BREAK_LABELS`
    that are owned by that slot are excluded.

    Currently excluded per-slot:
      * slot 10: `tier n`, `tier 1`..`tier 9`, `pay structure`,
        `award tier`/`s`, `compensation tier`/`s`, `payout tier`/`s`,
        `annual budget allocation`, `annual budget`,
        `budget allocation`, `funding allocation`,
        `resource allocation`, `financial allocation`,
        `award structure`. (All are part of slot 10's payout info;
        no body-clip happens between them.)
      * slot 14: `history`. (Slot 14's own label.)
      * slot 12: `definitions`, `key definitions`, `main definitions`,
        `core definitions`, `definition`. (Slot 12's own label.)
    """
    global _INLINE_CLAUSE_BREAK_REGEX_CACHE
    if slot_id is None:
        # No slot filter — return the all-labels regex (cached on None).
        key = "__None__"
        cache = _INLINE_CLAUSE_BREAK_PER_SLOT_CACHE.setdefault(key, {})
        if cache.get("compiled") is not None:
            return cache["compiled"]
    else:
        key = f"_slot_{slot_id}_"
        cache = _INLINE_CLAUSE_BREAK_PER_SLOT_CACHE.setdefault(key, {})
        if cache.get("compiled") is not None:
            return cache["compiled"]

    import re as _re
    labels = list(_STRUCTURAL_BODY_BREAK_LABELS)
    if slot_id == 10:
        labels = [
            l for l in labels if l
            not in (
                "annual budget", "annual budget allocation",
                "budget allocation", "funding allocation",
                "resource allocation", "financial allocation",
            )
        ]
    elif slot_id == 12:
        labels = [
            l for l in labels if l
            not in (
                "definitions", "definition",
                "key definitions", "main definitions",
                "core definitions",
            )
        ]
    elif slot_id == 14:
        labels = [l for l in labels if l != "history"]

    joined = "|".join(sorted(set(labels), key=len, reverse=True))
    compiled = _re.compile(
        r"[.?!;]\s+(?:" + joined + r")\s*[:\-.\ufffd]?",
        _re.IGNORECASE,
    )
    cache["compiled"] = compiled
    return compiled


_METADATA_MARKER_RE = re.compile(
    r"^\s*[\[\(]\s*(?:english|burmese|myanmar|japanese|chinese|french|spanish|german|"
    r"page\s+\d+|page\s+\d+\s+of\s+\d+|toc|table\s+of\s+contents|"
    r"continued|end\s+of\s+document|confidential|draft|internal\s+use)\s*[\]\)]\s*$",
    re.IGNORECASE,
)


def _is_metadata_marker(paragraph: str) -> bool:
    """True if the paragraph is a metadata marker (language tag, page
    number marker, etc.) that should be excluded from slot body content.

    Examples: "[English]" / "(Page 1 of 10)" / "[Confidential]" /
    "[Draft]" / "[Continued]" / "[End of document]".
    """
    if not paragraph:
        return False
    text = paragraph.strip()
    if not text:
        return False
    return bool(_METADATA_MARKER_RE.match(text))


def build_section_index(paragraphs: List[str]) -> Dict[int, int]:
    """Build a per-paragraph slot index.

    Returns:
        Dict mapping paragraph_idx -> slot_id (1-15). Paragraphs
        not assigned to any slot are NOT in the dict.

    A paragraph is assigned to a slot if:
    - It matches a heading-anchor for that slot, OR
    - It's inside the body of a heading-anchored section for that slot
      (paragraphs[start_idx..end_idx]).

    Slot 1/2/3/4/11 are NOT assigned here (they're handled by
    field_parser). Slot 15 is the logo slot.

    For slot 10 (Award Structure), we also do a heading detection
    even though it's not in HEADING_ANCHOR_SLOTS - the source may
    have a heading paragraph that's the title of a table.

    Iterates slots in their canonical order (slot 5 first, then 6, 7,
    8, 9, 10, 12, 13, 14). Each iteration passes the already-claimed
    paragraph indices as `reserved_paragraphs` so the next slot's
    anchor search skips them. This is Fix 3 — it prevents slot 7 from
    starting on a paragraph that slot 6 already owns, and prevents
    slot 9 from extending its body into slot 7/8 territory.
    """
    idx: Dict[int, int] = {}
    # Process slots in canonical order so each slot's anchor search
    # sees the indices already claimed by earlier slots.
    slot_order: list[int] = sorted(HEADING_ANCHOR_SLOTS)
    claimed: set = set()
    for slot_id in slot_order:
        result = find_heading_match(slot_id, paragraphs, claimed)
        if result is None:
            continue
        start_idx, end_idx, _ = result
        for pi in range(start_idx, end_idx + 1):
            if pi not in idx:
                idx[pi] = slot_id
        claimed.update(range(start_idx, end_idx + 1))
    # Also try slot 10 (Award Structure & Payout Tiers) heading.
    # Slot 10 is in TABLE_SLOTS but not HEADING_ANCHOR_SLOTS; the
    # source may still have a heading paragraph that introduces the
    # table.
    if 10 not in idx:
        for i, p in enumerate(paragraphs):
            if _is_heading_for_slot(10, p):
                # Find the next boundary.
                end = len(paragraphs) - 1
                for j in range(i + 1, len(paragraphs)):
                    if _is_boundary(paragraphs[j]):
                        end = j - 1
                        break
                for pi in range(i, end + 1):
                    if pi not in idx:
                        idx[pi] = 10
                break
    return idx


def get_paragraph_slot(paragraph_idx: int, section_index: Dict[int, int]) -> Optional[int]:
    """Return the slot id assigned to `paragraph_idx`, or None."""
    return section_index.get(paragraph_idx)
