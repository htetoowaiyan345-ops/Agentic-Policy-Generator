"""Shared prose normalizer (Phase Q1).

A format-parity fix for "all input file types must yield the same output".

Strategy
--------

Every extractor returns raw lines.  Lines from different formats have
different shapes:

- PDF (PyMuPDF)  : page.get_text("text").split("\\n")  — one line per
                   visual row, often broken mid-sentence at column
                   boundaries. Reads row by row, ignoring column order.
- PDF (pdfminer) : high-level extraction yields more accurate read-
                   order and column-aware ordering.
- DOCX           : paragraphs are natural language paragraphs already,
                   one line = one paragraph (typically).
- TXT            : splitlines() — one line per newline.
- RTF            : split("\\n") — one line per newline.

To get format parity, every extractor funnels its raw lines through
THIS module, which converts raw lines into a stream of `Block`s:

    Block.kind = "paragraph"   |  {"text": str}
    Block.kind = "label_row"   |  {"pairs": [(label, value), ...]}
    Block.kind = "table"       |  {"rows": [[str, ...]]}

Block stream invariants:
1. The same input content yields the same `blocks` (modulo format-
   specific quirks we explicitly normalize here) regardless of input
   format.
2. Empty raw lines become **paragraph breaks** between two paragraph
   blocks.
3. Mid-sentence line wraps inside a single paragraph get **joined**
   into one paragraph (this is the fix for Flood's
   `'...their immediate'` truncation issue).
4. A `label:` value match on its own line is grouped into a
   `label_row` block; consecutive `label:` lines become a single
   `label_row` block.
5. Continuation rule (conservative — never over-extends):
   - Lines from the SAME source paragraph (joined by `_is_continuation`):
     join only if `prev not terminated by [.!?;:] AND next does NOT
     start with capital letter`, OR if previous is empty/blank, OR if
     next starts with a Brain-canonical label prefix.
   - Sentence boundary (split): split on `[.!?;:] + space + Capital`,
     OR on `\\n\\n` (paragraph break).

The Block stream is consumed by `field_parser` (Phase Q1b) — instead of
parsing cleaned paragraphs of strings, it consumes a flat list of
`Block` objects.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Literal


# Conservative cap on a single paragraph's character length. Without
# this, an over-aggressive merge could produce an entire document in
# one paragraph.  Use 2× the typical prose-paragraph upper bound.
DEFAULT_MAX_PARAGRAPH_CHARS = 1200


_BRAIN_LABEL_REGEX: re.Pattern[str] | None = None
_BRAIN_LABEL_REGEX_AT_BOUNDARY: re.Pattern[str] | None = None


def _ensure_brain_label_regex() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Lazy import so `prose_normalize` doesn't require brain_fields at module
    import time (test discovery can be faster this way).

    Returns (label_pattern, boundary_pattern):
      label_pattern   — matches `Label:` anywhere in the text.
      boundary_pattern — matches `Label:` only at start-of-line OR after
                        a sentence terminator + whitespace.

    Both patterns are CASE-INSENSITIVE because Brain synonyms span
    Title Case input variations like `description` / `Description`.

    IMPORTANT: We match `[:\t]?\\s+` (optional colon/whitespace)
    after the label name so that a synonym entry like `description`
    in brain_fields still matches the input `Description: value`.
    The label name itself doesn't include a colon (because synonyms
    don't), but the input always has `name:` (label followed by
    value). Our suffix covers both shapes:
      `Description:`     (colon before whitespace)
      `Description value` (no colon, just space — unusual but allowed)
    """
    global _BRAIN_LABEL_REGEX, _BRAIN_LABEL_REGEX_AT_BOUNDARY
    if _BRAIN_LABEL_REGEX is None:
        from policy_platform.framework.brain_fields import BRAIN_LABEL_ROWS

        # Build an alternation regex of all canonical labels AND their
        # synonyms. Canonical labels keep their trailing colon; synonyms
        # don't (Brain's synonym list has bare names).
        opts: list[str] = []
        for canonical, syns in BRAIN_LABEL_ROWS:
            opts.append(re.escape(canonical))
            for syn in syns:
                opts.append(re.escape(syn))
        opts.sort(key=len, reverse=True)
        joined = "|".join(opts)
        # Suffix: optional `:`, then whitespace.
        # `[:\t]?` makes the trailing separator optional so synonyms work.
        # `\s+` ensures there is at least one whitespace (or newline)
        # between the label name and the value.
        suffix = r"[:\t]?\s+"
        # Label pattern (no anchor) — `Label[: ] value`.
        _BRAIN_LABEL_REGEX = re.compile(
            r"(?P<lab>(" + joined + r"))" + suffix,
            re.MULTILINE | re.IGNORECASE,
        )
        # Boundary pattern — start-of-line OR right after terminator.
        # `\s*` lets position 0 match without preceding whitespace.
        _BRAIN_LABEL_REGEX_AT_BOUNDARY = re.compile(
            r"(?:^|(?<=[.!?;:])\s*)(?P<lab>(" + joined + r"))" + suffix,
            re.MULTILINE | re.IGNORECASE,
        )
    assert _BRAIN_LABEL_REGEX is not None
    assert _BRAIN_LABEL_REGEX_AT_BOUNDARY is not None
    return _BRAIN_LABEL_REGEX, _BRAIN_LABEL_REGEX_AT_BOUNDARY


