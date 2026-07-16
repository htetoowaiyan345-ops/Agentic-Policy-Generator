"""Post-extraction paragraph normalization (utility, NOT WIRED INTO DISPATCH).

Different file formats emit text in different shapes:

- PDF (PyMuPDF): page.get_text("text").split("\\n") — many short lines per page.
- DOCX (python-docx): doc.paragraphs[i].text — natural paragraph splits.
- TXT: splitlines() — one line per newline.
- RTF: rtf_to_text(...).split("\\n") — line per newline.

`normalize_lines_to_paragraphs` is a deliberately conservative stitcher
that ONLY merges a line with the previous when both:

  1. The previous accumulated line does NOT end with a sentence
     terminator `[.!?;:]`.
  2. The next line starts with a lowercase letter (a clear continuation
     signal — most line-broken mid-sentences in PDFs start with
     lowercase because the sentence was line-wrapped at a page break).

Defensive cap: `max_paragraph_chars` (default 600).

STATUS (Phase P outcome): we tried wiring this into `extractors/__init__.py`'s
dispatch path on PDFs, but the conservative rule didn't catch enough
mid-sentence breaks (next line often starts with a capital letter after a
page wrap), and a broader rule merged label-row tables into single long
paragraphs.  So normalize is kept as a UTILITY but is NOT auto-applied.
Future work can experiment with smarter rules; for now, the dispatch path
remains unchanged from before Phase P.
"""
from __future__ import annotations

import re


# Terminators that end a sentence/clause in prose. Conservative set.
_TERMINATORS = re.compile(r"[.!?;:]\s*$")

# Defensive maximum paragraph length (chars).
DEFAULT_MAX_PARAGRAPH_CHARS = 600


def normalize_lines_to_paragraphs(
    lines: list[str],
    max_paragraph_chars: int = DEFAULT_MAX_PARAGRAPH_CHARS,
) -> list[str]:
    """Take a stream of raw extracted lines and join them into coherent
    paragraphs using the conservative rule described at module top.
    """
    out: list[str] = []
    buf: list[str] = []

    def _flush() -> None:
        if buf:
            joined = " ".join(s.strip() for s in buf if s.strip()).strip()
            if joined:
                out.append(joined)
            buf.clear()

    for raw in lines:
        s = (raw or "").strip()
        if not s:
            _flush()
            continue
        if not buf:
            buf.append(s)
            continue
        prev = " ".join(buf).rstrip()
        prev_terminated = bool(_TERMINATORS.search(prev))
        starts_lower = bool(s) and s[0].islower()
        if (not prev_terminated) and starts_lower:
            tentative_len = len(prev) + 1 + len(s)
            if tentative_len >= max_paragraph_chars:
                _flush()
                buf.append(s)
            else:
                buf.append(s)
        else:
            _flush()
            buf.append(s)

    _flush()
    return out
