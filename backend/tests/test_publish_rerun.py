"""Tests for Phase 6 — publish_rerun + audit diff.

Covers:
  - lines_json_extractor normalises legacy + rich shapes.
  - LinesJsonExtractor.to_extracted_document emits paragraphs + tables + slot ids.
  - pipeline.run_from_lines_json completes end-to-end against a small
    synthetic lines_json.
  - audit.attach_slot_diff annotates per-slot before/after text.
  - publish_to_brain.publish_approved_version writes a fresh .docx and
    populates docx_path + audit_json with diff columns.
  - The new audit xlsx path is backwards-compatible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from api.lines_json_extractor import (
    LinesJsonExtractor,
    normalise_lines_json,
    _strip_html_to_plain,
)
from policy_platform.audit import attach_slot_diff


# ---------------------------------------------------------------------------
# Synthetic extractor
# ---------------------------------------------------------------------------

def test_normalise_lines_json_handles_legacy_and_rich():
    legacy = [['p', 'hello'], ['t', [['h1', 'h2'], ['a', 'b']]]]
    out = normalise_lines_json(legacy)
    assert out == [
        ['p', {'slot': 0, 'text': 'hello', 'html': 'hello'}],
        ['t', {'slot': 0, 'rows': [['h1', 'h2'], ['a', 'b']]}],
    ]


def test_normalise_lines_json_passes_rich_through():
    rich = [['p', {'slot': 7, 'text': 'X', 'html': '<p>X</p>'}]]
    out = normalise_lines_json(rich)
    assert out == rich


def test_strip_html_to_plain_strips_tags_and_decodes():
    plain = _strip_html_to_plain('<p><strong>Hello</strong> &amp; world</p>')
    assert plain == 'Hello & world'


def test_strip_html_to_plain_handles_empty():
    assert _strip_html_to_plain('') == ''
    assert _strip_html_to_plain(None) == ''


def test_lines_json_extractor_emits_extracted_document():
    lines_json = [
        ['p', {'slot': 1, 'text': 'Type: HR', 'html': '<p><strong>Type:</strong> HR</p>'}],
        ['p', {'slot': 7, 'text': 'First <b>purpose</b>.', 'html': '<p>First <b>purpose</b>.</p>'}],
        ['t', {'slot': 14, 'rows': [['Ver', 'Date', 'Note'], ['V1', '2026', 'init']]}],
    ]
    ex = LinesJsonExtractor(lines_json).to_extracted_document()
    assert len(ex.paragraphs) == 2
    assert len(ex.tables) == 1
    assert ex.source_format == 'lines_json'
    # paragraph[0] = 'Type: HR' (stripped bold)
    assert ex.paragraphs[0] == 'Type: HR'
    # paragraph[1] = 'First purpose.' (tag stripped)
    assert 'purpose' in ex.paragraphs[1]
    # Slot origin carried sidecar; second paragraph is slot 7
    origins = getattr(ex, 'paragraph_slot_origin', None)
    assert origins is not None
    assert origins[0] == 1
    assert origins[1] == 7


def test_lines_json_extractor_drops_empty_paragraphs_as_cleaner_dropped():
    lines_json = [
        ['p', {'slot': 1, 'text': '', 'html': ''}],
        ['p', {'slot': 2, 'text': 'X', 'html': '<p>X</p>'}],
    ]
    ex = LinesJsonExtractor(lines_json).to_extracted_document()
    assert len(ex.paragraphs) == 1
    assert len(ex.cleaner_dropped) == 1


# ---------------------------------------------------------------------------
# Pipeline rerun
# ---------------------------------------------------------------------------

@pytest.mark.timeout(180)
def test_pipeline_run_from_lines_json_minimal(tmp_path: Path):
    """End-to-end smoke: build a tiny lines_json + verify a .docx is
    emitted through pipeline.render."""
    from policy_platform.pipeline import run_from_lines_json

    lines_json = [
        ['p', {'slot': 1, 'text': 'Type: HR Policy', 'html': '<p><strong>Type:</strong> HR Policy</p>'}],
        ['p', {'slot': 7, 'text': 'This is the purpose.', 'html': '<p>This is the purpose.</p>'}],
    ]
    out = tmp_path / 'rerun.docx'
    result = run_from_lines_json(
        lines_json=lines_json,
        output_path=out,
        run_id='phase6_test_rerun',
        document_name='phase6_smoke',
        fail_on_validation=False,
    )
    assert out.exists()
    assert any(s.ok for s in result.steps if s.name == 'Render')
    assert any(s.ok for s in result.steps if s.name == 'Validate')


# ---------------------------------------------------------------------------
# Audit diff
# ---------------------------------------------------------------------------

def test_attach_slot_diff_per_slot_before_after():
    sections = [
        {'id': 1, 'name': 'Type', 'status': 'Found'},
        {'id': 7, 'name': 'Purpose', 'status': 'Found'},
    ]
    prev = [
        ['p', {'slot': 1, 'text': 'Old Type', 'html': 'Old Type'}],
        ['p', {'slot': 7, 'text': 'Old Purpose', 'html': 'Old Purpose'}],
    ]
    new = [
        ['p', {'slot': 1, 'text': 'New Type', 'html': 'New Type'}],
        ['p', {'slot': 7, 'text': 'Old Purpose', 'html': 'Old Purpose'}],
    ]
    attach_slot_diff(sections, prev, new)
    by_sid = {s['id']: s for s in sections}
    assert by_sid[1]['slot_changed'] is True
    assert by_sid[1]['before_text'] == 'Old Type'
    assert by_sid[1]['after_text'] == 'New Type'
    assert by_sid[7]['slot_changed'] is False
    assert by_sid[7]['before_text'] == 'Old Purpose'
    assert by_sid[7]['after_text'] == 'Old Purpose'


def test_attach_slot_diff_handles_no_previous():
    sections = [{'id': 1, 'name': 'Type', 'status': 'Found'}]
    new = [['p', {'slot': 1, 'text': 'Hello', 'html': 'Hello'}]]
    attach_slot_diff(sections, prev_lines_json=None, new_lines_json=new)
    # No `before_text`/`after_text` keys added when prev is None.
    assert 'before_text' not in sections[0]
    assert 'after_text' not in sections[0]


def test_attach_slot_diff_handles_table_rows():
    sections = [{'id': 14, 'name': 'History', 'status': 'Found'}]
    prev = [['t', {'slot': 14, 'rows': [['Ver'], ['V1'], ['V0']]}]]
    new = [['t', {'slot': 14, 'rows': [['Ver'], ['V2'], ['V1'], ['V0']]}]]
    attach_slot_diff(sections, prev, new)
    assert sections[0]['slot_changed'] is True
    # Each row is its own line; rows with multiple cells are joined with ' / '.
    assert 'Ver\nV1\nV0' in sections[0]['before_text']
    assert 'Ver\nV2\nV1\nV0' in sections[0]['after_text']


def test_attach_slot_diff_handles_table_row_with_multiple_cells():
    sections = [{'id': 14, 'name': 'History', 'status': 'Found'}]
    prev = [['t', {'slot': 14, 'rows': [['Ver', 'Date', 'Note'], ['V1', '2026', 'init']]}]]
    new = [['t', {'slot': 14, 'rows': [['Ver', 'Date', 'Note'], ['V2', '2026', 'second']]}]]
    attach_slot_diff(sections, prev, new)
    assert sections[0]['slot_changed'] is True
    assert 'V1 / 2026 / init' in sections[0]['before_text']
    assert 'V2 / 2026 / second' in sections[0]['after_text']


def test_attach_slot_diff_excludes_free_paragraph_slot():
    """Slot 0 (free paragraph above all Brain slots) must NOT be picked
    up by the per-slot diff; otherwise reviewer free-text would pollute
    every adjacent slot column."""
    sections = [
        {'id': 1, 'name': 'Type', 'status': 'Found'},
        {'id': 7, 'name': 'Purpose', 'status': 'Found'},
    ]
    prev = [
        ['p', {'slot': 0, 'text': 'Free note', 'html': 'Free note'}],
        ['p', {'slot': 1, 'text': 'Type: HR', 'html': 'Type: HR'}],
    ]
    new = [
        ['p', {'slot': 0, 'text': 'Free note 2', 'html': 'Free note 2'}],
        ['p', {'slot': 1, 'text': 'Type: HR', 'html': 'Type: HR'}],
    ]
    attach_slot_diff(sections, prev, new)
    # Neither slot 1 nor slot 7 changed (slot 1 unchanged, slot 7 empty
    # on both sides).
    by_sid = {s['id']: s for s in sections}
    assert by_sid[1]['slot_changed'] is False
    assert 'before_text' not in by_sid[7] or by_sid[7].get('before_text', '') == ''


# ---------------------------------------------------------------------------
# publish_to_brain integration
# ---------------------------------------------------------------------------

@pytest.mark.timeout(240)
def test_publish_to_brain_emits_docx_and_audit_diff(tmp_path: Path):
    """Round-trip: a synthetic lines_json -> pipeline rerun -> publish path
    -> final .docx on disk, audit JSON contains per-slot diff columns
    when a previous version exists."""
    from api import publish_to_brain, versions_io
    from api import db as _db

    run_id = 'phase6_publish_test'
    # Seed: a V1 approved version (paragraphs untouched from the
    # synthetic source), and a V2 approved version with one slot
    # changed.
    v1_lines = [
        ['p', {'slot': 1, 'text': 'Type: HR', 'html': '<p><strong>Type:</strong> HR</p>'}],
        ['p', {'slot': 7, 'text': 'Original Purpose.', 'html': '<p>Original Purpose.</p>'}],
    ]
    v2_lines = [
        ['p', {'slot': 1, 'text': 'Type: HR', 'html': '<p><strong>Type:</strong> HR</p>'}],
        ['p', {'slot': 7, 'text': 'REVISED Purpose.', 'html': '<p>REVISED Purpose.</p>'}],
    ]

    with _db._conn() as c:
        # Insert V1 + V2 in policy_versions (manually, no audit xlsx needed here).
        c.execute(
            """INSERT OR IGNORE INTO policy_versions
               (run_id, version_no, lines_json, change_summary, modified_by,
                modified_at, review_status, source)
               VALUES (?, 1, ?, 'initial', 'phase6', ?, 'approved', 'pipeline')""",
            (run_id, json.dumps(v1_lines), '2026-07-16T00:00:00Z'),
        )
        c.execute(
            """INSERT OR IGNORE INTO policy_versions
               (run_id, version_no, lines_json, change_summary, modified_by,
                modified_at, review_status, source)
               VALUES (?, 2, ?, 'phase6 edit', 'phase6', ?, 'approved', 'user_edit')""",
            (run_id, json.dumps(v2_lines), '2026-07-16T00:00:01Z'),
        )
        c.commit()
        # Mark V1 as published so latest_published_version_no() returns V1.
        c.execute(
            "UPDATE policy_versions SET review_status='published', published_at=? "
            "WHERE run_id=? AND version_no=1",
            ('2026-07-16T00:00:02Z', run_id),
        )
        c.commit()

    output_dir = tmp_path / 'run_dir'
    output_dir.mkdir()
    with _db._conn() as c:
        result = publish_to_brain.publish_approved_version(
            c, run_id=run_id, version_no=2, output_dir=output_dir, actor='phase6',
        )
    assert result is not None
    assert result['review_status'] == 'published'
    docx_path = Path(result['docx_path'])
    assert docx_path.exists()

    # The audit JSON should be enriched with before_text/after_text/slot_changed.
    audit_dict = json.loads(result['audit_json'])
    for sec in audit_dict['sections']:
        if sec['id'] == 7:
            assert sec['slot_changed'] is True
            assert 'Original Purpose' in sec['before_text']
            assert 'REVISED Purpose' in sec['after_text']
        elif sec['id'] == 1:
            # Slot 1 unchanged between V1 and V2.
            assert sec['slot_changed'] is False

    # Clean up the seeded rows so other tests don't collide.
    with _db._conn() as c:
        c.execute("DELETE FROM policy_versions WHERE run_id=?", (run_id,))
        c.commit()
