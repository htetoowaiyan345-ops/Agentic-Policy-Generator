"""Tests for lines_json_extractor (Phase 6 — publish-rerun helper)."""
from __future__ import annotations

from api.lines_json_extractor import (
    extract_from_lines_json,
    slot_label,
)


# ---------------------------------------------------------------------------
# extract_from_lines_json
# ---------------------------------------------------------------------------

def test_extract_from_empty_lines_json():
    extracted = extract_from_lines_json([], run_id='abc')
    assert extracted.paragraphs == []
    assert extracted.tables == []
    assert extracted.source_format == 'lines_json'
    assert extracted.source_sha256  # non-empty
    assert slot_label(0) == 'Free Paragraph'


def test_extract_legacy_p_string_shape():
    extracted = extract_from_lines_json(
        [['p', 'Hello'], ['p', 'World']], run_id='r1'
    )
    assert extracted.paragraphs == ['Hello', 'World']
    assert extracted.tables == []
    assert extracted.original_indices == [0, 1]
    assert all(o is None for o in extracted.paragraph_table_origin)


def test_extract_rich_p_dict_shape():
    extracted = extract_from_lines_json(
        [
            ['p', {'slot': 7, 'text': 'Hello world', 'html': '<p>Hello</p>'}],
            ['p', {'slot': 8, 'text': 'Goodbye', 'html': '<p>Bye</p>'}],
        ],
        run_id='r2',
    )
    assert extracted.paragraphs == ['Hello world', 'Goodbye']


def test_extract_mixed_legacy_and_rich():
    extracted = extract_from_lines_json(
        [
            ['p', 'legacy-1'],
            ['p', {'slot': 1, 'text': 'rich-1'}],
            ['p', 'legacy-2'],
        ],
        run_id='r3',
    )
    assert extracted.paragraphs == ['legacy-1', 'rich-1', 'legacy-2']


def test_extract_table_rows_legacy():
    extracted = extract_from_lines_json(
        [
            ['p', 'before'],
            ['t', [['h1', 'h2'], ['a', 'b']]],
            ['p', 'after'],
        ],
        run_id='r4',
    )
    assert extracted.paragraphs == ['before', 'after']
    assert len(extracted.tables) == 1
    assert extracted.tables[0] == [['h1', 'h2'], ['a', 'b']]
    assert extracted.table_paragraph_indices == [1]


def test_extract_table_rows_rich_with_dict_cells():
    extracted = extract_from_lines_json(
        [
            ['t', {
                'slot': 14,
                'rows': [
                    [{'text': 'X', 'html': '<b>X</b>'}, {'text': 'Y', 'html': '<i>Y</i>'}],
                    [{'text': 'P', 'html': ''}, {'text': 'Q', 'html': ''}],
                ],
            }],
        ],
        run_id='r5',
    )
    # `tables` is List[List[List[str]]]: a list of tables, each a list of rows.
    assert extracted.tables == [[['X', 'Y'], ['P', 'Q']]]
    assert extracted.table_paragraph_indices == [0]  # table preceded no paragraphs


def test_extract_invalid_lines_are_skipped():
    extracted = extract_from_lines_json(
        ['garbage', None, [], ['only-one'], ['p', 'good'], ['p', None]],
        run_id='r6',
    )
    # 'garbage' is a string, not a list — skipped. None skipped. [] skipped.
    # ['only-one'] length 1 — skipped. ['p', 'good'] kept. ['p', None] kept ('').
    assert extracted.paragraphs == ['good', '']


def test_extract_includes_paragraph_table_origin_unset_for_p_lines():
    extracted = extract_from_lines_json(
        [['p', 'x'], ['p', 'y']],
        run_id='r7',
    )
    assert extracted.paragraph_table_origin == [None, None]
