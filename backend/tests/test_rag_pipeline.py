"""Tests for the RAG-Hybrid retrieval pipeline orchestrator."""
from __future__ import annotations

from policy_platform.rag import RetrievalPipeline
from policy_platform.rag.retrieval_pipeline import (
    DEFAULT_TIMEOUT_SECONDS,
    SlotAssignment,
)


SAMPLE_PARAGRAPHS = [
    "Policy Title: Workplace Health and Safety",
    "Effective Date: 2024-01-15",
    "Approved by: Board of Directors",
    "1. Purpose: This policy establishes the framework for workplace safety standards across all business units.",
    "2. Scope: This policy applies to all full-time and part-time employees of the company.",
    "3. Exclusions: Contractors and temporary workers are governed by separate agreements.",
    "Document History: v1.0 2024-01-15, v1.1 2024-06-15",
    "Definitions: Hazard means any source of potential damage or harm.",
    "Related Policies: See also the Environmental Policy and the Travel Policy.",
]


def test_default_timeout_is_120_seconds():
    """Default RAG timeout is 120s (raised from 60s so dense PDFs like
    Earthquake PDF can finish Tier-3 RAG for tier-1 slots like
    HISTORY without being marked "timeout")."""
    assert DEFAULT_TIMEOUT_SECONDS == 120.0


def test_run_returns_all_15_slots():
    pipe = RetrievalPipeline(timeout_seconds=180)
    result = pipe.run(SAMPLE_PARAGRAPHS)
    assert sorted(result.slots.keys()) == list(range(1, 16))


def test_logo_slot_is_always_none():
    pipe = RetrievalPipeline(timeout_seconds=180)
    result = pipe.run(SAMPLE_PARAGRAPHS)
    sa = result.slots[15]
    assert sa.chunk_text is None
    assert sa.backend == "logo"


def test_run_with_empty_input_returns_skipped():
    pipe = RetrievalPipeline(timeout_seconds=180)
    result = pipe.run([])
    # All slots 1-14 should be present, none should have content.
    for sid in range(1, 15):
        assert sid in result.slots
        assert result.slots[sid].chunk_text is None


def test_slot_assignment_dataclass_fields():
    sa = SlotAssignment(slot_id=7, chunk_text="hello", score=1.0, backend="x")
    assert sa.slot_id == 7
    assert sa.chunk_text == "hello"
    assert sa.score == 1.0


def test_run_records_backend_strings():
    pipe = RetrievalPipeline(timeout_seconds=180)
    result = pipe.run(SAMPLE_PARAGRAPHS)
    assert result.embedder_backend in ("sentence-transformers", "tfidf", "hash")
    assert result.faiss_backend in ("faiss", "numpy")
    assert result.reranker_backend in ("cross-encoder", "fallback")


def test_run_elapsed_is_nonnegative():
    pipe = RetrievalPipeline(timeout_seconds=180)
    result = pipe.run(SAMPLE_PARAGRAPHS)
    assert result.elapsed_seconds >= 0.0


def test_run_does_not_mutate_input_list():
    pipe = RetrievalPipeline(timeout_seconds=180)
    inputs = list(SAMPLE_PARAGRAPHS)
    snapshot = list(SAMPLE_PARAGRAPHS)
    pipe.run(inputs)
    assert inputs == snapshot


def test_pipeline_singleton_via_get_pipeline():
    """Calling the module-level get_pipeline() returns the same instance."""
    from policy_platform.pipeline import get_pipeline
    a = get_pipeline()
    b = get_pipeline()
    assert a is b