_SENTENCE_TERMINATOR_RE = re.compile(r"[.!?;:]\s*$")


@dataclass
class Block:
    kind: Literal["paragraph", "label_row", "table"]
    text: str = ""
    pairs: list[tuple[str, str]] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def _is_continuation(prev: str, nxt: str, *, profile: str = "aggressive") -> bool:
    """Decide whether `nxt` continues the sentence in `prev`.

    Two profiles:
      - `"aggressive"` (default; PDF-friendly): merge across mid-
        sentence column-overflow breaks even when nxt starts with
        a capital letter. The 240-char fragment limit means we
        still split on a hard terminator + long next-line, so we
        don't join unrelated paragraphs.
      - `"conservative"` (DOCX/TXT/RTF-friendly): only merge when
        (a) the previous line has no terminator at the end AND
        (b) the next line starts with a lowercase letter OR a
        Brain-canonical label prefix. Used for non-PDF formats
        where mid-sentence column-wrap breaks are rare.

    Rules (in priority order):
      1. Always continue if either side is empty/whitespace.
      2. Continue if `nxt` starts with a lowercase letter AND
         `prev` does NOT end with a sentence terminator.
      3. Continue if `nxt` starts with a Brain-canonical label prefix
         AND `prev` does NOT end with a sentence terminator.
      4. **aggressive only**: continue if `prev` does NOT end with
         `.!?;:` AND `nxt` is a short fragment (≤ 240 chars). This
         is the only path that joins lines even when `nxt` starts
         with a capital letter — used for PDF column-overflow cases.
      5. Otherwise: split (return False).
    """
    prev = prev.rstrip()
    nxt = nxt.strip()
    if not prev or not nxt:
        return False
    prev_terminated = bool(_SENTENCE_TERMINATOR_RE.search(prev))
    # Rule 2: lowercase start, only when prev is unterminated.
    if (not prev_terminated) and nxt[0].islower():
        return True
    # Rule 3: Brain-label prefix, only when prev is unterminated.
    _, boundary = _ensure_brain_label_regex()
    if (not prev_terminated) and boundary.match(nxt):
        return True
    # Rule 4: aggressive only.
    if profile == "aggressive":
        if (not prev_terminated) and len(nxt) <= 240:
            return True
    return False


def _join_brain_split(text: str) -> list[str]:
    """Treat Brain-label boundaries as paragraph breaks inside a single
    text run.  Returns a list of mini-paragraphs.

    Example: `'Type: HR. Policy Title: Attendance.'` ->
        `['Type: HR.', 'Policy Title: Attendance.']`

    Only label boundaries preceded by `^` or a sentence terminator + space
    are treated as breaks — i.e., a sentence that *contains* a label
    inside its body is NOT split at that label.
    """
    _, boundary = _ensure_brain_label_regex()
    parts: list[str] = []
    cursor = 0
    for m in boundary.finditer(text):
        # m.start() points to the whitespace BEFORE the label; the label
        # itself begins after the whitespace.  We slice up to but not
        # including the whitespace (so the next segment starts at the
        # label, clean).
        chunk = text[cursor : m.start()].rstrip()
        if chunk:
            parts.append(chunk)
        cursor = m.start() + len(m.group(0)) - len(m.group("lab")) - 1  # at the label itself
        # Actually simpler: cursor starts AT the label position.
        # Re-find label position: scan back from m.start() for first non-whitespace.
        s = m.start()
        while s < m.end() and text[s].isspace():
            s += 1
        cursor = s
    rest = text[cursor:].rstrip()
    if rest:
        parts.append(rest)
    return parts


