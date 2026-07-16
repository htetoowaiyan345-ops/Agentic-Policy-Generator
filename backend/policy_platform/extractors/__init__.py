from __future__ import annotations

from pathlib import Path

from . import docx_extractor, doc_extractor, pdf_extractor, rtf_extractor, txt_extractor
from .base import ExtractedDocument
from .cleaner import clean_paragraphs
from .doc_extractor import UnsupportedFormatError


SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".docx", ".txt", ".rtf"}


def _join_mid_sentence_lines(lines: list[str]) -> list[str]:
    """Phase R: minimal cross-format column-wrap fix.

    Joins adjacent cleaned lines when one of the following holds:

    1. **Lowercase-continuation rule (universal)**: previous line
       has no terminator `[.!?;:]` AND next line starts with a
       lowercase letter. This handles Flood's
       `'...their immediate'` truncation and similar PDF column-
       wrap patterns. Applies to all formats.

    2. **Brain-label-continuation rule (PDF only)**: previous line
       ends with a Brain label (e.g., `'Prepared by: Group'`) AND
       the next line is a short fragment (<= 240 chars) that does
       NOT start with another Brain label AND is NOT a section
       heading (e.g., `INTRODUCTION`, `POLICY STATEMENT`,
       `1. Purpose`, etc.). This handles the case where a label's
       value is wrapped across two visual rows in the source PDF,
       e.g.:
           `'...Prepared by: Group'`
           `'Corporate Affairs and Human Resources.'`
       The second line starts with capital C (so rule 1 wouldn't
       fire), but it's a value continuation of `Prepared by:`.

    Blank lines are paragraph breaks (preserved).
    Empty/whitespace-only lines are passed through unchanged.
    """
    import re as _re

    # Lazy import to avoid circulars.
    # `_ensure_brain_label_regex` returns `(label_pattern, boundary_pattern)`.
    # The LABEL pattern matches `Label: value` anywhere; the BOUNDARY
    # pattern only matches at start-of-line OR after `[.!?;:] + space`.
    # For the label-continuation check, we want the LABEL pattern so
    # we detect mid-paragraph labels like `'...Type: Policy'`.
    from .prose_normalize import _ensure_brain_label_regex

    label_regex, _boundary = _ensure_brain_label_regex()

    # Build a regex of "looks like a section heading" so we don't
    # merge into a line that starts with one.
    section_heading_re = _build_section_heading_regex()

    out: list[str] = []
    for line in lines:
        s = (line or "").strip()
        if not s:
            out.append(line)
            continue
        if not out:
            out.append(s)
            continue
        prev = out[-1].rstrip()
        prev_stripped = prev.rstrip()
        prev_terminated = bool(_re.search(r"[.!?;:]\s*$", prev_stripped))
        starts_lower = bool(s) and s[0].islower()
        # Rule 1: lowercase continuation.
        if (not prev_terminated) and starts_lower:
            out[-1] = (prev_stripped + " " + s).strip()
            continue
        # Rule 2: brain-label continuation.
        # Block if nxt looks like a section heading (`INTRODUCTION`,
        # `1. Purpose`, etc.) — we must not bleed that into the
        # previous label's value.
        looks_like_heading = bool(section_heading_re.match(s))
        if (
            (not prev_terminated)
            and len(s) <= 240
            and not label_regex.match(s)
            and not looks_like_heading
        ):
            if _prev_has_unterminated_brain_label(prev_stripped, label_regex):
                out[-1] = (prev_stripped + " " + s).strip()
                continue
        out.append(s)
    return out


def _build_section_heading_regex() -> "re.Pattern[str]":
    """Return a regex that matches strings that look like Brain
    section headings (e.g., `INTRODUCTION`, `1. Purpose`,
    `2. Scope & Beneficiaries`).

    Built dynamically from `framework.section_map.FROZEN_SECTIONS`.
    """
    import re as _re
    from policy_platform.framework.section_map import FROZEN_SECTIONS
    titles: list[str] = []
    for sec in FROZEN_SECTIONS:
        title = (sec.get("title") or "").strip()
        if not title:
            continue
        titles.append(_re.escape(title))
    if not titles:
        return _re.compile(r"^\b\B$")
    pat = r"^(?:" + "|".join(titles) + r")\b"
    return _re.compile(pat, _re.IGNORECASE)


def _prev_has_unterminated_brain_label(prev: str, label_regex) -> bool:
    """True if `prev` ends with `Label: <value>` where value is
    non-empty and unterminated (no `.!?;:` at end).

    Walks back from the end of `prev` to find the LAST Brain label
    position. If that position is followed by a value with no
    terminator, returns True.

    Examples (return True):
        `'...Effective Date/Period: 01 July 2026'`
        `'...Prepared by: Group'`
        `'...Approved by: Group CEO. Responsible Functions: Foo'`
    Examples (return False):
        `'...Approved by: Group CEO.'` (terminator)
        `'Flood Emergency Assistance Policy'` (no label)
        `'Brief Description'` (no colon — header)
    """
    import re as _re
    matches = list(label_regex.finditer(prev))
    if not matches:
        return False
    last = matches[-1]
    matched_text = last.group(0)
    if ":" not in matched_text:
        return False
    after = prev[last.end():]
    after_stripped = after.lstrip()
    if not after_stripped:
        return False
    return not bool(_re.search(r"[.!?;:]\s*$", after_stripped))


