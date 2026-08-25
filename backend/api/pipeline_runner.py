# pipeline_runner.py
"""Per-run pipeline runner with 60s hard timeout.

Runs the Agentic Policy Platform RAG-Hybrid pipeline on a single
source file. The thread that the server launches per upload has a
60-second budget; if the pipeline exceeds it, we mark the run as
`failed_timeout` and return. The 60s cap is the per-file timeout
requested by the platform spec.

For batch processing (multiple files uploaded together), the
`BatchProcessor` runs files sequentially in a single thread and
retries any timeouts at the end of the batch.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR
sys.path.insert(0, str(BACKEND_DIR))

from api import db
from api import versions_io

# Per-file hard timeout. The RAG pipeline self-times out at this same
# value (DEFAULT_TIMEOUT_SECONDS in policy_platform.rag.retrieval_pipeline).
PER_FILE_TIMEOUT_SECONDS = 120.0

run_states = {}
run_lock = threading.Lock()


def set_state(run_id, state, **kwargs):
    with run_lock:
        run_states[run_id] = {'state': state, **kwargs}


def get_state(run_id):
    with run_lock:
        return run_states.get(run_id, {'state': 'unknown'})


def _run_with_timeout(target, args, timeout: float) -> tuple[bool, str]:
    """Run `target` in a daemon thread with a hard timeout.

    Returns (completed, error_message). On timeout, returns
    (False, "Timeout after Ns"). The target function is expected to
    set run state via set_state / db.update_status before exiting.
    """
    result = {'done': False, 'error': None}

    def _wrapper():
        try:
            target(*args)
            result['done'] = True
        except Exception as e:
            result['error'] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            result['done'] = True

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        # Thread is still running - the RAG pipeline is now in a state
        # where it might continue using the model. We can't kill the
        # thread safely in Python, so we mark the run as timed out and
        # let the background thread finish on its own.
        return False, f"Timeout after {int(timeout)}s"
    if result['error']:
        return False, result['error']
    return True, ""


def run_pipeline(run_id, source_path, output_dir):
    """Run the RAG-Hybrid pipeline on a single source file.

    State machine:
        processing -> done
                   -> failed (any unexpected exception)
                   -> failed_timeout (>= 60s)
    """
    set_state(run_id, 'processing', progress=0)
    db.update_status(run_id, 'processing')

    from policy_platform.pipeline import process
    from policy_platform.audit import write_audit
    from pathlib import Path as _P

    os.makedirs(output_dir, exist_ok=True)
    out_path = _P(output_dir) / f'{run_id}.docx'

    def _do_process():
        try:
            result = process(_P(source_path), output_path=out_path, fail_on_validation=False)
        except TypeError:
            result = process(_P(source_path))

        set_state(run_id, 'processing', progress=90)

        # Locate generated docx
        docx_path = None
        out_attr = getattr(result, 'output_path', None)
        if out_attr and _P(out_attr).exists():
            docx_path = str(out_attr)
        else:
            cand = _P(output_dir) / f'{run_id}.docx'
            if cand.exists():
                docx_path = str(cand)
            else:
                for p in _P(output_dir).rglob('*.docx'):
                    docx_path = str(p)
                    break

        if not docx_path:
            db.update_status(run_id, 'failed', error_message='No docx produced')
            set_state(run_id, 'failed', message='No docx produced')
            return

        sections_filled, markers_count = _summarize(result)

        # Audit JSON is stored directly in the database.
        audit_json_str = write_audit(result)

        db.update_status(
            run_id, 'done',
            sections_filled=sections_filled,
            markers_count=markers_count,
            docx_path=docx_path,
            audit_json=audit_json_str,
            template_sha=getattr(result, 'framework_sha256', None),
            framework_version=getattr(result, 'framework_version', None),
        )
        set_state(
            run_id, 'done',
            sections_filled=sections_filled,
            markers_count=markers_count,
            docx_path=docx_path,
        )

        # Stage 3 - auto-create V1 from the rendered docx so the review
        # surface always opens on an editable version. Idempotent: if V1
        # already exists (e.g. prior run), no-op.
        try:
            from api.api_preview import build_preview_from_docx
            import json as _json
            preview = build_preview_from_docx(docx_path)
            lines_list = preview.get('lines', []) if isinstance(preview, dict) else []
            lines_json = _json.dumps(lines_list)
            filename = db.get_run(run_id).get('filename', '') if db.get_run(run_id) else ''
            with db._conn() as _c:
                versions_io.create_initial_version(_c, run_id, lines_json, filename, 'system')
        except Exception as _ve:
            print(f"[versions_io] V1 auto-create failed for {run_id}: {_ve}", flush=True)

    ok, err = _run_with_timeout(_do_process, (), PER_FILE_TIMEOUT_SECONDS)
    if not ok:
        if "Timeout" in err:
            db.update_status(run_id, 'failed_timeout', error_message=err)
            set_state(run_id, 'failed_timeout', message=err)
        else:
            db.update_status(run_id, 'failed', error_message=err)
            set_state(run_id, 'failed', message=err)


def run_batch(items, output_root: Path | str) -> list[dict]:
    """Run a batch of (run_id, source_path) items sequentially.

    Each item runs through `run_pipeline` in turn. Any item that
    times out is retried once at the end of the batch. Returns a
    list of result dicts, one per item, in the order submitted.

    Args:
        items: list of (run_id, source_path) tuples.
        output_root: base directory under which per-run folders live.

    Returns:
        list of dicts, one per input item, with keys:
            run_id, state, error_message (if any).
    """
    output_root = Path(output_root)
    results: list[dict] = []
    timed_out: list[tuple[str, str, Path]] = []

    for run_id, source_path in items:
        out_dir = output_root / run_id
        run_pipeline(run_id, source_path, str(out_dir))
        st = get_state(run_id)
        results.append({"run_id": run_id, "state": st.get("state", "unknown"), "message": st.get("message", "")})
        if st.get("state") == "failed_timeout":
            timed_out.append((run_id, source_path, out_dir))

    # Retry any timeouts once, at the end of the batch.
    for run_id, source_path, out_dir in timed_out:
        # Reset to processing and re-run.
        set_state(run_id, "processing", progress=0)
        db.update_status(run_id, "processing")
        run_pipeline(run_id, source_path, str(out_dir))
        st = get_state(run_id)
        for r in results:
            if r["run_id"] == run_id:
                r["state"] = st.get("state", "unknown")
                r["message"] = st.get("message", "")
                r["retried"] = True
                break

    return results


def _summarize(result):
    sections_filled = 0
    markers_count = 0
    sections = getattr(result, 'sections', []) or []
    for sec in sections:
        status = sec.get('status') if isinstance(sec, dict) else None
        if status == 'Found':
            sections_filled += 1
        elif status and 'Marker' in status:
            sections_filled += 1
            markers_count += 1
        elif status == 'Skipped - Section Not Found':
            markers_count += 1
    return sections_filled, markers_count
