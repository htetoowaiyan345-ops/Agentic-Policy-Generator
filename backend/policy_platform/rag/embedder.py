"""Local sentence-transformer embedder with TF-IDF fallback.

The primary backend uses the `all-MiniLM-L6-v2` model from
sentence-transformers (~80 MB, downloads lazily on first use). If the
package or model is unavailable for any reason, the embedder falls
back to a TF-IDF + L2-normalized dense representation built with
scikit-learn. The two backends are API-compatible.

The embedder is process-wide cached. `embed(texts)` returns a
`numpy.ndarray` of shape (N, D), dtype float32, L2-normalized so
dot-product == cosine similarity.
"""
from __future__ import annotations

import hashlib
import os
import threading
from typing import Iterable, List, Optional

import numpy as np

_EMBEDDER_LOCK = threading.Lock()

# Disable HuggingFace telemetry noise.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
FALLBACK_DIM = 384  # match MiniLM output dim for cross-backend compatibility


class Embedder:
    """Local, open-source sentence embedder with deterministic fallback.

    Thread-safe; the underlying model is loaded at most once per process.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, *, prefer_tfidf: bool = False) -> None:
        self.model_name = model_name
        self._model = None
        self._backend: Optional[str] = None
        self._tfidf_vectorizer = None
        self._tfidf_matrix = None
        self._tfidf_corpus: List[str] = []
        self._dim: Optional[int] = None
        self._prefer_tfidf = prefer_tfidf

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._ensure_loaded()
        return self._dim or FALLBACK_DIM

    @property
    def backend(self) -> str:
        self._ensure_loaded()
        return self._backend or "unknown"

    def _ensure_loaded(self) -> None:
        with _EMBEDDER_LOCK:
            if self._backend is not None:
                return
            if not self._prefer_tfidf:
                try:
                    from sentence_transformers import SentenceTransformer  # type: ignore

                    self._model = SentenceTransformer(self.model_name)
                    self._backend = "sentence-transformers"
                    dim_fn = getattr(self._model, "get_embedding_dimension", None) or getattr(
                        self._model, "get_sentence_embedding_dimension", None
                    )
                    self._dim = int(dim_fn()) if dim_fn else FALLBACK_DIM
                    return
                except Exception:
                    # Fall through to TF-IDF.
                    self._model = None
            # TF-IDF fallback.
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore

                self._tfidf_vectorizer = TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                    max_df=0.95,
                    sublinear_tf=True,
                )
                self._backend = "tfidf"
                self._dim = FALLBACK_DIM
            except Exception:
                # Last-resort deterministic hash embedding.
                self._backend = "hash"
                self._dim = FALLBACK_DIM

    def _ensure_corpus(self, texts: List[str]) -> None:
        """For TF-IDF backend, fit on the union of any new texts."""
        if self._backend != "tfidf":
            return
        if self._tfidf_vectorizer is None:
            return
        new_corpus = list(self._tfidf_corpus)
        for t in texts:
            if t not in new_corpus:
                new_corpus.append(t)
        if len(new_corpus) == len(self._tfidf_corpus):
            return
        self._tfidf_corpus = new_corpus
        matrix = self._tfidf_vectorizer.fit_transform(self._tfidf_corpus)
        if self._dim is None or matrix.shape[1] != self._dim:
            # Resize dim to actual feature count (truncated / padded to FALLBACK_DIM
            # for backend-compatibility with sentence-transformers).
            actual = matrix.shape[1]
            if actual <= FALLBACK_DIM:
                # Pad with zeros to FALLBACK_DIM for cross-backend parity.
                padded = np.zeros((matrix.shape[0], FALLBACK_DIM), dtype=np.float32)
                arr = matrix.toarray().astype(np.float32)
                padded[:, :actual] = arr
                self._tfidf_matrix = padded
            else:
                # Truncate columns.
                self._tfidf_matrix = matrix.toarray().astype(np.float32)[:, :FALLBACK_DIM]
            self._dim = FALLBACK_DIM
        else:
            self._tfidf_matrix = matrix.toarray().astype(np.float32)

    def _embed_hash(self, texts: List[str]) -> np.ndarray:
        """Deterministic last-resort embedding: SHA-256 -> random projection.

        Used only when neither sentence-transformers nor scikit-learn
        is importable. Output is L2-normalized.
        """
        out = np.zeros((len(texts), FALLBACK_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            digest = hashlib.sha256(t.encode("utf-8", errors="ignore")).digest()
            # Repeat digest to fill 384 dims.
            seed_bytes = (digest * ((FALLBACK_DIM // len(digest)) + 1))[: FALLBACK_DIM * 4]
            arr = np.frombuffer(seed_bytes, dtype=np.uint8).astype(np.float32)
            arr = arr[:FALLBACK_DIM]
            arr -= arr.mean()
            out[i] = arr
        # L2 normalize.
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        """Embed a sequence of strings. Returns (N, D) float32 L2-normalized."""
        self._ensure_loaded()
        text_list = [t if t else "" for t in texts]
        if not text_list:
            return np.zeros((0, self.dim), dtype=np.float32)

        if self._backend == "sentence-transformers" and self._model is not None:
            embeddings = self._model.encode(
                text_list,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=32,
            )
            arr = np.asarray(embeddings, dtype=np.float32)
            return arr

        if self._backend == "tfidf" and self._tfidf_vectorizer is not None:
            self._ensure_corpus(text_list)
            if self._tfidf_matrix is None or len(self._tfidf_corpus) == 0:
                return np.zeros((len(text_list), self.dim), dtype=np.float32)
            # Map each text to its row in the corpus.
            index = {t: i for i, t in enumerate(self._tfidf_corpus)}
            rows = np.array([index[t] for t in text_list], dtype=np.int64)
            arr = self._tfidf_matrix[rows]
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return (arr / norms).astype(np.float32)

        # Hash fallback.
        return self._embed_hash(text_list)
