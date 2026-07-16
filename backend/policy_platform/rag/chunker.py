"""Sentence-aware text chunker for RAG ingestion.

Splits an input list of paragraphs (already extracted from PDF/DOCX/TXT)
into overlapping chunks sized for sentence-transformer embedding:

    - target_chunk_size  = 300 characters
    - overlap            = 50 characters

The chunker operates at paragraph granularity first, then refines very
long paragraphs by sentence. It never splits mid-word. Whitespace is
normalized. Empty chunks are dropped.

A `Chunk` carries:
    - text       : the chunk text
    - source_idx : the originating paragraph index in the input list
    - chunk_id   : a stable, deterministic integer id (0..N-1)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


@dataclass
class Chunk:
    text: str
    source_idx: int
    chunk_id: int

    def __len__(self) -> int:
        return len(self.text)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\t", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _split_long_paragraph(text: str, target: int) -> List[str]:
    """Split a single paragraph into sentence-aware pieces of ~target chars.

    Falls back to word-level splitting if the paragraph has no
    sentence-ending punctuation.
    """
    text = _normalize(text)
    if len(text) <= target:
        return [text] if text else []

    # Sentence split (only on real sentence boundaries).
    sentences = _SENTENCE_SPLIT_RE.split(text)
    sentences = [s.strip() for s in sentences if s and s.strip()]

    if not sentences or len(sentences) == 1:
        # No sentence boundaries - split on word boundaries.
        words = text.split(" ")
        out: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for w in words:
            wlen = len(w) + 1
            if cur and cur_len + wlen > target:
                out.append(" ".join(cur).strip())
                cur = [w]
                cur_len = len(w)
            else:
                cur.append(w)
                cur_len += wlen
        if cur:
            out.append(" ".join(cur).strip())
        return [s for s in out if s]

    # Greedy pack sentences into chunks of ~target chars.
    out = []
    cur: List[str] = []
    cur_len = 0
    for s in sentences:
        slen = len(s) + 1
        if cur and cur_len + slen > target:
            out.append(" ".join(cur).strip())
            cur = [s]
            cur_len = len(s)
        else:
            cur.append(s)
            cur_len += slen
    if cur:
        out.append(" ".join(cur).strip())
    return [s for s in out if s]


def chunk_paragraphs(
    paragraphs: Iterable[str],
    *,
    target_chunk_size: int = 300,
    overlap: int = 50,
) -> List[Chunk]:
    """Chunk a sequence of paragraphs into overlapping sentence-aware chunks.

    First, paragraphs are split at bullet markers so each bullet
    becomes its own paragraph. Then each paragraph is chunked by
    sentence-aware splitting (300 chars target with 50 char overlap).

    Args:
        paragraphs: cleaned paragraphs in source order.
        target_chunk_size: target chunk size in characters.
        overlap: overlap between consecutive chunks in characters.

    Returns:
        List of Chunk objects in source order, with stable chunk_id.
    """
    if target_chunk_size <= 0:
        raise ValueError("target_chunk_size must be > 0")
    if overlap < 0 or overlap >= target_chunk_size:
        raise ValueError("overlap must be in [0, target_chunk_size)")

    # Split paragraphs at bullet markers so each bullet is its own paragraph.
    paragraphs = list(paragraphs)
    split_paragraphs = []
    for p in paragraphs:
        bullets = split_bullets(p)
        split_paragraphs.extend(bullets)
    paragraphs = split_paragraphs

    chunks: List[Chunk] = []
    chunk_id = 0

    for src_idx, para in enumerate(paragraphs):
        if not para or not para.strip():
            continue
        # Strip leading bullet marker from this paragraph so the rendered
        # output shows clean text (e.g. "o Touching..." becomes "Touching...").
        para = _strip_leading_bullet(para)
        if not para or not para.strip():
            continue
        pieces = _split_long_paragraph(para, target_chunk_size)
        if not pieces:
            continue

        if len(pieces) == 1:
            chunks.append(Chunk(text=pieces[0], source_idx=src_idx, chunk_id=chunk_id))
            chunk_id += 1
            continue

        # Add overlap between consecutive pieces from the same paragraph.
        for i, piece in enumerate(pieces):
            if i == 0:
                chunks.append(Chunk(text=piece, source_idx=src_idx, chunk_id=chunk_id))
                chunk_id += 1
                continue
            # Prepend the tail of the previous piece as overlap.
            prev = pieces[i - 1]
            if overlap > 0 and len(prev) > overlap:
                tail = prev[-overlap:].lstrip()
                piece_with_overlap = (tail + " " + piece).strip()
            else:
                piece_with_overlap = piece
            chunks.append(
                Chunk(text=piece_with_overlap, source_idx=src_idx, chunk_id=chunk_id)
            )
            chunk_id += 1

    return chunks


def _strip_leading_bullet(text: str) -> str:
    """Strip a leading bullet marker from a paragraph.

    Real policy documents use a variety of bullet markers
    ("o Touching..." / "• Foo" / "* Bar" / "▪ Baz"). The marker
    itself is not content - strip it so the rendered output is clean.

    Only strips when the bullet appears at the very start of the
    paragraph (after optional leading whitespace), so we don't
    accidentally mangle prose like "Oranges are sweet." which
    contains an "o " mid-word.

    Returns the paragraph with the bullet marker stripped. If no
    bullet marker is detected, returns the paragraph unchanged.
    """
    if not text:
        return text
    stripped = text.lstrip()
    if not stripped:
        return text
    for marker in _BULLET_MARKERS:
        if stripped.startswith(marker):
            rest = stripped[len(marker):]
            # Only strip if the next char is whitespace or end-of-string.
            # This prevents false positives like "O" alone being stripped
            # (no - that's intentional) or "Office hours..." being
            # misidentified (would need a space after marker).
            if not rest or rest[0].isspace():
                return rest.lstrip()
    return text


# Bullet markers that PDF extractors commonly use.
# We accept several variants seen in real policy documents.
_BULLET_MARKERS = [
    "\u2022",  # bullet •
    "\u25e6",  # white bullet ◦
    "\u25aa",  # small black square ▪
    "\u25cb",  # white circle ○
    "\u25cf",  # black circle ●
    "\u25c6",  # black diamond ◆
    "\u25b8",  # black right-pointing small triangle ▸
    "\u2023",  # triangular bullet ‣
    "o",  # letter o bullet
    "O",  # capital O bullet
    "*",  # asterisk bullet
    "\u2013",  # en dash –
    "\u2014",  # em dash —
]


def split_bullets(paragraph: str) -> List[str]:
    """Split a paragraph into one-paragraph-per-bullet pieces.

    PDF extractors often concatenate multiple bullets into one paragraph,
    e.g. "Physical acts of a sexual nature, such as: Touching, pinching,
    patting, kissing..." This makes RAG match the whole paragraph to one
    slot when each bullet actually belongs to a different sub-topic.

    This function detects bullet markers and splits the paragraph so each
    bullet (and its body) becomes its own paragraph.

    Returns:
        List of paragraphs. If no bullets are detected, returns the
        original paragraph as a single item.
    """
    if not paragraph or not paragraph.strip():
        return [paragraph] if paragraph else []

    text = paragraph.strip()

    # Quick check: if there are no bullet markers at all, return as-is.
    has_bullet = False
    for marker in _BULLET_MARKERS:
        if marker in text:
            has_bullet = True
            break
    if not has_bullet:
        return [paragraph]

    # Find all bullet positions.
    # A bullet position is the start of a bullet item. Bullets can be at
    # the start of a line OR after a sentence-ending punctuation.
    lines = text.split("\n")
    if len(lines) > 1:
        # Multi-line paragraph: each line might be a bullet. Split by line.
        # Keep the bullet markers with their content.
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Check if this line starts with a bullet.
            starts_with_bullet = any(
                stripped.startswith(m) for m in _BULLET_MARKERS
            )
            if starts_with_bullet:
                # Clean up the marker so it doesn't interfere with RAG matching.
                for m in _BULLET_MARKERS:
                    if stripped.startswith(m):
                        stripped = stripped[len(m):].strip()
                        break
                result.append(stripped)
            else:
                # Continuation of previous bullet: append to last result.
                if result:
                    result[-1] = result[-1] + " " + stripped
                else:
                    result.append(stripped)
        return result if result else [paragraph]

    # Single-line paragraph: bullets are inline, separated by markers
    # after sentence-ending punctuation.
    # Pattern: sentence-ending punctuation + space + bullet marker + content.
    # We split at any "<sentence-end> <bullet-marker>" boundary.
    split_pattern = re.compile(
        r"(?<=[.!?])\s+(?=[\u2022\u25e6\u25aa\u25cb\u25cf\u25c6\u25b8\u2023oO*\u2013\u2014])",
        re.UNICODE,
    )
    pieces = split_pattern.split(text)
    if len(pieces) <= 1:
        return [paragraph]

    # Strip leading bullet markers from each piece.
    result = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        for m in _BULLET_MARKERS:
            if piece.startswith(m):
                piece = piece[len(m):].strip()
                break
        if piece:
            result.append(piece)
    return result if result else [paragraph]


def is_label_row_paragraph(paragraph: str) -> bool:
    """True if the paragraph looks like a label-row (e.g. 'Type: HR Policy').

    Label rows are slot-1/2/3/4/11 input. They should NOT be matched
    by RAG for prose slots.

    Uses the single source of truth in framework.brain_fields:
    every canonical Brain label (Type:, Policy Title:, etc.) plus
    every synonym it knows about. Any paragraph starting with one
    of these followed by a separator is a label row.
    """
    if not paragraph:
        return False
    text = paragraph.strip()
    if not text:
        return False
    # Lazy import to avoid a circular dependency.
    from ..framework import brain_fields
    # Build a combined list of all known labels (canonical + synonyms),
    # sorted by length descending so longer phrases match first.
    all_labels: list[str] = []
    for canonical, syns in brain_fields.BRAIN_LABEL_ROWS:
        all_labels.append(canonical.rstrip(":"))
        all_labels.extend(syns)
    # De-duplicate and sort by length descending.
    seen = set()
    sorted_labels = []
    for label in all_labels:
        norm = label.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            sorted_labels.append(label)
    sorted_labels.sort(key=lambda s: -len(s))
    lower = text.lower()
    for label in sorted_labels:
        lnorm = label.strip().lower()
        # Match if paragraph starts with this label followed by a separator.
        # Acceptable separators: ":", " -", "/", whitespace + word boundary.
        for sep in (":", " -", " - ", "/", " by"):
            if lower.startswith(lnorm + sep):
                return True
        # Match if label is followed by space then a separator (e.g. "Type /" or "Title:").
        if lower == lnorm:
            return True
    return False


def is_short_title(paragraph: str) -> bool:
    """True if the paragraph is a short title/subtitle (no body text).

    Pure title paragraphs (e.g. "POLICY TEMPLATE - AWARD AND RECOGNITION
    PROGRAM" or "MANAGEMENT POLICY") are not real content and should
    not be matched by RAG.
    """
    if not paragraph:
        return False
    text = paragraph.strip()
    if not text:
        return False
    # Short paragraph (<= 80 chars) with no sentence-ending punctuation
    # other than a final period.
    if len(text) > 80:
        return False
    # If it has a colon and a value, it's a label row (handled elsewhere).
    if ":" in text:
        return False
    # If it ends with a sentence terminator other than period, it has
    # a complete sentence.
    if text.endswith(".") and len(text) > 30:
        return False
    return True


# Footnote / endnote pattern: a paragraph that starts with a numbered
# superscript-like reference ("1 ", "2 ", "1\t", "2)") followed by
# the footnote text. These appear at the bottom of policy documents
# and are references to the main text, not content for any slot.
_FOOTNOTE_RE = re.compile(
    r"^\s*\d+\s*[\.\)]?\s+\S",
)


def is_footnote(paragraph: str) -> bool:
    """True if the paragraph looks like a footnote / endnote.

    Footnotes typically start with a number followed by a period/paren
    and space (e.g. "1 While this policy..." or "2 A non-employee...").
    They appear as a reference to the main text and are not content
    for any Brain slot.

    This is a conservative check: only paragraphs that are long enough
    (>= 50 chars) AND start with a number + space + uppercase letter
    are treated as footnotes. This avoids false positives on short
    section headings like "1. Purpose" (which is 9 chars).
    """
    if not paragraph:
        return False
    text = paragraph.strip()
    if len(text) < 50:
        return False
    return bool(_FOOTNOTE_RE.match(text))


def chunk_text(text: str, **kwargs) -> List[Chunk]:
    """Convenience wrapper that chunks a single blob by paragraph first."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p and p.strip()]
    return chunk_paragraphs(paragraphs, **kwargs)
