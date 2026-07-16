"""Pre-routing text cleaning.

Applied between extraction and analysis. Removes obvious noise:

1. **Pure page-number lines** — `^\\s*\\d+\\s*$`, `^Page \\d+`, `^\\d+ of \\d+`, `^[IVX]+\\s*$`.
2. **Repeating headers / footers** — any non-empty line whose trimmed form
   appears >= REPEAT_THRESHOLD times is dropped (configurable; default 3).
3. **Garbled-character lines** — lines dominated by replacement / control
   characters (e.g. ``, ``, `` from CP1252→UTF-8 misreads)
   are dropped. Lines with >=30% non-printable/replacement chars are junk.
4. **Pure whitespace / empty lines** — kept (analyzer relies on blank-line
   breaks between paragraphs); not stripped here.

Returned structure:
    CleanedDocument:
        paragraphs: list[str]    # cleaned line stream
        dropped:   list[dict]    # each {index, text, reason}
        source_format: str
        source_sha256: str
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


# How many repeats to flag a line as a header/footer
REPEAT_THRESHOLD = _env_int("CLEANER_REPEAT_THRESHOLD", 3)

# How many non-printable / replacement chars (as a fraction of total chars)
# make a line garbage.
GARBLED_RATIO = _env_int("CLEANER_GARBLED_RATIO_PERCENT", 30) / 100.0


_PAGE_NUMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"^\s*Page\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s+of\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*[IVXLCDM]+\s*$"),
    re.compile(r"^\s*[-—–]\s*\d+\s*[-—–]\s*$"),
)


# Combined noise patterns: short header/footer noise lines that contain
# version markers, page indicators, or revision markers, e.g.
#   "10/19 Version | Page 2 of 8"
#   "Version 1.0 | Page 1 of 4"
#   "Page 2 of 8"
# These appear in many policy footers. Drop the whole line.
#
# IMPORTANT: version noise must be anchored to `$` so we don't drop
# real content lines that happen to mention "Version" (e.g., the
# "Supersedes Version 0.9 dated 01 January 2026" label-row in the
# Award PDF). Only pure-noise version markers are dropped; lines
# with substantive content after "Version" are preserved.
_NOISE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*Page\s+\d+\s*(of|/)\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*(of|/)\s*\d+\s*$"),
    # Standalone version marker: "Version 1.0" (possibly with
    # trailing date or page indicator). Must be anchored to $ so we
    # don't drop real content like "Version 1.0 of the policy" or
    # "Supersedes Version 0.9 dated ...".
    re.compile(r"^\s*Version\s*[\d.]+(?:\s*-\s*\d{4})?\s*$", re.IGNORECASE),
    re.compile(
        r"^\s*(?:[A-Za-z0-9./-]+?\s+)?Version\s*[\d.]+(?:\s*-\s*\d{4})?\s*(?:\|\s*Page\s+\d+(?:\s*(?:of|/)\s*\d+)?)?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\|\s*Page\s+\d+\s*(of|/)\s*\d+", re.IGNORECASE),
    re.compile(r"\bPage\s+\d+\s*(of|/)\s*\d+\b", re.IGNORECASE),
    re.compile(r"^\s*Revision\b.*History\b", re.IGNORECASE),
    re.compile(r"^\s*Effective\s+(?:Date|as of)\b.*\|", re.IGNORECASE),
    re.compile(r"\|\s*Page\b", re.IGNORECASE),
    # HISTORY / CHANGE table header fragments. When a PDF/DOCX renders
    # a multi-column table (DATE | VERSION | DESCRIPTION OF CHANGE |
    # AUTHOR / REVIEWER) as flattened text, the column headers appear
    # as standalone lines. These are noise — not real label rows.
    re.compile(r"^\s*DESCRIPTION\s+OF\s+CHANGE\s*$", re.IGNORECASE),
    re.compile(r"^\s*DATE\s+VERSION\s+DESCRIPTION.*$", re.IGNORECASE),
    re.compile(r"^\s*AUTHOR\s*/\s*REVIEWER\s*$", re.IGNORECASE),
)


@dataclass
class CleanedDocument:
    """Output of the cleaner.

    `paragraphs` is a clean line stream ready for the analyzer.
    `dropped` is an audit-friendly list of (index, text, reason) triples.
    `source_format` and `source_sha256` are forwarded verbatim.
    """
    paragraphs: list[str] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    source_format: str = ""
    source_sha256: str = ""


def _is_page_number(line: str) -> bool:
    for pat in _PAGE_NUMBER_PATTERNS:
        if pat.match(line):
            return True
    return False


def _is_combined_noise(line: str) -> bool:
    """Matches lines containing version markers, page X of Y, or '|Page X' artifact."""
    for pat in _NOISE_LINE_PATTERNS:
        if pat.search(line):
            return True
    return False


def _is_garbled(line: str) -> bool:
    """True if the line has too many control / replacement / non-printable chars."""
    if not line:
        return False
    bad = 0
    total = 0
    for ch in line:
        cat = unicodedata.category(ch)
        # Control / format / private-use / surrogate / unassigned / line/para separators
        if cat.startswith(("C", "Z", "Cs", "Cn", "Co")) and cat != "Zs":
            bad += 1
        elif ch == "\ufffd":  # replacement char
            bad += 1
        elif cat in ("Zs",) and ch.strip() == "":
            # whitespace OK
            pass
        total += 1
    if total == 0:
        return False
    return (bad / total) >= GARBLED_RATIO


# Pattern: a "proper name" looks like 1-4 short capitalised words
# (e.g., "Htet Oo Wai Yan", "U Win Myint Aung", "John Smith").
# Distinguishing rule: each word is between 1-20 chars, AND the line
# as a whole is short (<=60 chars), AND the line consists of 1-4
# capitalised words with no English sentence-like vocabulary.
#
# To exclude Title-Case sentence fragments like
# "Contains Nonbinding Recommendations", we additionally require the
# line NOT contain any common English "stopword" that signals a
# sentence fragment.
_PROPER_NAME_RE = re.compile(
    r"^[A-Z][a-zA-Z\-']{0,19}(?:\s+[A-Z][a-zA-Z\-']{0,19}){0,3}$"
)


# Words that signal a sentence-fragment Title-Case header/footer
# (not a personal name). Keep this list tight; false negatives here
# only cause a legitimate name to be counted as a footer (recoverable).
_NAME_STOPWORDS = frozenset({
    "contains", "nonbinding", "recommendations",
    "introduction", "background", "purpose", "scope",
    "definitions", "section", "form", "page", "version",
    "approved", "effective", "policy", "guidance", "guidelines",
    "review", "history", "appendix", "table", "contents", "preamble",
    "department", "division", "company", "group", "office",
    "amendment", "issuance", "regulation", "rules", "clauses",
    "reporting", "summary", "overview", "conclusion",
    "consistent", "fair", "transparent", "establish",
})


def _is_proper_name(s: str) -> bool:
    """Return True if the trimmed line looks like a personal name.

    A line qualifies as a proper name iff:
      1. Length 1-60 chars.
      2. Each word is 1-20 chars, 1-5 words total.
      3. No word appears in `_NAME_STOPWORDS` (which excludes
         sentence-fragment Title-Case headers like
         "Contains Nonbinding Recommendations").
    """
    s = s.strip()
    if not s or len(s) > 60:
        return False
    if not _PROPER_NAME_RE.match(s):
        return False
    words = s.split()
    if not (1 <= len(words) <= 5):
        return False
    for w in words:
        if w.casefold() in _NAME_STOPWORDS:
            return False
    return True


def _repeating_lines(paragraphs: list[str]) -> set[str]:
    """Return the set of trimmed strings that appear >= REPEAT_THRESHOLD times
    AND are NOT proper names.

    Proper names (e.g., `Htet Oo Wai Yan`) often repeat 2-3 times in
    legitimate contexts — once or twice in slot 1/3 label-rows and
    once in a HISTORY table. They are NEVER page headers or footers.
    """
    from collections import Counter
    counts: Counter[str] = Counter()
    for p in paragraphs:
        s = p.strip()
        if not s:
            continue
        if len(s) > 120:
            continue
        if _is_proper_name(s):
            # Skip proper names entirely; they are exempt from header-repeat.
            continue
        counts[s] += 1
    return {s for s, n in counts.items() if n >= REPEAT_THRESHOLD}


def clean_paragraphs(
    paragraphs: Iterable[str],
) -> tuple[list[str], list[dict], list[int]]:
    """Clean a paragraph list.

    Returns:
        cleaned:           The cleaned line stream.
        dropped_records:   Audit-friendly list of dropped paragraphs
                           (each {'index', 'text', 'reason'}).
        original_indices:  Parallel list to `cleaned`; for each cleaned
                           line, the index in the original (un-cleaned)
                           input list. Used by parsers to recover
                           cleaner-dropped values via the dropped_records.

    Why a 3-tuple: callers (analyzer, parser) need the alignment
    between cleaned-line-N and original-line-original_indices[N] so they
    can ask "which original lines were dropped between these two
    cleaned lines, and were any of them a likely value?".
    """
    raw = list(paragraphs)
    repeats = _repeating_lines(raw)
    cleaned: list[str] = []
    dropped: list[dict] = []
    original_indices: list[int] = []
    for i, line in enumerate(raw):
        s = line.strip()
        # Empty/whitespace-only: pass through (paragraph break signal).
        if not s:
            cleaned.append(line)
            original_indices.append(i)
            continue
        if _is_page_number(line):
            dropped.append({"index": i, "text": line, "reason": "page_number"})
            continue
        if _is_combined_noise(line):
            dropped.append({"index": i, "text": line, "reason": "version_page_noise"})
            continue
        if s in repeats:
            dropped.append({"index": i, "text": line, "reason": "header_repeat"})
            continue
        if _is_garbled(line):
            dropped.append({"index": i, "text": line, "reason": "garbled"})
            continue
        cleaned.append(line)
        original_indices.append(i)
    # Stage D: normalize mojibake on cleaned output. Done last so we don't
    # perturb the dropped-record text (preserved as-is for audit).
    try:
        from policy_platform.extractors.mojibake import normalize_mojibake

        cleaned = [normalize_mojibake(line) for line in cleaned]
    except Exception:
        pass
    return cleaned, dropped, original_indices


def clean_extracted(
    paragraphs: list[str],
    source_format: str,
    source_sha256: str,
) -> CleanedDocument:
    """Clean an extracted paragraph list. Convenience wrapper.

    The `original_indices` mapping is NOT forwarded through this
    helper — callers needing it should call `clean_paragraphs()` directly.
    """
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    return CleanedDocument(
        paragraphs=cleaned,
        dropped=dropped,
        source_format=source_format,
        source_sha256=source_sha256,
    )