def _normalize_label_colons(lines: list[str]) -> list[str]:
    """Insert a colon between a Brain-label synonym and its value when
    the source omits one.

    Two cases:
      1. `Label. value` — synonym ends with a period (e.g., `Policy No.`).
         We insert a colon: `Policy No.: value`.
      2. `Label value` — synonym has no separator (e.g., pdfplumber's
         visual-row output: `Policy Type Academic Examination Policy`).
         We insert a colon: `Policy Type: Academic Examination Policy`.

    Both cases are detected by trying the label against
    `canonical_label()`. The result makes the line match the standard
    `Label: value` regex that the field_parser uses.
    """
    import re as _re
    from policy_platform.framework.brain_fields import canonical_label
    out: list[str] = []
    for line in lines:
        s = (line or "").strip()
        if not s:
            out.append(line)
            continue
        # Case 1: Label. value (synonym ends with period).
        m = _re.match(r"^([A-Z][A-Za-z ()/&.'\-]+?)\.\s+(\S.+)$", s)
        if m:
            label_part = m.group(1)
            value_part = m.group(2)
            canon = canonical_label(label_part + ":")
            if canon is not None:
                out.append(f"{label_part}.: {value_part}".strip())
                continue
        # Case 2: Label value (no separator at all, e.g., pdfplumber's
        # visual-row output). Walk forward from the start of the line
        # to find a label-like prefix that is a COMPLETE Brain label
        # (not a prefix of a longer one). The label is followed by
        # whitespace then the value.
        #
        # IMPORTANT: only fire Case 2 if the line does NOT already
        # contain a colon separator. If the source already has a
        # colon (e.g., "Reason for Policy: To establish ..."), the
        # split function will handle it correctly. Inserting another
        # colon would corrupt the line.
        if ":" in s:
            out.append(line)
            continue
        # Try the LONGEST possible prefix first, then shorten until
        # is_exact_label_or_synonym() accepts it. We use the exact
        # match (not canonical_label's prefix-match) to avoid over-
        # eager matches like "Brief Description Framework governing"
        # being treated as a label when only "Brief Description" is
        # a real label.
        #
        # IMPORTANT: require the value to have at least 1 word
        # (so we don't split a label-only line like "Effective
        # Date/Period" into "Effective: Date/Period"). The regex
        # backtracks greedily, so a 2-word line would otherwise
        # be split as label + 1-word "value".
        from policy_platform.framework.brain_fields import is_exact_label_or_synonym
        # Require at least 3 whitespace-separated tokens: 2+ for
        # label and 1+ for value. This prevents splitting
        # 2-word label-only lines.
        if len(s.split()) < 3:
            out.append(line)
            continue
        m2 = _re.match(r"^([A-Z][A-Za-z ()/&.'\-]{1,60})(\s+)(\S.+)$", s)
        if m2:
            label_part = m2.group(1)
            value_part = m2.group(3)
            words = label_part.split()
            best_label = None
            for n in range(len(words), 0, -1):
                cand = " ".join(words[:n])
                if is_exact_label_or_synonym(cand) is not None:
                    remainder = label_part[len(cand):].lstrip()
                    if remainder:
                        # Reject if remainder's first word is itself
                        # a label — that suggests the label is
                        # actually longer.
                        first_remainder_word = remainder.split(None, 1)[0]
                        if is_exact_label_or_synonym(first_remainder_word) is not None:
                            continue
                    # Reject if the value is too short to be a real
                    # value. Conservative rules:
                    # - 1-word value: only accept if it has distinctive
                    #   chars (digits, hyphens, slashes) — e.g.,
                    #   "EDU-MAT-001", "2026".
                    # - 2-word value: only accept if it has distinctive
                    #   chars OR the first word is a short uppercase
                    #   acronym (e.g., "HR Policy").
                    # - 3+ word value: always accept (unless it's a
                    #   suffix of a longer canonical label).
                    candidate_value = (
                        (remainder + " " + value_part).strip()
                        if remainder
                        else value_part
                    )
                    val_word_count = len(candidate_value.split())
                    has_distinctive = bool(_re.search(r"[\d\-_/]", candidate_value))
                    val_words = candidate_value.split()
                    first_val_word = val_words[0] if val_words else ""
                    is_acronym = (
                        bool(first_val_word)
                        and len(first_val_word) <= 5
                        and first_val_word.isupper()
                    )
                    if val_word_count == 1 and not has_distinctive:
                        continue
                    # 2-word value: accept if it has distinctive chars,
                    # is an acronym, or NEITHER word is a label
                    # (e.g., "Human Resources", "Award Nomination").
                    if val_word_count == 2 and not has_distinctive and not is_acronym:
                        no_word_is_label = all(
                            is_exact_label_or_synonym(w) is None for w in val_words
                        )
                        if not no_word_is_label:
                            continue
                    # Also reject if the candidate_value is a suffix
                    # of a longer canonical label. E.g., for
                    # "Reason for Policy" with cand="Reason" and
                    # value="for Policy", the value "for Policy" is
                    # a suffix of the canonical "Reason for Policy".
                    # The label is actually longer.
                    from policy_platform.framework.brain_fields import (
                        BRAIN_LABEL_ROWS,
                        _norm,
                    )
                    value_norm = _norm(candidate_value)
                    value_is_label_suffix = False
                    for canon_candidate, _ in BRAIN_LABEL_ROWS:
                        canon_norm = _norm(canon_candidate.rstrip(":"))
                        if (
                            canon_norm.endswith(value_norm)
                            and canon_norm != value_norm
                        ):
                            value_is_label_suffix = True
                            break
                    if value_is_label_suffix:
                        continue
                    # Also reject if extending the label by ONE more
                    # word (the value's first word) would produce a
                    # canonical label, UNLESS the value is very long
                    # (4+ words) — in which case accepting the shorter
                    # label is fine because the value is clearly
                    # substantial content.
                    #
                    # Example: "Approved By Academic Board" — cand
                    # "Approved" with value "By Academic Board" (3
                    # words) is rejected because cand + "By" =
                    # "Approved By" is a longer canonical. But cand
                    # "Approved By" with value "Academic Board" (2
                    # words) also passes the 2-word threshold (no
                    # label words). So the loop will try the longer
                    # label on the next iteration.
                    first_val_word = candidate_value.split(None, 1)[0]
                    if val_word_count < 4:
                        extended = f"{cand} {first_val_word}".strip()
                        if first_val_word and is_exact_label_or_synonym(extended) is not None:
                            # Extending makes a longer canonical — try a
                            # longer label candidate.
                            continue
                    best_label = cand
                    value_part = candidate_value
                    break
            # Fallback: if the loop exhausted without finding a match,
            # try the LONGEST valid canonical label and use the
            # remaining words as the value, regardless of threshold
            # checks. This handles edge cases like
            # "Prepared By Examination Department" where:
            #   - cand="Prepared By" (value="Examination Department",
            #     2 words, "Department" is a label synonym → rejected)
            #   - cand="Prepared" (value="By Examination Department",
            #     3 words, extending makes canonical → rejected)
            # The correct split is "Prepared By" + "Examination
            # Department", which the threshold rejects because
            # "Department" is a label. In this fallback, we accept
            # the 2-word value with a label word because the overall
            # line has 4+ words and the split is unambiguous.
            if best_label is None and len(s.split()) >= 4:
                for n in range(len(words), 0, -1):
                    cand = " ".join(words[:n])
                    if is_exact_label_or_synonym(cand) is not None:
                        remainder = label_part[len(cand):].lstrip()
                        candidate_value = (
                            (remainder + " " + value_part).strip()
                            if remainder
                            else value_part
                        )
                        if candidate_value:
                            best_label = cand
                            value_part = candidate_value
                            break
            if best_label is not None:
                value_part = _deduplicate_value_leading_label(
                    best_label, value_part
                )
                out.append(f"{best_label}: {value_part}".strip())
                continue
        out.append(line)
    return out


