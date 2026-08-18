"""Burmese localized strings for the renderer's placeholder text.

The Brain template's English labels are kept as-is; only the
"Data is not found in source file." placeholder gets a Burmese mirror,
applied per-paragraph by ``policy_platform.style.render_not_found_placeholder``.
"""
from __future__ import annotations


# Burmese translation of the standard placeholder.
DATA_NOT_FOUND_MY = "အချက်အလက် မတွေ့ပါ"

# English (canonical).
DATA_NOT_FOUND_EN = "Data is not found in source file."


def data_not_found_for_lang(lang: str) -> str:
    """Return the placeholder string for the given paragraph language.

    ``"my"`` -> Burmese; ``"en"`` and ``"mixed"`` -> English (the
    mixed case keeps the canonical English placeholder because the
    paragraph also has Latin content and the reviewer can read both).
    """
    if lang == "my":
        return DATA_NOT_FOUND_MY
    return DATA_NOT_FOUND_EN