"""lines_json_to_source.py

Phase 6 — convert the reviewer's saved `lines_json` into a plain-text
synthetic source file that `pipeline.process()` can re-run against.

Why a temp .txt? `pipeline.process()` takes a `Path` and dispatches on
file extension (.pdf / .docx / .txt). Our reviewer-edited content lives
in memory as a rich payload, so we render it back into a deterministic
.txt (slot labels + paragraph text), drop it in a temp file, and feed
it to `pipeline.process()` for a full brain-framework rerun.

This preserves the invariant:

    "Save -> Publish triggers full pipeline rerun"

because every publish re-runs Phases R, 1, 2, 3, 4, 5, 6, 7 against the
reviewer's content.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List, Optional


# Slot-id -> section heading (mirrors the Brain template style).
_SLOT_HEADINGS = {
    1: "Type",
    2: "Brief Description",
    3: "Approval & Effective",
    4: "Reason for Policy",
    5: "Introduction",
    6: "POLICY STATEMENT",
    7: "1. Purpose",
    8: "2. Scope & Beneficiaries",
    9: "3. Exclusions",
    10: "4. Award Structure & Payout Tiers",
    11: "5. Procedural & Compliance",
    12: "DEFINITIONS",
    13: "RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES",
    14: "POLICY REVIEW NOTE",
    15: "HISTORY",
}


def _coerce_paragraph_text(payload) -> str:
    if isinstance(payload, dict):
        return str(payload.get('text') or '')
    if payload is None:
        return ''
    return str(payload)


def _render_slot_block(slot_id: int, paragraphs: List[str]) -> List[str]:
    """Render one slot as: heading + paragraphs (one per line)."""
    out: List[str] = []
    heading = _SLOT_HEADINGS.get(slot_id)
    if heading:
        out.append(heading)
    for p in paragraphs:
        out.append(p)
    return out


def lines_json_to_source_text(lines_json) -> str:
    """Render a lines_json payload as a deterministic .txt corpus.

    Slot 0 (free paragraph) is preserved at the top; slots 1..15 are
    emitted in numeric order so the resulting source roughly matches
    the Brain framework's expected ordering.
    """
    paragraphs_by_slot: dict = {}
    for line in lines_json or []:
        if not isinstance(line, list) or len(line) != 2:
            continue
        kind, payload = line[0], line[1]
        if kind != 'p':
            continue
        if isinstance(payload, dict):
            slot = int(payload.get('slot', 0) or 0)
            text = _coerce_paragraph_text(payload)
        else:
            slot = 0
            text = _coerce_paragraph_text(payload)
        paragraphs_by_slot.setdefault(slot, []).append(text)

    out_lines: List[str] = []
    for slot_id in range(0, 16):
        if slot_id in paragraphs_by_slot:
            out_lines.extend(_render_slot_block(
                slot_id, paragraphs_by_slot[slot_id]
            ))
    return "\n".join(out_lines)


def write_lines_json_as_tempfile(lines_json, run_id: Optional[str] = None) -> Path:
    """Write the lines_json as a temp .txt file and return its Path.

    Caller must delete the file when done (use `tempfile.cleanup()`
    or `pathlib.Path.unlink()`).
    """
    body = lines_json_to_source_text(lines_json)
    prefix = (run_id or 'lines_json') + '_'
    fd, name = tempfile.mkstemp(prefix=prefix, suffix='.txt')
    import os
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(body)
    return Path(name)


def cleanup_tempfile(path: Path) -> None:
    """Best-effort delete of a temp source file."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
