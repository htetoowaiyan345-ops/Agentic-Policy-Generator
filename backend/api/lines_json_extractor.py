"""lines_json_extractor.py

Phase 6 — convert an approved policy_versions.lines_json back into the
shape of a freshly-extracted source document, so the Brain pipeline can
re-run end-to-end against the reviewer's saved edits.

The synthetic ExtractedDocument:
  - puts every ['p', rich/text] paragraph into `paragraphs` (one
    paragraph per item, in the order saved), and every ['t', rich/text]
    row's first cell into `tables` (one table per item).
  - records the Brain slot id per paragraph via `paragraph_slot_origin`,
    a parallel list indexed identically to `paragraphs`. The pipeline's
    downstream steps use this for slot-by-slot diff audit columns.
  - provides a default `source_sha256` derived from the lines_json
    contents so the rest of the pipeline keeps its identity invariants.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, List, Optional

from policy_platform.extractors.base import ExtractedDocument
from policy_platform.framework.brain_slot_map import BRAIN_SLOT_RANGES, SLOT_HEADINGS


HTML_TAG_RE = re.compile(r'<[^>]+>')


# Slot-1 metadata field patterns (case-insensitive prefix match on the
# paragraph text after stripping HTML). These mirror the brain template's
# header field labels (Type, Policy Title, Policy Number, etc.).
_METADATA_SLOT1_PATTERNS = (
    re.compile(r'^Type\s*[:\u00A0]', re.IGNORECASE),
    re.compile(r'^Policy\s*Title\s*[:\u00A0]', re.IGNORECASE),
    re.compile(r'^Policy\s*Number\s*[:\u00A0]', re.IGNORECASE),
    re.compile(r'^Applicable\s+Sector', re.IGNORECASE),
    re.compile(r'^Functional\s+Area', re.IGNORECASE),
)

# Slot-3 metadata field patterns (Approval & Governance).
_METADATA_SLOT3_PATTERNS = (
    re.compile(r'^Effective\s+Date', re.IGNORECASE),
    re.compile(r'^Approved\s+by\s*[:\u00A0]', re.IGNORECASE),
    re.compile(r'^Prepared\s+by\s*[:\u00A0]', re.IGNORECASE),
    re.compile(r'^Responsible\s+Function', re.IGNORECASE),
    re.compile(r'^Supersedes\s*[:\u00A0]', re.IGNORECASE),
    re.compile(r'^Last\s+Reviewed', re.IGNORECASE),
    re.compile(r'^Applies\s+to\s*[:\u00A0]', re.IGNORECASE),
)

_METADATA_SLOT2_PATTERN = re.compile(r'^Brief\s+Description\s*[:\u00A0]', re.IGNORECASE)
_METADATA_SLOT4_PATTERN = re.compile(r'^Reason\s+for\s+Policy\s*[:\u00A0]', re.IGNORECASE)


def _classify_slot_from_text(text: str) -> int:
    """Return the Brain slot id for a single paragraph by matching its
    text against the framework's heading labels + metadata prefixes.

    Returns 0 when no slot can be confidently identified.
    """
    if not text:
        return 0
    t = text.strip()
    upper = t.upper()
    # Section headings (exact or near-exact match).
    if upper in {'INTRODUCTION', 'POLICY STATEMENT', 'DEFINITIONS', 'HISTORY'}:
        return {'INTRODUCTION': 5, 'POLICY STATEMENT': 6, 'DEFINITIONS': 12, 'HISTORY': 14}[upper]
    if upper.startswith('RELATED POLICIES'):
        return 13
    if upper.startswith('1. PURPOSE') or upper == 'PURPOSE':
        return 7
    if upper.startswith('2. SCOPE') or upper == 'SCOPE & BENEFICIARIES':
        return 8
    if upper.startswith('3. EXCLUSIONS') or upper == 'EXCLUSIONS':
        return 9
    if upper.startswith('4. AWARD STRUCTURE') or upper == 'AWARD STRUCTURE & PAYOUT TIERS':
        return 10
    if upper.startswith('POLICY REVIEW NOTE'):
        return 11
    # Metadata prefixes.
    for pat in _METADATA_SLOT1_PATTERNS:
        if pat.match(t):
            return 1
    if _METADATA_SLOT2_PATTERN.match(t):
        return 2
    for pat in _METADATA_SLOT3_PATTERNS:
        if pat.match(t):
            return 3
    if _METADATA_SLOT4_PATTERN.match(t):
        return 4
    return 0


def _set_slot(payload: dict, slot: int) -> dict:
    """Return a copy of `payload` with `slot` set to `slot`. Tables keep
    their `slot` (mirrors paragraphs)."""
    out = dict(payload)
    out['slot'] = slot
    return out


def infer_anchor_slots(lines_json: Iterable) -> list:
    """Pattern-based slot inference for the saved `lines_json`.

    Walks the saved lines in order. For every paragraph whose
    `slot == 0` (i.e. was typed as a free paragraph by the reviewer or
    saved without a slot), try to assign a slot id by matching its
    text against the Brain framework headings + metadata prefixes.

    Slot inheritance: after assigning a slot to a paragraph, subsequent
    `slot=0` paragraphs inherit that slot until the next recognised
    heading. This matches the editor's `lastSlot` behaviour so the
    published .docx routes content into the right slot body.

    Only paragraphs with `slot == 0` are rewritten. Paragraphs that
    already carry a non-zero slot (e.g. from the editor's `data-slot`
    attribute or `anchor_slot`) are left alone — those assignments are
    explicit and trusted.

    Returns a new list; the input is not mutated.
    """
    out: list = []
    last_slot = 0
    for raw in lines_json or []:
        if not isinstance(raw, list) or len(raw) != 2:
            continue
        kind, payload = raw[0], raw[1]
        if kind == 'p':
            p = payload if isinstance(payload, dict) else {'slot': 0, 'text': str(payload), 'html': str(payload)}
            current_slot = int(p.get('slot', 0) or 0)
            text = (
                p.get('text')
                or _strip_html_to_plain(p.get('html') or '')
            )
            if current_slot != 0:
                last_slot = current_slot
                out.append(['p', p])
                continue
            inferred = _classify_slot_from_text(text)
            if inferred != 0:
                last_slot = inferred
                out.append(['p', _set_slot(p, inferred)])
                continue
            if last_slot != 0:
                out.append(['p', _set_slot(p, last_slot)])
                continue
            out.append(['p', p])
        elif kind == 't':
            p = payload if isinstance(payload, dict) else {'slot': 0, 'rows': (payload or [])}
            current_slot = int(p.get('slot', 0) or 0)
            if current_slot != 0:
                out.append(['t', p])
                continue
            if last_slot != 0:
                out.append(['t', _set_slot(p, last_slot)])
                continue
            out.append(['t', p])
        else:
            out.append([kind, payload])
    return out


def preserve_editor_anchor_slot(lines_json: Iterable) -> list:
    """Propagate the editor-set `anchor_slot` field onto `slot` when
    `slot == 0`. The editor attaches `data-slot` to its paragraphs but
    earlier pipelines dropped that field on save — this helper recovers
    the assignment from any surviving `anchor_slot` sidecar.

    Returns a new list; the input is not mutated.
    """
    out: list = []
    for raw in lines_json or []:
        if not isinstance(raw, list) or len(raw) != 2:
            continue
        kind, payload = raw[0], raw[1]
        if kind in ('p', 't') and isinstance(payload, dict):
            current_slot = int(payload.get('slot', 0) or 0)
            anchor = payload.get('anchor_slot')
            try:
                anchor_slot = int(anchor) if anchor is not None else 0
            except (TypeError, ValueError):
                anchor_slot = 0
            if current_slot == 0 and anchor_slot != 0:
                out.append([kind, _set_slot(payload, anchor_slot)])
                continue
        out.append([kind, payload])
    return out


def _strip_html_to_plain(text: str) -> str:
    """Best-effort plain-text extractor for HTML; falls back to the
    literal input on parse failure."""
    if not text:
        return ''
    if '<' not in text:
        return text
    plain = HTML_TAG_RE.sub('', text)
    return (plain
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&quot;', '"')
            .replace('&apos;', "'"))


def normalise_lines_json(lines_json: Iterable) -> list:
    """Accept either legacy `['p', str]` / `['t', list[list[str]]]`
    payloads or rich `['p', dict]` / `['t', dict]` payloads and always
    return the rich shape. Strings are passed through with slot=0."""
    out: list = []
    for raw in lines_json or []:
        if not isinstance(raw, list) or len(raw) != 2:
            continue
        kind, payload = raw[0], raw[1]
        if kind == 'p':
            if isinstance(payload, str):
                out.append(['p', {'slot': 0, 'text': payload, 'html': payload}])
            elif isinstance(payload, dict):
                out.append(['p', payload])
        elif kind == 't':
            if isinstance(payload, dict) and 'rows' in payload:
                out.append(['t', payload])
            elif isinstance(payload, list):
                rows = [[str(c) if c is not None else '' for c in (row or [])]
                        for row in payload]
                out.append(['t', {'slot': 0, 'rows': rows}])
    return out


def reviewer_slot_bindings(lines_json: Iterable) -> dict[int, list[str]]:
    """Build a `{slot_id: [paragraph_text, ...]}` map from a reviewer's
    saved `lines_json`. Used by the publish pipeline to override
    RAG-retrieved slot content with the reviewer's edits.

    Only `['p', ...]` paragraphs are included; tables are excluded
    (the publish pipeline keeps RAG-rendered table content unless
    tables are explicitly bound, which the current data model does
    not support).

    Paragraphs whose `slot` is 0 (or missing) are bucketed under
    slot 0 and represent additions the reviewer placed outside any
    known slot — these are returned but should generally be ignored
    by the binding step (they are appended to the body verbatim by
    the reviewer; the pipeline's downstream steps handle them).

    Empty/whitespace-only paragraphs are skipped.
    """
    bindings: dict[int, list[str]] = {}
    for raw in lines_json or []:
        if not isinstance(raw, list) or len(raw) != 2:
            continue
        kind, payload = raw[0], raw[1]
        if kind != 'p':
            continue
        if isinstance(payload, str):
            slot = 0
            text = payload
        elif isinstance(payload, dict):
            try:
                slot = int(payload.get('slot', 0) or 0)
            except (TypeError, ValueError):
                slot = 0
            text = (
                payload.get('text')
                or _strip_html_to_plain(payload.get('html') or '')
            )
        else:
            continue
        if not text or not text.strip():
            continue
        bindings.setdefault(slot, []).append(text)
    return bindings


class LinesJsonExtractor:
    """Convert an approved lines_json payload into an ExtractedDocument.

    Usage:
        ex = LinesJsonExtractor(lines_json)
        extracted = ex.to_extracted_document()
        # Feed into pipeline.run_from_lines_json() or similar.
    """

    def __init__(self, lines_json: Iterable):
        self.normalised = normalise_lines_json(lines_json)

    def to_extracted_document(self) -> ExtractedDocument:
        paragraphs: List[str] = []
        tables: List[List[List[str]]] = []
        paragraph_slot_origin: List[Optional[int]] = []
        paragraph_table_origin: List[Optional[int]] = []
        table_paragraph_indices: List[int] = []
        cleaned_dropped: List[dict] = []

        for kind, payload in self.normalised:
            if kind == 'p':
                p = payload if isinstance(payload, dict) else {'slot': 0, 'text': str(payload), 'html': str(payload)}
                slot = p.get('slot', 0)
                text = p.get('text') or _strip_html_to_plain(p.get('html') or '')
                if not text.strip():
                    cleaned_dropped.append({'index': len(paragraphs), 'text': text, 'reason': 'empty'})
                    continue
                paragraphs.append(text)
                paragraph_slot_origin.append(int(slot) if slot is not None else 0)
                paragraph_table_origin.append(None)
            elif kind == 't':
                slot = payload.get('slot', 0)
                raw_rows = payload.get('rows') or []
                rows: List[List[str]] = []
                for row in raw_rows:
                    cells = []
                    for cell in (row or []):
                        if isinstance(cell, dict):
                            cell_text = cell.get('text') or _strip_html_to_plain(cell.get('html') or '')
                        else:
                            cell_text = '' if cell is None else str(cell)
                        cells.append(cell_text)
                    rows.append(cells)
                if rows:
                    tables.append(rows)
                    table_paragraph_indices.append(len(paragraphs))
                # We don't synthesise individual paragraphs from cells; the
                # pipeline uses `tables` directly when populating slot 10
                # and 14 content.

        ex = ExtractedDocument(
            paragraphs=paragraphs,
            tables=tables,
            source_sha256=self._digest(paragraphs, tables),
            source_format='lines_json',
            cleaner_dropped=cleaned_dropped,
            original_indices=list(range(len(paragraphs))),
            table_paragraph_indices=table_paragraph_indices,
            paragraph_table_origin=paragraph_table_origin,
        )
        # Attach the slot-origin list as a sidecar; pipeline code reads it
        # off the ExtractedDocument (attribute set below).
        setattr(ex, 'paragraph_slot_origin', paragraph_slot_origin)
        return ex

    @staticmethod
    def _digest(paragraphs: List[str], tables: List[List[List[str]]]) -> str:
        h = hashlib.sha256()
        for p in paragraphs:
            h.update(p.encode('utf-8'))
            h.update(b'\n')
        for table in tables:
            for row in table:
                for cell in row:
                    h.update((cell or '').encode('utf-8'))
                    h.update(b'|')
                h.update(b'\n')
        return h.hexdigest()