def _deduplicate_value_leading_label(label: str, value: str) -> str:
    """Strip leading word-duplication from the value.

    Handles two patterns:
      1. `Supersedes: Version 0.9` where the first word of the value
         exactly matches the label name.
      2. `Supersedes: Version Version 0.9` where the first word is
         duplicated within the value (a source-data artifact).

    Both are stripped so the output is `Supersedes: Version 0.9`.

    This is conservative: only strips the first 1-2 words, only when
    they exactly match the label or are immediately repeated.
    """
    import re as _re
    value = value.strip()
    if not value or not label:
        return value
    label_norm = label.rstrip(":").strip().casefold()
    if not label_norm:
        return value
    words = value.split()
    if not words:
        return value
    # Pattern 1: first word matches the label.
    first_norm = words[0].rstrip(":,.").casefold()
    if first_norm == label_norm:
        return " ".join(words[1:]).strip()
    # Pattern 2: first two words together match the label.
    if len(words) >= 2:
        first_two = (words[0] + " " + words[1]).casefold()
        if first_two == label_norm:
            return " ".join(words[2:]).strip()
    # Pattern 3: first word is immediately repeated (e.g.,
    # "Version Version 0.9" → "Version 0.9"). This handles source
    # artifacts where the value's first word is duplicated, regardless
    # of whether it matches the label.
    if len(words) >= 2 and words[0].casefold() == words[1].casefold():
        return " ".join(words[1:]).strip()
    return value


