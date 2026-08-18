"""Burmese RAG synonym loader.

Loads ``data/i18n/burmese_synonyms.yaml`` at import time (or on demand
if hot-reload is enabled) and exposes accessors for both section headings
and label-row keys.

The file is YAML, not Python, so business users can add new Burmese
synonyms without code changes.

Structure:
    headings:
      slots:
        5: [heading phrases for slot 5]
        ...
    labels:
      type: [label phrases for "Type:"]
      policy_title: [label phrases for "Policy Title:"]
      ...
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional


_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "i18n"
_YAML_PATH = _DATA_DIR / "burmese_synonyms.yaml"

# In-memory caches
_HEADINGS_CACHE: Dict[int, List[str]] = {}
_LABELS_CACHE: Dict[str, List[str]] = {}
_BURMESE_TO_CANONICAL: Dict[str, str] = {}
_LOADED = False


# Canonical mapping: snake_case YAML key -> English canonical label
_LABEL_KEY_TO_CANONICAL: Dict[str, str] = {
    "type": "Type:",
    "policy_title": "Policy Title:",
    "policy_number": "Policy Number:",
    "applicable_sectors": "Applicable Sector(s):",
    "functional_areas": "Functional Area(s):",
    "brief_description": "Brief Description:",
    "effective_date": "Effective Date/Period:",
    "approved_by": "Approved by:",
    "prepared_by": "Prepared by:",
    "responsible_functions": "Responsible Function(s):",
    "responsible_function_officers": "Responsible Function Officer(s):",
    "supersedes": "Supersedes:",
    "last_reviewed": "Last Reviewed:",
    "applies_to": "Applies to:",
    "reason_for_policy": "Reason for Policy:",
    "policy_review_note": "Policy Review Note:",
}


def _try_yaml():
    """Try to import a YAML library without making it a hard dependency."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load
    except ImportError:
        pass
    return None


def _parse_simple_yaml(text: str) -> tuple[Dict[int, List[str]], Dict[str, List[str]]]:
    """Minimal YAML parser for the two-section structure.

    Returns (headings_dict, labels_dict).
    """
    headings: Dict[int, List[str]] = {}
    labels: Dict[str, List[str]] = {}

    in_headings = False
    in_labels = False
    current_slot: Optional[int] = None
    current_label_key: Optional[str] = None
    current_indent = -1

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())

        if not in_headings and not in_labels:
            if stripped == "headings:":
                in_headings = True
                current_indent = indent
                continue
            if stripped == "labels:":
                in_labels = True
                current_indent = indent
                continue
            continue

        if in_headings:
            if stripped == "slots:":
                continue
            if indent <= current_indent and stripped != "slots:":
                in_headings = False
                if stripped == "labels:":
                    in_labels = True
                    current_indent = indent
                continue
            if stripped.startswith("- "):
                value = stripped[2:].strip()
                if current_slot is not None:
                    headings.setdefault(current_slot, []).append(value)
            else:
                key = stripped.split(":", 1)[0].strip()
                try:
                    current_slot = int(key)
                except ValueError:
                    current_slot = None
            continue

        if in_labels:
            if indent <= current_indent and not stripped.startswith("- "):
                in_labels = False
                continue
            if stripped.startswith("- "):
                value = stripped[2:].strip()
                if current_label_key is not None:
                    labels.setdefault(current_label_key, []).append(value)
            else:
                key = stripped.split(":", 1)[0].strip()
                current_label_key = key

    return headings, labels


def _load_from_disk() -> tuple[Dict[int, List[str]], Dict[str, List[str]], Dict[str, str]]:
    """Load YAML from disk.

    Returns (headings_dict, labels_dict, burmese_to_canonical_reverse_index).
    """
    if not _YAML_PATH.exists():
        return {}, {}, {}
    text = _YAML_PATH.read_text(encoding="utf-8")
    yaml_fn = _try_yaml()
    if yaml_fn is not None:
        parsed = yaml_fn(text)
        if isinstance(parsed, dict):
            headings = {}
            if "headings" in parsed and isinstance(parsed["headings"], dict):
                slots_data = parsed["headings"].get("slots", {})
                headings = {
                    int(k): list(v) if isinstance(v, list) else []
                    for k, v in slots_data.items()
                }
            labels = {}
            if "labels" in parsed and isinstance(parsed["labels"], dict):
                labels = {
                    k: list(v) if isinstance(v, list) else []
                    for k, v in parsed["labels"].items()
                }
            # Build reverse index
            burmese_to_canonical = {}
            for label_key, phrases in labels.items():
                canonical = _LABEL_KEY_TO_CANONICAL.get(label_key)
                if canonical:
                    for phrase in phrases:
                        burmese_to_canonical[phrase.strip().lower()] = canonical
            return headings, labels, burmese_to_canonical
        return {}, {}, {}
    return _parse_simple_yaml(text)


def _ensure_loaded() -> None:
    global _HEADINGS_CACHE, _LABELS_CACHE, _BURMESE_TO_CANONICAL, _LOADED
    hot = os.environ.get("AGENTIC_POLICY_BURMESE_SYNONYMS_HOT_RELOAD", "").lower() in ("1", "true", "yes")
    if not _LOADED or hot:
        _HEADINGS_CACHE, _LABELS_CACHE, _BURMESE_TO_CANONICAL = _load_from_disk()
        _LOADED = True


def get_burmese_synonyms(slot_id: int) -> List[str]:
    """Return the list of Burmese synonyms for the given heading slot.

    Returns an empty list if the slot has no Burmese synonyms or the
    YAML file is missing.
    """
    _ensure_loaded()
    return list(_HEADINGS_CACHE.get(int(slot_id), []))


def get_all_burmese_synonyms() -> Dict[int, List[str]]:
    """Return the full heading slot -> synonyms mapping (read-only snapshot)."""
    _ensure_loaded()
    return {k: list(v) for k, v in _HEADINGS_CACHE.items()}


def get_burmese_label_synonyms(label_key: str) -> List[str]:
    """Return Burmese equivalents for an English label-row canonical key.

    Mirrors the English BRAIN_LABEL_ROWS structure: callers pass the
    snake_case form of the English canonical (e.g., 'effective_date' for
    'Effective Date/Period:') and receive the list of Burmese phrases.
    """
    _ensure_loaded()
    return list(_LABELS_CACHE.get(label_key, []))


def get_all_burmese_labels() -> Dict[str, List[str]]:
    """Return full snake_case-key -> phrases mapping (read-only snapshot)."""
    _ensure_loaded()
    return {k: list(v) for k, v in _LABELS_CACHE.items()}


def get_canonical_for_burmese_label(burmese_phrase: str) -> Optional[str]:
    """Reverse lookup: given a Burmese phrase, return the English canonical
    it maps to (e.g., 'Type:').

    Returns None if the phrase is not a known Burmese label.
    Accepts phrases with or without trailing colon.
    """
    _ensure_loaded()
    # Strip trailing colon and any trailing/leading whitespace
    cleaned = burmese_phrase.strip().rstrip(":").strip().lower()
    return _BURMESE_TO_CANONICAL.get(cleaned)


def reset_cache() -> None:
    """Drop the in-process cache. Used by tests after editing the YAML."""
    global _HEADINGS_CACHE, _LABELS_CACHE, _BURMESE_TO_CANONICAL, _LOADED
    _HEADINGS_CACHE = {}
    _LABELS_CACHE = {}
    _BURMESE_TO_CANONICAL = {}
    _LOADED = False