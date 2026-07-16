"""Tests for the FAISS store + numpy fallback."""
from __future__ import annotations

import numpy as np

from policy_platform.rag.faiss_store import FaissStore


def _vec(values):
    arr = np.array(values, dtype=np.float32)
    n = np.linalg.norm(arr)
    if n > 0:
        arr = arr / n
    return arr


def test_empty_index_search_returns_empty():
    store = FaissStore()
    store.build(np.zeros((0, 4), dtype=np.float32))
    assert store.search(np.array([[1, 0, 0, 0]], dtype=np.float32), k=3) == []


def test_search_returns_top_k_sorted_by_score():
    store = FaissStore()
    vectors = np.stack([
        _vec([1, 0, 0, 0]),
        _vec([0.9, 0.1, 0, 0]),
        _vec([0, 1, 0, 0]),
    ])
    store.build(vectors)
    q = np.array([[1, 0, 0, 0]], dtype=np.float32)
    hits = store.search(q, k=2)
    assert len(hits) == 1
    assert len(hits[0]) == 2
    # Top hit should be index 0 (exact match).
    assert hits[0][0][0] == 0
    assert hits[0][0][1] > hits[0][1][1]


def test_search_k_larger_than_corpus_returns_all():
    store = FaissStore()
    vectors = np.stack([_vec([1, 0]), _vec([0, 1])])
    store.build(vectors)
    q = np.array([[1, 0]], dtype=np.float32)
    hits = store.search(q, k=10)
    assert len(hits) == 1
    assert len(hits[0]) == 2


def test_search_k_zero_returns_empty_lists():
    store = FaissStore()
    vectors = np.stack([_vec([1, 0]), _vec([0, 1])])
    store.build(vectors)
    q = np.array([[1, 0]], dtype=np.float32)
    hits = store.search(q, k=0)
    assert hits == [[]]


def test_backend_is_one_of_supported():
    store = FaissStore()
    store.build(np.stack([_vec([1, 0]), _vec([0, 1])]))
    assert store.backend in ("faiss", "numpy")
