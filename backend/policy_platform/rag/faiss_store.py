"""Per-document FAISS vector index with numpy fallback.

The primary backend uses `faiss` (CPU build) for fast inner-product
search over dense vectors. If `faiss` is not importable, the store
falls back to a pure-numpy cosine similarity search. The two
backends produce identical results given identical embeddings.

A `FaissStore` is single-document: build once with a corpus of
embeddings, then issue `search(query_vecs, k)` calls.
"""
from __future__ import annotations

import threading
from typing import List, Tuple

import numpy as np

_FAISS_LOCK = threading.Lock()


class FaissStore:
    """Single-document vector index.

    The store is read-only after `build()`; the only mutation is the
    initial population. Searches are thread-safe and L2-normalized
    (inner-product == cosine similarity).
    """

    def __init__(self) -> None:
        self._index = None
        self._matrix: np.ndarray | None = None
        self._backend: str | None = None
        self._n: int = 0
        self._dim: int = 0

    @property
    def backend(self) -> str:
        return self._backend or "unbuilt"

    @property
    def n_items(self) -> int:
        return self._n

    def build(self, embeddings: np.ndarray) -> None:
        """Build the index from a (N, D) matrix of L2-normalized vectors."""
        with _FAISS_LOCK:
            if embeddings is None or len(embeddings) == 0:
                self._matrix = np.zeros((0, 0), dtype=np.float32)
                self._n = 0
                self._dim = 0
                self._backend = "numpy"
                return
            arr = np.ascontiguousarray(embeddings, dtype=np.float32)
            if arr.ndim != 2:
                raise ValueError("embeddings must be 2D (N, D)")
            self._n = arr.shape[0]
            self._dim = arr.shape[1]

            try:
                import faiss  # type: ignore

                index = faiss.IndexFlatIP(self._dim)
                index.add(arr)
                self._index = index
                self._backend = "faiss"
            except Exception:
                # Pure-numpy fallback: store the matrix and search via
                # matrix multiplication (cosine == inner product on
                # L2-normalized vectors).
                self._matrix = arr
                self._backend = "numpy"

    def search(self, query_vecs: np.ndarray, k: int) -> List[List[Tuple[int, float]]]:
        """Search for the top-k nearest neighbours per query.

        Returns a list of lists of (index, score) tuples sorted by
        descending score. If the index is empty, returns an empty list.
        """
        if self._n == 0 or self._dim == 0:
            return []
        if query_vecs is None or len(query_vecs) == 0:
            return []
        if k <= 0:
            return [[] for _ in range(len(query_vecs))]
        k_eff = min(k, self._n)

        q = np.ascontiguousarray(query_vecs, dtype=np.float32)
        if q.ndim == 1:
            q = q.reshape(1, -1)

        if self._backend == "faiss" and self._index is not None:
            scores, indices = self._index.search(q, k_eff)
            results: List[List[Tuple[int, float]]] = []
            for row_scores, row_indices in zip(scores, indices):
                row: List[Tuple[int, float]] = []
                for score, idx in zip(row_scores, row_indices):
                    if int(idx) < 0:
                        continue
                    row.append((int(idx), float(score)))
                results.append(row)
            return results

        # Numpy fallback.
        if self._matrix is None:
            return [[] for _ in range(len(q))]
        # Cosine similarity = q @ matrix.T (vectors are L2-normalized).
        sims = q @ self._matrix.T  # (Q, N)
        # Top-k via argpartition for speed.
        results = []
        for row in sims:
            if k_eff >= self._n:
                order = np.argsort(-row)
            else:
                part = np.argpartition(-row, k_eff - 1)[:k_eff]
                order = part[np.argsort(-row[part])]
            results.append([(int(i), float(row[i])) for i in order])
        return results
