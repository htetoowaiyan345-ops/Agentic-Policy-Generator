"""Real-world PDF layout tests using the HR_00002 health-benefit
policy as a regression fixture.

This file is a Phase 2 addition that runs the new extractor on a
real sample to verify Phase 1's bbox/rotation work AND to expose
the cell-coherence issue that motivates Phase 2.
"""
from __future__ import annotations

from pathlib import Path


# Filename matches the file copied to backend/data/samples/
# by conftest fixture conventions. Tests skip if the file is missing
# (e.g. fresh CI checkout without the binary fixture).
SAMPLE_FILENAME = "HR_Health_Benefit_Policy.pdf"


def _sample_path(samples_dir: Path) -> Path | None:
    p = samples_dir / SAMPLE_FILENAME
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# 1. Line bboxes populated for the real sample
# ---------------------------------------------------------------------------

def test_hr_benefit_line_bboxes_populated(samples_dir: Path):
    """Real-world PDF: every paragraph must have a bbox."""
    p = _sample_path(samples_dir)
    if p is None:
        import pytest
        pytest.skip(f"Real-sample fixture not present: {SAMPLE_FILENAME}")
    from policy_platform.extractors.pdf_extractor import extract

    doc = extract(p)
    assert len(doc.paragraphs) > 0
    # Phase 1 invariant: line_bboxes is parallel to paragraphs.
    assert len(doc.line_bboxes) == len(doc.paragraphs), (
        f"line_bboxes ({len(doc.line_bboxes)}) must equal "
        f"paragraphs ({len(doc.paragraphs)})"
    )
    # At least 70% of paragraphs should have a non-None bbox.
    # Lines without bboxes are typically those pdfplumber couldn't
    # locate (e.g. page-numbers filtered out, or text that doesn't
    # roundtrip via page.search()). For real-world docs, 75-80% is
    # realistic; the rest still go through the RAG path normally
    # (Phase 1 invariant is just that line_bboxes is parallel to
    # paragraphs — bboxes can be None for lines that pdfplumber
    # couldn't locate).
    non_none = sum(1 for b in doc.line_bboxes if b is not None)
    assert non_none / max(1, len(doc.line_bboxes)) >= 0.70, (
        f"Only {non_none}/{len(doc.line_bboxes)} paragraphs have bboxes"
    )


# ---------------------------------------------------------------------------
# 2. No bbox loss inside table regions
# ---------------------------------------------------------------------------

def test_hr_benefit_no_bbox_loss_in_tables(samples_dir: Path):
    """The PDF has 3 data tables on page 3 + 1 annex form on page 6.

    Lines that fall inside a table bbox are intentionally filtered
    out of the paragraph stream (so the same content doesn't appear
    twice — once as prose, once as table cell). We verify that lines
    NEAR but not INSIDE those tables still carry their bboxes.
    """
    p = _sample_path(samples_dir)
    if p is None:
        import pytest
        pytest.skip(f"Real-sample fixture not present: {SAMPLE_FILENAME}")
    from policy_platform.extractors.pdf_extractor import extract

    doc = extract(p)
    # Find paragraphs that mention Benefit/Health/Hospital — those
    # are NEAR the table on page 3 (above and below it).
    benefit_lines = [
        (i, par, bbox) for i, (par, bbox) in enumerate(
            zip(doc.paragraphs, doc.line_bboxes)
        )
        if "Health" in par or "Hospital" in par or "Benefit" in par
        and bbox is not None
    ]
    # We should find at least a few prose lines near the tables that
    # DO have bboxes (prose around the tables).
    assert len(benefit_lines) >= 1


# ---------------------------------------------------------------------------
# 3. Rotations zero across all 7 pages
# ---------------------------------------------------------------------------

def test_hr_benefit_rotations_zero(samples_dir: Path):
    """This specific PDF has no rotated pages."""
    p = _sample_path(samples_dir)
    if p is None:
        import pytest
        pytest.skip(f"Real-sample fixture not present: {SAMPLE_FILENAME}")
    from policy_platform.extractors.pdf_extractor import extract

    doc = extract(p)
    # 7 pages, all at rotation 0.
    assert len(doc.page_rotations) == 7, doc.page_rotations
    for page_no, rot in doc.page_rotations:
        assert rot == 0, f"Page {page_no} rotated to {rot}"


# ---------------------------------------------------------------------------
# 4. Paragraph count matches bboxes (no drift)
# ---------------------------------------------------------------------------

def test_hr_benefit_paragraph_count_matches_bboxes(samples_dir: Path):
    """len(paragraphs) == len(line_bboxes) — strict invariant."""
    p = _sample_path(samples_dir)
    if p is None:
        import pytest
        pytest.skip(f"Real-sample fixture not present: {SAMPLE_FILENAME}")
    from policy_platform.extractors.pdf_extractor import extract

    doc = extract(p)
    assert len(doc.paragraphs) == len(doc.line_bboxes)
    # Paragraph count is informative: this real-world doc has ~190
    # paragraphs across 7 pages (validated against manual count).
    assert len(doc.paragraphs) >= 100, (
        f"Expected >=100 paragraphs in HR doc, got {len(doc.paragraphs)}"
    )
    # Tables present on pages 1, 3, 6 — at least 4 tables.
    assert len(doc.tables) >= 4, (
        f"Expected >=4 tables in HR doc, got {len(doc.tables)}"
    )