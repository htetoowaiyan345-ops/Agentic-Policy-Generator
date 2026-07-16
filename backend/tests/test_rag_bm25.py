"""Tests for the BM25 keyword store."""
from __future__ import annotations

from policy_platform.rag.bm25_store import BM25Store


def test_empty_corpus_returns_empty():
    store = BM25Store()
    store.build([])
    assert store.search(["anything"], k=3) == [[]]


def test_query_matches_own_corpus():
    store = BM25Store()
    store.build([
        "workplace safety policy",
        "chocolate cake recipe",
        "employee training guidelines",
    ])
    hits = store.search(["safety"], k=2)
    assert len(hits) == 1
    assert len(hits[0]) == 2
    # The "safety" doc should be the top hit.
    assert hits[0][0][0] == 0
    # The chocolate-cake doc should score below the safety doc.
    top_idx, top_score = hits[0][0]
    second_idx, second_score = hits[0][1]
    assert top_idx == 0
    assert second_idx != 1 or second_score <= top_score


def test_no_token_overlap_returns_zero_scored():
    store = BM25Store()
    store.build(["alpha beta"])
    hits = store.search(["xyz"], k=2)
    # BM25 returns the corpus with 0 scores when there's no overlap;
    # verify all returned scores are 0.
    assert len(hits) == 1
    for _idx, score in hits[0]:
        assert score == 0.0


def test_top_k_limited():
    store = BM25Store()
    store.build(["a"] * 10 + ["b"] * 5)
    hits = store.search(["a"], k=3)
    assert len(hits[0]) == 3


def test_k_zero_returns_empty():
    store = BM25Store()
    store.build(["hello world"])
    hits = store.search(["hello"], k=0)
    assert hits == [[]]


def test_results_sorted_descending():
    store = BM25Store()
    store.build([
        "policy safety compliance",
        "policy",
        "policy safety compliance review",
    ])
    hits = store.search(["policy safety compliance"], k=3)
    scores = [s for _, s in hits[0]]
    assert scores == sorted(scores, reverse=True)
