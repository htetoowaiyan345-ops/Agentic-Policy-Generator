"""RAG-Hybrid retrieval pipeline (orchestrator).

For each of the 15 Brain slots this orchestrator uses a 3-tier lookup:

    TIER 1: Heading-anchored retrieval (deterministic, 100% accurate)
        - For prose slots 5, 6, 7, 8, 9, 12, 13, 14, look for paragraphs
          whose first line matches a slot-specific heading pattern
          (e.g. "Purpose:", "Scope:", "Definitions:").
        - If found, take the whole paragraph(s) up to the next heading.
        - No model involvement; pure regex.

    TIER 2: Table passthrough (for slots 9, 10, 14)
        - If TIER 1 didn't match and the source has a table whose
          content matches the slot's signal keywords, pass the WHOLE
          table through as-is.
        - The user's directive: "if the input data is table - pass it
          like this, no need to do as the brain (framework)".

    TIER 3: RAG (hybrid FAISS + BM25 + cross-encoder rerank)
        - If TIER 1 and TIER 2 didn't match, fall back to RAG.
        - The original hybrid retrieval path.

    60s hard timeout per document. If the budget runs out, return the
    partial result with whatever slots have been collected so far.
"""
from __future__ import annotations

import signal
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .chunker import Chunk, chunk_paragraphs, is_label_row_paragraph, is_short_title, is_footnote
from .embedder import Embedder
from .faiss_store import FaissStore
from .bm25_store import BM25Store
from .reranker import Reranker
from .slot_queries import SLOT_QUERIES
from .heading_anchors import HEADING_ANCHOR_SLOTS, find_heading_match, build_section_index
from .table_routing import TABLE_SLOTS, find_table_for_slot, find_table_for_slot_with_context, find_all_tables_for_slot_with_context
from .section_detector import looks_like_section_heading
from .. import config


# Default per-document timeout in seconds. Hard cap; if exceeded, the
# pipeline returns the partial result rather than blocking the batch.
# Source: policy_platform.config (env-var-backed; defaults match
# the previous hardcoded values exactly).
DEFAULT_TIMEOUT_SECONDS = config.RAG_TIMEOUT_SECONDS

# Hybrid scoring weights: alpha for vector (FAISS), 1-alpha for keyword (BM25).
# Source: policy_platform.config (env var: RAG_ALPHA).
DEFAULT_ALPHA = config.RAG_ALPHA

# Top-k candidates to retrieve from each backend before merging + reranking.
# Source: policy_platform.config (env var: RAG_TOP_K_PER_BACKEND).
TOP_K_PER_BACKEND = config.RAG_TOP_K_PER_BACKEND

# Final number of candidates we rerank per slot.
# Source: policy_platform.config (env var: RAG_RERANK_POOL).
RERANK_POOL = config.RAG_RERANK_POOL

# Minimum hybrid score for a RAG match to be accepted. Below this, the
# slot is reported as no_match rather than a random wrong paragraph.
# Source: policy_platform.config (env var: RAG_MIN_CONFIDENCE).
MIN_RAG_CONFIDENCE = config.RAG_MIN_CONFIDENCE


def _record_claimed_tables(
    matched_tables: List[List[List[str]]],
    tables: List[List[List[str]]],
    section_index: dict,
    table_paragraph_indices: Optional[List[int]],
    paragraphs: List[str],
    claimed_tables: set[int],
) -> None:
    """Record table indices that were claimed by a slot.

    For each table in `matched_tables`, find the corresponding
    `table_idx` in the source `tables` list and add it to
    `claimed_tables`. This prevents the same table from being
    assigned to another slot later in the pipeline.

    Strategy:
    1. Try to match by table_paragraph_indices (when position
       context is available). Walk all source tables, for each
       that maps to slot_id (via _find_table_section_slot), mark it
       claimed.
    2. Fallback: identity match by row content. Walk all source
       tables; if any matches one of `matched_tables` by first-row
       content, mark it claimed.
    """
    from .table_routing import _find_table_section_slot

    # Strategy 1: position-based claim (preferred).
    if table_paragraph_indices:
        for table_idx, src_table in enumerate(tables):
            if table_idx in claimed_tables:
                continue
            # Check if this source table's content matches one of
            # matched_tables (identity match by first-row text).
            if not src_table or not src_table[0]:
                continue
            src_sig = tuple(
                (c or "").strip().lower() for c in src_table[0]
            )
            for mt in matched_tables:
                if not mt or not mt[0]:
                    continue
                mt_sig = tuple((c or "").strip().lower() for c in mt[0])
                if src_sig == mt_sig:
                    claimed_tables.add(table_idx)
                    break

    # Strategy 2: always do content-signature match as a backup
    # so any unmatched table that matches by content is also claimed.
    for table_idx, src_table in enumerate(tables):
        if table_idx in claimed_tables:
            continue
        if not src_table or not src_table[0]:
            continue
        src_sig = tuple((c or "").strip().lower() for c in src_table[0])
        for mt in matched_tables:
            if not mt or not mt[0]:
                continue
            mt_sig = tuple((c or "").strip().lower() for c in mt[0])
            if src_sig == mt_sig:
                claimed_tables.add(table_idx)
                break