def _label_line_match(line: str) -> tuple[str, str] | None:
    """Return `(label_with_colon, value)` if `line` is `Label: value`.

    Uses regex only (no synonym matching here — caller is responsible).
    """
    s = line.strip()
    if not s:
        return None
    # Same shape as brain_fields._LABEL_LINE_RE.
    m = re.match(
        r"^\s*([A-Za-z][A-Za-z0-9 ()/&.,'\-_]*?)\s*[:\t]\s*(.+?)\s*$",
        s,
    )
    if not m:
        return None
    label = m.group(1).strip()
    value = m.group(2).strip()
    if not value:
        return None
    return f"{label}:", value


def _is_label_value_line(line: str) -> bool:
    return _label_line_match(line) is not None


def raw_lines_to_blocks(
    lines: Iterable[str],
    *,
    max_paragraph_chars: int = DEFAULT_MAX_PARAGRAPH_CHARS,
    profile: str = "aggressive",
) -> list[Block]:
    """Convert raw extracted lines into a Block stream.

    Args:
      lines: Raw extracted line stream (one entry per visual row,
             per paragraph, etc., depending on file format).
      max_paragraph_chars: Defensive cap on paragraph length. Past
             this we flush and start a new paragraph.
      profile: `"aggressive"` or `"conservative"`. Aggressive merges
             across mid-sentence wraps even when nxt starts with a
             capital letter (used for PDFs that break columns). The
             conservative profile only merges when nxt starts
             lowercase or with a Brain-label prefix (used for DOCX/
             TXT/RTF where mid-sentence merges are rare).

    Algorithm
    ---------
    Phase A: collapse raw lines into "raw paragraphs" using blank-line
    breaks and continuation rules.
    Phase B: walk paragraphs and emit `Block` objects.
    """
    raw_list = list(lines)

    paragraphs: list[str] = []
    buf: list[str] = []
    for raw in raw_list:
        s = (raw or "").strip()
        if not s:
            if buf:
                joined = " ".join(b for b in buf if b).strip()
                if joined:
                    paragraphs.append(joined)
                buf.clear()
            continue
        if not buf:
            buf.append(s)
            continue
        prev = " ".join(buf).rstrip()
        if _is_continuation(prev, s, profile=profile) and len(prev) + 1 + len(s) < max_paragraph_chars:
            buf.append(s)
        else:
            joined = " ".join(b for b in buf if b).strip()
            if joined:
                paragraphs.append(joined)
            buf = [s]
    if buf:
        joined = " ".join(b for b in buf if b).strip()
        if joined:
            paragraphs.append(joined)

    blocks: list[Block] = []
    for p in paragraphs:
        clauses = _sentence_split_for_label_blocks(p)
        if clauses and all(_label_line_match(c) is not None for c in clauses):
            pairs: list[tuple[str, str]] = []
            seen: set[str] = set()
            for c in clauses:
                lt = _label_line_match(c)
                if lt is None:
                    continue
                lab, val = lt
                if lab in seen:
                    continue
                seen.add(lab)
                pairs.append((lab, val))
            if pairs:
                blocks.append(Block(kind="label_row", pairs=pairs))
                continue
        blocks.append(Block(kind="paragraph", text=p))
    return blocks


def paragraphs_from_blocks(blocks: list[Block]) -> list[str]:
    """Convenience: flatten a block stream back to a paragraph stream."""
    out: list[str] = []
    for b in blocks:
        if b.kind == "paragraph":
            out.append(b.text)
        elif b.kind == "label_row":
            for lab, val in b.pairs:
                out.append(f"{lab} {val}")
    return out


def _sentence_split_for_label_blocks(paragraph: str) -> list[str] | None:
    """Try to split a paragraph into `Label: value` clauses.

    Returns `None` if the paragraph is NOT a label-rows block (i.e. it
    contains free prose the caller should treat as a regular
    paragraph).  Returns a list of clauses if the paragraph could be
    fully segmented.
    """
    p = paragraph.strip()
    if not p:
        return None
    _, boundary = _ensure_brain_label_regex()

    # If the paragraph starts with a Brain label and contains at least
    # one more Brain label OR ends with a sentence terminator, treat
    # it as a label-row block.  Then split on Brain-label boundaries.
    starts_with_label = bool(boundary.match(p))
    if not starts_with_label:
        return None
    mini = _join_brain_split(p)
    # If the paragraph has at least 2 Brain-label segments OR at least
    # one well-formed label:value pair, it's a label-row block.
    if len(mini) >= 2:
        return mini
    # Single-segment paragraph that IS a label:value line itself.
    if _is_label_value_line(p):
        return [p]
    return None
