"""Audit builder: builds a per-run audit dict (sections, integrity_checks, steps).
The dict is JSON-serialized and stored in the SQLite runs.db (audit_json column)
by the pipeline_runner. No files are written by this module."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def new_run_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_audit(result: Any) -> dict:
    """Build a JSON-serializable audit dict from a pipeline result object.
    The structure matches the legacy Excel workbook sheets (Run, Sections, Integrity, Steps).
    """
    sections = getattr(result, 'sections', []) or []
    steps = []
    for st in (getattr(result, 'steps', []) or []):
        if hasattr(st, '__dict__'):
            steps.append({
                'no': getattr(st, 'no', None),
                'name': getattr(st, 'name', None),
                'ok': getattr(st, 'ok', None),
                'detail': getattr(st, 'detail', ''),
            })
        elif isinstance(st, dict):
            steps.append(st)
        else:
            steps.append({'value': str(st)})
    return {
        'run_id': getattr(result, 'run_id', None),
        'filename': getattr(result, 'document_name', None),
        'created_at': now_iso(),
        'started_at': getattr(result, 'started_at', None),
        'finished_at': getattr(result, 'finished_at', None),
        'processing_time_ms': getattr(result, 'processing_time_ms', None),
        'framework_version': getattr(result, 'framework_version', None),
        'framework_sha256': getattr(result, 'framework_sha256', None),
        'extraction_path': getattr(result, 'extraction_path', None),
        'validation_ok': getattr(result, 'validation_ok', None),
        'fallback_used': getattr(result, 'fallback_used', None),
        'output_path': getattr(result, 'output_path', None),
        'total_placed_chars': getattr(result, 'total_placed_chars', 0),
        'total_dropped_chars': getattr(result, 'total_dropped_chars', 0),
        'total_dropped_paragraphs': getattr(result, 'total_dropped_paragraphs', 0),
        'sections': sections,
        'integrity_checks': getattr(result, 'integrity_checks', []) or [],
        'steps': steps,
        'dropped_paragraphs_sample': getattr(result, 'dropped_paragraphs_sample', []) or [],
    }


def build_audit_json(result: Any) -> str:
    """Build the audit dict and serialize it to a JSON string for DB storage."""
    return json.dumps(build_audit(result), default=str)


def write_audit(result: Any) -> str:
    """Backward-compatible entry point. Returns the audit JSON string.
    Callers (pipeline_runner) store the returned string in runs.db.
    No files are written."""
    return build_audit_json(result)