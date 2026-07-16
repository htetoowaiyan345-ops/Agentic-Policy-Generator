"""End-to-end RAG tests on Flood_Emergency_Assistance_Policy.pdf.

Mirrors the structure of `test_e2e_rag_samples.py` (which covers
Earthquake and Award). The Flood PDF exercises the structural-body-
break fix in `policy_platform.rag.heading_anchors
._is_cross_slot_boundary` — verifies that slot 12 (Definitions)
and the structural-break paragraph `Required Documents include ...`
do NOT bleed into slot 10 (Award Structure & Payout Tiers).

Resolves the PDF path via `Path(__file__).resolve().parent.parent /
"data" / "samples"` so no absolute filesystem path is hardcoded
in this test. The test is skipped if the file is missing.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import pytest

from policy_platform.extractors import dispatch
from policy_platform.rag import RetrievalPipeline
from policy_platform.rag_adapter import build_classification_from_rag


SAMPLES = BACKEND / "data" / "samples"
FLOOD_PDF = SAMPLES / "Flood_Emergency_Assistance_Policy.pdf"


def _run_on_sample(sample_path: Path, *, timeout: float = 180.0):
    extracted = dispatch(sample_path)
    pipe = RetrievalPipeline(timeout_seconds=timeout)
    rag = pipe.run(
        list(extracted.paragraphs),
        tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
    )
    classified = build_classification_from_rag(
        rag, source_paragraph_count=len(extracted.paragraphs)
    )
    return classified, rag


@pytest.fixture(scope="module")
def flood_classified():
    """Run the full pipeline against
    data/samples/Flood_Emergency_Assistance_Policy.pdf.

    Skips the test module if the PDF is not present in the local
    samples directory, so CI / other contributors can run the
    suite without the file.
    """
    if not FLOOD_PDF.exists():
        pytest.skip(f"sample not found: {FLOOD_PDF}")
    classified, _rag = _run_on_sample(FLOOD_PDF)
    return classified


def test_flood_slot_10_contains_tier_definitions(flood_classified):
    """Slot 10 (Award Structure & Payout Tiers) of the Flood PDF
    contains the tier definitions and the Annual Budget line.

    Loose substring assertions tolerate PDF byte variation.
    """
    slot = flood_classified.sections[10]
    text = " ".join(slot.content_paragraphs)
    lower = text.lower()
    assert "tier 1" in lower, (
        f"slot 10 missing Tier 1 content; got: {text[:200]!r}"
    )
    assert "total annual budget" in lower or "mmk 100,000,000" in lower, (
        f"slot 10 missing Annual Budget line; got: {text[:200]!r}"
    )


def test_flood_slot_12_contains_flood_event_definition(flood_classified):
    """Slot 12 (Definitions) of the Flood PDF contains the Flood
    Event definition clause. Loose substring match.
    """
    slot = flood_classified.sections[12]
    text = " ".join(slot.content_paragraphs)
    assert "Flood Event" in text, (
        f"slot 12 missing Flood Event definition; got: {text[:200]!r}"
    )
    assert "City Holdings Group" in text, (
        f"slot 12 missing Company definition; got: {text[:200]!r}"
    )


def test_flood_slot_10_does_not_leak_definitions_text(flood_classified):
    """Slot 10 (Award Structure & Payout Tiers) MUST NOT contain
    the slot-12 Definitions clause chain. This is the bleed-over
    regression assertion: a chunk starting with
    'means an officially recognized flood' is slot-12 territory
    and must not appear in slot 10.
    """
    slot = flood_classified.sections[10]
    text = " ".join(slot.content_paragraphs).lower()
    assert "means an officially recognized flood" not in text, (
        f"slot 10 leaked the Flood Event definition clause; got: "
        f"{text[:200]!r}"
    )
