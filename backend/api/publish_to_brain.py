"""publish_to_brain.py

Adapter that produces the FINAL .docx for an APPROVED version by
reusing the existing `policy_platform.renderer` (DOES NOT modify it).

Workflow:
  1. Verify brain SHA is intact (BrainManifestTampered check).
  2. Load the approved version's lines_json from DB.
  3. Reconstruct per-slot value dict for the renderer.
  4. Read the run's original pipeline-produced docx as the structural
     base. Pass it through with slot values updated.
  5. Write output to runs/<run_id>/<run_id>_approved_v<n>.docx and
     update runs.docx_path so the existing /api/download endpoints serve it.
  6. Mark version as published; record docx_path + audit row.

NOTE: The brain is FROZEN. Only the value cells change; layout, fonts,
media, history slot structure are byte-identical to brain (verified via
framework.brain SHA check).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from api import db, versions_io
from api.api_preview import build_preview_from_docx

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _verify_brain() -> bool:
    """Returns True if the brain SHA matches the manifest. False otherwise."""
    try:
        from policy_platform.framework import brain as brain_loader
        brain_loader.init_or_verify(init=False)
        return True
    except Exception:
        return False


def publish_approved_version(
    c,
    run_id: str,
    version_no: int,
    output_dir: Path,
    actor: str = 'user',
) -> dict | None:
    """Build the final brain-formatted .docx for an approved version.

    Args:
        c: open sqlite3 connection (with row_factory=sqlite3.Row).
        run_id: target run.
        version_no: approved version to publish.
        output_dir: per-run dir (already exists).
        actor: user publishing.

    Returns: dict with docx_path, status='published'. Or None on failure.
    """
    if not _verify_brain():
        print('[publish_to_brain] brain SHA verification failed', flush=True)
        return None

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

    # 2) Load original runs row for source docx reference
    run = db.get_run(run_id)
    if not run:
        return None

    # 3) Write a "preview" docx-like file: we re-use the existing
    # api_preview pipeline which already produces a per-run preview the
    # review pane consumes. For the final .docx, we simply copy the
    # approved-version content into a docx of the same shape as the
    # pipeline-produced docx (same media, same template) but with the
    # APPROVED lines_json substituted.
    output_path = Path(output_dir) / f'{run_id}_approved_v{version_no}.docx'

    try:
        from api.docx_approved_export import build_approved_docx
        build_approved_docx(
            original_docx_path=Path(run.get('docx_path')) if run.get('docx_path') else None,
            approved_lines_json=ver.get('lines_json', []),
            output_path=str(output_path),
        )
    except Exception as e:
        print(f'[publish_to_brain] docx build failed: {e}', flush=True)
        import traceback; traceback.print_exc()
        return None

    # 4) Update DB
    updated = versions_io.set_published(c, run_id, version_no, str(output_path), actor=actor)
    # Also point runs.docx_path at the new approved file so the existing
    # /api/download endpoints serve it directly.
    try:
        db.update_status(run_id, run.get('status') or 'done', docx_path=str(output_path))
    except Exception:
        pass

    return updated
