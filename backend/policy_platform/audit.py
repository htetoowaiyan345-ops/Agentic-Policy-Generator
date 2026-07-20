"""Audit builder: builds a per-run audit dict (sections, integrity_checks, steps).
The dict is JSON-serialized and stored in the SQLite runs.db (audit_json column)
by the pipeline_runner. No files are written by this module."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def new_run_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def attach_slot_diff(
    sections: list[dict],
    prev_lines_json: Optional[list],
    new_lines_json: list,
) -> None:
    """Phase 6 — annotate each section dict with `before_text`, `after_text`,
    and `changed: bool` comparing the previous version's lines_json to the
    new one, bucketed by slot id.

    Operates in-place on the provided `sections` list (the same list that
    build_audit() emits). Skips silently when `prev_lines_json` is None
    (initial run) or when slots have no editable content.
    """
    if prev_lines_json is None:
        return
    prev_by_slot = _bucket_lines_json_by_slot(prev_lines_json)
    new_by_slot = _bucket_lines_json_by_slot(new_lines_json)
    for sec in sections:
        sid = sec.get('id')
        if sid is None:
            continue
        before = prev_by_slot.get(int(sid), [])
        after = new_by_slot.get(int(sid), [])
        before_text = '\n'.join(before)
        after_text = '\n'.join(after)
        changed = before_text != after_text
        sec['before_text'] = before_text
        sec['after_text'] = after_text
        sec['slot_changed'] = changed
        sec['before_chars'] = len(before_text)
        sec['after_chars'] = len(after_text)


def _bucket_lines_json_by_slot(lines_json) -> dict[int, list[str]]:
    """Bucket ['p', payload] / ['t', payload] lines by slot id (1..15) and
    return {slot_id: [plain text lines]}. Slot id 0 (free paragraph) is
    excluded so that any reviewer free-text appended above the slots doesn't
    pollute the per-slot diff columns.
    """
    out: dict[int, list[str]] = {}
    for raw in lines_json or []:
        if not isinstance(raw, list) or len(raw) != 2:
            continue
        kind, payload = raw
        if kind == 'p':
            slot = 0
            text = ''
            if isinstance(payload, dict):
                slot = int(payload.get('slot', 0) or 0)
                text = str(payload.get('text') or '')
            elif isinstance(payload, str):
                text = payload
            if not text or slot < 1 or slot > 15:
                continue
            out.setdefault(slot, []).append(text)
        elif kind == 't':
            slot = 0
            rows = []
            if isinstance(payload, dict):
                slot = int(payload.get('slot', 0) or 0)
                rows = payload.get('rows') or []
            elif isinstance(payload, list):
                rows = payload
            if slot < 1 or slot > 15 or not rows:
                continue
            for row in rows:
                cells = []
                for cell in (row or []):
                    text = ''
                    if isinstance(cell, dict):
                        text = str(cell.get('text') or '')
                    else:
                        text = '' if cell is None else str(cell)
                    cells.append(text)
                out.setdefault(slot, []).append(' / '.join(cells))
    return out


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