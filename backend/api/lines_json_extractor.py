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
from policy_platform.framework.brain_slot_map import BRAIN_SLOT_RANGES


HTML_TAG_RE = re.compile(r'<[^>]+>')


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
