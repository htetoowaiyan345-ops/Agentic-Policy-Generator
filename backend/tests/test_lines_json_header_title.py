"""test_lines_json_header_title.py

Regression: the header's Policy Title must mirror the body's explicit
`Policy Title:` line 1:1, even when a long body sentence like
`APPLICABLE TO ALL SECTORS UNDER CITY HOLDINGS GROUP...` would
otherwise out-score the real title via the renderer's heuristic
length-based scoring.

The fix lives in two places:

1. `api.docx_approved_export.extract_explicit_title_and_version`
   pulls the explicit `Policy Title:` / `Policy Number:` from
   `lines_json` and `run_from_lines_json` threads them through as
   `header_text` / `header_version`.

2. `policy_platform.extractors.header_extractor._score_title` now
   rejects scope/audience-style sentences (`APPLICABLE TO`,
   `This policy supports...`) so the heuristic can't pick them
   when no explicit override is supplied.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


BRAIN_TEMPLATE = ROOT / 'data' / 'brain_template' / 'Policy_Framework_5.docx'


def test_extract_explicit_title_and_version_picks_policy_title_line():
    from api.docx_approved_export import extract_explicit_title_and_version

    lines = [
        ['p', {'slot': 0, 'text': 'Type: HR Policy',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0,
               'text': 'Policy Title: EarthQuake Emergency Assistance Policy',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Number: CL&H_03/26',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0,
               'text': 'APPLICABLE TO ALL SECTORS UNDER CITY HOLDINGS GROUP '
                       'AND ALL LOCAL EMPLOYEES AFFECTED BY EARTHQUAKE-RELATED '
                       'DISASTERS.',
               'html': '', 'footnotes': []}],
    ]
    title, version = extract_explicit_title_and_version(lines)
    assert title == 'EarthQuake Emergency Assistance Policy', (
        f"expected the explicit Policy Title, got {title!r}"
    )
    assert version == 'CL&H_03/26', (
        f"expected the explicit Policy Number, got {version!r}"
    )


def test_extract_explicit_title_and_version_returns_none_when_missing():
    from api.docx_approved_export import extract_explicit_title_and_version

    lines = [
        ['p', {'slot': 0, 'text': 'Type: HR Policy',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'This is just a body paragraph.',
               'html': '', 'footnotes': []}],
    ]
    title, version = extract_explicit_title_and_version(lines)
    assert title is None
    assert version is None


def test_extract_explicit_title_and_version_legacy_string_shape():
    from api.docx_approved_export import extract_explicit_title_and_version

    # Legacy: ['p', str] payload.
    lines = [
        ['p', 'Type: HR Policy'],
        ['p', 'Policy Title: Legacy Title From String Payload'],
        ['p', 'Policy Number: LEG-99'],
    ]
    title, version = extract_explicit_title_and_version(lines)
    assert title == 'Legacy Title From String Payload'
    assert version == 'LEG-99'


def test_header_extractor_rejects_scope_audience_lines():
    """The renderer's heuristic title-extractor must NOT pick
    `APPLICABLE TO ...` / `This policy supports ...` as the title even
    when it's the longest line on page 1."""
    from policy_platform.extractors.header_extractor import _score_title

    scope_line = (
        'APPLICABLE TO ALL SECTORS UNDER CITY HOLDINGS GROUP AND ALL '
        'LOCAL EMPLOYEES AFFECTED BY EARTHQUAKE-RELATED DISASTERS.'
    )
    real_title = 'EarthQuake Emergency Assistance Policy'

    assert _score_title(scope_line, position=0) < 0, (
        'scope/audience sentence should be rejected by the scorer'
    )
    assert _score_title(real_title, position=0) > 0, (
        'real title should be accepted by the scorer'
    )


def test_run_from_lines_json_header_mirrors_body_title(tmp_path: Path):
    """End-to-end: when `lines_json` contains an explicit
    `Policy Title:` line plus a long body sentence, the rendered
    output's header title MUST equal the explicit Policy Title — NOT
    the long body sentence."""
    if not BRAIN_TEMPLATE.exists():
        return
    from policy_platform.pipeline import run_from_lines_json
    from api.lines_json_extractor import normalise_lines_json

    explicit_title = 'EarthQuake Emergency Assistance Policy'
    explicit_number = 'CL&H_03/26'
    body_sentence = (
        'APPLICABLE TO ALL SECTORS UNDER CITY HOLDINGS GROUP AND ALL '
        'LOCAL EMPLOYEES AFFECTED BY EARTHQUAKE-RELATED DISASTERS.'
    )

    lines_json = [
        ['p', {'slot': 0, 'text': 'Type: HR Policy',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': f'Policy Title: {explicit_title}',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': f'Policy Number: {explicit_number}',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Effective Date/Period: 01 July 2026',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Approved by: The Board',
               'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': body_sentence,
               'html': body_sentence, 'footnotes': []}],
    ]

    out = tmp_path / 'regression.docx'
    run_from_lines_json(
        lines_json=normalise_lines_json(lines_json),
        output_path=out,
        run_id='regression_test',
        document_name='regression_test',
        fail_on_validation=False,
    )

    assert out.exists()
    with zipfile.ZipFile(out) as z:
        h2 = z.read('word/header2.xml').decode('utf-8', errors='replace')
        texts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', h2)
        joined = ''.join(t for t in texts)
        assert explicit_title in joined, (
            f'expected {explicit_title!r} in header2.xml; got {joined!r}'
        )
        assert body_sentence not in joined, (
            f'body sentence {body_sentence!r} leaked into header2.xml; got {joined!r}'
        )