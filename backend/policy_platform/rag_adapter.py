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
            content_paragraphs = [_normalize_chunk(stripped_text)]
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
            content_paragraphs = [_normalize_chunk(stripped_text)]
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
    import re

    s = re.sub(r"\s+", " ", text or "").strip()
    if s and s[-1] not in ".!?":
        s = s + "."
    return s
