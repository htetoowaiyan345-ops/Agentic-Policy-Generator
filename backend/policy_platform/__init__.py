"""Agentic Policy Processing Platform — top-level package.

This package wires together:
  * `shims` (Python 3.14 / lxml compatibility; MUST be imported first).
  * `rag` (RAG-Hybrid retrieval subsystem).

Importing this package must NEVER fail at module-load time, otherwise
every code path that touches the platform (api.server, tests, scripts)
crashes with `ModuleNotFoundError`. The previous version of this file
had `from .retrieval_pipeline import ...` which is broken because the
`retrieval_pipeline.py` module lives under `.rag.retrieval_pipeline`,
not directly under `.retrieval_pipeline`. This module fixes the path.
"""
from __future__ import annotations

# Apply Python 3.14 / lxml compatibility shims FIRST before any other
# import in this package may load `lxml.etree` indirectly.
from . import shims  # noqa: F401

__version__ = "0.1.0"

# Public re-exports for callers that want the RAG subsystem symbols at
# the top level (e.g. `from policy_platform import RetrievalPipeline`).
# Imported lazily so a missing optional dependency cannot break import.
try:
    from .rag.retrieval_pipeline import RetrievalPipeline, RAGResult  # noqa: F401
    __all__ = ["RetrievalPipeline", "RAGResult"]
except Exception:  # pragma: no cover - defensive; the modules exist.
    # Don't crash import — the RAG subsystem is still importable via
    # `policy_platform.rag.retrieval_pipeline` for callers that need it.
    __all__ = []