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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image


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
      6. Penalty for lines that look like scope/audience statements
         (start with `APPLICABLE TO`, `SCOPE`, `AUDIENCE`, `This
         policy`, `The policy`, etc.). These are body sentences that
         happen to be long and can otherwise out-score the real title.
      7. **Myanmar title density bonus**: lines with ≥5 Myanmar chars
         and length 30-120 chars get a +30 bonus. This helps Myanmar
         policy titles out-score OCR header artifacts.
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
    # Penalty: lines that read like scope/audience statements rather
    # than titles. Without this, a sentence like
    # "APPLICABLE TO ALL SECTORS UNDER CITY HOLDINGS GROUP..." out-scores
    # the real title because of length.
    if _looks_like_scope_or_audience(s):
        return -1.0
    # Title Case bonus.
    score = len(s)
    if s == s.upper() and any(c.isalpha() for c in s):
        score += 60
    # Myanmar title density bonus. Conservative: ≥5 Myanmar chars
    # (not 1-2 char OCR artifacts) and length 30-120 (typical title range).
    myanmar_count = sum(1 for c in s if 0x1000 <= ord(c) <= 0x109F)
    if 30 <= len(s) <= 120 and myanmar_count >= 5:
        score += 30
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


_SCOPE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"applicable\s+to"
    r"|scope\s*[:\-]"
    r"|audience\s*[:\-]"
    r"|coverage\s*[:\-]"
    r"|this\s+policy\s+supports?"
    r"|this\s+policy\s+applies?"
    r"|this\s+policy\s+recogni[sz]es?"
    r"|the\s+policy\s+supports?"
    r"|the\s+policy\s+applies?"
    r"|the\s+policy\s+recogni[sz]es?"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_scope_or_audience(s: str) -> bool:
    """True if `s` reads like a scope/audience/applicability statement
    rather than a policy title. These sentences are typically long
    (60-150 chars), start with `APPLICABLE TO` / `SCOPE` / `This policy
    supports` etc., and end with a period. They are body content, not
    the policy title."""
    if _SCOPE_PREFIX_RE.match(s):
        return True
    return False


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
    # Match a label at start, followed by `:` and a value. Burmese
    # characters (U+1000-U+109F and U+AA60-U+AA7F) are accepted so
    # Myanmar labels like `မူဝါဒအမည်:` are also caught.
    m = _re.match(
        r"^\s*([A-Za-z\u1000-\u109F\uAA60-\uAA7F][A-Za-z0-9 ()/&.,'\-_\u1000-\u109F\uAA60-\uAA7F]*?)\s*[:\t]\s*\S",
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


# ---------------------------------------------------------------------------
# Header table value extraction (for Myanmar and other label-row tables
# that pdfplumber/PyMuPDF miss in the visual header region).
# ---------------------------------------------------------------------------

# Same regex as brain_fields._LABEL_LINE_RE / field_parser._LABEL_LINE_RE
# after the Gate-1 broadening (accepts Burmese chars in label).
_HEADER_TABLE_LABEL_LINE_RE = re.compile(
    r"^\s*([A-Za-z\u1000-\u109F\uAA60-\uAA7F][A-Za-z0-9 ()/&.,'\-_\u1000-\u109F\uAA60-\uAA7F]*?)\s*[:\t]\s*(.+?)\s*$"
)


def _parse_header_table_text(header_text: str) -> dict[str, str]:
    """Parse OCR output from a header table region into label -> value.

    Generic, no per-file hardcoding. Handles three layouts:
      1. explicit `Label: value` lines (English or Burmese)
      2. tab/2+ space separated pairs (e.g., `Policy no  HR_GP_00002`)
      3. **alternating label/value lines** (e.g., line N is labels,
         line N+1 is values). This is the dominant layout in Myanmar
         policy PDFs where the table headers come back as a single line
         of labels and the next line has the values.

    For layout 3, label phrases are matched greedily from left to right
    against ``canonical_label()``. Tokens like "Policy no" resolve to
    "Policy Number:" via synonym matching, while "Approved" alone resolves
    to "Approved by:" but only after consuming the next token "by".

    Returns dict of canonical_label -> raw_value. Empty if nothing matches.
    """
    from policy_platform.framework.brain_fields import canonical_label

    out: dict[str, str] = {}
    if not header_text:
        return out

    def _store_if_canon(label: str, value: str) -> bool:
        if not value or re.fullmatch(r"\d+", value):
            return False
        canon = canonical_label(label + ":")
        if canon and canon not in out:
            out[canon] = value
            return True
        return False

    def _split_labels_with_spans(label_line: str) -> list[tuple[int, int, str]]:
        """Split a labels line into (start_char, end_char, canonical_label)
        spans using canonical_label().

        Tokenizes by whitespace, then walks tokens left-to-right. At each
        starting token, tries phrases of length 1, then 2, then ... up to
        max_phrase_len (5). Picks the SHORTEST phrase that resolves to a
        canonical label. Rationale: short forms like "Approved", "Policy",
        "Prepared" are registered synonyms in BRAIN_LABEL_ROWS, so a
        single token often matches completely. Longer phrases only matter
        when no short match exists (e.g., "Policy no" requires 2 tokens
        because "Policy" alone doesn't resolve).
        """
        token_spans: list[tuple[int, int, str]] = []
        i = 0
        n = len(label_line)
        while i < n:
            while i < n and label_line[i].isspace():
                i += 1
            if i >= n:
                break
            start = i
            while i < n and not label_line[i].isspace():
                i += 1
            token_spans.append((start, i, label_line[start:i]))

        out_spans: list[tuple[int, int, str]] = []
        ti = 0
        max_phrase_len = 5
        while ti < len(token_spans):
            tok_start_pos = token_spans[ti][0]
            match: tuple[int, int, str] | None = None
            for L in range(1, min(max_phrase_len, len(token_spans) - ti) + 1):
                phrase = " ".join(t[2] for t in token_spans[ti:ti + L]).strip(":")
                c = canonical_label(phrase + ":")
                if c is not None:
                    end_idx = ti + L
                    end_pos = (
                        token_spans[end_idx][0] if end_idx < len(token_spans) else n
                    )
                    match = (end_pos, L, c)
                    break  # shortest match wins
            if match is not None:
                _, L, canon = match
                out_spans.append((tok_start_pos, match[0], canon))
                ti += L
            else:
                ti += 1
        return out_spans

    lines = [ln.strip() for ln in header_text.splitlines() if ln.strip()]

    # Strategy 3 (run first): alternating label/value lines.
    # Use character-position spans from the labels line to slice the
    # values line at the same column positions. This handles multi-token
    # values (e.g., "Daw Win Win Tint" for "Approved by:") correctly.
    #
    # OCR may insert 1-2 short lines between labels and values (e.g., a
    # leftover "date" token between the labels line and the values line
    # in Myanmar PDFs). Try pairing each labels line with up to 3 lines
    # forward; the values line is the one whose char positions best match
    # the label column positions.
    for i in range(len(lines) - 1):
        labels_line = lines[i]

        spans = _split_labels_with_spans(labels_line)
        if len(spans) < 2:
            continue

        # Find the best matching values line within next 3 lines.
        # Requirements for a values line:
        #   1. fewer label matches than the labels line
        #   2. at least as many whitespace tokens as labels (each label
        #      needs at least one value token)
        best_values: str | None = None
        for j in range(i + 1, min(i + 4, len(lines))):
            cand = lines[j]
            cand_spans = _split_labels_with_spans(cand)
            cand_n_canon = len(cand_spans)
            cand_tokens = len(cand.split())
            if cand_n_canon < len(spans) and cand_tokens >= len(spans):
                best_values = cand
                break
        if best_values is None:
            continue
        values_line = best_values

        # Compute fractional column boundaries from labels line and use
        # them to slice the values line. Tokenize values_line first so
        # each label gets whole tokens (cleaner than raw char slices).
        labels_len = len(labels_line)
        if labels_len == 0:
            continue
        vlen = len(values_line)
        value_tokens = values_line.split()
        n_val = len(value_tokens)

        # Map each span's start position to a fractional position,
        # then find which value token falls at that fractional position.
        # Use cumulative character offsets of value tokens.
        token_offsets: list[int] = []
        offset = 0
        for tok in value_tokens:
            token_offsets.append(offset)
            offset += len(tok) + 1  # +1 for space

        pairs: list[tuple[str, str]] = []
        n_labels = len(spans)

        # First pass: compute initial fractional token boundaries.
        boundaries: list[tuple[int, int]] = []  # (tok_start_idx, tok_end_idx)
        for idx, (start, end, canon) in enumerate(spans):
            start_frac = start / labels_len
            end_frac = end / labels_len

            tok_start_idx = 0
            for ti, off in enumerate(token_offsets):
                if off / vlen >= start_frac:
                    tok_start_idx = ti
                    break

            if idx + 1 < n_labels:
                next_start = spans[idx + 1][0]
                next_frac = next_start / labels_len
                tok_end_idx = n_val
                for ti, off in enumerate(token_offsets):
                    if off / vlen >= next_frac:
                        tok_end_idx = ti
                        break
            else:
                tok_end_idx = n_val

            boundaries.append((tok_start_idx, tok_end_idx))

        # Second pass: if any label got 0 tokens but the previous label
        # has more than 1 token, redistribute. This handles name fields
        # like "Daw Win Win Tint" that span multiple tokens.
        if any(end - start == 0 for start, end in boundaries):
            # Find first label with multi-token value AND next empty label.
            for i in range(n_labels - 1):
                cur_start, cur_end = boundaries[i]
                next_start, next_end = boundaries[i + 1]
                if (cur_end - cur_start) >= 2 and (next_end - next_start) == 0:
                    # Move tokens from current to next.
                    move_count = (cur_end - cur_start) // 2
                    move_count = max(move_count, 1)
                    new_cur_end = cur_end - move_count
                    new_next_start = new_cur_end
                    # Recompute next's end as new_next_start + move_count
                    # or until next boundary.
                    new_next_end = next_end + move_count
                    if new_next_end > n_val:
                        new_next_end = n_val
                    boundaries[i] = (cur_start, new_cur_end)
                    boundaries[i + 1] = (new_next_start, new_next_end)
                    break

        # Materialize pairs.
        for idx, (start, end, canon) in enumerate(spans):
            tok_start_idx, tok_end_idx = boundaries[idx]
            value = " ".join(value_tokens[tok_start_idx:tok_end_idx]).strip()
            pairs.append((canon, value))

        for canon, val in pairs:
            if val and not re.fullmatch(r"\d+", val):
                if canon not in out:
                    out[canon] = val

    # Strategy 1: explicit `Label: value` (Burmese or English).
    for line in lines:
        m = _HEADER_TABLE_LABEL_LINE_RE.match(line)
        if m:
            label = m.group(1).strip()
            value = m.group(2).strip()
            _store_if_canon(label, value)

    # Strategy 2: tab or 2+ space separated pair on a single line.
    for line in lines:
        parts = re.split(r"\t+|\s{2,}", line, maxsplit=1)
        if len(parts) == 2:
            label = parts[0].strip().rstrip(":")
            value = parts[1].strip()
            if 2 <= len(label) <= 60:
                _store_if_canon(label, value)

    return out


def extract_header_table_values(image: Image.Image) -> dict[str, str]:
    """End-to-end: OCR a full page image (rendered at HEADER_DPI) and
    parse label-value pairs from the OCR text.

    Args:
        image: PIL Image of a full page (page 1 of the PDF, rendered at
               HEADER_DPI by the caller).

    Returns:
        Dict of canonical_label -> value. Empty if nothing matched.
    """
    from policy_platform.extractors.ocr_fallback import (
        _ocr_page1_high_dpi,
    )

    if image is None:
        return {}

    try:
        page_text = _ocr_page1_high_dpi(image)
    except Exception:
        return {}

    if not page_text:
        return {}

    return _parse_header_table_text(page_text)
