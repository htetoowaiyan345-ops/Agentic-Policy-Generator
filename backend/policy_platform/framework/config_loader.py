"""Phase 4 — Centralized loader for layout_config.yaml overrides.

Provides typed accessors for the slots / signals / templates / order
hints that were previously hard-coded in Python. Each accessor:
  1. Reads the corresponding section from the loaded YAML config.
 2. Returns an empty list / dict if missing.
  3. Lets the caller fall back to its hard-coded Python literal.

Consumers:
  * framework.section_map.get_section_synonyms(slot_id)
  * rag.slot_queries.get_slot_queries(slot_id)  (TODO Phase 4)
  * rag.table_routing.get_table_signals(slot_id)  (TODO Phase 4)
  * rag.table_routing.get_generic_table_signals()  (TODO Phase 4)
  * rag.table_routing.get_label_row_keywords()  (TODO Phase 4)
  * rag.heading_anchors.get_structural_body_breaks()  (TODO Phase 4)
  * rag.chunker.get_default_phrase_templates()  (TODO Phase 4)
  * rag.table_routing.get_table_order_hints()  (TODO Phase 4)

This module is intentionally tiny — it's a typed wrapper around
`config.load_layout_config()` so consumers don't have to know the
YAML schema.
"""
from __future__ import annotations

from policy_platform.config import load_layout_config


def _cfg() -> dict:
    return load_layout_config()


def get_slot_section(slot_id: int) -> dict:
    """Return the YAML `slots[<slot_id>]` section, or {} if missing.

    YAML keys may be parsed as int (e.g. `10:`) or str (e.g. `"10":`).
    We accept both forms.
    """
    slots = _cfg().get("slots", {}) or {}
    if not isinstance(slots, dict):
        return {}
    sec = slots.get(slot_id)
    if not isinstance(sec, dict):
        sec = slots.get(str(slot_id))
    return sec if isinstance(sec, dict) else {}


def get_slot_synonyms_overrides(slot_id: int) -> list[str]:
    """Return YAML heading_synonyms override for a slot (may be empty)."""
    sec = get_slot_section(slot_id)
    out = sec.get("heading_synonyms", []) or []
    return [str(s) for s in out if isinstance(s, str)]


def get_slot_table_signal_overrides(slot_id: int) -> list[str]:
    """Return YAML table_signals override for a slot (may be empty)."""
    sec = get_slot_section(slot_id)
    out = sec.get("table_signals", []) or []
    return [str(s) for s in out if isinstance(s, str)]


def get_slot_column_templates(slot_id: int) -> dict[str, str]:
    """Return YAML column_templates override for a slot (header→template).

    Used by RowPhraseBuilder to override per-column phrasing. Empty dict
    means "no override, use defaults".

    Keys are normalized to lowercase so callers can match against
    header text uniformly (e.g. "Amount" key matches "Amount" header
    regardless of YAML capitalization).
    """
    sec = get_slot_section(slot_id)
    out = sec.get("column_templates", {}) or {}
    return {str(k).strip().lower(): str(v) for k, v in out.items() if isinstance(v, str)}


def get_phrase_templates() -> list[str]:
    """Return YAML phrase_templates override (empty list = use defaults)."""
    out = _cfg().get("phrase_templates", []) or []
    return [str(s) for s in out if isinstance(s, str)]


def get_table_order_hints() -> dict:
    """Return YAML table_order_hints dict (string keys → string values)."""
    out = _cfg().get("table_order_hints", {}) or {}
    return {str(k): str(v) for k, v in out.items()}


def get_slot_queries_override(slot_id: int) -> list[str]:
    """Return YAML slot_queries override for a slot (may be empty)."""
    out = _cfg().get("slot_queries", {}) or {}
    slot_q = out.get(str(slot_id)) or out.get(slot_id)
    if not isinstance(slot_q, list):
        return []
    return [str(s) for s in slot_q if isinstance(s, str)]


def get_generic_table_signals() -> list[str]:
    """Return YAML generic_table_signals override (may be empty)."""
    out = _cfg().get("generic_table_signals", []) or []
    return [str(s) for s in out if isinstance(s, str)]


def get_label_row_keywords() -> list[str]:
    """Return YAML label_row_keywords override (may be empty)."""
    out = _cfg().get("label_row_keywords", []) or []
    return [str(s) for s in out if isinstance(s, str)]


def get_structural_body_breaks() -> list[str]:
    """Return YAML structural_body_breaks override (may be empty)."""
    out = _cfg().get("structural_body_breaks", []) or []
    return [str(s) for s in out if isinstance(s, str)]


def reset_cache() -> None:
    """Reset the in-process config cache. Used by tests."""
    global _LAYOUT_CONFIG_CACHE
    _LAYOUT_CONFIG_CACHE = None  # type: ignore[name-defined]


# Re-import for the reset_cache helper.
from policy_platform.config import _LAYOUT_CONFIG_CACHE  # noqa: E402, F401