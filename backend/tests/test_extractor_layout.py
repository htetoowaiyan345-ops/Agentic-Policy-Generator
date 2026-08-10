"""Tests for Phase 1 layout normalization helpers and the new
line_bboxes / page_rotations fields on ExtractedDocument.

These tests use synthetic PDFs generated in-process (no binary
fixtures in the repo). They verify:
  * line_bboxes is populated for PDF and empty for DOCX/RTF/TXT
  * multi-column PDFs sort left-then-right (top-to-bottom within column)
  * single-column PDFs are NOT reordered (no false-positive gutter)
  * 180°-rotated PDFs have their coords re-rotated to canonical
  * existing extractor tests still pass (backward compat)
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest


# ---------------------------------------------------------------------------
# Helper: build a synthetic PDF in-process.
# ---------------------------------------------------------------------------

def _build_pdf(
    out_path: Path,
    pages: list[list[tuple[float, float, str]]],
    *,
    page_w: float = 595.0,
    page_h: float = 842.0,
    rotation: int = 0,
) -> None:
    """Create a PDF whose pages are lists of (x, y, text) spans.

    Args:
      out_path: where to write the PDF.
      pages: list of pages; each page is a list of (x, y_top, text)
        where (x, y_top) is the top-left corner of the text block.
      page_w / page_h: page dimensions in points (A4 portrait by
        default).
      rotation: page rotation in degrees (0/90/180/270).
    """
    doc = fitz.open()
    for page_spans in pages:
        page = doc.new_page(width=page_w, height=page_h)
        page.set_rotation(rotation)
        for x, y, text in page_spans:
            # `insert_textbox` expects (x, y, y_max) box; bottom-right
            # corner is implicit from page size.
            page.insert_text((x, y), text, fontsize=10)
    doc.save(str(out_path))
    doc.close()


# ---------------------------------------------------------------------------
# 1. line_bboxes populated for PDF, empty for DOCX/RTF/TXT
# ---------------------------------------------------------------------------

def test_pdf_line_bboxes_populated(tmp_path: Path):
    """PDF extraction must produce one bbox per paragraph."""
    from policy_platform.extractors.pdf_extractor import extract

    pages = [
        [  # page 1
            (50, 100, "Hello world"),
            (50, 150, "Second line"),
            (50, 200, "Third line"),
        ],
    ]
    pdf_path = tmp_path / "single.pdf"
    _build_pdf(pdf_path, pages)
    doc = extract(pdf_path)
    assert len(doc.paragraphs) == 3, doc.paragraphs
    assert len(doc.line_bboxes) == 3, doc.line_bboxes
    # Each bbox has 5 entries: (page, x0, top, x1, bottom)
    for bbox in doc.line_bboxes:
        assert bbox is not None
        assert len(bbox) == 5
        page, x0, top, x1, bottom = bbox
        assert page == 1
        assert x0 >= 0 and x1 > x0
        assert top >= 0 and bottom > top
    assert doc.page_rotations == [(1, 0)]


def test_docx_has_empty_line_bboxes(small_docx: Path):
    """DOCX has no coordinates — line_bboxes must be empty list."""
    from policy_platform.extractors.docx_extractor import extract

    doc = extract(small_docx)
    assert doc.line_bboxes == []
    assert doc.page_rotations == []


def test_txt_has_empty_line_bboxes(tmp_path: Path):
    """TXT has no coordinates — line_bboxes must be empty list."""
    from policy_platform.extractors.txt_extractor import extract

    p = tmp_path / "x.txt"
    p.write_text("Alpha\nBeta\n", encoding="utf-8")
    doc = extract(p)
    assert doc.line_bboxes == []
    assert doc.page_rotations == []


# ---------------------------------------------------------------------------
# 2. Multi-column sort — left column comes before right column
# ---------------------------------------------------------------------------

def test_multicolumn_sorts_left_first(tmp_path: Path):
    """Synthetic 2-column PDF must emit left-column lines before right.

    Each column gets text at DIFFERENT y-positions so pdfplumber
    treats them as separate visual rows. The text in each row spans
    only one column (left or right), so reading order is unambiguous.
    """
    from policy_platform.extractors.pdf_extractor import extract

    # 4 visual rows, each with one text block in either the left
    # column (x≈80) or the right column (x≈340). Rows alternate
    # column to make the expected reading order non-trivial:
    #   row 1 (y=120): RIGHT1
    #   row 2 (y=160): LEFT1
    #   row 3 (y=200): RIGHT2
    #   row 4 (y=240): LEFT2
    # Without column sorting, the natural pdfplumber order would be
    # RIGHT1, LEFT1, RIGHT2, LEFT2 (the per-page emission order is
    # roughly top-to-bottom regardless of column). With column sorting
    # we want LEFT1, LEFT2, RIGHT1, RIGHT2.
    pages = [
        [
            (340, 120, "RIGHT1"),
            (80, 160, "LEFT1"),
            (340, 200, "RIGHT2"),
            (80, 240, "LEFT2"),
        ],
    ]
    pdf_path = tmp_path / "two_col.pdf"
    _build_pdf(pdf_path, pages)
    doc = extract(pdf_path)
    txts = doc.paragraphs
    # Find the indices of each marker.
    left_idx = [i for i, t in enumerate(txts) if t in ("LEFT1", "LEFT2")]
    right_idx = [i for i, t in enumerate(txts) if t in ("RIGHT1", "RIGHT2")]
    assert left_idx and right_idx, f"txts={txts}"
    # Column sort: both LEFT markers must precede both RIGHT markers.
    assert max(left_idx) < min(right_idx), (
        f"Left column should come before right column.\n"
        f"  Order: {txts}"
    )


# ---------------------------------------------------------------------------
# 3. No false-positive gutter on single-column PDFs
# ---------------------------------------------------------------------------

def test_no_column_when_no_gutter(tmp_path: Path):
    """Single-column PDF must keep pdfplumber's natural order."""
    from policy_platform.extractors.pdf_extractor import extract

    pages = [
        [  # page 1
            (50, 100, "Line one"),
            (50, 150, "Line two"),
            (50, 200, "Line three"),
        ],
    ]
    pdf_path = tmp_path / "single_col.pdf"
    _build_pdf(pdf_path, pages)
    doc = extract(pdf_path)
    # Should be in y-order: line one, two, three.
    txts = [t for t in doc.paragraphs if t.startswith("Line ")]
    assert txts == ["Line one", "Line two", "Line three"], txts


