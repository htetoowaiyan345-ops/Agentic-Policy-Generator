"""api_preview.py

Reads an actual generated .docx file and returns its paragraphs and
tables in the EXACT order they appear in the docx. The web preview
then renders this list line-by-line, with no slot titles, no labels,
no grouping — just the docx content as it is.

This guarantees the preview matches the .docx output 1:1.

PHASE 2 NOTE:
   The TipTap-based Word-style editor emits rich payloads shaped as
   `['p', {slot, text, html}]` and `['t', {slot, rows: [[{text, html}, ...]]}]`.
   To stay backwards-compatible with runs that were rendered by the
   previous editor (which stored paragraphs as plain `str` and table
   rows as `list[list[str]]`), we normalise every `line` here before
   returning to the front-end. The editor's `normalisePreviewLine()`
   on the Svelte side mirrors this exactly.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from policy_platform.extract_myanmar.debug_logging import log_checkpoint


def _html_escape(s: str) -> str:
    """Minimal HTML escape — used when we synthesise rich payloads from
    plain strings (e.g. legacy `['p', str]` lines read from a .docx)."""
    if s is None:
        return ''
    return (
        s.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _normalise_paragraph_payload(payload):
    """Accept either:
       - legacy: `str` (the plain text)
       - rich:   `dict` with keys `slot`, `text`, `html`, optional `footnotes`
       and always return the rich dict shape:
       `{'slot': int, 'text': str, 'html': str, 'footnotes': list}`
    """
    if isinstance(payload, dict):
        text = str(payload.get('text') or '')
        slot = int(payload.get('slot', 0) or 0)
        html = payload.get('html') or ''
        footnotes = payload.get('footnotes') or []
        if not html:
            html = _html_escape(text)
        return {
            'slot': slot,
            'text': text,
            'html': html,
            'footnotes': footnotes,
        }
    # Legacy: plain string
    text = '' if payload is None else str(payload)
    return {
        'slot': 0,
        'text': text,
        'html': _html_escape(text),
        'footnotes': [],
    }


def _normalise_table_payload(payload):
    """Accept either:
       - legacy: `list[list[str]]`  (rows of plain-text cells)
       - rich:   `dict` with keys `slot`, `rows` (rows of `{text, html}` cells)
       and always return the rich dict shape.
    """
    if isinstance(payload, dict) and 'rows' in payload:
        slot = int(payload.get('slot', 0) or 0)
        raw_rows = payload.get('rows') or []
        normalised_rows = []
        for row in raw_rows:
            new_row = []
            for cell in (row or []):
                if isinstance(cell, dict):
                    text = str(cell.get('text') or '')
                    html = cell.get('html') or ''
                    if not html:
                        html = _html_escape(text)
                    new_row.append({'text': text, 'html': html})
                else:
                    text = '' if cell is None else str(cell)
                    new_row.append({'text': text, 'html': _html_escape(text)})
            normalised_rows.append(new_row)
        return {'slot': slot, 'rows': normalised_rows}
    # Legacy: list[list[str]]
    if isinstance(payload, list):
        rows = []
        for row in payload:
            if not isinstance(row, list):
                rows.append([])
                continue
            new_row = []
            for cell in row:
                text = '' if cell is None else str(cell)
                new_row.append({'text': text, 'html': _html_escape(text)})
            rows.append(new_row)
        return {'slot': 0, 'rows': rows}
    return {'slot': 0, 'rows': []}


def _extract_paragraphs_from_docx(docx_path):
    """Read the .docx and return a list of (kind, payload) where:
       kind='p' and payload=str          -> legacy paragraph
       kind='t' and payload=list[list[str]] -> legacy table (rows of cells)
    Empty paragraphs are skipped. Normalisation to rich payloads happens
    downstream in `build_preview_from_docx`.
    """
    p = Path(docx_path)
    if not p.exists():
        return []
    with zipfile.ZipFile(p) as z:
        with z.open('word/document.xml') as f:
            xml = f.read().decode('utf-8')
    xml_clean = re.sub(r'xmlns[^=]*="[^"]+"', '', xml)
    xml_clean = re.sub(r'<(/?)w:', r'<\1', xml_clean)
    xml_clean = re.sub(r'<(/?)r:', r'<\1', xml_clean)

    out = []
    for m in re.finditer(r'<(p|tbl)\b[^>]*>(.*?)</\1>', xml_clean, flags=re.S):
        tag, body = m.group(1), m.group(2)
        if tag == 'p':
            text = re.sub(r'<[^>]+>', '', body)
            text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').strip()
            if text:
                out.append(('p', text))
        else:
            rows = []
            for tr in re.finditer(r'<tr\b[^>]*>(.*?)</tr>', body, flags=re.S):
                cells = []
                for tc in re.finditer(r'<tc\b[^>]*>(.*?)</tc>', tr.group(1), flags=re.S):
                    cell_text = re.sub(r'<[^>]+>', '', tc.group(1))
                    cell_text = cell_text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').strip()
                    cells.append(cell_text)
                if cells:
                    rows.append(cells)
            if rows:
                out.append(('t', rows))
    return out


def build_preview_from_docx(docx_path):
    """Read the .docx and return a flat ordered list of lines + tables
    in the exact order they appear. No slot titles, no labels, no
    grouping. The web preview renders this list line-by-line.

    Every line is normalised to the rich payload shape:
       ('p', {'slot': int, 'text': str, 'html': str, 'footnotes': [...]})
       ('t', {'slot': int, 'rows': [[{'text': str, 'html': str}, ...], ...]})
    """
    items = _extract_paragraphs_from_docx(docx_path)
    normalised: list = []
    for kind, payload in items:
        if kind == 'p':
            normalised.append(['p', _normalise_paragraph_payload(payload)])
        elif kind == 't':
            normalised.append(['t', _normalise_table_payload(payload)])

    preview_lines = []
    for kind, payload in normalised:
        if kind == 'p' and isinstance(payload, dict):
            preview_lines.append(payload.get('text', ''))
        elif kind == 't' and isinstance(payload, dict):
            for row in payload.get('rows', []):
                for cell in row:
                    if isinstance(cell, dict):
                        preview_lines.append(cell.get('text', ''))
    log_checkpoint(
        "before_ui_serve",
        "\n\n".join(preview_lines),
        run_id=Path(docx_path).stem,
    )

    return {'lines': normalised}