def _prose_table_overlap(anchor_text: str, table: List[List[str]]) -> bool:
    """Return True if anchor_text is largely a substring of the table's cells.

    Used to suppress prose paragraphs whose content is already
    represented by a table in the same slot. Strict 70% threshold:
    at least 70% of the prose's alphanumeric characters must appear
    in the concatenated table cell text.
    """
    import re as _re

    if not anchor_text or not table:
        return False
    prose_norm = _re.sub(r"[^a-z0-9]+", " ", anchor_text.lower()).strip()
    if len(prose_norm) < 20:
        # Too short to overlap meaningfully.
        return False
    table_parts: list[str] = []
    for row in table:
        for cell in row:
            if cell:
                table_parts.append(str(cell))
    table_norm = _re.sub(r"[^a-z0-9]+", " ", " ".join(table_parts).lower()).strip()
    if not table_norm:
        return False
    # Check how much of prose_norm appears in table_norm.
    # Use the simpler "all prose tokens are in table_norm" check.
    prose_tokens = set(prose_norm.split())
    table_tokens = set(table_norm.split())
    if not prose_tokens:
        return False
    overlap = len(prose_tokens & table_tokens) / len(prose_tokens)
    return overlap >= 0.70


def _has_version_history_markers(paragraphs) -> bool:
    """True if the document contains version-history markers.

    Used as a guard for slot 14 (History) RAG fallback: if the document
    has no version-history content, RAG may pick semantically-similar
    but wrong content (e.g. checkbox lines, form fields). In that case
    slot 14 should return 'Data is not found' instead of fabricating
    content.

    Markers checked (general, not hardcoded to specific files):
    - "version" / "versions" / "versioned"
    - "FY" followed by digits (e.g. FY26, FY26-27)
    - "initial draft" / "first draft"
    - "revision history" / "version history" / "document history"
    - "change log" / "changelog"
    - "amendment history"
    """
    import re as _re
    if not paragraphs:
        return False
    combined = " ".join(p.lower() for p in paragraphs)
    if "version" in combined:
        return True
    if "initial draft" in combined or "first draft" in combined:
        return True
    if "revision history" in combined or "version history" in combined:
        return True
    if "document history" in combined or "change log" in combined or "changelog" in combined:
        return True
    if "amendment history" in combined:
        return True
    if _re.search(r"\bfy\s*\d", combined):
        return True
    return False


def _has_exclusions_section_markers(paragraphs) -> bool:
    """True if the document contains an Exclusions section heading.

    Used as a guard for slot 9 (Exclusions) RAG fallback: if the document
    has no Exclusions section, RAG may pick semantically-similar but
    wrong content (e.g. a clause from the claims process that mentions
    "not eligible") when no dedicated Exclusions section exists in the
    source.

    Checks for the standard Exclusions section-heading synonyms used
    by the heading-anchor detector. General pattern, not hardcoded to
    specific files.
    """
    import re as _re
    if not paragraphs:
        return False
    # The section-heading regex matches a line that starts with the
    # synonym followed by a separator (:, -, ., or EOL). We use the
    # same pattern as _EXCLUSIONS_HEADING_RE in narrative_inference.py.
    heading_re = _re.compile(
        r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
        r"(?:exclusions?|exceptions?|limitations?|not\s+covered|out\s+of\s+scope|not\s+applicable|excluded\s+groups?|excluded\s+persons?|excluded\s+entities?)"
        r"\s*[:\-.]?\s*$",
        _re.IGNORECASE,
    )
    for p in paragraphs:
        first_line = p.split("\n")[0].strip() if p else ""
        if heading_re.match(first_line):
            return True
    return False


def _has_related_policies_section_markers(paragraphs) -> bool:
    """True if the document contains a Related Policies section heading.

    Used as a guard for slot 13 (Related Policies) RAG fallback: if the
    document has no Related Policies section, RAG may pick semantically-
    similar but wrong content (e.g. a body sentence mentioning
    "supporting documents" in the claims process). In that case slot
    13 should return 'Data is not found'.

    Checks for the standard Related Policies section-heading synonyms
    at the START of a line (with optional numbering prefix). General
    pattern, not hardcoded to specific files.
    """
    import re as _re
    if not paragraphs:
        return False
    heading_re = _re.compile(
        r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
        r"(?:related\s+(?:policies|documents|forms|procedures|guidelines|materials|resources)|"
        r"references|associated\s+(?:policies|documents)|"
        r"linked\s+(?:policies|documents)|companion\s+(?:policies|documents)|"
        r"supplementary\s+(?:policies|documents)|reference\s+materials|"
        r"external\s+references|further\s+reading|see\s+also|"
        r"for\s+more\s+information|see\s+related|other\s+(?:policies|resources))"
        r"\b",
        _re.IGNORECASE,
    )
    for p in paragraphs:
        first_line = p.split("\n")[0].strip() if p else ""
        if heading_re.match(first_line):
            return True
    return False


