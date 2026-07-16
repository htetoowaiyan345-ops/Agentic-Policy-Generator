from __future__ import annotations

import hashlib
from pathlib import Path

import fitz  # PyMuPDF

from .base import ExtractedDocument


def _try_pymupdf(path: Path) -> ExtractedDocument | None:
    try:
        doc = fitz.open(str(path))
    except Exception:
        return None
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    paragraphs: list[str] = []
    tables: list[list[list[str]]] = []
    table_paragraph_indices: list[int] = []
    try:
        for page in doc:
            text = page.get_text("text")
            for line in text.split("\n"):
                paragraphs.append(line)
            tabs = page.find_tables()
            for t in tabs:
                try:
                    df = t.extract()
                except Exception:
                    continue
                rows: list[list[str]] = []
                for row in df or []:
                    cells = [("" if c is None else str(c)) for c in row]
                    rows.append(cells)
                if rows:
                    tables.append(rows)
                    # Record the paragraph index AFTER which this table
                    # appeared (end of current page's text).
                    table_paragraph_indices.append(len(paragraphs))
    finally:
        doc.close()
    return ExtractedDocument(
        paragraphs=paragraphs,
        tables=tables,
        source_sha256=sha,
        source_format="pdf",
        table_paragraph_indices=table_paragraph_indices,
    )


def _try_pdfplumber(path: Path) -> ExtractedDocument:
    import pdfplumber
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    paragraphs: list[str] = []
    tables: list[list[list[str]]] = []
    table_paragraph_indices: list[int] = []
    paragraph_table_origin: list[int | None] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            # Use x/y tolerance settings that preserve visual row
            # layout (Label and Value on the same line) while
            # avoiding column-wrap noise. With default tolerances,
            # pdfplumber may break a single visual row into multiple
            # lines. x_tolerance=3 groups characters within ~3pt
            # horizontally; y_tolerance=3 groups characters within
            # ~3pt vertically. This gives a 1-line-per-row output
            # that matches what the user sees in the PDF.
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            # Detect data tables (3+ columns) on this page. Lines that
            # fall inside a data table's bbox must NOT be emitted into
            # the paragraph stream — they're already in the `tables`
            # list, and emitting them again would cause the same
            # content to appear in slot 9/10/14 via table_passthrough
            # AND in some other slot via RAG-retrieved prose.
            #
            # Only filter 3+ column data tables. 2-column label-row
            # tables (the slot-1 header) still emit cells as paragraphs
            # so the field_parser + analyzer can detect Label/Value
            # pairs.
            try:
                page_tables_raw = page.find_tables() or []
            except Exception:
                page_tables_raw = []
            data_table_bboxes = []
            label_row_bboxes = []
            for t in page_tables_raw:
                rows = t.extract() or []
                if not rows:
                    continue
                max_cols = max((len(r) for r in rows if r), default=0)
                bbox = getattr(t, "bbox", None)
                if bbox is None:
                    continue
                if max_cols >= 3:
                    # 3+ column data tables (slot 9/10/14 candidates):
                    # filter ALL of their cell text from paragraph stream
                    # so content doesn't duplicate (table + prose).
                    data_table_bboxes.append(bbox)
                elif max_cols == 2 and len(rows) >= 3:
                    # 2-column tables with 3+ rows: check if first
                    # column contains label-row keywords (Type, Policy
                    # Title, Effective Date, etc.). If so, it's the
                    # slot-1 schema label-row table and should also be
                    # filtered from the paragraph stream — its cells
                    # are already extracted into slot 1 by field_parser.
                    # Phase K.1: this prevents the slot-1 schema from
                    # leaking into slot 9/10 prose via keyword match.
                    try:
                        from ..rag.table_routing import _LABEL_ROW_KEYWORDS
                        first_col = [
                            str(r[0]).strip().lower().rstrip(":")
                            for r in rows[1:] if r and r[0]
                        ]
                        hits = sum(
                            1 for c in first_col if c in _LABEL_ROW_KEYWORDS
                        )
                        if hits >= 2:
                            label_row_bboxes.append(bbox)
                    except Exception:
                        pass
            for line in text.split("\n"):
                # Look up the line's bbox to decide whether it sits
                # inside a data table.
                line_bbox = None
                try:
                    words = page.search(
                        line, x_tolerance=3, y_tolerance=3
                    ) or []
                    if words:
                        xs0 = min(w["x0"] for w in words)
                        ys0 = min(w["top"] for w in words)
                        xs1 = max(w["x1"] for w in words)
                        ys1 = max(w["bottom"] for w in words)
                        line_bbox = (xs0, ys0, xs1, ys1)
                except Exception:
                    line_bbox = None
                inside_data_table = False
                if line_bbox is not None:
                    for tbbox in data_table_bboxes:
                        if (
                            line_bbox[0] >= tbbox[0] - 1
                            and line_bbox[1] >= tbbox[1] - 1
                            and line_bbox[2] <= tbbox[2] + 1
                            and line_bbox[3] <= tbbox[3] + 1
                        ):
                            inside_data_table = True
                            break
                    if not inside_data_table:
                        for tbbox in label_row_bboxes:
                            if (
                                line_bbox[0] >= tbbox[0] - 1
                                and line_bbox[1] >= tbbox[1] - 1
                                and line_bbox[2] <= tbbox[2] + 1
                                and line_bbox[3] <= tbbox[3] + 1
                            ):
                                inside_data_table = True
                                break
                if inside_data_table:
                    # Skip — already captured in `tables`.
                    continue
                paragraphs.append(line)
                paragraph_table_origin.append(None)
            try:
                page_tables = page.extract_tables() or []
            except Exception:
                page_tables = []
            for t in page_tables:
                rows = []
                for row in t:
                    cells = [("" if c is None else str(c)) for c in row]
                    rows.append(cells)
                if rows:
                    tables.append(rows)
                    # Record the paragraph index AFTER which this table
                    # appeared (end of current page's text).
                    table_paragraph_indices.append(len(paragraphs))
    return ExtractedDocument(
        paragraphs=paragraphs,
        tables=tables,
        source_sha256=sha,
        source_format="pdf",
        table_paragraph_indices=table_paragraph_indices,
        paragraph_table_origin=paragraph_table_origin,
    )


