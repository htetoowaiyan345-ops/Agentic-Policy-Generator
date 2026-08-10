"""Tests for Phase 4 — layout_config.yaml override behavior.

Verifies that:
  * Custom YAML config drives different routing decisions
  * Slot synonym overrides are appended to defaults
  * Empty YAML sections = no override = use defaults
  * Custom column_templates override RowPhraseBuilder output
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    from policy_platform.framework.config_loader import reset_cache
    reset_cache()
    yield
    reset_cache()


def test_empty_yaml_section_no_override():
    """When YAML provides empty heading_synonyms for slot 10, the
    merged list equals the default SECTION_HEADING_SYNONYMS[10]."""
    from policy_platform.config import LAYOUT_CONFIG_PATH, load_layout_config
    from policy_platform.framework.section_map import (
        SECTION_HEADING_SYNONYMS,
        get_section_synonyms,
    )

    # Default file has empty heading_synonyms for all slots (the
    # override example fields show empty lists).
    cfg = load_layout_config(path=LAYOUT_CONFIG_PATH, force_reload=True)
    if not cfg:
        pytest.skip("Default config not loadable")
    overrides = cfg.get("slots", {}).get("10", {}).get("heading_synonyms", [])
    if overrides:
        pytest.skip(
            "Default config has slot 10 heading_synonyms — this test "
            "assumes an empty override"
        )

    # The merged list must equal the default base.
    merged = get_section_synonyms(10)
    base = SECTION_HEADING_SYNONYMS[10]
    assert merged == base, (
        f"Empty YAML override should produce default base; "
        f"merged={merged!r} base={base!r}"
    )


def test_custom_yaml_drives_different_routing(tmp_path: Path, monkeypatch):
    """A custom YAML with extra synonyms for slot 10 makes
    `get_section_synonyms(10)` return more entries than the default.
    """
    from policy_platform.config import load_layout_config
    from policy_platform.framework.config_loader import reset_cache
    from policy_platform.framework.section_map import (
        SECTION_HEADING_SYNONYMS,
        get_section_synonyms,
    )

    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "slots:\n"
        "  10:\n"
        "    heading_synonyms:\n"
        "      - tier reimbursement band\n"  # NEW domain term
        "      - award fee schedule\n"
        "phrase_templates:\n"
        "  - '{header} = {value}'\n",
        encoding="utf-8",
    )

    # Use monkeypatch on the config module path.
    monkeypatch.setattr(
        "policy_platform.config.LAYOUT_CONFIG_PATH", custom,
    )
    reset_cache()
    cfg = load_layout_config(path=custom, force_reload=True)
    assert "slots" in cfg
    # YAML `10:` parses as int key (PyYAML convention).
    assert 10 in cfg["slots"]
    assert "tier reimbursement band" in cfg["slots"][10]["heading_synonyms"]

    # Get merged synonyms and verify the new terms appear.
    merged = get_section_synonyms(10)
    base = SECTION_HEADING_SYNONYMS[10]
    assert len(merged) > len(base), (
        f"Merged ({len(merged)}) should be longer than base ({len(base)})"
    )
    assert "tier reimbursement band" in merged
    assert "award fee schedule" in merged


def test_column_template_override_used_in_phrases(tmp_path: Path, monkeypatch):
    """Custom column_templates in YAML change RowPhraseBuilder output."""
    from policy_platform.config import load_layout_config
    from policy_platform.framework.config_loader import (
        get_slot_column_templates,
        reset_cache,
    )

    custom = tmp_path / "phrases.yaml"
    custom.write_text(
        "slots:\n"
        "  10:\n"
        "    column_templates:\n"
        "      Amount: 'payout is {value} dollars'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "policy_platform.config.LAYOUT_CONFIG_PATH", custom,
    )
    reset_cache()
    cfg = load_layout_config(path=custom, force_reload=True)
    templates = get_slot_column_templates(10)
    assert templates.get("amount") == "payout is {value} dollars"


def test_phrase_template_override_used_in_chunker(tmp_path: Path, monkeypatch):
    """Custom phrase_templates in YAML change `get_default_phrase_templates`."""
    from policy_platform.config import load_layout_config
    from policy_platform.framework.config_loader import reset_cache
    from policy_platform.rag.chunker import get_default_phrase_templates

    custom = tmp_path / "phr.yaml"
    custom.write_text(
        "phrase_templates:\n"
        "  - '{header} equals {value}'\n"
        "  - 'the {header} is {value} (custom)'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "policy_platform.config.LAYOUT_CONFIG_PATH", custom,
    )
    reset_cache()
    cfg = load_layout_config(path=custom, force_reload=True)
    templates = get_default_phrase_templates()
    assert "the {header} is {value} (custom)" in templates


def test_table_order_hint_override(tmp_path: Path, monkeypatch):
    """Custom table_order_hints in YAML override the Python default."""
    from policy_platform.config import load_layout_config
    from policy_platform.framework.config_loader import reset_cache
    from policy_platform.rag.table_routing import get_table_order_hints

    custom = tmp_path / "order.yaml"
    custom.write_text(
        "table_order_hints:\n"
        "  10: last_table_in_section\n"  # override default 'first_after_9'
        "  9: single_table_only\n",      # new slot
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "policy_platform.config.LAYOUT_CONFIG_PATH", custom,
    )
    reset_cache()
    cfg = load_layout_config(path=custom, force_reload=True)
    hints = get_table_order_hints()
    assert hints[10] == "last_table_in_section"
    assert hints[9] == "single_table_only"
    assert hints[14] == "all_in_section"  # default kept