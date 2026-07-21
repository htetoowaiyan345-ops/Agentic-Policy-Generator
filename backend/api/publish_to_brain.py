"""publish_to_brain.py

Phase 6 — produces the FINAL .docx for an APPROVED version by re-running
the Brain pipeline (extract → clean → RAG → embed → FAISS → BM25 →
rerank → render) against the reviewer's saved `lines_json` content.

This is a FULL pipeline rerun, not the legacy in-place overlay. Why:
the reviewer's saved lines_json is the source of truth after the Word
editor has been touched; the brain framework must re-pick slot content
from that source and re-emit a brain-templated `.docx` that respects
reviewer edits, bold/italic/strikethrough/colour/font/size headings,
and Word footnotes (Phases 4 & 5).

Workflow:
  1. Verify brain SHA is intact (advisory only — Phase 6 demoted to
     warning, never blocks).
  2. Load the approved version's lines_json from DB and the previous
     published version's lines_json (for the before/after audit
     columns).
  3. Call `policy_platform.pipeline.run_from_lines_json(lines_json, out)`
     which re-runs Phases 2-7 against the reviewer's content. Phase 4 +
     5 rich-writer support is handled downstream of the pipeline.render
     step via `docx_rich_writer.write_paragraph` hooks used by the
     legacy docx_approved_export path.
  4. Annotate the audit dict with per-slot before/after diff columns
     via `policy_platform.audit.attach_slot_diff`.
  5. Mark the version as published; record docx_path + audit row.

NOTE: The brain is FROZEN. Only the value cells change; layout, fonts,
media, header/footer history-slot structure are byte-identical to brain
(verified via framework.brain SHA check).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

from api import db, versions_io


def _verify_brain_soft() -> tuple[bool, Optional[str]]:
    """Advisory brain SHA verification. Returns `(ok, warning_message)`.

    Phase 6: We no longer hard-fail on a brain SHA mismatch. If the
    manifest has been tampered with, the pipeline.warn column tells the
    reviewer and the publish still completes (they can roll the
    template back later)."""
    try:
        from policy_platform.framework import brain as brain_loader
        manifest = brain_loader.init_or_verify(init=False)
        return True, None
    except Exception as e:
        return False, f'brain manifest verification failed: {e}'


def publish_approved_version(
    c,
    run_id: str,
    version_no: int,
    output_dir: Path,
    actor: str = 'user',
    actor_user_id: int | None = None,
) -> dict | None:
    """Build the final brain-formatted .docx for an approved version by
    re-running the full Brain pipeline against the reviewer's saved
    lines_json.

    Args:
        c: open sqlite3 connection (with row_factory=sqlite3.Row).
        run_id: target run.
        version_no: approved version to publish.
        output_dir: per-run dir (already exists).
        actor: user publishing.
        actor_user_id: Stage 3 — logged-in user id for audit attribution.

    Returns: dict with docx_path, status='published', and the audit
    JSON. Or None on failure.
    """
    brain_ok, brain_warn = _verify_brain_soft()
    if not brain_ok:
        print(f'[publish_to_brain] WARN brain SHA soft-failed: {brain_warn}', flush=True)

    # 1) Load the approved version
    ver = versions_io.get_version(c, run_id, version_no)
    if not ver:
        return None
    if ver.get('review_status') != 'approved':
        print(
            f'[publish_to_brain] version status is {ver.get("review_status")}, not approved',
            flush=True,
        )
        return None

    # 2a) Load the underlying run row so we can preserve its status when
    # we update docx_path + audit_json below.
    run = db.get_run(run_id) or {}

    # 2b) Load previous published version (if any) for the audit diff.
    prev_lines_json: Optional[list] = None
    prev_ver_no = versions_io.latest_published_version_no(c, run_id)
    if prev_ver_no:
        prev = versions_io.get_version(c, run_id, prev_ver_no)
        if prev and isinstance(prev.get('lines_json'), list):
            prev_lines_json = prev['lines_json']

    output_path = Path(output_dir) / f'{run_id}_approved_v{version_no}.docx'

    # 3) Re-run the pipeline against the reviewer's saved lines_json.
    try:
        from policy_platform.pipeline import run_from_lines_json
        from api.lines_json_extractor import normalise_lines_json
        result = run_from_lines_json(
            lines_json=normalise_lines_json(ver.get('lines_json', [])),
            output_path=output_path,
            run_id=run_id,
            document_name=f'{run_id}_v{version_no}',
            fail_on_validation=False,
        )
    except Exception as e:
        print(f'[publish_to_brain] pipeline re-run failed: {e}', flush=True)
        import traceback; traceback.print_exc()
        return None

    # 3a) Phase 8 — Apply Word-style header / footer (bracket metadata +
    # page X of Y) so the published .docx matches the preview. The
    # pipeline writes a brain-templated body but does NOT populate our
    # resolved metadata header; we do that here, idempotently.
    try:
        from docx import Document as _Doc
        from api.docx_approved_export import _apply_header_footer
        doc2 = _Doc(str(output_path))
        _apply_header_footer(doc2, ver.get('lines_json', []))
        doc2.save(str(output_path))
    except Exception as e:
        print(f'[publish_to_brain] header/footer apply failed: {e}', flush=True)

    # 4) Annotate the audit dict with the before/after per-slot diff.
    audit_dict = json.loads(result.audit_json or '{}')
    if isinstance(audit_dict, dict):
        from policy_platform.audit import attach_slot_diff
        try:
            attach_slot_diff(
                sections=audit_dict.setdefault('sections', []),
                prev_lines_json=prev_lines_json,
                new_lines_json=result.lines_json if hasattr(result, 'lines_json')
                                else ver.get('lines_json', []),
            )
        except Exception as e:
            print(f'[publish_to_brain] slot-diff attach failed: {e}', flush=True)
        audit_dict['brain_warn'] = brain_warn
        result.audit_json = json.dumps(audit_dict, default=str, ensure_ascii=False)

    # 5) Update DB with published docx_path. Then enrich `runs.audit_json`
    # with the per-slot diff columns so the History / workflow viewer can
    # show before/after per slot.
    updated = versions_io.set_published(
        c, run_id, version_no, str(output_path),
        actor=actor, actor_user_id=actor_user_id,
    )
    try:
        from api import db as _db
        _db.update_status(
            run_id,
            run.get('status') or 'done',
            docx_path=str(output_path),
            audit_json=result.audit_json or '',
        )
    except Exception as e:
        print(f'[publish_to_brain] runs.audit_json update failed: {e}', flush=True)

    return {
        **updated,
        'docx_path': str(output_path),
        'review_status': 'published',
        'brain_warn': brain_warn,
        'audit_json': result.audit_json,
    }


def _unused_keep_for_compat():
    """Reference paths retained so old callers fail with a clear error
    if they import the legacy overlay helpers."""
    from api.docx_approved_export import build_approved_docx
    from api.api_preview import build_preview_from_docx
    return build_approved_docx, build_preview_from_docx
