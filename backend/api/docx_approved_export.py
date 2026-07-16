"""docx_approved_export.py

Builds the final .docx for an approved version by mirroring the
brain-template's body structure as closely as possible WITHOUT modifying
`policy_platform/renderer.py` or the brain file.

Strategy:
  - Read `original_docx_path` (the pipeline-produced docx for this run).
  - Walk its body paragraphs/tables and rewrite the run text from the
    approved `lines_json` so ONLY the values change.
  - Write to `output_path`.

This means:
  - Brain template structure preserved (headings, tables, fonts, layout).
  - Only the body text reflects the approved content.
  - SHA of the brain manifest remains valid.

NOTE: This is a "best-effort overlay" — it preserves the layout
*as closely as possible* by editing the existing docx in place rather
than rebuilding from scratch. For PDFs/docx that follow the brain layout,
this produces an output that is visually indistinguishable from the
brain template.
"""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.shared import RGBColor, Pt


def _set_paragraph_text(p, new_text: str) -> None:
    """Replace all the runs of a paragraph with a single run of `new_text`.

    Preserves the formatting of the first run if present.
    """
    runs = list(p.runs)
    if not runs:
        # No runs - add one
        run = p.add_run(new_text)
        return
    # Keep the first run's style, but replace its text; remove other runs.
    first = runs[0]
    first.text = new_text
    for r in runs[1:]:
        r._element.getparent().remove(r._element)


def build_approved_docx(
    original_docx_path: Path | None,
    approved_lines_json: list,
    output_path: str,
) -> str:
    """Rewrite an existing docx's body text using approved lines_json.

    Args:
        original_docx_path: existing pipeline docx (or None -> empty new docx).
        approved_lines_json: list of [kind, payload]; kind in {'p','t'}.
        output_path: where to write the result.

    Returns: output_path as string.
    """
    output_path = str(output_path)
    if original_docx_path and Path(original_docx_path).exists():
        # Open the original; rewrite body content from approved lines
        doc = Document(str(original_docx_path))
        body_paragraphs = list(doc.paragraphs)
        body_tables = list(doc.tables)
        # Walk through approved_lines and update matching paragraphs/tables
        p_iter = iter(body_paragraphs)
        t_iter = iter(body_tables)
        for line in approved_lines_json or []:
            if not isinstance(line, list) or len(line) != 2:
                continue
            kind, payload = line[0], line[1]
            if kind == 'p':
                try:
                    para = next(p_iter)
                except StopIteration:
                    break
                if isinstance(payload, str):
                    _set_paragraph_text(para, payload)
            elif kind == 't':
                try:
                    tbl = next(t_iter)
                except StopIteration:
                    break
                if not isinstance(payload, list):
                    continue
                for ri, row in enumerate(payload):
                    if ri >= len(tbl.rows):
                        break
                    trow = tbl.rows[ri]
                    for ci, cell_text in enumerate(row or []):
                        if ci >= len(trow.cells):
                            break
                        text = str(cell_text) if cell_text is not None else ''
                        cell_para = trow.cells[ci].paragraphs[0] if trow.cells[ci].paragraphs else trow.cells[ci].add_paragraph()
                        _set_paragraph_text(cell_para, text)
        doc.save(output_path)
    else:
        # No original docx available - construct a minimal one
        doc = Document()
        for line in approved_lines_json or []:
            if not isinstance(line, list) or len(line) != 2:
                continue
            kind, payload = line[0], line[1]
            if kind == 'p':
                doc.add_paragraph(str(payload) if isinstance(payload, str) else '')
            elif kind == 't' and isinstance(payload, list):
                rows = payload
                if not rows:
                    continue
                cols = max(len(r) for r in rows) if rows else 0
                if cols <= 0:
                    continue
                tbl = doc.add_table(rows=len(rows), cols=cols)
                tbl.style = 'Table Grid'
                for ri, row in enumerate(rows):
                    for ci in range(cols):
                        cell_text = str(row[ci]) if ci < len(row) and row[ci] is not None else ''
                        tbl.cell(ri, ci).text = cell_text
        doc.save(output_path)
    return output_path
