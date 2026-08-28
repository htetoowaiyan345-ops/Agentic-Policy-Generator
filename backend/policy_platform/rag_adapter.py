"""Adapter that converts RAG results into the ClassificationResult shape
the renderer expects.

The renderer's contract is `ClassificationResult` with per-slot
`SectionSlot(status, content_paragraphs, content_tables,
placed_paragraphs, routing_rule)`. We reuse the dataclasses defined
in the legacy `analyzer` module (kept for the type definitions only)
and populate them from the RAG-Hybrid retrieval output.

This module is the only place that bridges the new RAG pipeline to
the existing renderer.
"""
from __future__ import annotations

import re
from typing import List

from . import config
from .analyzer import ClassificationResult, SectionSlot
from .rag import RAGResult
from .rag.heading_anchors import _strip_heading_label


# Routing rule names reported to the renderer / audit. Short, stable,
# human-readable.
_RULE = "rag_hybrid"
_RULE_HEADING = "heading_anchor"
_RULE_TABLE = "table_passthrough"


# Split at numbered-clause starts (e.g. "2.1.", "1.1.", "3.4.") that
# already exist in the source document. Lookahead-only so the matched
# prefix is preserved on the next chunk. The negative lookbehind
# `(?<![\d.])` prevents splitting mid-number like "1.2.3".
#
# The regex requires a period after the digit(s) (or a multi-part
# number like "2.1") to qualify as a clause boundary. This prevents
# splitting at dates ("03 July 2026"), version numbers ("FY26-27"),
# or tier numbers ("Tier 1 Minor") which are NOT numbered clauses.
_CLAUSE_SPLIT_RE = re.compile(
    r"(?<![\d.])"
    r"(?="
    r"\d+(?:\.\d+)+\.?\s+[A-Z]"  # multi-part: "2.1. ", "3.4.2. "
    r"|"
    r"\d+\.\s+[A-Z]"               # single-part with period: "2. ", "1. "
    r")"
)
# Split at bullet markers (▪) that already exist in the source.
_BULLET_SPLIT_RE = re.compile(r"(\s*\u25aa\s*)")


def build_classification_from_rag(
    rag_result: RAGResult,
    *,
    source_paragraph_count: int = 0,
) -> ClassificationResult:
    """Translate a RAGResult into a renderer-compatible ClassificationResult.

    Each slot gets:
    - chunk_text -> content_paragraphs (single paragraph) for prose slots
    - table      -> content_tables (whole table, as-is) for table slots
    - both       -> both fields populated (heading-anchor prose + table)

    Empty slots are reported as `Skipped - Section Not Found` so the
    renderer writes the standard "Data is not found in source file"
    marker.

    Heading labels are stripped from the body content so the rendered
    output doesn't show the heading word twice (once as the heading
    itself, once as the start of the body paragraph).
    """
    result = ClassificationResult()
    result.fallback_used = rag_result.timed_out

    used_source_indices: set[int] = set()

    for slot_id, assignment in rag_result.slots.items():
        content_paragraphs: list[str] = []
        content_tables: list[list[list[str]]] = []
        status: str
        routing_rule: str

        if assignment.chunk_text and assignment.table:
            stripped_text = _strip_heading_label(assignment.chunk_text, slot_id)
            content_paragraphs = _split_into_source_paragraphs(stripped_text)
            content_tables = [assignment.table]
            if getattr(assignment, "extra_tables", None):
                content_tables.extend(assignment.extra_tables)
            status = "Found"
            routing_rule = _RULE_HEADING
            if assignment.source_idx is not None:
                used_source_indices.add(assignment.source_idx)
        elif assignment.table:
            content_tables = [assignment.table]
            if getattr(assignment, "extra_tables", None):
                content_tables.extend(assignment.extra_tables)
            status = "Found"
            routing_rule = _RULE_TABLE
        elif assignment.chunk_text:
            stripped_text = _strip_heading_label(assignment.chunk_text, slot_id)
            content_paragraphs = _split_into_source_paragraphs(stripped_text)
            status = "Found"
            if assignment.backend == "heading_anchor":
                routing_rule = _RULE_HEADING
            else:
                routing_rule = _RULE
            if assignment.source_idx is not None:
                used_source_indices.add(assignment.source_idx)
        else:
            if assignment.backend == "label_row_external":
                status = "External"
                routing_rule = "field_parser"
            elif assignment.backend == "logo":
                status = "External"
                routing_rule = "logo"
            else:
                status = config.SKIPPED_STATUS
                routing_rule = assignment.backend or "no_match"

        result.sections[slot_id] = SectionSlot(
            status=status,
            content_paragraphs=list(content_paragraphs),
            content_tables=content_tables,
            placed_paragraphs=list(content_paragraphs),
            routing_rule=routing_rule,
        )

    for sid in range(1, 16):
        if sid not in result.sections:
            result.sections[sid] = SectionSlot(
                status=config.SKIPPED_STATUS,
                routing_rule="no_match",
            )

    for sid, slot in result.sections.items():
        assignment = rag_result.slots.get(sid)
        if assignment is not None and assignment.source_idx is not None and slot.status == "Found":
            result.routing_source_indices.setdefault(sid, []).append(assignment.source_idx)

    if source_paragraph_count > 0:
        all_used = set()
        for indices in result.routing_source_indices.values():
            all_used.update(indices)
        result.dropped_paragraph_indices = sorted(
            set(range(source_paragraph_count)) - all_used
        )

    return result


