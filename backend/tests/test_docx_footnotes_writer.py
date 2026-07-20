"""Tests for docx_footnotes_writer (Phase 5).

Asserts that rich footnote payloads (paragraph.footnotes) round-trip
into:
  - word/footnotes.xml containing the correct footnote ids and bodies,
  - inline `<w:footnoteReference>` runs in word/document.xml,
  - the [Content_Types].xml override,
  - the document.xml.rels relationship,
  - preserved ordering via build_approved_docx.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List

from docx import Document
from docx.oxml.ns import qn

from api.docx_footnotes_writer import (
    _collect_footnotes,
    _build_footnotes_part,
    write_footnotes,
    collect_footnote_anchors_from_html,
)
from api.docx_approved_export import build_approved_docx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_docx_part(docx_path, part_name: str) -> str:
    with zipfile.ZipFile(str(docx_path)) as z:
        if part_name not in z.namelist():
            return ''
        return z.read(part_name).decode('utf-8')


def _build_source_with_n_paragraphs(docx_path: Path, n: int = 5) -> None:
    doc = Document()
    for _ in range(n):
        doc.add_paragraph('placeholder')
    doc.save(str(docx_path))


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------

def test_collect_footnotes_returns_unique_entries():
    paragraphs = [
        ['p', {'slot': 1, 'text': 'X', 'html': '<p>X</p>', 'footnotes': [
            {'id': 'a', 'body': 'First note'},
            {'id': 'a', 'body': 'First note'},  # duplicate by id+body
        ]}],
        ['p', {'slot': 2, 'text': 'Y', 'html': '<p>Y</p>', 'footnotes': [
            {'id': 'a', 'body': 'First note'},
            {'id': 'b', 'body': 'Different note'},
        ]}],
        ['p', {'slot': 3, 'text': 'Z', 'html': '<p>Z</p>'},],  # no footnotes key
    ]
    out = _collect_footnotes(paragraphs)
    assert len(out) == 2  # 'a::First note' once + 'b::Different note' once
    keys = {f['key'] for f in out}
    assert 'a::First note' in keys
    assert 'b::Different note' in keys


def test_collect_footnotes_skips_empty_id_or_body():
    paragraphs = [
        ['p', {'slot': 1, 'text': 'X', 'html': '<p>X</p>', 'footnotes': [
            {'id': '', 'body': 'no-id'},
            {'id': 'only-id', 'body': ''},
            {'id': 'good', 'body': 'good'},
        ]}],
    ]
    out = _collect_footnotes(paragraphs)
    assert len(out) == 1
    assert out[0]['key'] == 'good::good'


def test_build_footnotes_part_emits_separators():
    fn_xml, _id_map = _build_footnotes_part([])
    # default separator and continuation separator must always be present
    assert 'w:type="separator"' in fn_xml
    assert 'w:type="continuationSeparator"' in fn_xml
    assert 'w:id="0"' in fn_xml
    assert 'w:id="1"' in fn_xml


def test_build_footnotes_part_assigns_ids():
    fn_xml, id_map = _build_footnotes_part([
        {'key': 'a::Note A', 'orig_id': 'a', 'body': 'Note A'},
        {'key': 'b::Note B', 'orig_id': 'b', 'body': 'Note B'},
    ])
    # id_map should map composite keys to integer ids starting at 2
    assert id_map['a::Note A'] == 2
    assert id_map['b::Note B'] == 3
    # The XML must mention both new ids and bodies
    assert 'w:id="2"' in fn_xml
    assert 'w:id="3"' in fn_xml
    assert 'Note A' in fn_xml
    assert 'Note B' in fn_xml


def test_build_footnotes_part_escapes_xml_entities():
    fn_xml, _id = _build_footnotes_part([
        {'key': 'e::<&>', 'orig_id': 'e', 'body': 'Tom & Jerry <3'},
    ])
    assert 'Tom &amp; Jerry &lt;3' in fn_xml


def test_collect_footnote_anchors_from_html():
    html = (
        '<p>First<sup data-fn-id="fn1">1</sup> and '
        'second<sup data-fn-id="fn2">2</sup>.</p>'
    )
    anchors = collect_footnote_anchors_from_html(html)
    ids = [a[0] for a in anchors]
    assert ids == ['fn1', 'fn2']


def test_collect_footnote_anchors_from_html_handles_empty():
    assert collect_footnote_anchors_from_html('') == []
    assert collect_footnote_anchors_from_html('<p>plain</p>') == []


# ---------------------------------------------------------------------------
# Integration tests on .docx side-effects
# ---------------------------------------------------------------------------

def test_write_footnotes_creates_part_and_patches_ct_and_rels(tmp_path: Path):
    src = tmp_path / 'src.docx'
    out = tmp_path / 'out.docx'
    _build_source_with_n_paragraphs(src, n=3)
    build_approved_docx(
        src,
        [
            ['p', {'slot': 1, 'text': 'X', 'html': '<p>X</p>', 'footnotes': [
                {'id': 'noteA', 'body': 'First note'},
                {'id': 'noteB', 'body': 'Second note'},
            ]}],
            ['p', 'plain'],
            ['p', 'plain'],
        ],
        str(out),
    )
    # footnotes.xml part exists
    fn_xml = _read_docx_part(out, 'word/footnotes.xml')
    assert 'First note' in fn_xml
    assert 'Second note' in fn_xml
    # Content-Types patched
    ct_xml = _read_docx_part(out, '[Content_Types].xml')
    assert 'wordprocessingml.footnotes+xml' in ct_xml
    # document.xml.rels patched
    rels_xml = _read_docx_part(out, 'word/_rels/document.xml.rels')
    assert 'footnotes.xml' in rels_xml
    assert 'officeDocument/2006/relationships/footnotes' in rels_xml


def test_write_footnotes_emits_inline_reference_runs_in_document_xml(tmp_path: Path):
    src = tmp_path / 'src.docx'
    out = tmp_path / 'out.docx'
    _build_source_with_n_paragraphs(src, n=1)
    build_approved_docx(
        src,
        [
            ['p', {
                'slot': 1,
                'text': 'Body',
                'html': '<p>Body<sup data-fn-id="noteA">1</sup></p>',
                'footnotes': [{'id': 'noteA', 'body': 'first note'}],
            }],
        ],
        str(out),
    )
    doc_xml = _read_docx_part(out, 'word/document.xml')
    # An inline <w:footnoteReference w:id="2"/> is expected (id=2 because
    # the writer reserves 0=separator, 1=continuation, 2+=user).
    assert '<w:footnoteReference' in doc_xml
    assert 'w:id="2"' in doc_xml


def test_write_footnotes_idempotent_on_empty_footnotes(tmp_path: Path):
    """If no paragraphs carry footnotes, word/footnotes.xml must NOT be
    created (no orphan part with only separators)."""
    src = tmp_path / 'src.docx'
    out = tmp_path / 'out.docx'
    _build_source_with_n_paragraphs(src, n=2)
    build_approved_docx(
        src,
        [
            ['p', 'plain-1'],
            ['p', {'slot': 1, 'text': 'X', 'html': '<p>X</p>'}],
        ],
        str(out),
    )
    with zipfile.ZipFile(str(out)) as z:
        assert 'word/footnotes.xml' not in z.namelist()
    # Content-Types unchanged
    ct_xml = _read_docx_part(out, '[Content_Types].xml')
    assert 'wordprocessingml.footnotes' not in ct_xml


def test_write_footnotes_unique_dedup_works_across_slots(tmp_path: Path):
    """Two paragraphs sharing the same (id, body) reuse one Word footnote."""
    src = tmp_path / 'src.docx'
    out = tmp_path / 'out.docx'
    _build_source_with_n_paragraphs(src, n=3)
    build_approved_docx(
        src,
        [
            ['p', {'slot': 1, 'text': 'A', 'html': '<p>A</p>', 'footnotes': [
                {'id': 'same', 'body': 'shared note'},
            ]}],
            ['p', {'slot': 2, 'text': 'B', 'html': '<p>B</p>', 'footnotes': [
                {'id': 'same', 'body': 'shared note'},  # same key
            ]}],
            ['p', {'slot': 3, 'text': 'C', 'html': '<p>C</p>', 'footnotes': [
                {'id': 'diff', 'body': 'second note'},
            ]}],
        ],
        str(out),
    )
    fn_xml = _read_docx_part(out, 'word/footnotes.xml')
    # Only two user footnotes (id=2 and id=3); not three.
    user_ids = re.findall(r'<w:footnote w:id="(\d+)">', fn_xml)
    user_ids = [i for i in user_ids if i not in ('0', '1')]
    assert sorted(user_ids) == ['2', '3']
    # The shared note appears once.
    assert fn_xml.count('shared note') == 1


def test_footnote_anchors_position_in_paragraph_text(tmp_path: Path):
    """Placement of <w:footnoteReference> must lie inside the text run,
    not before/after all runs."""
    src = tmp_path / 'src.docx'
    out = tmp_path / 'out.docx'
    _build_source_with_n_paragraphs(src, n=1)
    build_approved_docx(
        src,
        [
            ['p', {
                'slot': 1,
                'text': 'before-after',
                'html': '<p>before<sup data-fn-id="noteX">X</sup>after</p>',
                'footnotes': [{'id': 'noteX', 'body': 'inline note'}],
            }],
        ],
        str(out),
    )
    doc_xml = _read_docx_part(out, 'word/document.xml')
    # order matters: <w:t>before</w:t> must come before <w:footnoteReference>
    before_idx = doc_xml.find('before')
    after_idx = doc_xml.find('after')
    ref_idx = doc_xml.find('<w:footnoteReference')
    assert before_idx < ref_idx < after_idx