def _has_introduction_section_markers(paragraphs) -> bool:
    """True if the document contains an Introduction section heading.

    Used as a guard for slot 5 (Introduction) RAG fallback and
    position-based fallback: if the document has no Introduction section,
    both fallbacks may pick semantically-similar but wrong content (e.g.
    a Scope sentence). In that case slot 5 should return 'Data is not
    found' instead.

    Checks for the standard Introduction section-heading synonyms used
    by the heading-anchor detector. General pattern, not hardcoded to
    specific files.
    """
    import re as _re
    if not paragraphs:
        return False
    heading_re = _re.compile(
        r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
        r"(?:introduction|background|preamble|overview|policy\s+introduction|"
        r"executive\s+overview|introduction\s+and\s+scope|"
        r"introduction\s+and\s+purpose|introduction\s+and\s+background)"
        r"\s*[:\-.]?\s*$",
        _re.IGNORECASE,
    )
    for p in paragraphs:
        first_line = p.split("\n")[0].strip() if p else ""
        if heading_re.match(first_line):
            return True
    return False


def _has_policy_statement_section_markers(paragraphs) -> bool:
    """True if the document contains a Policy Statement section heading.

    Used as a guard for slot 6 (Policy Statement) RAG fallback: if the
    document has no Policy Statement section, RAG may pick semantically-
    similar but wrong content (e.g. a Scope sentence). In that case
    slot 6 should return 'Data is not found'.

    Checks for the standard Policy Statement section-heading synonyms
    used by the heading-anchor detector. General pattern, not hardcoded
    to specific files.
    """
    import re as _re
    if not paragraphs:
        return False
    heading_re = _re.compile(
        r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
        r"(?:policy\s+statement|policy\s+statement\s*[-:&\s]+\s*(?:purpose|company|scope)?|"
        r"statement\s+of\s+policy|company\s+policy|corporate\s+policy)"
        r"\s*[:\-.]?\s*$",
        _re.IGNORECASE,
    )
    for p in paragraphs:
        first_line = p.split("\n")[0].strip() if p else ""
        if heading_re.match(first_line):
            return True
    return False



def _find_intro_paragraph(paragraphs, section_index):
    """Find the Introduction paragraph by position.

    The Introduction is the first prose paragraph that:
    1. Is NOT in the slot 1/2/3/4/11 header (label-row paragraphs).
    2. Is NOT a short title (<= 80 chars, no body punctuation).
    3. Is NOT marked as a section heading for slots 5-14.
    4. Appears BEFORE any slot 6/7/... heading.

    Returns (paragraph_idx, text) or None if no intro found.
    """
    from .chunker import is_label_row_paragraph, is_short_title
    from .heading_anchors import _is_heading_for_slot

    first_section_heading = None
    for i, p in enumerate(paragraphs):
        for sid in (6, 7, 8, 9, 10, 12, 13, 14):
            if _is_heading_for_slot(sid, p):
                first_section_heading = i
                break
        if first_section_heading is not None:
            break

    end = first_section_heading if first_section_heading is not None else len(paragraphs)
    for i in range(end):
        p = paragraphs[i]
        if not p or not p.strip():
            continue
        if is_label_row_paragraph(p):
            continue
        if is_short_title(p):
            continue
        is_any_heading = False
        for sid in (5, 6, 7, 8, 9, 10, 12, 13, 14):
            if _is_heading_for_slot(sid, p):
                is_any_heading = True
                break
        if is_any_heading:
            continue
        return (i, p.strip())
    return None


@dataclass
class SlotAssignment:
    slot_id: int
    chunk_text: Optional[str]
    chunk_id: Optional[int] = None
    source_idx: Optional[int] = None
    score: float = 0.0
    backend: str = ""
    # Whole-table passthrough. When set, the adapter puts this in
    # content_tables (not content_paragraphs).
    table: Optional[List[List[str]]] = None
    # Additional tables for this slot (e.g. when a section has
    # multiple data tables). The first table is `table`; the rest
    # are in `extra_tables`. Both are rendered in the slot.
    extra_tables: list = field(default_factory=list)


@dataclass
class RAGResult:
    slots: Dict[int, SlotAssignment] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    timed_out: bool = False
    embedder_backend: str = ""
    faiss_backend: str = ""
    bm25_backend: str = "rank_bm25"
    reranker_backend: str = ""

    def get(self, slot_id: int) -> Optional[SlotAssignment]:
        return self.slots.get(slot_id)


class _TimeoutError(RuntimeError):
    pass


def _install_timeout(timeout: float) -> None:
    """Install a SIGALRM-based timeout (POSIX). No-op on Windows.

    On Windows, the pipeline uses wall-clock time checks inside the
    main loop instead of signals. We still keep the API identical.
    """

    def _handler(signum, frame):
        raise _TimeoutError("rag pipeline timeout")

    if not hasattr(signal, "SIGALRM"):
        return
    try:
        signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, timeout)
    except (ValueError, OSError):
        return


def _clear_timeout() -> None:
    if not hasattr(signal, "SIGALRM"):
        return
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except (ValueError, OSError):
        return


