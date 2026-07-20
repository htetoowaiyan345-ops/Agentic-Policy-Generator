"""Tests for audit_diff (Phase 6 — per-slot before/after diff)."""
from __future__ import annotations

from pathlib import Path

from api.audit_diff import (
    build_diff_rows,
    write_diff_xlsx,
    read_diff_xlsx,
)


def test_build_diff_rows_emits_one_row_per_slot_zero_through_fifteen():
    prev = []
    curr = []
    rows = build_diff_rows('run-1', 2, prev, curr)
    assert len(rows) == 16
    assert rows[0]['slot_id'] == 0
    assert rows[15]['slot_id'] == 15
    assert all(r['changed'] is False for r in rows)
    assert all(r['before_text'] == '' for r in rows)
    assert all(r['after_text'] == '' for r in rows)


def test_build_diff_rows_marks_changed_when_before_differs_from_after():
    """Use rich payloads with explicit slot 1 so the diff lands at slot 1."""
    prev = [['p', {'slot': 1, 'text': 'old-text-in-slot-1', 'html': '<p>old.</p>'}]]
    curr = [['p', {'slot': 1, 'text': 'new-text-in-slot-1', 'html': '<p>new.</p>'}]]
    rows = build_diff_rows('run-2', 2, prev, curr)
    by_slot = {r['slot_id']: r for r in rows}
    assert by_slot[1]['changed'] is True
    assert by_slot[1]['before_text'] == 'old-text-in-slot-1'
    assert by_slot[1]['after_text'] == 'new-text-in-slot-1'


def test_build_diff_rows_handles_string_payload_lines_json():
    import json
    prev = json.dumps([['p', 'one']])
    curr = json.dumps([['p', 'two']])
    rows = build_diff_rows('run-3', 2, prev, curr)
    by_slot = {r['slot_id']: r for r in rows}
    assert by_slot[0]['before_text'] == 'one'
    assert by_slot[0]['after_text'] == 'two'
    assert by_slot[0]['changed'] is True


def test_build_diff_rows_with_rich_payloads():
    prev = [['p', {'slot': 7, 'text': 'OLD', 'html': '<p>OLD</p>'}]]
    curr = [['p', {'slot': 7, 'text': 'NEW', 'html': '<p>NEW</p>'}]]
    rows = build_diff_rows('run-4', 2, prev, curr)
    by_slot = {r['slot_id']: r for r in rows}
    assert by_slot[7]['before_text'] == 'OLD'
    assert by_slot[7]['after_text'] == 'NEW'
    assert by_slot[7]['changed'] is True


def test_write_diff_xlsx_round_trip(tmp_path: Path):
    prev = [['p', 'before-text']]
    curr = [['p', 'after-text']]
    out = tmp_path / 'diff.xlsx'
    written = write_diff_xlsx('run-x', 2, prev, curr, out)
    assert written == 16
    assert out.exists()

    rows = read_diff_xlsx(out)
    assert len(rows) == 16
    headers = list(rows[0].keys())
    assert 'slot_id' in headers
    assert 'changed' in headers
    # Find row for slot 0 (where the difference is recorded).
    slot0 = next(r for r in rows if int(r['slot_id']) == 0)
    assert slot0['before_text'] == 'before-text'
    assert slot0['after_text'] == 'after-text'
    # changed may be the string 'True' or bool True.
    assert slot0['changed'] in (True, 'True', 'true', 1, '1')


def test_write_diff_xlsx_handles_no_previous_version(tmp_path: Path):
    out = tmp_path / 'no_prev.xlsx'
    written = write_diff_xlsx('run-y', 1, None, [['p', 'first']], out)
    assert written == 16
    rows = read_diff_xlsx(out)
    slot0 = next(r for r in rows if int(r['slot_id']) == 0)
    assert slot0['before_text'] == ''
    assert slot0['after_text'] == 'first'


def test_write_diff_xlsx_handles_no_current_version(tmp_path: Path):
    out = tmp_path / 'no_curr.xlsx'
    written = write_diff_xlsx('run-z', 2, None, None, out)
    assert written == 16
    rows = read_diff_xlsx(out)
    assert all(r['before_text'] == '' for r in rows)
    assert all(r['after_text'] == '' for r in rows)


def test_read_diff_xlsx_returns_empty_list_for_missing_file(tmp_path: Path):
    rows = read_diff_xlsx(tmp_path / 'nope.xlsx')
    assert rows == []


def test_write_diff_xlsx_handles_mixed_paragraphs_in_multiple_slots(tmp_path: Path):
    prev = [
        ['p', {'slot': 1, 'text': 'A1'}],
        ['p', {'slot': 2, 'text': 'A2'}],
    ]
    curr = [
        ['p', {'slot': 1, 'text': 'A1-updated'}],
        ['p', {'slot': 2, 'text': 'A2'}],
    ]
    out = tmp_path / 'diff2.xlsx'
    write_diff_xlsx('run-m', 2, prev, curr, out)
    rows = read_diff_xlsx(out)
    by_slot = {int(r['slot_id']): r for r in rows}
    assert by_slot[1]['before_text'] == 'A1'
    assert by_slot[1]['after_text'] == 'A1-updated'
    assert by_slot[1]['changed'] in (True, 'True', 'true', 1, '1')
    assert by_slot[2]['before_text'] == 'A2'
    assert by_slot[2]['after_text'] == 'A2'
    assert by_slot[2]['changed'] in (False, 'False', 'false', 0, '0')
