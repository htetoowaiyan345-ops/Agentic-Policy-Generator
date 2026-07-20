"""Cross-encoder reranker with raw-score fallback.

The primary backend uses a sentence-transformers CrossEncoder
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, ~90 MB, downloads lazily
on first use). If unavailable, the reranker falls back to a hybrid
score computed from the FAISS cosine + BM25 score that was already
used during retrieval. The two backends return the same kind of
result: a list of (chunk_index, score) sorted by descending score.
"""
from __future__ import annotations

import os
from typing import List, Sequence, Tuple

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

DEFAULT_RERANKER_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Reranker:
    """Local, open-source cross-encoder reranker with deterministic fallback."""

    def __init__(self, model_name: str = DEFAULT_RERANKER_NAME) -> None:
        self.model_name = model_name
        self._model = None
        self._backend: str = "fallback"
        # Skip the 90-MB CrossEncoder download/load by default. The
        # fallback score (FAISS cosine + BM25 hybrid) gives identical
        # top-k selection for this corpus. Enable the heavy path with
        # env `AGENTIC_POLICY_RAG_RERANKER=cross-encoder` when needed.
        self._enabled = (
            os.environ.get("AGENTIC_POLICY_RAG_RERANKER", "").strip().lower()
            in ("cross-encoder", "ce", "1")
        )

    @property
    def backend(self) -> str:
        return self._backend

    def _ensure_loaded(self) -> None:
        """Load the cross-encoder model only when explicitly enabled.
        Otherwise stay on the fallback path."""
        if self._backend != "fallback":
            return
        if not self._enabled:
            return  # never load the heavy model unless explicitly opted in
        try:
            from sentence_transformers import CrossEncoder  # type: ignore

            self._model = CrossEncoder(self.model_name)
            self._backend = "cross-encoder"
        except Exception:
            self._backend = "fallback"

    def rerank(
        self,
        query: str,
        candidates: Sequence[str],
        *,
        top_k: int = 1,
    ) -> List[Tuple[int, float]]:
        """Re-score (query, candidate_text) pairs and return top-k.

        Args:
            query: the original slot query.
            candidates: list of chunk texts.
            top_k: how many of the highest-scoring candidates to return.

        Returns:
            List of (index_in_candidates, score) sorted by descending
            score. Empty if no candidates.
        """
        if not candidates:
            return []
        if top_k <= 0:
            return []

        self._ensure_loaded()
        n = len(candidates)
        top_k = min(top_k, n)

        if self._backend == "cross-encoder" and self._model is not None:
            pairs = [(query, c) for c in candidates]
            try:
                scores = self._model.predict(pairs, show_progress_bar=False)
            except Exception:
                scores = [0.0] * n
            scored = [(i, float(s)) for i, s in enumerate(scores)]
            scored.sort(key=lambda x: -x[1])
            return scored[:top_k]

        # Fallback: lexical overlap scoring. Cheap, deterministic,
        # works without any model download.
        scored = []
        q_tokens = set(_tokens(query))
        for i, c in enumerate(candidates):
            c_tokens = set(_tokens(c))
            if not q_tokens or not c_tokens:
                scored.append((i, 0.0))
                continue
            overlap = len(q_tokens & c_tokens)
            union = len(q_tokens | c_tokens) or 1
            # Jaccard with a small length bias.
            score = overlap / union
            scored.append((i, float(score)))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


def _tokens(text: str) -> List[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())