class RetrievalPipeline:
    """End-to-end RAG orchestrator for the 15-slot Brain framework.

    Holds a single shared `Embedder` + `Reranker` across calls (they
    are expensive to construct). The FAISS + BM25 stores are rebuilt
    per document because they are per-corpus.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        alpha: float = DEFAULT_ALPHA,
    ) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.alpha = float(alpha)
        # Phase 12 — default Embedder to TF-IDF (skips the 80-MB
        # sentence-transformers cold load). Reranker() honors the
        # AGENTIC_POLICY_RAG_RERANKER env var and otherwise stays on
        # the fallback path.
        self._embedder = Embedder(prefer_tfidf=True)
        self._reranker = Reranker()

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    @property
    def reranker(self) -> Reranker:
        return self._reranker

    def _check_time(self, t0: float) -> bool:
        """Return True if we still have time budget remaining."""
        return (time.perf_counter() - t0) < self.timeout_seconds

    def run(
        self,
        paragraphs: List[str],
        *,
        tables: Optional[List[List[List[str]]]] = None,
        table_paragraph_indices: Optional[List[int]] = None,
    ) -> RAGResult:
        """Run the full RAG pipeline on cleaned paragraphs + tables.

        Args:
            paragraphs: cleaned paragraphs in source order.
            tables: optional list of source tables (each is list of
                    rows, each row is list of cell text). Used by
                    TIER 2 (table passthrough) for slots 9, 10, 14.
            table_paragraph_indices: optional list, parallel to `tables`,
                    where each entry is the paragraph index AFTER which
                    the table appeared in the source. Used for
                    accurate context-aware table routing (issue 1-3).

        Returns:
            RAGResult with one SlotAssignment per slot (1-15).
            Slot 15 is always None (logo slot).
            Label-row slots (1, 2, 3, 4, 11) are always None - the
            renderer fills them from field_map, not from RAG.

        Global paragraph reservation:
            Once a paragraph is assigned to a slot via heading-anchor
            or table-passthrough, it is RESERVED. RAG will not pick
            it for a different slot. This prevents the same paragraph
            from appearing in multiple slots (the "duplicate" issue).
        """
        t0 = time.perf_counter()
        result = RAGResult()
        result.embedder_backend = self._embedder.backend
        result.reranker_backend = self._reranker.backend

        # Label-aware chunking contract: default-on at the RAG layer.
        # Splits dense paragraphs at every section-heading label so
        # slot-bleed is suppressed (e.g. Earthquake PDF slot 12/14).
        # Opt-out via config.RAG_LABEL_CHUNKING=False for legacy
        # single-paragraph routing parity.
        if config.RAG_LABEL_CHUNKING:
            from ..extractors import split_paragraphs
            paragraphs = split_paragraphs(
                paragraphs,
                slots=range(5, 15),
                terminator_aware=False,
            )

        # Slot 15 is the logo slot - always None.
        result.slots[15] = SlotAssignment(slot_id=15, chunk_text=None, backend="logo")

        # Label-row slots (1, 2, 3, 4, 11) are filled by field_parser,
        # not by RAG. Leave them None so the renderer reads field_map.
        LABEL_ROW_SLOTS = {1, 2, 3, 4, 11}
        for sid in LABEL_ROW_SLOTS:
            result.slots[sid] = SlotAssignment(
                slot_id=sid, chunk_text=None, backend="label_row_external"
            )

        if not paragraphs:
            result.elapsed_seconds = time.perf_counter() - t0
            for sid in range(1, 15):
                if sid not in result.slots:
                    result.slots[sid] = SlotAssignment(slot_id=sid, chunk_text=None, backend="empty_input")
            return result

        # Track which paragraph indices are RESERVED (already used).
        reserved_paragraphs: set[int] = set()
        # Phase K.1: Track which table indices have been claimed by
        # an earlier slot. This is shared across all table-routing
        # calls within this run() so the same table cannot be
        # assigned to multiple slots (the cross-slot duplication bug).
        claimed_tables: set[int] = set()

        # Build a section index FIRST: a per-paragraph mapping of
        # which slot each paragraph belongs to. This is the
        # foundation for context-aware table routing (issue 1-3):
        # we need to know which section a table appears in before
        # we can route it to the right slot.
        section_index = build_section_index(paragraphs)

        # ---- TIER 1 + TIER 2: deterministic, no model needed ----
        # These are checked first for every slot. RAG only runs as
        # fallback for slots that didn't match heading or table.
        slots_needing_rag: List[int] = []
        for slot_id in SLOT_QUERIES:
            if slot_id in LABEL_ROW_SLOTS or slot_id == 15:
                continue

            # TIER 2 (FIRST for slot 10): table passthrough.
            # Slot 10 is the Award Structure / Payout Tiers table.
            # Always check the table first because if the source has
            # a payout/facility table, that's almost always slot 10.
            # Use the multi-table variant so all tables in slot 10's
            # section are routed (not just the first one).
            if slot_id == 10 and tables:
                matched_tables = find_all_tables_for_slot_with_context(
                    slot_id, tables, section_index, paragraphs,
                    table_paragraph_indices=table_paragraph_indices,
                    claimed_tables=claimed_tables,
                )
                if matched_tables:
                    _record_claimed_tables(
                        matched_tables, tables, section_index,
                        table_paragraph_indices, paragraphs,
                        claimed_tables,
                    )
                    prose_anchor = find_heading_match(
                        slot_id, paragraphs, reserved_paragraphs
                    )
                    primary_table = matched_tables[0]
                    extra_tables = matched_tables[1:]
                    if prose_anchor is not None:
                        s_idx, e_idx, prose_text = prose_anchor
                        reserved_paragraphs.update(range(s_idx, e_idx + 1))
                        if prose_text and prose_text.strip() and prose_text.strip() != paragraphs[s_idx].strip():
                            slot_text = prose_text
                            slot_backend = "heading_anchor+table"
                        else:
                            slot_text = None
                            slot_backend = "table_passthrough"
                        result.slots[slot_id] = SlotAssignment(
                            slot_id=slot_id,
                            chunk_text=slot_text,
                            source_idx=s_idx if slot_text else None,
                            table=primary_table,
                            extra_tables=extra_tables,
                            score=1.0,
                            backend=slot_backend,
                        )
                    else:
                        result.slots[slot_id] = SlotAssignment(
                            slot_id=slot_id,
                            chunk_text=None,
                            table=primary_table,
                            extra_tables=extra_tables,
                            score=1.0,
                            backend="table_passthrough",
                        )
                    continue

            # TIER 1: heading-anchored match.
            # For table-capable slots (9, 14), we also try to attach
            # a table that appears in the same section.
            anchor_text = None
            anchor_start = anchor_end = None
            if slot_id in HEADING_ANCHOR_SLOTS:
                anchor = find_heading_match(
                    slot_id, paragraphs, reserved_paragraphs
                )
                if anchor is not None:
                    anchor_start, anchor_end, anchor_text = anchor

            # TIER 2 (other table slots): slot 9, slot 14.
            # Try to find tables in this slot's section. A slot's
            # section may have multiple data tables; route all of them.
            section_table = None
            section_extra_tables: list = []
            if slot_id in TABLE_SLOTS and tables:
                matched_tables = find_all_tables_for_slot_with_context(
                    slot_id, tables, section_index, paragraphs,
                    table_paragraph_indices=table_paragraph_indices,
                    claimed_tables=claimed_tables,
                )
                if matched_tables:
                    section_table = matched_tables[0]
                    section_extra_tables = matched_tables[1:]
                    _record_claimed_tables(
                        matched_tables, tables, section_index,
                        table_paragraph_indices, paragraphs,
                        claimed_tables,
                    )

            # Combine heading-anchor + table for table-capable slots.
            if anchor_text is not None and section_table is not None:
                reserved_paragraphs.update(range(anchor_start, anchor_end + 1))
                # Phase K.1 — prose/table overlap suppression.
                # If the prose anchor text is largely a substring of
                # the table's cell text, the prose IS the table content
                # (likely from PDF paragraph-stream leakage or a heading
                # line followed by the table). Drop the prose, keep only
                # the table. This prevents the user from seeing the
                # same content twice (once as prose, once in the table).
                if _prose_table_overlap(anchor_text, section_table):
                    slot_text = None
                    slot_backend = "table_passthrough"
                # If anchor_text is just the heading (no body), use
                # table-only mode.
                elif anchor_text.strip() and anchor_text.strip() != paragraphs[anchor_start].strip():
                    slot_text = anchor_text
                    slot_backend = "heading_anchor+table"
                else:
                    slot_text = None
                    slot_backend = "table_passthrough"
                # Phase K.2 — when slot 9 (Exclusions) has BOTH prose
                # AND a section table, the prose paragraph is the
                # "list of exclusions" sentence (e.g. "Off-site leased
                # premises not under hospital operational control.")
                # and the table is additional structured data. The
                # user wants ONE source of truth — keep the prose OR
                # the table, not both. When the table is present, drop
                # the prose so the user doesn't see duplicate content.
                if slot_id == 9 and slot_text is not None:
                    slot_text = None
                    slot_backend = "table_passthrough"
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=slot_text,
                    source_idx=anchor_start if slot_text else None,
                    table=section_table,
                    extra_tables=section_extra_tables,
                    score=1.0,
                    backend=slot_backend,
                )
                continue
            if anchor_text is not None:
                # Prose only.
                reserved_paragraphs.update(range(anchor_start, anchor_end + 1))
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=anchor_text,
                    source_idx=anchor_start,
                    score=1.0,
                    backend="heading_anchor",
                )
                continue
            if section_table is not None:
                # Table only.
                prose_anchor = find_heading_match(
                    slot_id, paragraphs, reserved_paragraphs
                )
                if prose_anchor is not None:
                    s_idx, e_idx, prose_text = prose_anchor
                    reserved_paragraphs.update(range(s_idx, e_idx + 1))
                    # Phase K.1 — also apply prose/table overlap here.
                    if _prose_table_overlap(prose_text or "", section_table):
                        slot_text = None
                        slot_backend = "table_passthrough"
                    elif prose_text and prose_text.strip() and prose_text.strip() != paragraphs[s_idx].strip():
                        slot_text = prose_text
                        slot_backend = "heading_anchor+table"
                    else:
                        slot_text = None
                        slot_backend = "table_passthrough"
                    result.slots[slot_id] = SlotAssignment(
                        slot_id=slot_id,
                        chunk_text=slot_text,
                        source_idx=s_idx if slot_text else None,
                        table=section_table,
                        extra_tables=section_extra_tables,
                        score=1.0,
                        backend=slot_backend,
                    )
                else:
                    result.slots[slot_id] = SlotAssignment(
                        slot_id=slot_id,
                        chunk_text=None,
                        table=section_table,
                        extra_tables=section_extra_tables,
                        score=1.0,
                        backend="table_passthrough",
                    )
                continue

            # Slot needs RAG.
            slots_needing_rag.append(slot_id)

        # Position-based fallback for slot 5 (INTRODUCTION): if slot 5
        # has no heading match, the Introduction content is the first
        # prose paragraph AFTER the slot 1/2/3/4 header table and BEFORE
        # the first explicit section heading (slot 5, 6, 7, ...). This
        # handles policy documents where the Introduction has no heading
        # (just a standalone paragraph).
        if 5 in slots_needing_rag and 5 not in result.slots:
            # Guard: only use position fallback if the document has
            # an Introduction section heading. Without this guard,
            # the fallback may pick a Scope sentence as Introduction.
            if not _has_introduction_section_markers(paragraphs):
                result.slots[5] = SlotAssignment(
                    slot_id=5,
                    chunk_text=None,
                    backend="no_introduction_section",
                )
                slots_needing_rag.remove(5)
            else:
                intro_para = _find_intro_paragraph(paragraphs, section_index)
                if intro_para is not None:
                    idx, text = intro_para
                    result.slots[5] = SlotAssignment(
                        slot_id=5,
                        chunk_text=text,
                        source_idx=idx,
                        score=1.0,
                        backend="position_fallback",
                    )
                    slots_needing_rag.remove(5)

        if not slots_needing_rag:
            result.elapsed_seconds = time.perf_counter() - t0
            return result

        if not self._check_time(t0):
            result.timed_out = True
            for sid in slots_needing_rag:
                if sid not in result.slots:
                    result.slots[sid] = SlotAssignment(slot_id=sid, chunk_text=None, backend="timeout")
            result.elapsed_seconds = time.perf_counter() - t0
            return result

        # ---- TIER 3: RAG for the remaining slots ----
        # ---- Stage 1: chunk the document ----
        try:
            chunks: List[Chunk] = chunk_paragraphs(paragraphs)
        except Exception:
            chunks = []
        if not chunks:
            result.elapsed_seconds = time.perf_counter() - t0
            for sid in slots_needing_rag:
                if sid not in result.slots:
                    result.slots[sid] = SlotAssignment(slot_id=sid, chunk_text=None, backend="no_chunks")
            return result

        # ---- Stage 2: embed the chunks ----
        try:
            embeddings = self._embedder.embed([c.text for c in chunks])
        except Exception:
            embeddings = None
        if embeddings is None or len(embeddings) == 0:
            result.elapsed_seconds = time.perf_counter() - t0
            for sid in slots_needing_rag:
                if sid not in result.slots:
                    result.slots[sid] = SlotAssignment(slot_id=sid, chunk_text=None, backend="embed_failed")
            return result

        # ---- Stage 3: build the FAISS + BM25 indices ----
        faiss_store = FaissStore()
        faiss_store.build(embeddings)
        result.faiss_backend = faiss_store.backend

        bm25_store = BM25Store()
        bm25_store.build([c.text for c in chunks])
        result.bm25_backend = "rank_bm25" if bm25_store._bm25 is not None and bm25_store._bm25.__class__.__name__ == "BM25Okapi" else "python_bm25"

        # Build a set of reserved chunk indices.
        # Map paragraph idx -> chunk idx (chunks have source_idx field).
        reserved_chunk_indices: set[int] = set()
        for ci, ch in enumerate(chunks):
            if ch.source_idx in reserved_paragraphs:
                reserved_chunk_indices.add(ci)

        # Exclude label-row paragraphs and short titles from RAG candidates.
        # These are already handled by field_parser or are not real content.
        # The exclusion is at the CHUNK level since chunks have source_idx.
        # Track the source_idx of excluded paragraphs to filter chunks.
        from .heading_anchors import _is_toc_entry
        excluded_paragraphs: set[int] = set()
        for pi, p in enumerate(paragraphs):
            if pi in reserved_paragraphs:
                continue
            if is_label_row_paragraph(p) or is_short_title(p) or _is_toc_entry(p) or is_footnote(p):
                excluded_paragraphs.add(pi)
        for ci, ch in enumerate(chunks):
            if ch.source_idx in excluded_paragraphs:
                reserved_chunk_indices.add(ci)

        # ---- Stage 4: per-slot RAG retrieval + rerank ----
        # Process critical (tier 1) slots FIRST so they get the
        # largest RAG time budget. If the timeout fires mid-loop,
        # tier-3 slots lose content but tier-1 slots (incl. slot 14
        # HISTORY) are guaranteed to be processed. Without this
        # ordering, dense PDFs like Earthquake timed out on slot 13
        # and slot 14 simultaneously, marking both as "timeout"
        # and rendering a placeholder instead of the version line.
        try:
            from policy_platform.framework.slot_tiers import SLOT_TIERS

            slots_needing_rag = sorted(
                slots_needing_rag,
                key=lambda sid: SLOT_TIERS.get(sid, 3),
            )
        except Exception:
            # If SLOT_TIERS unavailable for any reason, fall back
            # to the original iteration order — same behaviour.
            pass
        for slot_id in slots_needing_rag:
            if not self._check_time(t0):
                result.timed_out = True
                for sid in slots_needing_rag:
                    if sid not in result.slots:
                        result.slots[sid] = SlotAssignment(slot_id=sid, chunk_text=None, backend="timeout")
                break

            # Slot 14 (History) fallback guard: if the document contains
            # no version-history markers (version, FY, initial draft,
            # revision history, change log, amendment history), skip
            # RAG for this slot and return no_match. Without this guard,
            # RAG may pick semantically-similar but wrong content (e.g.
            # a checkbox line or form field) when no dedicated History
            # section exists in the source.
            if slot_id == 14 and not _has_version_history_markers(paragraphs):
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=None,
                    backend="no_history_markers",
                )
                continue

            # Slot 9 (Exclusions) fallback guard: if the document has
            # no Exclusions section heading, skip RAG and return
            # no_match. Without this guard, RAG may pick semantically-
            # similar but wrong content (e.g. a clause from the claims
            # process that mentions "not eligible") when no dedicated
            # Exclusions section exists in the source.
            if slot_id == 9 and not _has_exclusions_section_markers(paragraphs):
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=None,
                    backend="no_exclusions_section",
                )
                continue

            # Slot 13 (Related Policies) fallback guard: if the document
            # has no Related Policies section heading, skip RAG and
            # return no_match. Without this guard, RAG may pick
            # semantically-similar but wrong content (e.g. a body
            # sentence mentioning "supporting documents" in the claims
            # process) when no dedicated Related Policies section
            # exists in the source.
            if slot_id == 13 and not _has_related_policies_section_markers(paragraphs):
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=None,
                    backend="no_related_policies_section",
                )
                continue

            # Slot 6 (Policy Statement) fallback guard: if the document
            # has no Policy Statement section heading, skip RAG and
            # return no_match. Without this guard, RAG may pick
            # semantically-similar but wrong content (e.g. a Scope
            # sentence) when no dedicated Policy Statement section
            # exists in the source.
            if slot_id == 6 and not _has_policy_statement_section_markers(paragraphs):
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=None,
                    backend="no_policy_statement_section",
                )
                continue

            # Slot 5 (Introduction) fallback guard: if the document
            # has no Introduction section heading, skip RAG and
            # return no_match. Without this guard, RAG may pick
            # semantically-similar but wrong content (e.g. a Scope
            # sentence) when no dedicated Introduction section exists
            # in the source.
            if slot_id == 5 and not _has_introduction_section_markers(paragraphs):
                result.slots[slot_id] = SlotAssignment(
                    slot_id=slot_id,
                    chunk_text=None,
                    backend="no_introduction_section",
                )
                continue

            queries = SLOT_QUERIES.get(slot_id, [])
            assignment = self._rag_assign_slot(
                slot_id=slot_id,
                queries=queries,
                chunks=chunks,
                embeddings=embeddings,
                faiss_store=faiss_store,
                bm25_store=bm25_store,
                reserved_chunk_indices=reserved_chunk_indices,
            )
            result.slots[slot_id] = assignment

        result.elapsed_seconds = time.perf_counter() - t0
        return result

    def _rag_assign_slot(
        self,
        *,
        slot_id: int,
        queries: List[str],
        chunks: List[Chunk],
        embeddings: np.ndarray,
        faiss_store: FaissStore,
        bm25_store: BM25Store,
        reserved_chunk_indices: Optional[set] = None,
    ) -> SlotAssignment:
        """Pick the best chunk for one slot via hybrid retrieval + rerank.

        Includes:
        - Paragraph reservation: chunks whose source_idx is in
          reserved_chunk_indices are excluded from candidates.
        - Minimum chunk length: chunks shorter than 30 chars are
          excluded (they're typically random fragments like "8. This
          policy." that are not real content).
        - Position-aware scoring: chunks earlier in the document get
          a slight boost for early slots (5, 6, 7), chunks later for
          later slots (12, 13, 14). This helps when no heading match
          exists and RAG has to pick from a flat document.
        - Confidence threshold: if the final hybrid score is too low,
          the slot is reported as no_match rather than picking a
          random wrong paragraph.
        """
        if not queries:
            return SlotAssignment(slot_id=slot_id, chunk_text=None, backend="no_queries")

        if reserved_chunk_indices is None:
            reserved_chunk_indices = set()

        MIN_CHUNK_CHARS = 30

        # Aggregate (chunk_idx, hybrid_score) across all slot queries.
        agg: Dict[int, float] = {}

        for q in queries:
            # Vector search.
            try:
                q_vec = self._embedder.embed([q])
            except Exception:
                q_vec = None
            if q_vec is not None and len(q_vec) > 0:
                faiss_hits = faiss_store.search(q_vec, TOP_K_PER_BACKEND)
                for hits in faiss_hits:
                    for idx, score in hits:
                        if idx in reserved_chunk_indices:
                            continue
                        if len(chunks[idx].text.strip()) < MIN_CHUNK_CHARS:
                            continue
                        agg[idx] = agg.get(idx, 0.0) + self.alpha * float(score)

            # BM25 search.
            bm25_hits = bm25_store.search([q], TOP_K_PER_BACKEND)
            for hits in bm25_hits:
                if not hits:
                    continue
                # Normalize BM25 score to [0, 1] by dividing by max.
                max_bm25 = max(s for _, s in hits) or 1.0
                for idx, score in hits:
                    if idx in reserved_chunk_indices:
                        continue
                    if len(chunks[idx].text.strip()) < MIN_CHUNK_CHARS:
                        continue
                    agg[idx] = agg.get(idx, 0.0) + (1.0 - self.alpha) * (float(score) / max_bm25)

        if not agg:
            return SlotAssignment(slot_id=slot_id, chunk_text=None, backend="all_reserved")

        # Apply a position-aware score adjustment. Slots 5-9 (early
        # prose slots) prefer earlier paragraphs; slots 12-14 (later
        # slots) prefer later paragraphs. The adjustment is small
        # (max 10% of the score) so it doesn't dominate the hybrid
        # signal; it's a tie-breaker when scores are close.
        if chunks:
            n_paragraphs = max(1, max((c.source_idx for c in chunks), default=0) + 1)
            for idx in list(agg.keys()):
                src = chunks[idx].source_idx
                # Normalized position in [0, 1].
                pos = src / n_paragraphs
                if slot_id in (5, 6, 7, 8, 9):
                    # Earlier is better. Max boost at pos=0, no boost at pos=1.
                    boost = 0.10 * (1.0 - pos)
                elif slot_id in (12, 13, 14):
                    # Later is better. Max boost at pos=1, no boost at pos=0.
                    boost = 0.10 * pos
                else:
                    boost = 0.0
                agg[idx] += boost

        # For slot 5 (Introduction), if RAG couldn't find anything
        # above the confidence threshold, fall back to the EARLIEST
        # non-reserved, non-excluded chunk of meaningful length.
        # This handles docs where the introduction is a generic
        # paragraph without a clear "Introduction" heading.
        if slot_id == 5 and not agg:
            for ci, ch in enumerate(chunks):
                if ci in reserved_chunk_indices:
                    continue
                if len(ch.text.strip()) >= 50:
                    return SlotAssignment(
                        slot_id=slot_id,
                        chunk_text=ch.text,
                        source_idx=ch.source_idx,
                        score=0.5,
                        backend="fallback_position",
                    )

        # Take top candidates for reranking.
        ranked = sorted(agg.items(), key=lambda x: -x[1])[:RERANK_POOL]
        top_idx, top_hybrid_score = ranked[0]
        # Normalize the hybrid score to [0, 1] by dividing by the number
        # of queries, so the confidence threshold is meaningful.
        normalized = top_hybrid_score / max(len(queries), 1)
        if normalized < MIN_RAG_CONFIDENCE:
            return SlotAssignment(
                slot_id=slot_id,
                chunk_text=None,
                score=float(normalized),
                backend="low_confidence",
            )

        candidate_indices = [i for i, _ in ranked]
        candidate_texts = [chunks[i].text for i in candidate_indices]

        # Pick the most discriminative query for the reranker (longest one,
        # since it carries the most context).
        rerank_query = max(queries, key=len) if queries else "policy"

        try:
            reranked = self._reranker.rerank(rerank_query, candidate_texts, top_k=1)
        except Exception:
            reranked = [(0, 0.0)]

        if not reranked:
            # Fall back to the hybrid-ranked top-1.
            best_idx, best_score = ranked[0]
        else:
            best_pos, best_score = reranked[0]
            if 0 <= best_pos < len(candidate_indices):
                best_idx = candidate_indices[best_pos]
            else:
                best_idx, best_score = ranked[0]

        if not (0 <= best_idx < len(chunks)):
            return SlotAssignment(slot_id=slot_id, chunk_text=None, backend="oob_idx")

        # Final safety check: don't return a reserved chunk.
        if best_idx in reserved_chunk_indices:
            # Try the next-best.
            for cand_idx, _ in ranked[1:]:
                if cand_idx not in reserved_chunk_indices:
                    best_idx = cand_idx
                    break
            else:
                return SlotAssignment(
                    slot_id=slot_id, chunk_text=None, backend="all_reserved"
                )

        chosen = chunks[best_idx]
        return SlotAssignment(
            slot_id=slot_id,
            chunk_text=chosen.text,
            chunk_id=chosen.chunk_id,
            source_idx=chosen.source_idx,
            score=float(best_score),
            backend=f"rag:{self._embedder.backend}+{self._reranker.backend}",
        )