def _has_truncated_lines(paragraphs: list[str]) -> bool:
    """Heuristic: a line that ends mid-word (alphabetic char followed
    by no terminator, no whitespace) suggests the PDF text extractor
    truncated at a column boundary.

    For 100% format parity with DOCX (which has complete text), we
    prefer pdfplumber when PyMuPDF produces such lines.
    """
    if not paragraphs:
        return False
    truncated = 0
    for line in paragraphs:
        s = line.rstrip()
        if not s:
            continue
        # A line ending in a lowercase letter with no terminator
        # (`[.!?;:]`) is suspicious — likely a column-wrap truncation.
        # Skip short labels (single-word) and Brain labels.
        if len(s) < 30:
            continue
        if s[-1].isalpha() and s[-1].islower():
            # Check it doesn't end with a Brain label (e.g., "by: Group").
            if not (s.endswith(":") or s.endswith(";") or s.endswith(",")):
                # Additional check: the line should have a sentence
                # structure (spaces, multiple words). If it's just
                # a label-like fragment, skip.
                if " " in s:
                    truncated += 1
    return truncated >= 2  # 2+ suspicious lines → likely column-wrap


def extract(path: Path) -> ExtractedDocument:
    """Extract paragraphs and tables from a PDF.

    Strategy for 100% format parity with DOCX:
      1. Use pdfplumber for paragraphs — its x/y tolerance settings
         preserve the visual row layout (Label and Value on the same
         line), which matches what the user sees in the PDF.
      2. Use PyMuPDF for tables — it has more reliable table detection
         via `find_tables()`.

    PyMuPDF's `get_text("text")` tends to split visual rows into
    individual lines (e.g., "Supersedes Version 0.9 dated..." becomes
    "Supersedes\nVersion 0.9 dated..."), which loses the label-value
    association. pdfplumber with x_tolerance=3 groups characters on
    the same line correctly.

    For backward compatibility, we also keep a PyMuPDF-first fallback
    when pdfplumber produces no content.
    """
    pdfplumber_doc = _try_pdfplumber(path)
    if pdfplumber_doc.paragraphs or pdfplumber_doc.tables:
        # Augment tables from PyMuPDF if pdfplumber missed any.
        pymupdf_doc = _try_pymupdf(path)
        if pymupdf_doc is not None and pymupdf_doc.tables:
            if len(pymupdf_doc.tables) > len(pdfplumber_doc.tables):
                pdfplumber_doc.tables = pymupdf_doc.tables
        return pdfplumber_doc
    pymupdf_doc = _try_pymupdf(path)
    if pymupdf_doc is None or not (pymupdf_doc.paragraphs or pymupdf_doc.tables):
        raise ValueError(
            "PDF could not be parsed. It may be a scanned image without an "
            "OCR layer. v1 does not run OCR. Please provide a text-based PDF."
        )
    return pymupdf_doc
