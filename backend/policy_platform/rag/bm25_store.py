"""BM25 keyword search with deterministic tokenization.

Wraps `rank_bm25.BM25Okapi` for keyword-based scoring. Tokens are
lowercase, punctuation-stripped words; we use a minimal regex tokenizer
so behaviour is identical across Python versions and operating systems.

The store is single-document: build once with the corpus, then issue
`search(queries, k)` calls. Scores are non-negative floats, higher is
better.
"""
from __future__ import annotations

import re
import threading
from typing import List, Sequence, Tuple

_LOCK = threading.Lock()
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Store:
    """Single-document BM25 index over a tokenized corpus."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._bm25 = None
        self._n: int = 0
        self._corpus_tokens: List[List[str]] = []

    @property
    def n_items(self) -> int:
        return self._n

    def build(self, texts: Sequence[str]) -> None:
        """Tokenize and build the BM25 index."""
        with _LOCK:
            self._corpus_tokens = [_tokenize(t) for t in texts]
            self._n = len(self._corpus_tokens)
            if self._n == 0:
                self._bm25 = None
                return
            try:
                from rank_bm25 import BM25Okapi  # type: ignore

                self._bm25 = BM25Okapi(
                    self._corpus_tokens,
                    k1=self._k1,
                    b=self._b,
                )
            except Exception:
                # Pure-Python fallback: TF-IDF-like BM25 implemented in-line.
                self._bm25 = _PythonBM25(self._corpus_tokens, k1=self._k1, b=self._b)

    def search(self, queries: Sequence[str], k: int) -> List[List[Tuple[int, float]]]:
        """Score each query against the corpus and return top-k results.

        Returns a list of lists of (corpus_index, score) sorted by
        descending score.
        """
        if self._bm25 is None or self._n == 0:
            return [[] for _ in queries]
        if k <= 0:
            return [[] for _ in queries]
        k_eff = min(k, self._n)

        results: List[List[Tuple[int, float]]] = []
        for q in queries:
            tokens = _tokenize(q)
            if not tokens:
                results.append([])
                continue
            scores = self._bm25.get_scores(tokens)
            order = np_argsort_desc(scores)[:k_eff]
            results.append([(int(i), float(scores[i])) for i in order])
        return results


def np_argsort_desc(arr) -> list:
    """Pure-Python argsort descending; avoids numpy dependency for tiny arrays."""
    idx = list(range(len(arr)))
    idx.sort(key=lambda i: -arr[i])
    return idx


class _PythonBM25:
    """Minimal pure-Python BM25 Okapi implementation as a fallback."""

    def __init__(self, corpus_tokens: List[List[str]], k1: float, b: float) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus_tokens
        self.N = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        # Document frequency per term.
        df: dict[str, int] = {}
        for d in corpus_tokens:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        self.idf = {
            t: _idf(self.N, df[t]) for t in df
        }
        # Term frequencies per doc.
        self.tf: List[dict[str, int]] = []
        for d in corpus_tokens:
            tf: dict[str, int] = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            self.tf.append(tf)

    def get_scores(self, query_tokens: List[str]) -> List[float]:
        scores = [0.0] * self.N
        if not query_tokens or self.N == 0 or self.avgdl == 0:
            return scores
        for q in query_tokens:
            idf = self.idf.get(q, 0.0)
            if idf <= 0:
                continue
            for i, tf_map in enumerate(self.tf):
                f = tf_map.get(q, 0)
                if f == 0:
                    continue
                dl = self.doc_len[i] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return scores


def _idf(N: int, df: int) -> float:
    """Robertson IDF, with +0.5 smoothing (Lucene convention)."""
    import math
    return math.log((N - df + 0.5) / (df + 0.5) + 1.0)
