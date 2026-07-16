"""Tests for the cross-encoder reranker (with deterministic fallback)."""
from __future__ import annotations

from policy_platform.rag.reranker import Reranker


def test_empty_candidates_returns_empty():
    r = Reranker()
    assert r.rerank("anything", [], top_k=1) == []


def test_top_k_zero_returns_empty():
    r = Reranker()
    assert r.rerank("anything", ["x"], top_k=0) == []


def test_returns_top_k_in_descending_order():
    r = Reranker()
    candidates = [
        "this is a generic sentence",
        "this is about workplace safety policy",
        "another random sentence about food",
    ]
    reranked = r.rerank("workplace safety policy", candidates, top_k=2)
    assert len(reranked) == 2
    # Scores must be descending.
    assert reranked[0][1] >= reranked[1][1]
    # Indices must be valid.
    for idx, _ in reranked:
        assert 0 <= idx < len(candidates)


def test_backend_is_a_known_string():
    r = Reranker()
    assert r.backend in ("cross-encoder", "fallback")


def test_top_k_clamped_to_corpus_size():
    r = Reranker()
    candidates = ["only one candidate"]
    reranked = r.rerank("query", candidates, top_k=5)
    assert len(reranked) == 1
