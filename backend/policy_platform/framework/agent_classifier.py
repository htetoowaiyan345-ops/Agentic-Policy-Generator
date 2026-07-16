"""Reserved future stage: agent_classifier.

Classification only. NEVER rewrites, paraphrases, translates, summarizes,
or otherwise mutates any input text. If you wire an LLM here, the prompt
must forbid any text mutation and return only JSON {section_id: status}.
"""
from __future__ import annotations


def classify(*args, **kwargs):  # pragma: no cover - reserved
    raise NotImplementedError(
        "agent_classifier is reserved for future use. Rule-based analyzer is "
        "the only classifier in v1. It must never rewrite any text."
    )
