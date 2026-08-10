"""Tests for Phase 4 — layout_config.yaml loading + fallback behavior.

Verifies that:
  * the default layout_config.yaml loads successfully
  * missing file → empty dict (no error)
  * malformed YAML → empty dict (no error, warning logged)
  * env var AGENTIC_POLICY_LAYOUT_CONFIG overrides the default location
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the in-process config cache before each test."""
    from policy_platform.framework.config_loader import reset_cache
    reset_cache()
    yield
    reset_cache()


def test_default_layout_config_loads():
    """The default layout_config.yaml next to the Brain template loads
    and exposes the expected top-level sections."""
    from policy_platform.config import load_layout_config

    cfg = load_layout_config()
    assert isinstance(cfg, dict)
    assert cfg, "Default layout_config.yaml should produce non-empty dict"
    # Top-level sections that should exist.
    assert "slots" in cfg
    assert "phrase_templates" in cfg
    assert "table_order_hints" in cfg
    # All 15 slots should be present.
    for slot_id in range(1, 16):
        assert str(slot_id) in cfg["slots"], (
            f"Slot {slot_id} missing from layout_config.yaml slots section"
        )


def test_missing_file_returns_empty_dict(tmp_path: Path, monkeypatch):
    """When AGENTIC_POLICY_LAYOUT_CONFIG points to a non-existent file,
    the loader returns an empty dict (no exception)."""
    from policy_platform.config import LAYOUT_CONFIG_PATH, load_layout_config

    bogus = tmp_path / "does-not-exist.yaml"
    monkeypatch.setattr("policy_platform.config.LAYOUT_CONFIG_PATH", bogus)
    cfg = load_layout_config(path=bogus, force_reload=True)
    assert cfg == {}, f"Expected empty dict, got {cfg}"


def test_malformed_yaml_returns_empty_dict(tmp_path: Path, monkeypatch):
    """When the YAML file exists but has invalid syntax, the loader
    returns an empty dict (no exception, fallback to defaults)."""
    from policy_platform.config import load_layout_config

    bad = tmp_path / "bad.yaml"
    bad.write_text("invalid: : : : yaml\n  this is: not valid", encoding="utf-8")
    cfg = load_layout_config(path=bad, force_reload=True)
    assert cfg == {}, f"Expected empty dict on malformed YAML, got {cfg}"


def test_layout_config_cache_is_used(tmp_path: Path, monkeypatch):
    """Second call with same path returns cached value; force_reload=True
    re-reads."""
    from policy_platform.config import load_layout_config

    cfg_path = tmp_path / "cached.yaml"
    cfg_path.write_text("slots:\n  10:\n    heading_synonyms: [foo, bar]\n", encoding="utf-8")
    monkeypatch.setattr("policy_platform.config.LAYOUT_CONFIG_PATH", cfg_path)

    cfg1 = load_layout_config(path=cfg_path)
    cfg2 = load_layout_config(path=cfg_path)
    # Same content (cache hit).
    assert cfg1 == cfg2

    # Modify file, force reload.
    cfg_path.write_text("slots:\n  10:\n    heading_synonyms: [changed]\n", encoding="utf-8")
    cfg3 = load_layout_config(path=cfg_path, force_reload=True)
    assert cfg3 != cfg1, "force_reload should re-read file"


def test_env_var_overrides_path(monkeypatch, tmp_path: Path):
    """AGENTIC_POLICY_LAYOUT_CONFIG overrides the default path."""
    from policy_platform import config
    from policy_platform.config import load_layout_config

    custom = tmp_path / "custom.yaml"
    custom.write_text("phrase_templates:\n  - '{header} = {value}'\n", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_POLICY_LAYOUT_CONFIG", str(custom))
    # Re-import to pick up env var (load_layout_config uses module-level
    # LAYOUT_CONFIG_PATH set at import time).
    monkeypatch.setattr(config, "LAYOUT_CONFIG_PATH", Path(str(custom)))
    cfg = load_layout_config(path=custom, force_reload=True)
    assert cfg.get("phrase_templates") == ["{header} = {value}"]