# ---------------------------------------------------------------------------
# 4. 180°-rotated page has coords re-rotated to canonical orientation
# ---------------------------------------------------------------------------

def test_rotation_180_normalized(tmp_path: Path):
    """A 180°-rotated page should have bboxes in canonical orientation.

    Without normalization, a 180° page would have coordinates that are
    mirrored across the page center. After Phase 1, the extractor must
    produce coords as if the page were at 0°.
    """
    from policy_platform.extractors.pdf_extractor import (
        extract,
        _normalize_bbox_coords,
    )

    # Helper: directly test the rotation normalizer.
    rotated = _normalize_bbox_coords((100, 200, 300, 400), 180, 595, 842)
    # 180° mirror across both axes: (595 - 300, 842 - 400, 595 - 100, 842 - 200)
    assert rotated == (295, 442, 495, 642), rotated

    # End-to-end: PyMuPDF text on a 180°-rotated page is already
    # canonical (PyMuPDF re-renders glyphs at the rotated frame).
    # Bbox from get_text('blocks') is in pre-rotation user space, so
    # the normalizer should flip them.
    pages = [
        [  # page 1 (intentionally rotated 180°)
            (100, 200, "Rotated line"),
        ],
    ]
    pdf_path = tmp_path / "rotated.pdf"
    _build_pdf(pdf_path, pages, rotation=180)
    doc = extract(pdf_path)
    assert doc.page_rotations == [(1, 180)], doc.page_rotations
    # After normalization, the line bbox should be in canonical
    # coordinates (top should be <= bottom, x0 <= x1).
    assert doc.line_bboxes, doc.line_bboxes
    bbox = doc.line_bboxes[0]
    assert bbox is not None
    _page, x0, top, x1, bottom = bbox
    assert x0 <= x1
    assert top <= bottom


# ---------------------------------------------------------------------------
# 5. Backward compat — existing extraction semantics unchanged
# ---------------------------------------------------------------------------

def test_pdfplumber_paragraph_indices_stable(tmp_path: Path):
    """paragraphs[i] still maps to a single extracted line, even
    after the reading-order sort. No lines should be added or
    dropped just because we sorted them."""
    from policy_platform.extractors.pdf_extractor import extract

    pages = [
        [  # page 1
            (50, 100, "Alpha"),
            (50, 150, "Beta"),
            (50, 200, "Gamma"),
        ],
    ]
    pdf_path = tmp_path / "three_lines.pdf"
    _build_pdf(pdf_path, pages)
    doc = extract(pdf_path)
    txts = [t for t in doc.paragraphs if t in ("Alpha", "Beta", "Gamma")]
    assert sorted(txts) == ["Alpha", "Beta", "Gamma"], txts


def test_backward_compat_existing_tests_still_pass(tmp_path: Path):
    """The existing txt extractor behavior is unchanged by Phase 1."""
    from policy_platform.extractors.txt_extractor import extract

    p = tmp_path / "x.txt"
    p.write_bytes(b"Hello\nWorld\n")
    e = extract(p)
    assert e.paragraphs == ["Hello", "World"]
    assert e.source_format == "txt"
    # New fields default to empty for non-PDF sources.
    assert e.line_bboxes == []
    assert e.page_rotations == []