def _clean_pdf_column_wrap_tail(line: str) -> str:
    """Phase U3: clean PDF column-wrap garbage tails from
    `Label: value` lines.

    When a PDF renders a 2-column source, the extraction often picks
    up residue like:
      `'Policy No.: CL&H;_04/26) �'`
    where `) �` is the closing paren of the source's
    `(Policy No. CL&H;_04/26)` followed by a Unicode replacement
    character. Without cleanup, downstream consumers (e.g., the
    rendered .docx) show this garbage as the policy-number value.

    This function only operates on lines that look like
    `Label: value` (i.e., start with a Brain label followed by `:`).
    The cleanup is intentionally conservative:

      - Strip trailing `)` and `}` (closing brackets/parens).
      - Strip trailing en-dash / em-dash / hyphen (column separator).
      - Strip trailing Unicode replacement characters `\ufffd` and
        other unassigned / private-use control chars.
      - Then strip trailing whitespace.

    The function does NOT strip:
      - Trailing periods, exclamation, question marks (sentence
        terminators — those are intentional).
      - Trailing alphanumeric content (real data).

    Args:
      line: A single paragraph from the dispatch output.

    Returns:
      The same line with the PDF column-wrap garbage tail removed
      (when applicable).
    """
    import re as _re
    s = (line or "").strip()
    if not s:
        return line
    # Only operate on lines that look like "Label: value" (Brain
    # label followed by a colon, possibly preceded by a leading
    # Brain label like "Policy No.: value").
    if ":" not in s[:80]:
        return line
    # Find the position of the first colon in a Brain-label position.
    # We want the FIRST colon (the one that separates label from
    # value), not a colon inside the value (like a time "10:30").
    # Heuristic: the colon must be followed by a space or end of
    # string, and the label part must contain at least one letter.
    m = _re.match(r"^([A-Za-z][A-Za-z0-9 ()/&.,'\-_]*?)\s*:\s*(.*)$", s)
    if not m:
        return line
    label = m.group(1).strip()
    value = m.group(2)
    # Conservative: only clean if label is a known Brain label OR
    # the line's first colon is in the first 60 characters (suggests
    # the label is at the start).
    from policy_platform.framework.brain_fields import canonical_label
    canon = canonical_label(label + ":")
    if canon is None:
        # Even if not a known label, the value-tail cleanup is still
        # safe (we only strip clearly garbage characters, never
        # alphanumeric content).
        pass
    # Strip trailing garbage.
    cleaned_value = value
    # 1. Strip trailing replacement / private-use / unassigned chars.
    cleaned_value = _re.sub(r"[\ufffd\ufffe\uffff\uE000-\uF8FF]+$", "", cleaned_value)
    # 2. Strip trailing en-dash, em-dash, hyphen, closing paren/brace.
    cleaned_value = _re.sub(r"[\u2013\u2014\-)\]}]+\s*$", "", cleaned_value)
    # 3. Strip trailing whitespace.
    cleaned_value = cleaned_value.rstrip()
    if cleaned_value == value:
        return line
    return f"{label}: {cleaned_value}".strip()


def _split_paragraphs_on_brain_labels(lines: list[str]) -> list[str]:
    """Phase R: split merged paragraphs on Brain-label boundaries.

    After `_join_mid_sentence_lines` runs, a single paragraph may
    contain many label:value clauses joined by sentence boundaries
    (e.g., the Flood PDF source where `'Type: Policy. ... Brief
    Description: ... Effective Date: 01 July 2026. ...'` is one
    visual row).

    This function walks each merged paragraph and re-emits each
    Brain-label position as its own line. The strategy:

      1. Find all Brain-label matches in the paragraph (anywhere;
         not just at sentence boundaries). The label list includes
         BOTH label-row labels (slots 1, 2, 3, 4, 11) AND section-
         title labels (slots 5, 6, 7, 8, 9, 10, 12, 13, 14) so that
         labels like `'Introduction:'`, `'Purpose:'`, `'Scope and
         Beneficiaries:'` are also split out.
      2. For each match, build a new line `Label: <value>` where
         `<value>` extends from the match's end to either:
         (a) the start of the next label match, OR
         (b) the next sentence terminator — whichever comes first.
      3. Any preamble text (before the first label) is emitted as
         a separate line.
      4. If 0 or 1 real label matches are found, the paragraph is
         emitted unchanged.

    Empty/whitespace lines are preserved.
    """
    import re as _re
    from .prose_normalize import _ensure_brain_label_regex

    # Use the LABEL pattern (matches `Label: value` anywhere) so we
    # find mid-paragraph labels like `'...Type: Policy'`. The
    # BOUNDARY pattern would miss them.
    label_regex = _build_combined_label_regex()

    out: list[str] = []
    for line in lines:
        s = (line or "").strip()
        if not s:
            out.append(line)
            continue
        # Find all Brain-label positions in this paragraph.
        matches = list(label_regex.finditer(s))
        # Filter: only consider "real" label matches (have a colon
        # in the matched text, OR could be a full canonical prefix,
        # OR are a synonym followed by a value-terminator like `. `).
        real_matches: list[tuple[int, int, str]] = []
        for m in matches:
            mt = m.group(0)
            if ":" not in mt:
                after = s[m.end():]
                if not _could_be_full_canonical(s, m):
                    if not (mt.rstrip().endswith(".") and after and after[0:1].isalnum()):
                        continue
            real_matches.append((m.start(), m.end(), mt))
        # Always re-emit any paragraph that contains a real label
        # match. Even with a single match, splitting lets downstream
        # parsers see the label on its own line (which is the
        # format-parity invariant — every .pdf / .docx / .txt input
        # should produce the same per-label lines).
        if not real_matches:
            out.append(s)
            continue
        # Re-emit: preamble + each label:value chunk.
        # For synonym-prefix matches (e.g., `'Policy No.'` without
        # trailing colon), insert a colon so downstream regex
        # matches.
        first_start = real_matches[0][0]
        if first_start > 0:
            preamble = s[:first_start].strip()
            if preamble:
                # Skip preamble if it looks like a fragment of an
                # earlier line (e.g., ends with `(` because the
                # actual value is in the matched label). This
                # prevents "Flood Emergency Assistance Policy (" from
                # appearing as a separate paragraph when the full
                # title was already emitted from the raw line 0.
                if not _looks_like_incomplete(preamble):
                    out.append(preamble)
        for idx, (start, end, mt) in enumerate(real_matches):
            next_label_start = (
                real_matches[idx + 1][0]
                if idx + 1 < len(real_matches)
                else len(s)
            )
            value_range = s[end:next_label_start]
            # Find the first terminator followed by `Brain-label boundary`
            # or `Brain-label-start`. A `Brain-label` is a known label
            # name followed by `:`. We want to split before that, NOT
            # at sentence terminators inside a value.
            #
            # Strategy: walk `value_range` left-to-right; on each
            # terminator check if the chars after are the start of a
            # Brain label. If yes, stop before the terminator. If no,
            # keep the terminator in the value.
            cut_pos = len(value_range)
            for tm in _re.finditer(r"[.!?;:]", value_range):
                pos = tm.end()
                rest = value_range[pos:]
                # If `rest` starts with a Brain label, stop here.
                if rest and label_regex.match(" " + rest.lstrip()):
                    cut_pos = tm.start()
                    break
            value = value_range[:cut_pos].rstrip()
            label_part = mt.rstrip()
            if ":" in label_part:
                new_line = f"{label_part} {value}".strip()
            else:
                new_line = f"{label_part} {value}".strip()
            out.append(new_line)
    return out


