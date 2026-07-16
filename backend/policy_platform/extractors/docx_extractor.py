from __future__ import annotations

import hashlib

from docx import Document
from docx.oxml.ns import qn

from .base import ExtractedDocument


def _para_text(p_elem) -> str:
    """Concatenate all `<w:t>` runs in a paragraph element.

    Skips `<w:tab/>` and `<w:br/>` (these are inter-run formatting
    controls, not text content).

    Returns the joined text (no leading/trailing strip).
    """
    parts: list[str] = []
    for t in p_elem.iter(qn("w:t")):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _cell_text(tc_elem) -> str:
    """Concatenate all paragraphs in a table cell, joined by newlines.

    Mirrors how PDF text extraction (page.get_text("text")) flattens
    cell content into the text stream — each cell becomes a single
    newline-separated block.
    """
    lines: list[str] = []
    for p in tc_elem.iter(qn("w:p")):
        text = _para_text(p).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _is_label_row_table(table_rows: list[list[str]]) -> bool:
    """Heuristic: a 2-column table where column 0 is short and looks
    like a label (no terminators, <= 60 chars) is a "label-row" table.

    These tables are typically the policy header (Policy Type,
    Policy Number, Brief Description, etc.). When emitted into
    the paragraph stream, they provide the `Label | Value` alternation
    that the analyzer + field_parser need to populate slots 1, 2, 3,
    4, 11.

    IMPORTANT: to avoid misclassifying data tables (e.g. an exclusions
    table with columns "Category | Value"), require that the table has
    AT LEAST ONE row whose column 0 matches a known Brain label-row
    keyword (Type, Policy Title, Effective Date, etc.). This makes the
    detection much more robust: a true label-row table will have
    multiple label keywords; a random 2-column data table will not.

    PDF extraction does this implicitly via `page.get_text("text")`
    which flattens cell text into the line stream. We do the same
    here to maintain 100% format parity.
    """
    if not table_rows or len(table_rows[0]) != 2:
        return False
    # Brain label-row keywords (subset of LABEL_ROW_KEYWORDS in
    # framework.brain_fields). At least one row's column 0 must
    # match a known label for the table to be classified as label-row.
    labelish_count = 0
    non_empty_rows = 0
    has_known_label = False
    for row in table_rows:
        if len(row) < 2:
            return False
        label = (row[0] or "").strip().lower().rstrip(":")
        value = (row[1] or "").strip()
        if not label and not value:
            continue
        non_empty_rows += 1
        # Heuristic: label is short (<=60 chars), has no sentence
        # terminator (`.!?;:`), and is not a long prose clause.
        if 0 < len(label) <= 60 and not any(c in label for c in ".!?;:"):
            labelish_count += 1
        # Check if this label matches a known Brain label-row keyword.
        if label in _LABEL_ROW_KEYWORDS:
            has_known_label = True
    # Require at least one row with a known label keyword AND most
    # rows be labelish. Random data tables won't have any known
    # label keywords.
    return has_known_label and non_empty_rows >= 2 and labelish_count >= max(1, non_empty_rows // 2)


# Known Brain label-row keywords (subset of BRAIN_HEADER_FIELDS,
# BRAIN_APPROVAL_FIELDS, etc.). A 2-column table is a label-row
# table only if at least one of its column-0 values is in this set.
_LABEL_ROW_KEYWORDS: set[str] = {
    "type",
    "policy title",
    "policy number",
    "applicable sector",
    "applicable sector(s)",
    "functional area",
    "functional area(s)",
    "brief description",
    "effective date",
    "effective date/period",
    "approved by",
    "prepared by",
    "responsible function",
    "responsible function(s)",
    "responsible function officer",
    "responsible function officer(s)",
    "supersedes",
    "last reviewed",
    "last reviewed/updated",
    "applies to",
    "reason for policy",
    "policy review note",
}


def extract(path) -> ExtractedDocument:
    """Extract paragraphs AND tables from a .docx file in document order.

    The output paragraph stream is the union of:
      1. Top-level `<w:p>` elements (in document order).
      2. **Label and value** pairs from 2-column "label-row" tables
         (the policy header table). These are emitted as alternating
         `Label` / `Value` lines so the field_parser + analyzer see
         the same alternating pattern that PDF extraction produces
         via `page.get_text("text")` flattening.

    For 3+ column data tables (Award Structure, Exclusions, HISTORY),
    the cell text is NOT emitted into the paragraph stream. The cells
    are already in the `tables` list, and emitting them again as
    paragraphs causes duplication (the same content shows up in both
    slot 9/10/14 via table_passthrough AND via RAG/heading-anchor).

    This guarantees **format parity** with PDF for the policy header
    table. The 3+ column data tables are handled by table_passthrough
    (slots 9, 10, 14) and their cells are accessed via `tables[i]` not
    via paragraph streaming.

    The `tables` list is still returned (unchanged) so downstream
    consumers (slot 10/14 table routing) can find Award and HISTORY
    tables.
    """
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    doc = Document(str(path))

    paragraphs: list[str] = []
    tables: list[list[list[str]]] = []
    table_paragraph_indices: list[int] = []
    paragraph_table_origin: list[int | None] = []

    # Walk the body element-by-element in document order. This is
    # critical: the body may interleave paragraphs and tables, and
    # the analyzer needs the original order.
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            # Top-level paragraph.
            text = _para_text(child)
            paragraphs.append(text)
            paragraph_table_origin.append(None)
        elif tag == qn("w:tbl"):
            # Table: build the 2D matrix, and decide whether to emit
            # cells into the paragraph stream based on whether the
            # table is a label-row table or a data table.
            rows: list[list[str]] = []
            for tr in child.iter(qn("w:tr")):
                row_cells: list[str] = []
                for tc in tr.iter(qn("w:tc")):
                    row_cells.append(_cell_text(tc))
                rows.append(row_cells)
            table_index = len(tables)
            tables.append(rows)
            # Record the paragraph index of the LAST PARAGRAPH BEFORE
            # this table. The analyzer uses this to map the table to
            # the correct Brain slot based on the closest preceding
            # section heading. `len(paragraphs) - 1` is the index of
            # the last paragraph that was just added; if the table is
            # at the very start of the document (no preceding
            # paragraph), this is -1 and the caller will fall back to
            # other strategies.
            table_paragraph_indices.append(len(paragraphs) - 1)
            # Emit cells into the paragraph stream ONLY for label-row
            # tables (the slot-1 header table). Data tables (3+ columns
            # like Award Structure, Exclusions, HISTORY) keep their
            # cells in the `tables` list only — emitting them as
            # paragraphs would cause content duplication.
            if _is_label_row_table(rows):
                # 2-column label-row table: emit label and value as
                # alternating lines (matching PDF's flattened text
                # stream). These get routed to slot 1/3/11 via the
                # label-row detection in the analyzer.
                for row in rows:
                    label = (row[0] or "").strip()
                    value = (row[1] or "").strip()
                    if label:
                        paragraphs.append(label)
                        paragraph_table_origin.append(table_index)
                    if value:
                        paragraphs.append(value)
                        paragraph_table_origin.append(table_index)
            # For data tables (non-label-row), do NOT emit cells into
            # the paragraph stream. They're tracked in `tables[i]`
            # and `paragraph_table_origin` so downstream code can
            # distinguish table-cell text from real prose if needed.

    return ExtractedDocument(
        paragraphs=paragraphs,
        tables=tables,
        source_sha256=sha,
        source_format="docx",
        table_paragraph_indices=table_paragraph_indices,
        paragraph_table_origin=paragraph_table_origin,
    )
