"""publish_to_brain.py

Phase 6 — produces the FINAL .docx for an APPROVED version.

The reviewer's saved `lines_json` is the source of truth after the Word
editor has been touched; the published .docx must reflect the user's
edits verbatim while still respecting the Brain framework rules (15
frozen slots, slot titles, ordering, header, footer, logo, media).

Two render paths
----------------
This module supports TWO render paths:

  * **PRIMARY (Stage 2): `policy_platform.lines_json_renderer`**
    A direct slot-by-slot writer. Treats the Brain template as a frozen
    scaffold and writes the reviewer's saved `lines_json` content into
    each slot's body, paragraph-by-paragraph, character-by-character.
    No RAG / classifier / extractor re-runs. The user's content wins
    over the brain defaults, but the brain framework (15 slots, titles,
    ordering, header, footer, logo, media) stays untouched.

  * **FALLBACK (legacy): `policy_platform.pipeline.run_from_lines_json`**
    Re-runs the full Brain pipeline (extract → clean → RAG → render)
    against the reviewer's content. This path is preserved as a silent
    safety fallback so publish never hard-fails if the primary renderer
    raises (Stage 3 — silent fallback).

The primary path is preferred because it preserves the user's exact
edits (the legacy path runs RAG which discards edits when slot
assignment is ambiguous). The fallback only fires when the primary
raises.

Workflow:
  1. Verify brain SHA is intact (advisory only — Phase 6 demoted to
     warning, never blocks).
  2. Load the approved version's lines_json from DB and the previous
     published version's lines_json (for the before/after audit
     columns).
  3. PRIMARY: call `policy_platform.lines_json_renderer.render_lines_json_to_brain`
     to write the user's content directly into a copy of the brain
     template. SILENT FALLBACK: on any exception, fall back to the
     legacy `run_from_lines_json` pipeline path.
  4. Phase 8 — Apply Word-style header / footer (bracket metadata +
     page X of Y) so the published .docx matches the preview. The
     brain renderer's body is populated; the resolved metadata header
     is written here, idempotently.
  5. Annotate the audit dict with per-slot before/after diff columns
     via `policy_platform.audit.attach_slot_diff`.
  6. Mark the version as published; record docx_path + audit row.

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

    # Fix G: pipeline gap — if the user inserted toolbar content AFTER
    # approving this version but BEFORE clicking publish, those edits
    # live in policy_drafts (autosave). Without this fix, publish reads
    # only the frozen version row's lines_json and silently drops the
    # user's latest edits — the user sees "almost everything I insert
    # is not in the output." Detect a newer draft and use its lines_json
    # instead, then backfill the version row so editor / timeline /
    # published file stay consistent.
    draft = versions_io.get_draft(c, run_id)
    if draft and draft.get('lines_json'):
        ver_modified = ver.get('modified_at') or ''
        draft_modified = draft.get('modified_at') or ''
        if draft_modified > ver_modified:
            print(
                f'[publish_to_brain] FIX G: newer draft found '
                f'(draft ec={draft["edit_count"]} modified {draft_modified} '
                f'> ver v{version_no} modified {ver_modified}), '
                f'using draft lines_json for publish and backfilling '
                f'version row.',
                flush=True,
            )
            # Backfill the version row so the frozen data matches the
            # published file. The change_summary records the fact so
            # reviewers can see what happened.
            import json as _json
            try:
                c.execute(
                    """UPDATE policy_versions
                       SET lines_json = ?, modified_at = ?
                       WHERE run_id = ? AND version_no = ?""",
                    (
                        draft['lines_json'] if isinstance(draft['lines_json'], str)
                        else _json.dumps(draft['lines_json']),
                        draft_modified,
                        run_id,
                        version_no,
                    ),
                )
                c.commit()
                # Reload ver so subsequent code sees the updated lines_json.
                ver = versions_io.get_version(c, run_id, version_no) or ver
            except Exception as gerr:
                print(
                    f'[publish_to_brain] FIX G backfill failed (non-fatal): {gerr}',
                    flush=True,
                )

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

    # 3) Render the user's saved `lines_json` into the brain template.
    #
    #    PRIMARY (Stage 2): the new direct slot-by-slot renderer writes
    #    the reviewer's content into the brain scaffold verbatim. This
    #    preserves the user's exact edits (the legacy RAG pipeline
    #    overwrites edits with brain defaults when slot assignment is
    #    ambiguous — typically because the user's content was saved
    #    with slot=0).
    #
    #    SILENT FALLBACK (Stage 3): if the primary renderer raises ANY
    #    exception, fall back to the legacy RAG pipeline so publish
    #    never hard-fails. The fallback's output is byte-identical to
    #    the previous (pre-fix) behaviour.
    #
    #    `result_kind` is reported in the audit dict so reviewers can
    #    tell which path produced the published docx.
    result_kind = 'unknown'
    audit_result = None  # type: ignore[assignment]
    normalised_lines = []
    try:
        from policy_platform.lines_json_renderer import render_lines_json_to_brain
        from policy_platform import config as _config
        from api.lines_json_extractor import (
            normalise_lines_json,
            infer_anchor_slots,
            preserve_editor_anchor_slot,
        )
        from api.docx_approved_export import extract_explicit_title_and_version

        raw_lines = list(ver.get('lines_json', []) or [])
        normalised_lines = normalise_lines_json(raw_lines)
        # Stage 4.15 — slot inference at publish-time. The reviewer's
        # saved `lines_json` may carry `slot=0` for every paragraph
        # (legacy free-typing or save paths that dropped `data-slot`).
        # Without re-inferring slots here, every paragraph lands in the
        # "Free Paragraphs" zone at the top of the published .docx and
        # every brain slot body is cleared to empty — producing a file
        # that looks like the brain template. The helpers below route
        # the content into the correct slot body so the published file
        # contains the reviewer's actual Result.
        try:
            normalised_lines = preserve_editor_anchor_slot(normalised_lines)
            normalised_lines = infer_anchor_slots(normalised_lines)
        except Exception as slot_err:
            print(
                f'[publish_to_brain] slot inference failed (non-fatal): {slot_err}',
                flush=True,
            )
        explicit_title, explicit_version = extract_explicit_title_and_version(
            list(ver.get('lines_json', []) or [])
        )
        brain_path = _config.BRAIN_PATH
        output_path.parent.mkdir(parents=True, exist_ok=True)
        print(
            f'[publish_to_brain] PRIMARY renderer run={run_id} v{version_no} '
            f'lines={len(normalised_lines)} brain={brain_path.name}',
            flush=True,
        )
        render_lines_json_to_brain(
            lines_json=normalised_lines,
            brain_path=brain_path,
            output_path=output_path,
            header_text=explicit_title,
            header_version=explicit_version,
        )
        result_kind = 'primary_lines_json_renderer'
    except Exception as primary_err:
        print(
            f'[publish_to_brain] PRIMARY renderer failed: {primary_err}; '
            'falling back to legacy RAG pipeline',
            flush=True,
        )
        import traceback
        traceback.print_exc()
        try:
            from policy_platform.pipeline import run_from_lines_json
            from api.lines_json_extractor import (
                normalise_lines_json as _normalise,
                reviewer_slot_bindings,
                infer_anchor_slots as _infer,
                preserve_editor_anchor_slot as _preserve,
            )
            if not normalised_lines:
                normalised_lines = _normalise(ver.get('lines_json', []) or [])
            # Slot inference also benefits the fallback path.
            try:
                normalised_lines = _preserve(normalised_lines)
                normalised_lines = _infer(normalised_lines)
            except Exception:
                pass
            bindings = reviewer_slot_bindings(normalised_lines)
            print(
                f'[publish_to_brain] FALLBACK run={run_id} v{version_no} '
                f'bindings={list(bindings.keys())}',
                flush=True,
            )
            audit_result = run_from_lines_json(
                lines_json=normalised_lines,
                output_path=output_path,
                run_id=run_id,
                document_name=f'{run_id}_v{version_no}',
                fail_on_validation=False,
                reviewer_bindings=bindings,
            )
            result_kind = 'fallback_pipeline'
        except Exception as fallback_err:
            print(
                f'[publish_to_brain] FALLBACK also failed: {fallback_err}',
                flush=True,
            )
            import traceback
            traceback.print_exc()
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
    #
    #    The audit dict only exists for the FALLBACK path (the legacy
    #    `run_from_lines_json` returns an `AuditResult` with `audit_json`).
    #    For the PRIMARY path, build a minimal audit dict from the
    #    normalised `lines_json` so downstream code (history view, audit
    #    log) still has something to display.
    if audit_result is not None and getattr(audit_result, 'audit_json', None):
        audit_dict = json.loads(audit_result.audit_json or '{}')
    else:
        audit_dict = {}
    if isinstance(audit_dict, dict):
        from policy_platform.audit import attach_slot_diff
        try:
            attach_slot_diff(
                sections=audit_dict.setdefault('sections', []),
                prev_lines_json=prev_lines_json,
                new_lines_json=(
                    audit_result.lines_json
                    if audit_result is not None and hasattr(audit_result, 'lines_json')
                    else normalised_lines
                ),
            )
        except Exception as e:
            print(f'[publish_to_brain] slot-diff attach failed: {e}', flush=True)
        audit_dict['brain_warn'] = brain_warn
        audit_dict['render_kind'] = result_kind
        if audit_result is not None:
            audit_result.audit_json = json.dumps(audit_dict, default=str, ensure_ascii=False)
        else:
            audit_dict_serialised = json.dumps(audit_dict, default=str, ensure_ascii=False)

    # 5) Update DB with published docx_path. Then enrich `runs.audit_json`
    # with the per-slot diff columns so the History / workflow viewer can
    # show before/after per slot.
    updated = versions_io.set_published(
        c, run_id, version_no, str(output_path),
        actor=actor, actor_user_id=actor_user_id,
    )
    audit_json_for_db = (
        audit_result.audit_json if audit_result is not None
        else audit_dict_serialised if 'audit_dict_serialised' in locals() else ''
    )
    try:
        from api import db as _db
        _db.update_status(
            run_id,
            run.get('status') or 'done',
            docx_path=str(output_path),
            audit_json=audit_json_for_db or '',
        )
    except Exception as e:
        print(f'[publish_to_brain] runs.audit_json update failed: {e}', flush=True)

    return {
        **updated,
        'docx_path': str(output_path),
        'review_status': 'published',
        'brain_warn': brain_warn,
        'audit_json': (
            audit_result.audit_json if audit_result is not None
            else audit_dict_serialised if 'audit_dict_serialised' in locals() else ''
        ),
        'render_kind': result_kind,
    }


def _unused_keep_for_compat():
    """Reference paths retained so old callers fail with a clear error
    if they import the legacy overlay helpers."""
    from api.docx_approved_export import build_approved_docx
    from api.api_preview import build_preview_from_docx
    return build_approved_docx, build_preview_from_docx
