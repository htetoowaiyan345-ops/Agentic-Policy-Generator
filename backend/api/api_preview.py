"""api_preview.py

Reads an actual generated .docx file and returns its paragraphs and
tables in the EXACT order they appear in the docx. The web preview
then renders this list line-by-line, with no slot titles, no labels,
no grouping — just the docx content as it is.

This guarantees the preview matches the .docx output 1:1.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path


def _extract_paragraphs_from_docx(docx_path):
    """Read the .docx and return a list of (kind, payload) where:
       kind='p' and payload=str  -> a normal paragraph
       kind='t' and payload=list[list[str]] -> a table (rows of cells)
    Empty paragraphs are skipped.
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
    """
    items = _extract_paragraphs_from_docx(docx_path)
    return {'lines': items}
