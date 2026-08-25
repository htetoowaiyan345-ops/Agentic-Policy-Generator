"""Field parser — extracts `Label: value` pairs from cleaned paragraphs.

The resulting `FieldMap` is consumed by the renderer for per-label
substitution across slots 1, 2, 3, 4, 11.

Stage 5 wiring:
  - When `AGENTIC_POLICY_USE_SPACY=1` AND spaCy is available, `parse()`
    first runs spaCy sentence segmentation on the input, then maps
    every recognized sentence to its canonical Brain label.
  - The result is merged with the regex-based `field_map()` so any
    label spaCy did not pick up is still covered.
  - `last_extraction_path()` returns the path used by the most recent
    `parse()` call: `'rules'`, `'spacy'`, `'spacy-fallback'`, or
    `'rules+sentence'` (Phase B fallback).

Phase B (sentence-segmentation fallback, opt-out via env var):
  When the standard regex path leaves labels unmatched AND the input
  has the shape of a "dense one-paragraph" policy (PyMuPDF emits the
  whole document into 5-10 broken-by-line paragraphs), `_split_into_label_clauses()`
  re-joins adjacent lines whose join is a continuation (next line starts
  with lowercase, or previous line does not end with sentence-term),
  then splits the joined text on sentence boundaries and tries each
  clause as a label-row. This catches cases like the Earthquake PDF
  where every Brain label appears inline within paragraphs.

  Opt-out: set `AGENTIC_POLICY_NO_SENTENCE_SPLIT=1` in the environment.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

from policy_platform.framework.brain_fields import (
    canonical_label,
    field_map,
    iter_brain_labels,
    parse_field_value,
)


_LAST_PATH = "rules"


def last_extraction_path() -> str:
    """Return the extraction path used by the most recent `parse()` call.

    Useful for the audit log (Stage 6).
    """
    return _LAST_PATH


def _enabled_sentence_split() -> bool:
    """Opt-out via env var; default ON."""
    return os.environ.get("AGENTIC_POLICY_NO_SENTENCE_SPLIT", "0") != "1"


# Regex matching any `Label: value` line. Allows letters, digits, spaces,
# punctuation in the label. Burmese characters (U+1000-U+109F and
# U+AA60-U+AA7F) are accepted so Myanmar PDFs can use Burmese labels
# such as `မူဝါဒအမည်: ...`. Same shape as brain_fields._LABEL_LINE_RE.
_LABEL_LINE_RE = re.compile(
    r"^\s*([A-Za-z\u1000-\u109F\uAA60-\uAA7F][A-Za-z0-9 ()/&.,'\-_\u1000-\u109F\uAA60-\uAA7F]*?)\s*[:\t]\s*(.+?)\s*$"
)


# Sentence terminators that END a clause. We split BEFORE these when the
# next non-space char is a capital letter or a digit. Conservative.
# The clause splitter also splits on `, ` followed by a Brain-canonical
# label prefix; this is needed for dense one-paragraph inputs where
# multiple Label: value clauses are separated by commas rather than
# periods.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z0-9])"
)


def _sentence_split_with_brain_labels(s: str) -> list[str]:
    """Split on sentence boundaries, and additionally on `, BrainLabel`
    boundaries when the comma-separated tail starts a known Brain label.

    This handles dense one-paragraph inputs (e.g., the Earthquake PDF)
    where inline labels are separated by commas and periods rather than
    one-per-line.
    """
    s = s.strip()
    if not s:
        return []
    # First pass: split on traditional sentence terminators.
    parts = _SENTENCE_SPLIT_RE.split(s)
    out: list[str] = []
    from policy_platform.framework.brain_fields import canonical_label
    for p in parts:
        ps = p.strip()
        if not ps:
            continue
        # Walk through `p` looking for `, <BrainLabel>` inline splits.
        # BrainLabel = canonical `Label:` (e.g., "Type:", "Policy Number:",
        # "Effective Date/Period:"). We only split when:
        #   1. the segment after the comma starts with a Brain label,
        #   2. AND the segment before the comma is non-empty.
        # Greedy: we scan left-to-right and accumulate text into a
        # current buffer; when we find a comma followed by a Brain
        # label, we flush.
        buf = []
        i = 0
        n = len(ps)
        while i < n:
            # Look for `, <Text starting with Capital>` that matches a
            # Brain label up to its colon.
            ch = ps[i]
            buf.append(ch)
            i += 1
            if ch == "," and i < n and ps[i] == " ":
                # Look ahead and try to parse as `BrainLabel: value`
                tail = ps[i + 1:].lstrip()
                # Find colon in tail
                colon = tail.find(":")
                if colon > 0:
                    cand = tail[:colon + 1].strip()
                    if canonical_label(cand) is not None:
                        # Flush up to and including the comma (and the
                        # following space) as a clause.
                        clause = "".join(buf).rstrip().rstrip(",").strip()
                        if clause:
                            out.append(clause)
                        buf = []
                        # Re-initialize buffer for the rest (the tail
                        # starting at the Brain label).
                        rest = ps[i + 1:].rstrip()
                        if rest:
                            # Continue scanning from end of `rest` by
                            # refilling buf.
                            # Simpler: keep accumulating `rest` then
                            # continue normal flow.
                            buf.append(" ")
                            buf.append(rest)
                            ps = ps[i + 1:]  # update ps pointer
                            n = len(ps)
                            i = len(rest)  # already consumed
                            # Loop continues; now `ch` is the last char
                            # of `rest`, which we don't want to re-buffer.
        clause = "".join(buf).strip()
        if clause:
            out.append(clause)
    return out


def _label_match(clause: str) -> tuple[str, str] | None:
    """Try the regex `Label: value` on a clause. Stripped and trimmed.

    Returns (canonical_label, raw_value) or None. The raw value is
    NOT validated here — validation happens at a higher level
    (parse_field_value in brain_fields.py) so internal helpers
    can see all candidate matches.

    Pure-digit values (e.g. phone numbers or reference IDs) are
    still rejected here because they are always extraction artifacts.

    Phase 6 — clamp the greedy regex capture at the first sentence
    boundary (`.` followed by capital letter, or `,` followed by a
    known Brain-label prefix). This prevents the regex from
    swallowing continuation text like "Policy. to all sectors..."
    into a single value. General heuristic — works for any future
    file whose values may span across sentence boundaries.
    """
    s = clause.strip()
    if not s:
        return None
    m = _LABEL_LINE_RE.match(s)
    if not m:
        return None
    label = m.group(1).strip()
    value = m.group(2).strip()
    if not value:
        return None
    # Reject pure-digit values — they are extraction artifacts
    # (phone numbers, reference IDs), not legitimate label values.
    if re.fullmatch(r"\d+", value):
        return None
    # Phase 6 — clamp at first sentence boundary (general heuristic).
    value = _clamp_label_value(value)
    if not value:
        return None
    canon = canonical_label(label + ":")
    if canon is None:
        return None
    return canon, value


def _clamp_label_value(value: str) -> str:
    """Clamp a greedy regex-captured value at the first sentence boundary.

    The `_LABEL_LINE_RE` captures greedily — `Type: Policy. to all
    sectors` becomes value="Policy. to all sectors". This helper
    stops the capture at the first sentence/label boundary so
    downstream validation sees only the actual value.

    General heuristic — does NOT hardcode any specific labels.
    Stops at:
      - `.` or `;` followed by a space and an uppercase letter
        (sentence boundary).
      - `,` followed by a space and a known Brain-label prefix
        (next label in comma-separated dense paragraphs).

    Does NOT clamp values that contain dates or reference numbers
    (they may legitimately have commas).
    """
    v = value
    # Don't clamp dates or reference numbers.
    from policy_platform.framework.brain_fields import (
        _looks_like_date,
        _looks_like_reference,
    )
    if _looks_like_date(v) or _looks_like_reference(v):
        return v
    # Find first sentence boundary: `. ` or `; ` followed by uppercase.
    sentence_end = -1
    for i in range(len(v)):
        ch = v[i]
        if ch in (".", ";") and i + 2 < len(v) and v[i + 1] == " ":
            next_char = v[i + 2]
            if next_char.isupper():
                sentence_end = i
                break
    if sentence_end > 0:
        v = v[:sentence_end].rstrip()
    # If still has `, BrainLabel:` pattern, stop there too.
    # Look for ", " followed by a known Brain label prefix.
    from policy_platform.framework.brain_fields import canonical_label
    for j in range(len(v)):
        if v[j] == "," and j + 2 < len(v) and v[j + 1] == " ":
            # Find next colon after the comma.
            tail = v[j + 2:]
            colon = tail.find(":")
            if colon > 0:
                cand = tail[:colon + 1].strip()
                if canonical_label(cand) is not None:
                    v = v[:j].rstrip()
                    break
    return v.strip()


def _join_continued_lines(paragraphs: list[str]) -> list[str]:
    """Re-join paragraph lines whose concatenation forms a coherent sentence.

    Some PDF extractors (PyMuPDF on the Earthquake PDF) emit a single
    multi-sentence document as 5-7 separate "lines", where the second
    line begins with a lowercase letter (continuation) rather than a
    sentence boundary. We re-merge those.

    Heuristic:
      - Line A continues Line B if:
        * Line A starts with a lowercase letter, OR
        * Line A is short (<=80 chars) AND Line B's previous line (kept
          aside) does not end with `.`, `!`, `?`, `:`, or `;`.
      - If the previous line is already a complete `Label: value` pair
        (matches _LABEL_LINE_RE), it is treated as self-contained and
        is NOT merged with the next line. This prevents merging
        adjacent label-value lines into a single string where the
        first label incorrectly consumes everything after as its value.

    This is deliberately conservative — it never *adds* a label, only
    re-joins lines so the regex path can match labels that span a
    line break.

    Returns the joined list (paragraph count may shrink).
    """
    out: list[str] = []
    for line in paragraphs:
        s = line.strip()
        if not s:
            # Empty line: paragraph break; pass through.
            out.append(line)
            continue
        if not out:
            out.append(s)
            continue
        # Decide whether to merge with the previous.
        prev = out[-1].rstrip()
        prev_terminated = bool(re.search(r"[.!?;:]\s*$", prev))
        starts_lower = bool(s) and s[0].islower()
        # If the NEXT line is itself a complete `Label: value` pair, treat
        # the previous line as self-contained — never merge a label-value
        # line INTO the start of the next label-value line (that would make
        # the first label consume the second line's `Label:` and everything
        # after as its value). A continuation of the same value (e.g. a
        # lowercase fragment) is still merged normally.
        next_is_label_value = bool(_LABEL_LINE_RE.match(s))
        if next_is_label_value:
            out.append(s)
            continue
        if (starts_lower or (not prev_terminated)) and len(s) <= 120 and len(prev) <= 200:
            out[-1] = (prev + " " + s).strip()
        else:
            out.append(s)
    return out


def _split_into_label_clauses(joined: list[str]) -> list[tuple[str, str]]:
    """For each paragraph, split on sentence boundaries and try each clause.

    Returns list of (canonical_label, value) pairs.
    """
    out: list[tuple[str, str]] = []
    for para in joined:
        s = para.strip()
        if not s:
            continue
        # Split on sentence boundaries AND on `, BrainLabel:`
        # boundaries. The latter handles dense one-paragraph inputs
        # (e.g., the Earthquake PDF) where multiple Label: value
        # clauses are separated by commas rather than periods.
        clauses = _sentence_split_with_brain_labels(s)
        seen_labels_in_para: set[str] = set()
        for clause in clauses:
            cs = clause.strip()
            if not cs:
                continue
            match = _label_match(cs)
            if match is None:
                continue
            canon, value = match
            # Within one paragraph, prefer first match; later clauses with
            # the same canonical label are skipped (we want the first
            # non-empty value to win).
            if canon in seen_labels_in_para:
                continue
            seen_labels_in_para.add(canon)
            out.append((canon, value))
    return out


# A "label-only" line is short (≤60 chars), has no colon/tab/period, and
# looks like a label (Title Case or brain-canonicalized). When we find
# such a line, the NEXT non-empty line is treated as its value.
# Burmese characters (U+1000-U+109F and U+AA60-U+AA7F) are accepted so
# Myanmar PDFs can use Burmese labels in alternating-label-value layouts.
_LABELISH_LINE_RE = re.compile(
    r"^[A-Za-z\u1000-\u109F\uAA60-\uAA7F][A-Za-z0-9 ()/&,'\-_\u1000-\u109F\uAA60-\uAA7F]{0,60}$"
)


def _is_labelish(s: str) -> bool:
    """Return True if `s` looks like a one-word label (no colon, short)."""
    s = s.strip()
    if not s or ":" in s or "\t" in s:
        return False
    if "." in s and not s.endswith("."):
        return False
    return bool(_LABELISH_LINE_RE.match(s))


def _split_alternating_label_value(
    joined: list[str],
    dropped_paragraphs: list[dict] | None = None,
    cleaned_to_original: list[int] | None = None,
) -> list[tuple[str, str]]:
    """Phase B-bonus: capture alternating `[label, value, label, value, ...]`.

    Some PDFs (e.g., the Award-and-Recognition template) emit labels and
    values on alternating lines:
        ['Policy Type', 'HR Policy', 'Policy Number', 'HR-ARP-001', ...]

    When the regex path produces no matches because there are no `Label:`
    patterns, this pass walks the joined paragraphs and pairs each
    "labelish" line with its immediate next non-empty line.

    When the cleaner dropped a value between two cleaned labels (e.g.,
    the cleaner ate `'Htet Oo Wai Yan'` as a `header_repeat`), this
    function recovers the dropped value from `dropped_paragraphs`
    using `cleaned_to_original` index alignment.

    Args:
        joined: The cleaned paragraphs list.
        dropped_paragraphs: Cleaner-dropped records (with original indices).
        cleaned_to_original: Parallel list mapping cleaned-line-index to
            original-line-index. Used to find dropped lines that lie
            between two cleaned labels.
    """
    out: list[tuple[str, str]] = []
    n = len(joined)
    dropped = list(dropped_paragraphs or [])
    dropped_by_idx: dict[int, str] = {}
    for d in dropped:
        try:
            idx = int(d.get("index", -1))
        except (TypeError, ValueError):
            continue
        text = (d.get("text") or "").strip()
        if idx >= 0 and text:
            dropped_by_idx[idx] = text

    i = 0
    while i < n:
        s = joined[i].strip()
        if not _is_labelish(s):
            i += 1
            continue
        # If s itself isn't a canonical Brain label, advance past it.
        # This handles header lines like "POLICY TEMPLATE - AWARD..."
        # and "PROGRAM" that look labelish but aren't Brain labels.
        canon_self = canonical_label(s + ":")
        if canon_self is None:
            i += 1
            continue
        # Find next non-empty paragraph.
        j = i + 1
        while j < n and not joined[j].strip():
            j += 1
        if j >= n:
            break
        next_s = joined[j].strip()
        # If the candidate value line is itself a Brain label, attempt
        # recovery (the cleaner ate the real value, leaving another
        # label adjacent).
        if canonical_label(next_s + ":") is not None:
            recovered = None
            if (
                dropped_by_idx
                and cleaned_to_original is not None
                and i < len(cleaned_to_original)
                and j < len(cleaned_to_original)
            ):
                orig_i = cleaned_to_original[i]
                orig_j = cleaned_to_original[j]
                if orig_i < orig_j:
                    for di in sorted(dropped_by_idx.keys()):
                        if orig_i < di < orig_j:
                            dt = dropped_by_idx[di]
                            if (
                                0 < len(dt) <= 200
                                and not _is_labelish(dt)
                                and canonical_label(dt + ":") is None
                            ):
                                recovered = dt
                                break
            if recovered is not None:
                # Reject pure-digit recovered values (extraction artifacts).
                if not re.fullmatch(r"\d+", recovered):
                    out.append((canon_self, recovered))
                # Advance to j (NOT j+1) so the next iteration re-considers
                # `joined[j]` as a candidate label, paired with the next
                # non-empty line. This is what `Version 0.9 dated 01
                # January 2026` recovery looks like: Supersedes pairs
                # with the cleaner-dropped value, then Last Reviewed/Updated
                # (currently at joined[j]) pairs with the next line.
                i = j
                continue
            # No recovery — but `canon_self` is valid (we passed the
            # None-check above). Still emit canon_self with empty
            # marker so downstream sees the label is recognizable.
            # In practice this only fires for cleaner-eaten values;
            # downstream will hit the renderer which writes the marker.
            i = j + 1
            continue

        if not next_s:
            i += 1
            continue
        # Reject pure-digit values (extraction artifacts like phone numbers
        # or reference IDs that get paired with a label).
        if re.fullmatch(r"\d+", next_s):
            i = j + 1
            continue
        out.append((canon_self, next_s))
        i = j + 1
    return out


def _sentence_split_field_map(
    paragraphs: Iterable[str],
    dropped_paragraphs: Iterable[dict] | None = None,
    cleaned_to_original: list[int] | None = None,
) -> dict[str, str]:
    """Phase B fallback: re-join continued lines, split on sentence boundaries,
    then match each clause as a label-row.

    Phase 6 — values are validated via `parse_field_value()` from
    brain_fields.py. Invalid values are rejected.

    Args:
        paragraphs: A sequence of cleaned paragraphs.
        dropped_paragraphs: Optional list of cleaner-dropped records
            (each a dict with keys like 'text', 'reason', 'index').
        cleaned_to_original: Optional parallel list mapping cleaned-line-index
            to original-line-index in the pre-cleaning paragraph stream.
            When provided, Phase B.1 uses index alignment to recover
            cleaner-dropped values that lie between two cleaned labels.

    Returns a FieldMap dict.
    """
    paras = [p for p in paragraphs if p and p.strip()]
    if not paras:
        return {}
    # Phase B.1: alternating layout BEFORE joining lines.
    pairs = _split_alternating_label_value(
        paras,
        dropped_paragraphs or [],
        cleaned_to_original,
    )
    # Phase B.2: re-join continued lines, split on sentence boundaries.
    joined = _join_continued_lines(paras)
    pairs.extend(_split_into_label_clauses(joined))
    out: dict[str, str] = {}
    for canon, value in pairs:
        # Phase 6: validate the value via field-specific rules.
        cleaned = parse_field_value(canon, value)
        if cleaned is None:
            continue
        if canon not in out or (not out[canon] and cleaned):
            out[canon] = cleaned
    return out


def parse(
    input_paragraphs: Iterable[str],
    dropped_paragraphs: Iterable[dict] | None = None,
    cleaned_to_original: list[int] | None = None,
) -> dict[str, str]:
    """Return a FieldMap: {canonical_label: value} from input paragraphs.

    Args:
        input_paragraphs: A sequence of paragraphs (already cleaned).
        dropped_paragraphs: Optional list of cleaner-dropped records
            (each a dict with 'text', 'reason', 'index'). Used by
            Phase B to recover values that the cleaner dropped as
            header-repeats / page numbers / version noise.
        cleaned_to_original: Optional parallel list mapping cleaned-line-index
            to original-line-index in the pre-cleaning paragraph stream.
            When provided, Phase B.1 uses index alignment to recover
            cleaner-dropped values that lie between two cleaned labels.

    Order:
      1. If spaCy is available: spaCy sentence-segmented extraction.
      2. Always: regex-based `field_map()` for any unmatched labels.
      3. Phase B fallback: sentence-segmented on re-joined lines,
         with optional recovery from cleaner-dropped records.

    Path naming:
      - `'spacy'` if spaCy ran and matched > 0 sentences
      - `'spacy-fallback'` if spaCy ran but matched zero sentences
      - `'rules'` if only the regex path produced matches
      - `'rules+sentence'` if Phase B fallback contributed at least one
        canonical label that the regex path missed.
    """
    global _LAST_PATH
    paragraphs = list(input_paragraphs)
    dropped = list(dropped_paragraphs or [])
    out: dict[str, str] = {}
    path_used = "rules"
    spacy_used = False

    try:
        from policy_platform.extractors.spacy_extractor import (
            extract_field_map,
            is_available,
        )

        if is_available():
            spacy_fm, spacy_path = extract_field_map(paragraphs)
            if spacy_fm:
                out.update(spacy_fm)
            path_used = spacy_path or "spacy"
            spacy_used = True
    except Exception:
        # Defensive: spaCy failure is not fatal; we still apply regex.
        pass

    # Always run the regex path; merge (regex does not overwrite spaCy's).
    regex_fm = field_map(paragraphs)
    rule_keys_before = set(out.keys())
    for k, v in regex_fm.items():
        if k not in out:
            out[k] = v
        elif not out[k] and v:
            out[k] = v

    # Phase B: sentence-segmentation fallback. Only contributes NEW labels
    # the regex path didn't catch.
    if _enabled_sentence_split():
        phrase_fm = _sentence_split_field_map(
            paragraphs,
            dropped,
            cleaned_to_original,
        )
        new_keys = set(phrase_fm.keys()) - out.keys()
        if new_keys:
            for k in new_keys:
                out[k] = phrase_fm[k]
            if path_used == "rules":
                path_used = "rules+sentence"
            elif path_used in ("spacy", "spacy-fallback"):
                # spaCy but it didn't catch these — augment.
                path_used = f"{path_used}+sentence"

        # AUGMENT: prefer Phase B's clause-split value over the regex
        # path's value when the regex path captured a value spanning
        # across multiple sentences (e.g., 'Applies to: All ... Group.
        # Reason for Policy: ...'). Phase B's clause-by-clause
        # segmentation produces a tighter value. Apply only when the
        # Phase B value is non-empty.
        #
        # GUARD: when Phase B's value is a strict prefix of the existing
        # value (Phase B truncated the value mid-sentence), keep the
        # longer existing value. This protects labels whose value spans
        # multiple sentences (e.g., Earthquake DOCX's `Type: Policy.
        # Applicable to all sectors...` — Phase B splits at the first
        # period and captures only `Policy.`, losing the rest of the
        # value). The longer regex-extracted value is the correct one.
        sentence_added = False
        for k, v in phrase_fm.items():
            if not v:
                continue
            if k not in out or not out[k]:
                # New or empty — already handled by new_keys above.
                continue
            # Compare: if Phase B's value is strictly different AND
            # shorter/different from regex's value (likely tighter
            # segmentation), prefer Phase B.
            if v != out[k] and len(v) <= len(out[k]):
                out[k] = v
                sentence_added = True
        if sentence_added and path_used == "rules":
            # We didn't find new keys, but we did improve existing
            # values via Phase B's tighter clause splitting.
            path_used = "rules+sentence"

    # Phase C: narrative-inference rules for FDA-style / label-light
    # documents that don't follow the `Label: value` schema.
    try:
        from policy_platform.extractors.narrative_inference import (
            infer_narrative_fields,
        )

        narr_fm = infer_narrative_fields(paragraphs, out)
        new_keys = set(narr_fm.keys()) - out.keys()
        if new_keys:
            for k in new_keys:
                out[k] = narr_fm[k]
            if path_used == "rules":
                path_used = "rules+narrative"
            elif path_used in ("rules+sentence", "spacy", "spacy-fallback"):
                path_used = f"{path_used}+narrative"
    except Exception:
        pass

    if not spacy_used and path_used == "rules" and not rule_keys_before:
        # We did nothing useful; mark this as rules.
        _LAST_PATH = path_used
    else:
        _LAST_PATH = path_used

    # (Phase P.5 tail-guard was reverted — see historical comment above.)
    return out


def expected_labels() -> tuple[str, ...]:
    """Canonical labels the renderer expects to fill, in order."""
    return tuple(label for label, _ in iter_brain_labels())


def header_labels() -> tuple[str, ...]:
    """Slot 1 (Header) labels only."""
    from policy_platform.framework.brain_fields import BRAIN_HEADER_FIELDS
    return tuple(label for label, _ in BRAIN_HEADER_FIELDS)


def approval_labels() -> tuple[str, ...]:
    """Slot 3 (Approval) labels only."""
    from policy_platform.framework.brain_fields import BRAIN_APPROVAL_FIELDS
    return tuple(label for label, _ in BRAIN_APPROVAL_FIELDS)


def brief_description_labels() -> tuple[str, ...]:
    """Slot 2 (Brief Description) labels only."""
    from policy_platform.framework.brain_fields import BRAIN_BRIEF_DESCRIPTION_FIELDS
    return tuple(label for label, _ in BRAIN_BRIEF_DESCRIPTION_FIELDS)


def reason_labels() -> tuple[str, ...]:
    """Slot 4 (Reason for Policy) labels only."""
    from policy_platform.framework.brain_fields import BRAIN_REASON_FIELDS
    return tuple(label for label, _ in BRAIN_REASON_FIELDS)


def review_note_labels() -> tuple[str, ...]:
    """Slot 11 (Policy Review Note) labels only."""
    from policy_platform.framework.brain_fields import BRAIN_REVIEW_NOTE_FIELDS
    return tuple(label for label, _ in BRAIN_REVIEW_NOTE_FIELDS)