def _build_combined_label_regex() -> "re.Pattern[str]":
    """Build a single regex that matches ALL Brain labels — both
    label-row labels (slots 1, 2, 3, 4, 11) AND section-title labels
    (slots 5, 6, 7, 8, 9, 10, 12, 13, 14).

    The label-row list is built from `BRAIN_LABEL_ROWS`. The
    section-title list is built from `SECTION_HEADING_SYNONYMS`
    using ALL synonyms (not just the first) so that mid-paragraph
    variants like `'Scope and Beneficiaries:'` (from
    `["scope", "scope and beneficiaries", ...]`) are also matched.

    The suffix `[:.,\\t]?\\s+` allows BOTH `Label: value` and
    `Label. value` patterns (the latter is for synonyms like
    `'Policy No.'` that end with a period, not a colon).
    """
    import re as _re
    from policy_platform.framework.brain_fields import BRAIN_LABEL_ROWS
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS

    opts: list[str] = []
    # Label-row labels (Type:, Policy Title:, etc.).
    for canonical, syns in BRAIN_LABEL_ROWS:
        opts.append(_re.escape(canonical))
        for syn in syns:
            opts.append(_re.escape(syn))
    # Section-title labels (Introduction:, Purpose:, Scope and
    # Beneficiaries:, ...). Use ALL synonyms per slot.
    for sid, syns in SECTION_HEADING_SYNONYMS.items():
        if not syns:
            continue
        for syn in syns:
            opts.append(_re.escape(syn))
            if not syn.endswith(":"):
                opts.append(_re.escape(syn + ":"))
    opts = sorted(set(opts), key=len, reverse=True)
    joined = "|".join(opts)
    # Suffix accepts an OPTIONAL separator (colon, period, or tab)
    # followed by whitespace. This handles both `'Label: value'` and
    # `'Policy No. value'` (synonyms that already end with `.`).
    pattern = (
        r"(?P<lab>(" + joined + r"))\s*[:.\t]?\s+"
    )
    return _re.compile(pattern, _re.IGNORECASE)


def _looks_like_incomplete(s: str) -> bool:
    """True if `s` looks like a fragment of a longer line (e.g.,
    ends with `(`, `,`, or `;` mid-clause).

    Used to drop preambles produced by the split step that would
    otherwise pollute the paragraph stream with incomplete text.
    """
    s = s.rstrip()
    if not s:
        return True
    # Ends with an opening bracket, comma, semicolon, colon, or
    # hyphen.
    if s[-1] in "(,;:—-":
        return True
    return False


def _could_be_full_canonical(s: str, m: "re.Match[str]") -> bool:
    """Best-effort: returns True if the bare-word match `m` could
    be the start of a longer canonical label, given the chars that
    follow in `s`.

    E.g., for match `'Brief '` followed by `'Description: ...'`,
    this returns True (the canonical is `'Brief Description:'`).
    For match `'Brief '` followed by `'Htet Oo...'`, this returns
    False (no canonical matches).
    """
    from policy_platform.framework.brain_fields import BRAIN_LABEL_ROWS
    matched = m.group(0).strip()  # the label-name part
    after = s[m.end():].lstrip()
    if not after:
        return False
    for canonical, _ in BRAIN_LABEL_ROWS:
        # Compare case-insensitively.
        cn = canonical.rstrip(":").lower()
        mn = matched.lower()
        if cn.startswith(mn) and after.lower().startswith(cn[len(mn):]):
            return True
    return False


