"""RAG-Hybrid retrieval subsystem for the Agentic Policy Platform.

This package replaces the rule-based analyzer with a Retrieval-Augmented
Generation (RAG) hybrid pipeline:

    chunker  -> splits the cleaned document into sentence-aware chunks
    embedder -> dense vector embeddings (sentence-transformers) with
                TF-IDF fallback (sklearn)
    faiss_store -> per-document vector index, with numpy cosine fallback
    bm25_store  -> BM25 keyword search (rank_bm25)
    reranker    -> cross-encoder re-ranking with raw score fallback
    retrieval_pipeline -> orchestrator that ties everything together

All models are loaded lazily and cached on first use. Every component
has a deterministic fallback that works without GPU and without any
network download, so the pipeline never fails on missing models.
"""
from __future__ import annotations

from .retrieval_pipeline import RetrievalPipeline, RAGResult

__all__ = ["RetrievalPipeline", "RAGResult"]
