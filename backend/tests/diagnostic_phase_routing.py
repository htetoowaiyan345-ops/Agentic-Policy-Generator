"""
diagnostic_phase_routing.py
===========================

Read-only diagnostic for routing/classification.

For each sample PDF in backend/data/samples/, this script will:
  1. Run the same extraction+cleaning pipeline that production uses.
  2. Run field_parser.parse() and report last_extraction_path().
  3. Run analyzer.analyze() and print, per slot:
       - routing_rule
       - status (Found / Skeleton / etc.)
       - how many source paragraphs landed in this slot
       - the first ~3 text lines of the slot body (or (empty))
       - whether the slot would emit the 'Data is not found in source file' marker
  4. Print dropped_paragraph_indices count + the first ~10 dropped lines.

It does NOT:
  - Write any .docx
  - Touch the SQLite DB
  - Modify the Brain template
  - Call /api/process or any HTTP endpoint
  - Make any filesystem writes

Stdout only. Read-only against backend/data/samples/*.pdf.

Usage:
    python diagnostic_phase_routing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# this script lives at backend/tests/. Its package backend/ is its
# direct parent directory.
HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from policy_platform.extractors import dispatch as extractor_dispatch  # type: ignore
from policy_platform.extractors.cleaner import _is_proper_name  # for header-repeat context
from policy_platform.extractors import field_parser  # type: ignore
from policy_platform import analyzer  # type: ignore
from policy_platform.framework.section_map import FROZEN_SECTIONS  # type: ignore


SAMPLES_DIR = BACKEND_DIR / "data" / "samples"
MARKER_TEXT = "Data is not found in source file"


def _preview(text: str, limit: int = 110) -> str:
    s = text.replace("\n", " ").replace("\r", " ").strip()
    if len(s) > limit:
        return s[:limit] + "..."
    return s


def _looks_labelish(text: str) -> str:
    s = text.strip()
    if not s:
        return "blank"
    if len(s) <= 80 and ":" in s:
        return "labelish"
    if _is_proper_name(s):
        return "proper-name"
    if any(s.lower().startswith(k) for k in (
        "i.", "ii.", "iii.", "iv.", "v.", "vi.",
        "introduction", "purpose", "scope", "exclusions",
        "definitions", "history", "policy statement", "related",
    )):
        return "heading-ish"
    return "prose"


def _slot_will_render_marker(sid: int, slot) -> tuple[bool, str]:
    """Return (will_emit_marker, reason).

    A slot will emit the marker if it has body content but ALL of it
    appears to be the marker line, OR if it is required (Tier 1/2) and
    has empty body. For tier-3 slots with empty body the renderer
    leaves it blank without a marker.
    """
    from policy_platform.framework.slot_tiers import SLOT_TIERS
    body = getattr(slot, "content_paragraphs", []) or []
    tables = getattr(slot, "content_tables", []) or []
    status = getattr(slot, "status", "?")
    rule = getattr(slot, "routing_rule", "?")
    tier = SLOT_TIERS.get(sid, 3)

    has_real_body = any(p.strip() and MARKER_TEXT not in p for p in body)
    all_marker = bool(body) and all(MARKER_TEXT in p for p in body)

    if all_marker:
        return True, f"all body is marker (rule={rule})"

    # Mirror pipeline.py:106
    has_real_content = (status != "Found") and (not body or not any(p.strip() for p in body))
    placeholder_rendered = bool(slot_required_safe(sid) and (status != "Found") and has_real_content)
    if placeholder_rendered:
        return True, f"tier={tier} required + status={status} + empty body"

    if tier == 3 and not body and not tables:
        # Tier 3 with no body is "blank", not a marker.
        return False, "tier-3 blank (no marker)"

    return False, f"tier={tier} status={status} body_lines={len(body)} tables={len(tables)}"


def slot_required_safe(sid: int) -> bool:
    from policy_platform.framework.slot_tiers import SLOT_TIERS
    return SLOT_TIERS.get(sid, 3) <= 2


def _dump_paragraph_index(paragraphs: list[str], indices: list[int], heading: str, limit: int = 12) -> None:
    if not indices:
        return
    print(f"   {heading} (count={len(indices)}):")
    for i in indices[:limit]:
        idx = i if isinstance(i, int) else int(i[0])
        if 0 <= idx < len(paragraphs):
            label = _looks_labelish(paragraphs[idx])
            print(f"      [{idx:3d}] ({label:12s}) {_preview(paragraphs[idx])}")
    if len(indices) > limit:
        print(f"      ... ({len(indices) - limit} more)")


def run_one(path: Path) -> None:
    print("=" * 80)
    print(f"FILE: {path.name}")
    print("=" * 80)

    # 1. Extract + clean (production path)
    try:
        extracted = extractor_dispatch(path)
    except Exception as e:
        print(f"  EXTRACT FAILED: {type(e).__name__}: {e}")
        print()
        return

    print(f"extracted : source_format={extracted.source_format}  sha={extracted.source_sha256[:12]}")
    print(f"            paragraphs={len(extracted.paragraphs)} (raw before clean was higher)")
    print(f"            tables={len(extracted.tables)}")
    print(f"            cleaner_dropped={len(extracted.cleaner_dropped)}")
    print(f"            original_indices_aln={'yes' if hasattr(extracted, 'original_indices') else 'no'}")

    # 2. Field parser
    fm = field_parser.parse(
        extracted.paragraphs,
        dropped_paragraphs=getattr(extracted, "cleaner_dropped", None),
        cleaned_to_original=getattr(extracted, "original_indices", None),
    )
    path_used = field_parser.last_extraction_path()
    print(f"parser    : path={path_used}  labels_found={len(fm)}")
    if fm:
        for k, v in list(fm.items())[:8]:
            print(f"            {k!r:42s} -> {_preview(v, 80)!r}")
        if len(fm) > 8:
            print(f"            ... ({len(fm) - 8} more)")

    # 3. Analyze
    cls = analyzer.analyze(extracted)

    # 4. Per-slot summary
    print("slots     :")
    for sec in FROZEN_SECTIONS:
        sid = sec["id"]
        slot = cls.sections.get(sid)
        if slot is None:
            print(f"   [{sid:2d}] {sec['title'][:40]:42s} NO_SLOT")
            continue
        body = getattr(slot, "content_paragraphs", []) or []
        tables = getattr(slot, "content_tables", []) or []
        rule = getattr(slot, "routing_rule", "?")
        status = getattr(slot, "status", "?")
        will_marker, marker_reason = _slot_will_render_marker(sid, slot)
        marker_flag = "MARKER" if will_marker else "ok     "

        title = sec["title"][:40]
        print(f"   [{sid:2d}] {title:42s}  rule={rule!r:24s}  status={status!r:10s}  body={len(body):3d}  tables={len(tables):2d}  [{marker_flag}]")
        if will_marker:
            print(f"          -> marker reason: {marker_reason}")
        if body:
            for i, line in enumerate(body[:3]):
                print(f"          body[{i}]: {_preview(line, 110)!r}")
            if len(body) > 3:
                print(f"          ... {len(body) - 3} more lines")
        if tables:
            print(f"          tables[0] (first row): {_preview(' | '.join(tables[0][0]), 110)!r}")

    # 5. Source-index routing attribution
    print("routing   :")
    for sid in sorted(cls.routing_source_indices):
        idxs = cls.routing_source_indices[sid]
        _dump_paragraph_index(extracted.paragraphs, idxs, f"slot {sid} -> paragraphs", limit=6)

    for sid in sorted(cls.routing_table_indices):
        tidxs = cls.routing_table_indices[sid]
        if not tidxs:
            continue
        print(f"   slot {sid} -> tables[0..{len(tidxs)-1}] ({len(tidxs)} table(s))")
        for ti in tidxs:
            if 0 <= ti < len(extracted.tables):
                head = extracted.tables[ti][0] if extracted.tables[ti] else []
                print(f"            table[{ti}].header = {_preview(' | '.join(head), 100)!r}")

    # 6. Dropped
    dropped = cls.dropped_paragraph_indices or []
    if dropped:
        print(f"dropped   : {len(dropped)} paragraphs not routed anywhere")
        _dump_paragraph_index(extracted.paragraphs, dropped, "dropped", limit=10)
    else:
        print("dropped   : 0 paragraphs")

    # 7. Cleaner-dropped (different from analyzer-dropped; informational)
    if extracted.cleaner_dropped:
        reasons = {}
        for d in extracted.cleaner_dropped:
            r = d.get("reason", "?")
            reasons[r] = reasons.get(r, 0) + 1
        print(f"cleaner   : dropped {len(extracted.cleaner_dropped)} lines by reason: {reasons}")

    print()


def main() -> None:
    if not SAMPLES_DIR.exists():
        print(f"samples dir not found: {SAMPLES_DIR}", file=sys.stderr)
        sys.exit(1)
    paths = sorted(p for p in SAMPLES_DIR.glob("*.pdf"))
    if not paths:
        print("no PDFs in samples/", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(paths)} PDF(s) in {SAMPLES_DIR}")
    print()
    for p in paths:
        run_one(p)


if __name__ == "__main__":
    main()