def _rebuild_original_indices(
    cleaned: list[str],
    original_indices: list[int],
    transformed: list[str],
) -> list[int]:
    """Best-effort alignment of transformed paragraphs back to original
    indices in the pre-cleaner stream.

    For each transformed paragraph, find the first cleaned-line index
    that contributes to it. Falls back to the cursor index if no
    match is found.
    """
    new_orig: list[int] = []
    cursor = 0
    for tline in transformed:
        s = tline.strip()
        if not s:
            new_orig.append(
                original_indices[cursor] if cursor < len(original_indices) else 0
            )
            continue
        consumed = False
        for j in range(cursor, len(cleaned)):
            if cleaned[j].strip() == s:
                new_orig.append(original_indices[j])
                cursor = j + 1
                consumed = True
                break
            if s.startswith(cleaned[j].strip()):
                new_orig.append(original_indices[j])
                cursor = j + 1
                used_chars = len(cleaned[j].strip())
                while cursor < len(cleaned):
                    next_strip = cleaned[cursor].strip()
                    if next_strip and s[used_chars:].startswith(next_strip):
                        used_chars += len(next_strip) + 1
                        cursor += 1
                    else:
                        break
                consumed = True
                break
        if not consumed:
            new_orig.append(
                original_indices[cursor] if cursor < len(original_indices) else 0
            )
    return new_orig


