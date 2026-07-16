"""Type compatibility shim: re-exports the dataclasses the renderer
expects (`SectionSlot`, `ClassificationResult`).

The historical rule-based `analyze()` function has been removed; the
RAG-Hybrid pipeline (see `policy_platform.rag`) is the sole routing
mechanism. This module exists so existing imports of
`from .analyzer import SectionSlot, ClassificationResult` keep
working during the transition.
"""
from __future__ import annotations

# Re-export the dataclasses that other modules import from this package.
# Defined here as a single source of truth so the legacy analyzer.py can
# be removed without touching every consumer.
from dataclasses import dataclass, field


@dataclass
class SectionSlot:
    status: str
    content_paragraphs: list[str] = field(default_factory=list)
    content_tables: list[list[list[str]]] = field(default_factory=list)
    placed_paragraphs: list[str] = field(default_factory=list)
    routing_rule: str = ""


@dataclass
class ClassificationResult:
    sections: dict[int, SectionSlot] = field(default_factory=dict)
    routing_source_indices: dict[int, list[int]] = field(default_factory=dict)
    routing_table_indices: dict[int, list[int]] = field(default_factory=dict)
    dropped_paragraph_indices: list[int] = field(default_factory=list)
    dropped_table_indices: list[int] = field(default_factory=list)
    fallback_used: bool = False


__all__ = ["SectionSlot", "ClassificationResult"]