def _normalize_chunk(text: str) -> str:
    """Lightly clean the chunk text before passing to the renderer.

    - Collapse internal whitespace runs.
    - Trim leading/trailing whitespace.
    - Ensure trailing sentence terminator (renderer will add one if needed).
    """
    s = re.sub(r"\s+", " ", text or "").strip()
    if s and s[-1] not in ".!?":
        s = s + "."
    return s


def _split_into_source_paragraphs(text: str) -> list[str]:
    """Split a prose chunk at paragraph boundaries that already exist
    in the source document.

    Splits on:
      1. Double newlines (\n\n) — inserted by find_burmese_heading_match
         for Myanmar sub-section markers (၁-၁။, ၂-၃#, etc.)
      2. English numbered-clause boundaries (2.1., 1.1., 3.4., ...)
      3. Bullet markers (▪)

    No new line breaks are invented. Each split preserves source layout.
    """
    if not text or not text.strip():
        return [_normalize_chunk(text)] if text else []

    # Phase 2: Split at \n\n paragraph breaks (Myanmar sub-section separation)
    if "\n\n" in text:
        raw_chunks = text.split("\n\n")
    else:
        raw_chunks = [text]

    # Phase 7 (simplified): Split at pure dash bullet markers "- ".
    # After Phase 7 Step 5, "+ "-prefixed lines become "- Content".
    # Split on whitespace + dash + whitespace so each bullet becomes its own
    # paragraph, with the dash preserved at the START of each new chunk.
    _DASH_BULLET_SPLIT_RE = re.compile(r"\s+-\s+")

    out: list[str] = []
    for raw in raw_chunks:
        if not raw or not raw.strip():
            continue
        # Phase 7: Split on dash bullets. The split pattern consumes the
        # whitespace + dash + whitespace BEFORE each new bullet. We then
        # prepend "- " to each new chunk so the dash is preserved at the
        # START of the bullet paragraph (not absorbed into the previous
        # paragraph's tail).
        parts = _DASH_BULLET_SPLIT_RE.split(raw)
        rebuilt: list[str] = []
        if parts and parts[0].strip():
            rebuilt.append(parts[0])
        for p in parts[1:]:
            # Each new chunk was preceded by " - " — prepend "- " so it
            # starts with the dash visible.
            rebuilt.append("- " + p.lstrip())
        bullet_split = rebuilt if rebuilt else [raw]
        for bullet_chunk in bullet_split:
            if not bullet_chunk or not bullet_chunk.strip():
                continue
            # Further split English numbered clauses and bullets
            pieces = _CLAUSE_SPLIT_RE.split(bullet_chunk)
            for chunk in pieces:
                if not chunk or not chunk.strip():
                    continue
                bullet_pieces = _BULLET_SPLIT_RE.split(chunk)
                cur = ""
                for pc in bullet_pieces:
                    if not pc:
                        continue
                    if pc.strip() == "\u25aa":
                        if cur.strip():
                            out.append(_normalize_chunk(cur))
                        cur = "\u25aa "
                    else:
                        cur += pc
                if cur.strip():
                    out.append(_normalize_chunk(cur))
    if not out:
        return [_normalize_chunk(text)]
    if len(out) == 1:
        return out
    return out
