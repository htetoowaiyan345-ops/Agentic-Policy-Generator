"""Tests for the local embedder (sentence-transformers + TF-IDF fallback)."""
from __future__ import annotations

import numpy as np

from policy_platform.rag.embedder import Embedder


def test_empty_input_returns_zero_rows():
    emb = Embedder()
    out = emb.embed([])
    assert out.shape == (0, emb.dim)


def test_embed_returns_l2_normalized():
    emb = Embedder()
    out = emb.embed(["hello world", "goodbye world"])
    assert out.dtype == np.float32
    assert out.ndim == 2
    assert out.shape[0] == 2
    norms = np.linalg.norm(out, axis=1)
    np.testing.assert_allclose(norms, np.ones(2), atol=1e-5)


def test_similar_texts_have_higher_similarity():
    emb = Embedder()
    a = emb.embed(["workplace safety policy"])
    b = emb.embed(["employee safety guidelines"])
    c = emb.embed(["chocolate cake recipe"])
    sim_ab = float((a @ b.T)[0, 0])
    sim_ac = float((a @ c.T)[0, 0])
    assert sim_ab > sim_ac


def test_dim_matches_default():
    emb = Embedder()
    assert emb.dim > 0
    assert emb.dim == 384 or emb.dim >= 100


def test_backend_is_string():
    emb = Embedder()
    assert isinstance(emb.backend, str)
    assert emb.backend in ("sentence-transformers", "tfidf", "hash")


def test_tfidf_fallback_path():
    emb = Embedder(prefer_tfidf=True)
    out = emb.embed(["alpha beta gamma", "delta epsilon zeta"])
    assert out.shape == (2, emb.dim)
    assert emb.backend == "tfidf"