def dispatch(path: Path) -> ExtractedDocument:
    """Extract, clean, then normalize across formats.


    Phase R pipeline:

      1. Extract paragraphs from input format (PDF/DOCX/TXT/RTF).
      2. Cleaner: drop page numbers, header repeats, garbled text.
      3. `_join_mid_sentence_lines`: merge mid-sentence line-wraps
         (Flood's `'...their immediate'` truncation case, plus
         Brain-label-continuation merges).
      4. `_split_paragraphs_on_brain_labels`: re-split merged
         paragraphs on Brain-label boundaries so the analyzer sees
         one label:value per line (the format-parity step).

    This is the universal cross-format fix:
      - `.pdf`  : visual rows may concatenate multiple labels; the
                  split step recovers individual label:value pairs.
      - `.docx` : natural paragraphs already; the split is a no-op
                  when no embedded labels are present.
      - `.txt`  : same as .docx.
      - `.rtf`  : same as .docx.

    Opt-out: set `AGENTIC_POLICY_NO_PROSE_NORMALIZE=1` to skip both
    the join and the split steps (legacy behavior).
    """
    import os

    ext = path.suffix.lower()
    if ext == ".pdf":
        doc = pdf_extractor.extract(path)
    elif ext == ".docx":
        doc = docx_extractor.extract(path)
    elif ext == ".doc":
        doc = doc_extractor.extract(path)
    elif ext == ".txt":
        doc = txt_extractor.extract(path)
    elif ext == ".rtf":
        doc = rtf_extractor.extract(path)
    elif ext in SUPPORTED_EXTENSIONS:
        doc = pdf_extractor.extract(path)
    else:
        raise UnsupportedFormatError(
            f"Unsupported file extension '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    # Step 1: cleaner.
    cleaned, dropped, original_indices = clean_paragraphs(doc.paragraphs)
    doc.cleaner_dropped = dropped

    # Step 2 + 3: normalize (Phase R).
    if os.environ.get("AGENTIC_POLICY_NO_PROSE_NORMALIZE", "0") == "1":
        doc.paragraphs = cleaned
        doc.original_indices = original_indices
        return doc

    merged = _join_mid_sentence_lines(cleaned)
    split = _split_paragraphs_on_brain_labels(merged)
    normalized = _normalize_label_colons(split)
    new_orig = _rebuild_original_indices(cleaned, original_indices, normalized)

    # Phase U3: clean PDF column-wrap garbage tails from
    # `Label: value` lines. PDF column flow leaves residue like
    # `CL&H;_04/26) �` (where `)` and `�` are PDF-column-overflow
    # artifacts). Strip them so values like Policy Number become
    # `CL&H;_04/26` instead of `CL&H;_04/26) �`.
    normalized = [_clean_pdf_column_wrap_tail(line) for line in normalized]

    doc.paragraphs = normalized
    doc.original_indices = new_orig
    return doc


# ---------------------------------------------------------------------------
# Label-aware chunking contract (RAG-side helper).
#
# Contract: "Every paragraph is its own discrete unit. Every Brain
# section-heading label (slot 5-14) inside a paragraph defines a
# paragraph boundary."
#
# Unlike the existing `_split_paragraphs_on_brain_labels` (which only
# splits at terminator+label-lookahead boundaries and is a Phase R
# internal with 17 callers — see seam comment above the M1 family),
# the M1 family emits EVERY occurrence of a section-heading label as
# its own chunk. This prevents slot-routing bleed-over on dense
# single-paragraph sources like `Earthquake_Full_Policy_One_Paragraph.pdf`
# where the Definitions body contains a `History:` label mid-paragraph
# that would otherwise be claimed by the Definitions slot.
#
# Gated by env var `AGENTIC_POLICY_RAG_LABEL_CHUNKING` (default ON
# since the unification). When off, behaviour is identical to legacy
# (no extra splitting here).
#
# M1 family entry points (after unification):
#   - split_paragraphs(paragraphs, *, slots, terminator_aware, enabled)
#       CANONICAL helper. The one that does the work.
#   - split_on_section_heading_labels(paragraph) -> wrapper.
#   - chunk_paragraphs_by_section_heading(paragraphs, *, enabled)
#       -> wrapper.
#
# Phase R seam: the standalone `_split_paragraphs_on_brain_labels`
# above (terminator-aware, 17 callers) is intentionally NOT routed
# through `split_paragraphs`. Unifying it would touch 17 call sites
# for minimal benefit; the two families solve different problems
# (Phase R normalises *merged* paragraphs to one-line-per-label;
# M1 splits *single* dense paragraphs to suppress slot-bleed).
# ---------------------------------------------------------------------------

import os as _os
from typing import Iterable, List, Optional

# Default slot set for the M1 family: section-heading anchors (5-14),
# excluding label-row slots (1, 3, 11) and the logo slot (15).
_DEFAULT_M1_SLOTS: tuple[int, ...] = (5, 6, 7, 8, 9, 10, 12, 13, 14)


_SECTION_HEADING_LABELS_REGEX_CACHE: Optional["re.Pattern[str]"] = None


def _section_heading_labels_regex() -> "re.Pattern[str]":
    """Build a regex that matches ANY section-heading label from
    `SECTION_HEADING_SYNONYMS` (default slot set 5-14 excluding
    label-row slots 1/3/11 and logo slot 15). Used by
    `split_paragraphs()` to find every label occurrence inside a
    paragraph.

    Lazy-built and cached. Returns case-insensitive pattern.
    """
    global _SECTION_HEADING_LABELS_REGEX_CACHE
    if _SECTION_HEADING_LABELS_REGEX_CACHE is not None:
        return _SECTION_HEADING_LABELS_REGEX_CACHE
    import re as _re
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS

    opts: list[str] = []
    for sid, syns in SECTION_HEADING_SYNONYMS.items():
        if sid in (1, 3, 11, 15):
            # Label-row slots and the logo slot never appear as
            # prose section headings, so they are excluded from the
            # M1 chunker regex.
            continue
        for syn in syns:
            s = syn.strip()
            if not s:
                continue
            opts.append(_re.escape(s))
            if not s.endswith(":"):
                opts.append(_re.escape(s + ":"))
    joined = "|".join(sorted(set(opts), key=len, reverse=True))
    # The naive pattern is the joined label alternation with optional
    # colon and trailing whitespace. We deliberately do NOT bake a
    # lookbehind into the regex itself because Python `re` requires
    # fixed-width lookbehinds and our boundary classes vary in width.
    # Instead we collect candidate matches here and filter them in
    # `split_paragraphs()` using `_is_real_label_boundary()`
    # (see below) — which checks the chars immediately preceding each
    # candidate and rejects mid-sentence false positives like the
    # word "Purpose" appearing inside body prose.
    _SECTION_HEADING_LABELS_REGEX_CACHE = _re.compile(
        r"(?P<lab>(" + joined + r"))\s*[:.\t]?\s+",
        _re.IGNORECASE,
    )
    return _SECTION_HEADING_LABELS_REGEX_CACHE


def _is_real_label_boundary(s: str, match_start: int) -> bool:
    """Return True iff the label matched at `match_start` is a real
    sentence / chunk boundary, not a mid-sentence false positive.

    A label is a real boundary ONLY when the character immediately
    preceding it (after skipping zero or more spaces) is one of:
      - nothing (start of string) — match_start == 0
      - `.` (sentence-end)
      - `\\n` (paragraph break)

    This is the *narrow* boundary policy: `?`, `!`, `;`, `,`, `:`,
    `)`, `}`, `]`, and PDF column-wrap (>= 2 spaces) are NOT
    boundaries. Mid-sentence occurrences like the word "Purpose"
    inside "Purposeful" or after a comma are rejected. Sources that
    join labels with anything other than `. ` fall through to Tier 3
    RAG fallback, which uses FAISS + cross-encoder retrieval.

    Examples:
      * `"...the company's Purpose is..."`  -> False (mid-sentence)
      * `". Purpose: ..."`                  -> True  (sentence-end)
      * `"...x; Purpose: ..."`              -> False (semicolon NOT boundary)
      * `"...? Purpose: ..."`               -> False (question mark NOT boundary)
      * `"<newline>Purpose: ..."`           -> True  (paragraph break)
    """
    if match_start == 0:
        return True
    # Skip a single run of spaces (so ". Purpose" is recognised; the
    # boundary char is the `.` immediately before the run).
    j = match_start - 1
    while j >= 0 and s[j] == " ":
        j -= 1
    if j < 0:
        # Only whitespace back to start — still a boundary.
        return True
    ch = s[j]
    if ch == ".":
        return True
    if ch == "\n":
        return True
    return False


def split_paragraphs(
    paragraphs: List[str],
    *,
    slots: Optional[Iterable[int]] = None,
    terminator_aware: bool = False,
    enabled: bool = True,
) -> List[str]:
    """Canonical paragraph splitter (M1 family).

    Single entry point that both `split_on_section_heading_labels`
    and `chunk_paragraphs_by_section_heading` delegate to. Splits a
    list of paragraphs at every section-heading-label occurrence so
    each label becomes its own unit. Prevents slot-routing bleed-over
    on dense single-paragraph sources where multiple Brain section
    labels live inside the same paragraph.

    Args:
        paragraphs: input paragraphs (each element is a string).
        slots: section-heading slots to recognise when emitting
            boundaries. Defaults to (5, 6, 7, 8, 9, 10, 12, 13, 14).
            Pass a different iterable for experimental slot sets.
        terminator_aware: if True, also honour '. ' / '.\n' between
            labels (Phase R-style). Defaults to False (every label is
            a hard boundary).
        enabled: master kill-switch. When False, returns
            `list(paragraphs)` unchanged. Default True.

    Returns:
        Flat list of chunks. Paragraphs with no recognised labels
        are emitted as-is; paragraphs with one or more labels are
        replaced by their per-label sub-chunks (and an optional
        preamble chunk for text before the first label).

    Examples:
        >>> split_paragraphs(["Just text."])
        ['Just text.']

        >>> split_paragraphs(
        ...   ["Definitions: A means B. History: V1."]
        ... )
        ['Definitions: A means B.', 'History: V1.']
    """
    if not enabled:
        return list(paragraphs)
    if not paragraphs:
        return []
    # Resolve the slot set used to build the regex. Today there is
    # only one canonical set; the parameter is exposed for future
    # experimental slot groups without changing the wrapper API.
    _ = tuple(slots) if slots is not None else _DEFAULT_M1_SLOTS
    label_regex = _section_heading_labels_regex()

    out: List[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        s = paragraph.strip()
        if not s:
            continue
        # Find candidate label matches then filter by boundary check.
        candidates = list(label_regex.finditer(s))
        real_matches = [
            m for m in candidates
            if _is_real_label_boundary(s, m.start())
        ]
        if not real_matches:
            out.append(s)
            continue
        # Walk real matches in order. Each label begins a new chunk
        # that runs to the next label boundary (or end of string).
        # When `terminator_aware` is True the body of each chunk is
        # further split on sentence terminators — but the M1 default
        # is False because Earthquake-style dense sources intentionally
        # omit terminators between fields.
        out.extend(_emit_chunks_for_paragraph(
            s, real_matches, terminator_aware=terminator_aware,
        ))
    return out


def _emit_chunks_for_paragraph(
    s: str,
    matches: list,
    *,
    terminator_aware: bool,
) -> List[str]:
    """Internal: emit chunks between label matches.

    Public callers go through `split_paragraphs`. Kept as a separate
    helper so the wrapper layer (`split_on_section_heading_labels`)
    can call it directly without re-walking the regex.
    """
    out: List[str] = []
    cursor = 0
    for idx, m in enumerate(matches):
        # Preamble text before this label (only meaningful for the
        # first match).
        if m.start() > cursor:
            pre = s[cursor:m.start()].strip()
            if pre:
                out.append(pre)
        next_start = matches[idx + 1].start() \
            if idx + 1 < len(matches) else len(s)
        body = s[m.start():next_start].strip()
        if not body:
            cursor = next_start
            continue
        if not terminator_aware:
            # Every label is a hard boundary; emit body as-is.
            out.append(body)
        else:
            # Phase R-style: split body on '. ' / '.\n' too.
            import re as _re
            pieces = [
                p.strip() for p in
                _re.split(r"\.\s+|\.\n", body) if p and p.strip()
            ]
            for p in pieces:
                out.append(p if p.endswith((".", "!", "?")) else p)
        cursor = next_start
    return out


def split_on_section_heading_labels(paragraph: str) -> List[str]:
    """Split a single paragraph on every section-heading label.

    Thin wrapper around `split_paragraphs()` for back-compat. Splits
    one paragraph and returns the list of chunks. Empty / whitespace
    input returns an empty list.

    Example:
        >>> split_on_section_heading_labels(
        ...   "Definitions: A means B. History: V1."
        ... )
        ['Definitions: A means B.', 'History: V1.']
    """
    s = (paragraph or "").strip()
    if not s:
        return []
    # `_section_heading_labels_regex()` already excludes label-row and
    # logo slots, so the default `slots` set matches historical
    # behaviour for this wrapper.
    return split_paragraphs([paragraph])


def chunk_paragraphs_by_section_heading(
    paragraphs: List[str],
    *,
    enabled: Optional[bool] = None,
) -> List[str]:
    """Apply `split_paragraphs` to a list of paragraphs.

    Thin wrapper around the canonical helper. Preserves the original
    API: when `enabled` is None, reads
    `AGENTIC_POLICY_RAG_LABEL_CHUNKING` from the environment (default
    ON, post-unification). When explicitly set to True / False,
    overrides the env var.

    Returns a flat list of chunks where:
      - paragraphs with no section-heading labels are emitted as-is
      - paragraphs with section-heading labels are replaced by their
        per-label chunks
    """
    if enabled is None:
        enabled = _os.environ.get(
            "AGENTIC_POLICY_RAG_LABEL_CHUNKING", "1"
        ) != "0"
    return split_paragraphs(paragraphs, enabled=enabled)
