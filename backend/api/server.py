# server.py
import os
import sys
import json
import uuid
import threading
import re
import time
import zipfile
import io
import collections
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path

# backend/api/server.py Ã¢â€ â€™ backend/ (parent)
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR
sys.path.insert(0, str(BACKEND_DIR))

# Load .env file if present (used for ADMIN_PASSWORD etc.). No external
# dependency Ã¢â‚¬â€ stdlib only. Existing real-env vars are NOT overwritten.
_env_path = BACKEND_DIR / '.env'
if _env_path.exists():
    try:
        for _line in _env_path.read_text(encoding='utf-8').splitlines():
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _k, _v = _line.split('=', 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v
    except Exception as _e:
        print(f"[server] failed to load .env: {_e}", flush=True)

from api import db
from api import pipeline_runner
from api import versions_io
from api import users

DATA_DIR = PROJECT_ROOT / 'data'
RUNS_DIR = DATA_DIR / 'runs'
RUNS_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXT = {'.pdf', '.docx', '.txt'}
MAX_SIZE = 50 * 1024 * 1024

CORS_METHODS = 'GET, POST, OPTIONS'
CORS_HEADERS_ALLOWED = 'Content-Type, Accept, Authorization'


# ---------------------------------------------------------------------------
# Phase 10 Ã¢â‚¬â€ preview-cache memoization
#
# `build_preview_from_docx` walks the published .docx via python-docx and
# paragraph-normalises each one Ã¢â‚¬â€ a cold call costs ~2Ã¢â‚¬â€œ3s, a warm call
# ~25ms. We memoize the result keyed on (run_id, docx_path, docx_mtime) in
# an LRU so that subsequent Step-03 opens for the same run are instant.
#
# Invalidation rules:
#   * Cache entry is **invalidated** when the run's `docx_path` changes or
#     when `runs.audit_json` is rewritten (Phase 6/8 hook).
#   * TTL: 5 minutes. Even without explicit invalidation, the entry
#     expires and gets rebuilt on the next request.
#   * Size: max 64 entries Ã¢â‚¬â€ old ones evicted (LRU).
# ---------------------------------------------------------------------------
PREVIEW_CACHE_TTL_SEC = 5 * 60
PREVIEW_CACHE_MAX = 64


class PreviewCache:
    """Thread-safe LRU cache for `/api/preview/<run_id>` responses.

    Each entry is `(value, expire_at_ts, signature_str)` where
    `signature_str` is the (docx_path, docx_mtime, audit_json_len) tuple
    stringified Ã¢â‚¬â€ used to detect stale entries without DB read."""

    def __init__(self) -> None:
        self._data: "collections.OrderedDict[tuple, tuple]" = collections.OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _signature(run: dict) -> tuple:
        """Identify the run's current preview source. Returns
        `(docx_path, mtime, audit_json_signature)`."""
        docx_path = run.get('docx_path') or ''
        mtime = 0
        try:
            if docx_path:
                mtime = int(Path(docx_path).stat().st_mtime)
        except Exception:
            pass
        audit = run.get('audit_json') or ''
        # Truncate the audit; we only care whether it changed substantially.
        return (docx_path, mtime, len(audit))

    def get(self, run_id: str, sig: tuple) -> "tuple | None":
        """Return cached `{'lines': [...]}` value or None on miss."""
        key = (run_id, sig)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if time.time() >= expire_at:
                self._data.pop(key, None)
                return None
            # LRU touch
            self._data.move_to_end(key)
            return value

    def put(self, run_id: str, sig: tuple, value) -> None:
        key = (run_id, sig)
        with self._lock:
            # Evict oldest if we're at capacity.
            while len(self._data) >= PREVIEW_CACHE_MAX:
                self._data.popitem(last=False)
            self._data[key] = (value, time.time() + PREVIEW_CACHE_TTL_SEC)
            self._data.move_to_end(key)

    def invalidate(self, run_id: str) -> None:
        """Drop all cache entries for a given run_id. Called when the
        run's docx_path or audit_json changes (publish, save, regenerate)."""
        with self._lock:
            keys_to_drop = [k for k in self._data if k[0] == run_id]
            for k in keys_to_drop:
                self._data.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_preview_cache = PreviewCache()


def invalidate_preview_cache(run_id: str) -> None:
    """Drop the preview cache for `run_id`. Called from save/publish
    handlers whenever the underlying .docx (or audit_json) changes."""
    try:
        _preview_cache.invalidate(run_id)
    except Exception:
        pass


def _looks_like_uuid(s: str) -> bool:
    """Cheap UUID-shape check: 8-4-4-4-12 hex digits (case-insensitive).
    Accepts canonical `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` and
    `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (32 hex, no dashes) shapes.
    Reject anything that isn't a recognizable UUID so we don't accept
    user-supplied nonsense as an edit id.
    """
    if not s:
        return False
    stripped = s.replace('-', '')
    if len(stripped) != 32:
        return False
    try:
        int(stripped, 16)
        return True
    except ValueError:
        return False


def cors_headers(handler):
    origin = handler.headers.get('Origin', '*')
    return {
        'Access-Control-Allow-Origin': origin,
        'Vary': 'Origin',
        'Access-Control-Allow-Methods': CORS_METHODS,
        'Access-Control-Allow-Headers': CORS_HEADERS_ALLOWED,
        'Access-Control-Max-Age': '86400',
    }


def send_json(handler, obj, status=200):
    body = json.dumps(obj).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    for k, v in cors_headers(handler).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def send_file(handler, path, content_type, download_name=None):
    p = Path(path)
    if not p.exists():
        send_json(handler, {'error': 'not found'}, status=404)
        return
    data = p.read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(data)))
    if download_name:
        handler.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
    for k, v in cors_headers(handler).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(data)


def parse_multipart(handler):
    ctype = handler.headers.get('Content-Type', '')
    if not ctype.startswith('multipart/form-data'):
        return None, None
    m = re.search(r'boundary=([^;]+)', ctype)
    if not m:
        return None, None
    boundary = ('--' + m.group(1)).encode('utf-8')
    content_length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(content_length)
    parts = body.split(boundary)
    filename = None
    data = None
    for part in parts:
        if not part or part in (b'--', b'--\r\n'):
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        if part.endswith(b'\r\n'):
            part = part[:-2]
        header_end = part.find(b'\r\n\r\n')
        if header_end < 0:
            continue
        headers_raw = part[:header_end].decode('utf-8', errors='replace')
        payload = part[header_end + 4:]
        if 'filename=' not in headers_raw:
            continue
        fn_match = re.search(r'filename="([^"]*)"', headers_raw)
        if fn_match:
            filename = fn_match.group(1)
            data = payload
            break
    return filename, data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[api] {self.address_string()} - {fmt % args}", flush=True)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except Exception as e:
            try:
                body = json.dumps({'error': f'server: {e}'}).encode('utf-8')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                for k, v in cors_headers(self).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in cors_headers(self).items():
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        # Stage 1.5 Ã¢â‚¬â€ auth routes (public, no require_auth).
        if path == '/api/auth/login':
            return self.handle_login()
        if path == '/api/auth/logout':
            return self.handle_logout()
        if path == '/api/upload':
            return self.handle_upload()
        # Stage 4.2 Ã¢â‚¬â€ project-member add + mark-seen routes.
        m = re.match(
            r'^/api/runs/([a-f0-9]+)/members$', path
        )
        if m:
            return self.handle_add_project_member(m.group(1))
        m = re.match(
            r'^/api/runs/([a-f0-9]+)/seen$', path
        )
        if m:
            return self.handle_mark_project_seen(m.group(1))
        # Bulk-clear unread Flow 2 notifications (mark every project seen).
        if path == '/api/auth/mark-all-projects-seen' and self.command == 'POST':
            return self.handle_mark_all_projects_seen()
        # Bulk-dismiss Flow 2 notifications permanently for the current user.
        if path == '/api/auth/dismiss-all-notifications' and self.command == 'POST':
            return self.handle_dismiss_all_notifications()
        # Stage 4.11 Ã¢â‚¬â€ autosave-draft (POST /api/runs/<id>/draft).
        m = re.match(
            r'^/api/runs/([a-f0-9]+)/draft$', path
        )
        if m:
            return self.handle_post_draft(m.group(1))
        # Stage 4.2 Ã¢â‚¬â€ reviewer-assignment route for `submit`.
        m = re.match(
            r'^/api/versions/([a-f0-9]+)/(\d+)/assign$', path
        )
        if m:
            return self.handle_assign_reviewer(
                m.group(1), int(m.group(2))
            )
        m = re.match(r'^/api/process/([a-f0-9]+)$', path)
        if m:
            return self.handle_process(m.group(1))
        # Stage 4 - comment write routes (POST /api/versions/<id>/<n>/comments).
        m = re.match(r'^/api/versions/([a-f0-9]+)/(\d+)/comments$', path)
        if m:
            return self.handle_comment_add(m.group(1), int(m.group(2)))
        m = re.match(
            r'^/api/versions/([a-f0-9]+)/(\d+)/comments/(\d+)/resolve$', path
        )
        if m:
            return self.handle_comment_resolve(
                m.group(1), int(m.group(2)), int(m.group(3))
            )
        # Stage 5 - state-machine write routes.
        m = re.match(r'^/api/versions/([a-f0-9]+)$', path)
        if m:
            return self.handle_version_save(m.group(1))
        m = re.match(r'^/api/versions/([a-f0-9]+)/(\d+)/submit$', path)
        if m:
            return self.handle_version_submit(m.group(1), int(m.group(2)))
        m = re.match(r'^/api/versions/([a-f0-9]+)/(\d+)/review$', path)
        if m:
            return self.handle_version_review(m.group(1), int(m.group(2)))
        # Stage 6 - publish route (approved -> published; generates final docx).
        m = re.match(r'^/api/versions/([a-f0-9]+)/(\d+)/publish$', path)
        if m:
            return self.handle_version_publish(m.group(1), int(m.group(2)))
        send_json(self, {'error': 'not found'}, status=404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        # Stage 4.2 Ã¢â‚¬â€ project-member remove (Approver only).
        m = re.match(
            r'^/api/runs/([a-f0-9]+)/members/(\d+)$', path
        )
        if m:
            return self.handle_remove_project_member(
                m.group(1), int(m.group(2))
            )
        send_json(self, {'error': 'not found'}, status=404)

    def do_GET(self):
        path = urlparse(self.path).path
        # Stage 1.5 Ã¢â‚¬â€ auth routes (public for /me since it requires a token;
        # the handler itself enforces auth).
        if path == '/api/auth/me':
            return self.handle_me()
        if path == '/api/auth/users':
            return self.handle_list_users()
        if path == '/api/history':
            return self.handle_history()
        # Stage 4.2 Ã¢â‚¬â€ shareable-users (for Flow 1 popup type-ahead).
        if path == '/api/auth/shareable-users':
            return self.handle_list_shareable_users()
        # Stage 4.10 Ã¢â‚¬â€ shared-projects feed for the Flow 2 bell
        # ("you've been added to <project>" notifications).
        if path == '/api/auth/shared-projects':
            return self.handle_list_shared_projects()
        # Stage 4.2 Ã¢â‚¬â€ Flow 2 reviewer queue (assigned-to-me).
        if path == '/api/reviewer/queue':
            return self.handle_reviewer_queue()
        # Stage 4.2 Ã¢â‚¬â€ Flow 1 members list + Flow 2 mark-seen.
        m = re.match(r'^/api/runs/([a-f0-9]+)/members$', path)
        if m:
            return self.handle_list_project_members(m.group(1))
        # Stage 4.11 Ã¢â‚¬â€ autosave-draft read.
        m = re.match(r'^/api/runs/([a-f0-9]+)/draft$', path)
        if m:
            return self.handle_get_draft(m.group(1))
        m = re.match(r'^/api/status/([a-f0-9]+)$', path)
        if m:
            return self.handle_status(m.group(1))
        m = re.match(r'^/api/result/([a-f0-9]+)$', path)
        if m:
            return self.handle_result(m.group(1))
        m = re.match(r'^/api/preview/([a-f0-9]+)$', path)
        if m:
            return self.handle_preview(m.group(1))
        m = re.match(r'^/api/download/([a-f0-9]+)/docx$', path)
        if m:
            return self.handle_download(m.group(1))
        m = re.match(r'^/api/download/([a-f0-9]+)/all$', path)
        if m:
            return self.handle_download_all(m.group(1))
        # Stage 2 - workflow / version-control read routes.
        m = re.match(r'^/api/versions/([a-f0-9]+)$', path)
        if m:
            return self.handle_versions_list(m.group(1))
        m = re.match(r'^/api/versions/([a-f0-9]+)/(\d+)$', path)
        if m:
            return self.handle_version_get(m.group(1), int(m.group(2)))
        m = re.match(r'^/api/versions/([a-f0-9]+)/(\d+)/comments$', path)
        if m:
            return self.handle_comments_list(m.group(1), int(m.group(2)))
        m = re.match(r'^/api/versions/([a-f0-9]+)/audit$', path)
        if m:
            return self.handle_audit_list(m.group(1))
        send_json(self, {'error': 'not found'}, status=404)

    def handle_upload(self):
        try:
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            filename, data = parse_multipart(self)
            if not data:
                return send_json(self, {'error': 'no file in request'}, status=400)
            if not filename:
                return send_json(self, {'error': 'no filename'}, status=400)
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                return send_json(self, {'error': f'extension {ext} not allowed'}, status=400)
            if len(data) > MAX_SIZE:
                return send_json(self, {'error': 'file too large'}, status=400)
            run_id = uuid.uuid4().hex
            run_dir = RUNS_DIR / run_id
            run_dir.mkdir(exist_ok=True)
            src_path = run_dir / f'source{ext}'
            src_path.write_bytes(data)
            # Stage 3 Ã¢â‚¬â€ record who created this run; the creator is
            # auto-added to project_members as 'approver' in db.insert_run.
            db.insert_run(
                run_id, filename, len(data),
                source_path=str(src_path),
                created_by_user_id=int(current["id"]),
            )
            pipeline_runner.set_state(run_id, 'uploaded')
            send_json(self, {'run_id': run_id, 'filename': filename, 'size_bytes': len(data)})
        except Exception as e:
            send_json(self, {'error': f'upload: {e}'}, status=500)

    def handle_process(self, run_id):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            run_dir = RUNS_DIR / run_id
            sources = list(run_dir.glob('source.*'))
            if not sources:
                return send_json(self, {'error': 'source not found'}, status=404)
            src = sources[0]
            t = threading.Thread(target=pipeline_runner.run_pipeline, args=(run_id, str(src), str(run_dir)), daemon=True)
            t.start()
            send_json(self, {'state': 'processing', 'run_id': run_id})
        except Exception as e:
            send_json(self, {'error': f'process: {e}'}, status=500)

    def handle_status(self, run_id):
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            st = pipeline_runner.get_state(run_id)
            message = run.get('error_message') or st.get('message') or ''
            send_json(self, {
                'run_id': run_id,
                'state': run['status'],
                'sections_filled': run.get('sections_filled', 0),
                'markers_count': run.get('markers_count', 0),
                'message': message,
            })
        except Exception as e:
            send_json(self, {'error': f'status: {e}'}, status=500)

    def handle_result(self, run_id):
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            if run['status'] != 'done':
                return send_json(self, {'error': 'result not ready', 'status': run['status']}, status=400)
            # Audit JSON is stored in the runs.db audit_json column
            audit_json_str = run.get('audit_json')
            if not audit_json_str:
                return send_json(self, {'error': 'result not ready', 'status': run['status']}, status=400)
            try:
                data = json.loads(audit_json_str)
            except Exception:
                data = {}
            send_json(self, data)
        except Exception as e:
            send_json(self, {'error': f'result: {e}'}, status=500)

    def handle_download(self, run_id):
        try:
            from urllib.parse import parse_qs, urlparse
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run or not run.get('docx_path'):
                return send_json(self, {'error': 'no docx'}, status=404)

            # Optional `version_no` query param Ã¢â‚¬â€ when provided, serve
            # THAT specific version's .docx instead of the run's main
            # docx_path. Lets the user download the currently-viewing
            # version directly without having to publish first.
            #
            # - If the version is published and the per-version file
            #   exists on disk, serve it directly.
            # - If the version is approved but not yet published, build
            #   the .docx on the fly from that version's lines_json.
            # - Otherwise fall back to `runs.docx_path` (latest).
            qs = parse_qs(urlparse(self.path).query)
            raw_no = (qs.get('version_no') or qs.get('version') or [None])[0]
            try:
                requested_no = int(raw_no) if raw_no is not None else None
            except (TypeError, ValueError):
                requested_no = None

            with db._conn() as c:
                published_no = versions_io.latest_published_version_no(c, run_id)
                if requested_no is not None:
                    ver = versions_io.get_version(c, run_id, requested_no)
                    if ver:
                        status_v = ver.get('review_status')
                        # Prefer per-version file on disk if it exists.
                        per_version = (
                            RUNS_DIR
                            / run_id
                            / f'{run_id}_approved_v{requested_no}.docx'
                        )
                        if status_v == 'published' and per_version.exists():
                            send_file(
                                self,
                                str(per_version),
                                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                download_name=f'Policy_Output_v{requested_no}.docx',
                            )
                            return
                        # Approved but not yet published Ã¢â€ â€™ build on the fly
                        # so the user can download their edited .docx
                        # immediately after approval without an explicit
                        # publish step.
                        if status_v == 'approved':
                            try:
                                from policy_platform.pipeline import run_from_lines_json
                                from api.lines_json_extractor import normalise_lines_json
                                from api.docx_approved_export import _apply_header_footer
                                tmp_out = RUNS_DIR / run_id / f'{run_id}_approved_v{requested_no}_preview.docx'
                                run_from_lines_json(
                                    lines_json=normalise_lines_json(ver.get('lines_json') or []),
                                    output_path=tmp_out,
                                    run_id=run_id,
                                    document_name=f'{run_id}_v{requested_no}_preview',
                                    fail_on_validation=False,
                                )
                                # Phase 8 Ã¢â‚¬â€ apply the Word-style header /
                                # footer (strip brackets, preserve logo,
                                # add page X of Y) so the on-the-fly
                                # download matches what publish produces.
                                try:
                                    from docx import Document as _Doc2
                                    doc2 = _Doc2(str(tmp_out))
                                    _apply_header_footer(
                                        doc2,
                                        ver.get('lines_json') or [],
                                    )
                                    doc2.save(str(tmp_out))
                                except Exception as ee:
                                    print(f'[handle_download] header/footer apply failed: {ee}', flush=True)
                                send_file(
                                    self,
                                    str(tmp_out),
                                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                    download_name=f'Policy_Output_v{requested_no}.docx',
                                )
                                return
                            except Exception as e:
                                print(f'[handle_download] on-the-fly build failed: {e}', flush=True)
                                import traceback; traceback.print_exc()

            if published_no is None:
                return send_json(
                    self,
                    {
                        'error': 'no_published_version',
                        'message': (
                            'No published version. Approve a version, then click '
                            'Publish & Generate DOCX to enable download.'
                        ),
                        'current_status': run.get('status'),
                    },
                    status=409,
                )
            send_file(
                self,
                run['docx_path'],
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                download_name='Policy_Output.docx',
            )
        except Exception as e:
            send_json(self, {'error': f'download: {e}'}, status=500)

    def handle_download_all(self, run_id):
        """Bundle one `.docx` per file in the Results dropdown into a
        single zip and stream it to the browser.

        Per the user's spec ("Download all files Ã¢â‚¬â€ each file's currently
        viewing version"):
          - The frontend passes the list of run_ids it's showing in the
            Results dropdown via the `ids` query param (comma-separated),
            plus the current view version per file via the `versions`
            query param (comma-separated, parallel to `ids`).
          - For each (run_id, version_no) pair: bundle EXACTLY ONE
            `.docx` Ã¢â‚¬â€ the current view version's `.docx`. No other
            versions are included.
          - Source files are EXCLUDED. No `manifest.txt`. Only `.docx`
            files in the zip.
          - The zip entry filename is `<file_stem>_v<N>.docx` where
            `file_stem` is derived from `runs.filename` (the original
            upload's name, extension stripped and spaces normalised).

        Backward compatibility: when called with a single `run_id` and
        no `ids` query param, falls back to the single-file behavior
        (bundle that file's current view version's `.docx` only).
        """
        try:
            qs = parse_qs(urlparse(self.path).query)
            ids_param = (qs.get('ids') or [''])[0]
            versions_param = (qs.get('versions') or [''])[0]
            mode_param = (qs.get('mode') or [''])[0].lower()
            published_only = mode_param == 'published'

            if ids_param:
                ids_list = [s for s in ids_param.split(',') if s]
            else:
                ids_list = [run_id]

            # Stage 4.3 Ã¢â‚¬â€ auth + per-run membership check for every
            # requested run_id. 401 / 403 wins over any 404.
            from api.auth_middleware import require_auth, get_current_user
            current = require_auth(self)
            if current is None:
                return
            user_id = current['id']
            is_admin = int(current.get('is_admin', 0)) == 1
            with db._conn() as c:
                for rid_check in ids_list:
                    if not is_admin:
                        access = users.get_user_project_access(c, user_id, rid_check)
                        if access is None:
                            return send_json(
                                self,
                                {'error': 'forbidden',
                                 'message': f'No access to {rid_check}.'},
                                status=403,
                            )

            if ids_param:
                # Multi-file mode: ids + versions are comma-separated,
                # parallel lists.
                versions_list_raw = [s for s in versions_param.split(',') if s]
                if len(versions_list_raw) != len(ids_list):
                    return send_json(
                        self,
                        {
                            'error': 'param_mismatch',
                            'message': (
                                '`ids` and `versions` must have the same '
                                'number of entries (parallel lists).'
                            ),
                        },
                        status=400,
                    )
                # Build a list of (run_id, version_no) pairs. If a
                # version is missing or unparseable, fall back to None
                # (the per-run resolution below will pick a default).
                items: list[tuple[str, int | None]] = []
                for rid, vraw in zip(ids_list, versions_list_raw):
                    try:
                        items.append((rid, int(vraw)))
                    except (TypeError, ValueError):
                        items.append((rid, None))
            else:
                # Single-file mode (backward compat). The path
                # `/api/download/<run_id>/all?version_no=N` still works.
                try:
                    requested_no = int(
                        (qs.get('version_no') or qs.get('version') or ['0'])[0]
                    )
                except (TypeError, ValueError):
                    requested_no = 0
                items = [(run_id, requested_no or None)]

            files_to_zip: list[tuple[Path, str]] = []

            for rid, current_version in items:
                run_dir = RUNS_DIR / rid
                if not run_dir.exists():
                    continue
                with db._conn() as c:
                    versions = versions_io.get_versions(c, rid)
                    latest_pub = versions_io.latest_published_version_no(c, rid)
                    run_row = db.get_run(rid)
                if not versions:
                    continue

                # published_only mode (mode=published): bundle ONLY the
                # latest published version per run. Runs with no
                # published version are SKIPPED silently (no entry in
                # the zip, no error). If every run is skipped, the
                # 404 below fires with a friendly message.
                if published_only:
                    if latest_pub is None:
                        continue
                    current_version = int(latest_pub)

                # Resolve target version: explicit current > latest
                # published > highest-numbered.
                if current_version is None or current_version <= 0:
                    if latest_pub is not None:
                        target_no = latest_pub
                    else:
                        target_no = max(
                            v.get('version_no', 0) for v in versions
                        )
                else:
                    target_no = current_version

                # Find the version row.
                ver = next(
                    (
                        v for v in versions
                        if int(v.get('version_no') or 0) == int(target_no)
                    ),
                    None,
                )
                if ver is None:
                    continue

                # 1. Prefer per-version file already on disk.
                per_version_main = run_dir / f'{rid}_approved_v{target_no}.docx'
                per_version_preview = run_dir / f'{rid}_approved_v{target_no}_preview.docx'
                chosen_path: Path | None = None
                if per_version_main.exists():
                    chosen_path = per_version_main
                elif per_version_preview.exists():
                    chosen_path = per_version_preview
                else:
                    # 2. Build on-the-fly if approved/published but no file.
                    status_v = ver.get('review_status') or 'draft'
                    if status_v in ('approved', 'published'):
                        try:
                            from policy_platform.pipeline import run_from_lines_json
                            from api.lines_json_extractor import normalise_lines_json
                            from api.docx_approved_export import _apply_header_footer
                            tmp_out = per_version_preview
                            run_from_lines_json(
                                lines_json=normalise_lines_json(ver.get('lines_json') or []),
                                output_path=tmp_out,
                                run_id=rid,
                                document_name=f'{rid}_v{target_no}_preview',
                                fail_on_validation=False,
                            )
                            try:
                                from docx import Document as _Doc2
                                doc2 = _Doc2(str(tmp_out))
                                _apply_header_footer(
                                    doc2, ver.get('lines_json') or [],
                                )
                                doc2.save(str(tmp_out))
                            except Exception as ee:
                                print(f'[handle_download_all] header/footer apply failed: {ee}', flush=True)
                            chosen_path = tmp_out
                        except Exception as e:
                            print(f'[handle_download_all] on-the-fly build for v{target_no} failed: {e}', flush=True)

                if chosen_path is None:
                    continue

                # Derive file_stem from the original upload name, falling
                # back to the run_id.
                original_name = (run_row or {}).get('filename') or rid
                file_stem = re.sub(r'\.[^/.]+$', '', original_name)
                # Replace path separators and characters that are
                # awkward inside zip entries.
                file_stem = re.sub(r'[\\/:*?"<>|]', '_', file_stem).strip()
                if not file_stem:
                    file_stem = rid

                arcname = f'{file_stem}_v{target_no}.docx'
                files_to_zip.append((chosen_path, arcname))

            if not files_to_zip:
                if published_only:
                    return send_json(
                        self,
                        {
                            'error': 'no_published_versions',
                            'message': (
                                'None of the requested files have a published '
                                'version yet. Approve and publish at least '
                                'one version per file before downloading.'
                            ),
                        },
                        status=409,
                    )
                return send_json(
                    self,
                    {
                        'error': 'no_docx_available',
                        'message': (
                            'No .docx output is available for the requested '
                            'files / versions. Approve and publish at least '
                            'one version first.'
                        ),
                    },
                    status=404,
                )

            # Source file is EXPLICITLY excluded Ã¢â‚¬â€ see header docstring.
            # No `manifest.txt` either.

            zip_name = (
                f'all_files_{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}.zip'
                if len(items) > 1
                else (
                    f'{items[0][0]}_v{items[0][1] or 0}.zip'
                    if items
                    else 'all_files.zip'
                )
            )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for src, arcname in files_to_zip:
                    zf.write(src, arcname=arcname)
            data = buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(len(data)))
            self.send_header(
                'Content-Disposition',
                f'attachment; filename="{zip_name}"',
            )
            for k, v in cors_headers(self).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            send_json(self, {'error': f'download_all: {e}'}, status=500)

    def handle_preview(self, run_id):
        """Read the actual .docx output and return its paragraphs grouped
        into the 15 slots. This guarantees the web preview matches the
        docx output 1:1 (the audit 'sections' field is built from the
        analyzer's pre-render state, which can differ slightly from what
        was actually written to the docx).

        Phase 10: result is memoized in a thread-safe LRU keyed by
        `(run_id, docx_path, docx_mtime, audit_json_len)`. Cold backend
        first-call: ~2-3s. Warm: <5ms."""
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            if run['status'] != 'done':
                return send_json(self, {'error': 'result not ready', 'status': run['status']}, status=400)
            docx_path = run.get('docx_path')
            if not docx_path or not Path(docx_path).exists():
                return send_json(self, {'error': 'no docx on disk'}, status=404)
            sig = PreviewCache._signature(run)
            cached = _preview_cache.get(run_id, sig)
            if cached is not None:
                send_json(self, cached)
                return
            from api.api_preview import build_preview_from_docx
            data = build_preview_from_docx(docx_path)
            _preview_cache.put(run_id, sig, data)
            send_json(self, data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            send_json(self, {'error': f'preview: {e}'}, status=500)

    def handle_history(self):
        try:
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            user_id = current['id']
            is_admin = int(current.get('is_admin', 0)) == 1
            # Stage 4.3 Ã¢â‚¬â€ return only runs the user can access (i.e. is a
            # member of, or is admin). Admins see everything.
            with db._conn() as c:
                if is_admin:
                    rows = c.execute(
                        """SELECT run_id, filename, created_at, status,
                                  sections_filled, markers_count
                           FROM runs WHERE status='done'
                           ORDER BY created_at DESC LIMIT 50"""
                    ).fetchall()
                else:
                    rows = c.execute(
                        """SELECT r.run_id, r.filename, r.created_at,
                                  r.status, r.sections_filled, r.markers_count
                           FROM runs r
                           JOIN project_members pm
                             ON pm.run_id = r.run_id
                           WHERE r.status='done' AND pm.user_id = ?
                           ORDER BY r.created_at DESC LIMIT 50""",
                        (user_id,),
                    ).fetchall()
            send_json(self, [dict(r) for r in rows])
        except Exception as e:
            send_json(self, {'error': f'history: {e}'}, status=500)

    # Stage 1.5 - auth handlers.

    def handle_login(self):
        """POST /api/auth/login  body: {username, password}
        Returns: {token, user, expires_at} on success; 401 on failure."""
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            username = (body.get('username') or '').strip()
            password = body.get('password') or ''
            if not username or not password:
                return send_json(
                    self,
                    {'error': 'bad_request', 'message': 'username and password required'},
                    status=400,
                )
            with db._conn() as c:
                result = users.login(c, username, password)
            if not result:
                return send_json(
                    self,
                    {'error': 'invalid_credentials', 'message': 'Invalid username or password.'},
                    status=401,
                )
            return send_json(self, result)
        except Exception as e:
            return send_json(self, {'error': f'login: {e}'}, status=500)

    def handle_logout(self):
        """POST /api/auth/logout  body: {token} or Authorization: Bearer
        Deletes the session. Idempotent Ã¢â‚¬â€ returns 200 even if token absent."""
        try:
            token = None
            auth = self.headers.get('Authorization', '')
            if auth.lower().startswith('bearer '):
                token = auth.split(None, 1)[1].strip()
            if not token:
                length = int(self.headers.get('Content-Length', 0) or 0)
                raw = self.rfile.read(length).decode('utf-8') if length else '{}'
                try:
                    body = json.loads(raw) if raw else {}
                except Exception:
                    body = {}
                token = body.get('token')
            if token:
                with db._conn() as c:
                    users.delete_session(c, token)
            return send_json(self, {'logged_out': True})
        except Exception as e:
            return send_json(self, {'error': f'logout: {e}'}, status=500)

    def handle_me(self):
        """GET /api/auth/me  Ã¢â‚¬â€ returns the current user, or 401."""
        from api.auth_middleware import require_auth
        user = require_auth(self)
        if user is None:
            return  # require_auth sent 401
        return send_json(self, {'user': user})

    def handle_list_users(self):
        """GET /api/auth/users  Ã¢â‚¬â€ list all users (any logged-in user)."""
        from api.auth_middleware import require_auth
        user = require_auth(self)
        if user is None:
            return
        with db._conn() as c:
            all_users = users.list_users(c)
        return send_json(self, {'items': all_users})

    # Stage 2 - workflow / version-control read handlers (READ-ONLY).

    def handle_versions_list(self, run_id):
        """Stage 4.14 — return BOTH frozen rows AND in-progress draft
        rows. Each draft row gets a computed V# = latest_frozen + N,
        where N is the draft's position (1-based) in the ordered list
        sorted by `edit_count` ASC.

        Each item includes:
          - `kind`: 'frozen' or 'draft'
          - `version_no`: computed V# (frozen = stored, draft = latest_frozen + position)
          - `draft_id`: PK of the policy_drafts row (only for drafts)
          - `edit_count`: 1, 2, 3, ... (increments per 60s window)
          - `lines_json`: included for drafts (small payload; needed
            for click-to-load); excluded for frozen (clients call
            `getVersion` separately if they need full lines).
        """
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                frozen = versions_io.get_versions(c, run_id)
                drafts = versions_io.list_drafts(c, run_id)
            latest_frozen = max((v["version_no"] for v in frozen), default=0)
            merged = []
            for f in frozen:
                merged.append({**f, "kind": "frozen"})
            # Sort drafts by edit_count ASC so V# assignment is stable.
            drafts_sorted = sorted(drafts, key=lambda d: int(d["edit_count"]))
            for i, d in enumerate(drafts_sorted, start=1):
                merged.append({
                    "version_no": latest_frozen + i,
                    "edit_count": int(d["edit_count"]),
                    "draft_id": int(d["draft_id"]),
                    "review_status": "drafting",
                    "modified_by": d["modified_by"],
                    "modified_at": d["modified_at"],
                    "lines_json": d["lines_json"],
                    "kind": "draft",
                })
            send_json(self, {'items': merged})
        except Exception as e:
            send_json(self, {'error': f'versions_list: {e}'}, status=500)

    def handle_version_get(self, run_id, version_no):
        """Stage 4.14 — `version_no` may refer to a frozen row OR a
        drafting row (computed V# = latest_frozen + N where N is the
        draft's position in edit_count order).

        For drafts, we return the matching draft row's `lines_json`
        plus its `kind='draft'` and `draft_id`.
        """
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                frozen = versions_io.get_versions(c, run_id)
                drafts = versions_io.list_drafts(c, run_id)
            latest_frozen = max((v["version_no"] for v in frozen), default=0)
            if version_no > latest_frozen:
                # Find the Nth draft (sorted by edit_count ASC).
                drafts_sorted = sorted(
                    drafts, key=lambda d: int(d["edit_count"])
                )
                idx = int(version_no) - latest_frozen - 1
                if 0 <= idx < len(drafts_sorted):
                    draft = drafts_sorted[idx]
                    try:
                        lines = json.loads(draft["lines_json"])
                    except Exception:
                        lines = []
                    send_json(self, {
                        'version_no': int(version_no),
                        'kind': 'draft',
                        'draft_id': int(draft["draft_id"]),
                        'edit_count': int(draft["edit_count"]),
                        'review_status': 'drafting',
                        'modified_by': draft["modified_by"],
                        'modified_at': draft["modified_at"],
                        'lines_json': lines,
                    })
                    return
                return send_json(self, {'error': 'version not found'}, status=404)
            with db._conn() as c:
                v = versions_io.get_version(c, run_id, version_no)
            if not v:
                return send_json(self, {'error': 'version not found'}, status=404)
            v['kind'] = 'frozen'
            send_json(self, v)
        except Exception as e:
            send_json(self, {'error': f'version_get: {e}'}, status=500)

    def handle_comments_list(self, run_id, version_no):
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                items = versions_io.list_comments(c, run_id, version_no)
            send_json(self, {'items': items})
        except Exception as e:
            send_json(self, {'error': f'comments_list: {e}'}, status=500)

    def handle_audit_list(self, run_id):
        try:
            from api.auth_middleware import require_project_access
            result = require_project_access(self, run_id, 'viewer')
            if result is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                items = versions_io.list_audit(c, run_id)
            send_json(self, {'items': items})
        except Exception as e:
            send_json(self, {'error': f'audit_list: {e}'}, status=500)

    # Stage 4 - comment write handlers.

    def handle_comment_add(self, run_id, version_no):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            text = (body.get('body') or '').strip()
            if not text:
                return send_json(self, {'error': 'comment body required'}, status=400)
            anchor_kind = body.get('anchor_kind')
            anchor_key = body.get('anchor_key')
            # Stage 3 Ã¢â‚¬â€ author identity comes from the session. The
            # body `author` field is now ignored.
            author = current["username"]
            author_user_id = int(current["id"])
            with db._conn() as c:
                comment = versions_io.add_comment(
                    c, run_id, version_no, text, anchor_kind, anchor_key, author,
                    author_user_id=author_user_id,
                )
            send_json(self, comment)
        except Exception as e:
            send_json(self, {'error': f'comment_add: {e}'}, status=500)

    def handle_comment_resolve(self, run_id, version_no, comment_id):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                ok = versions_io.resolve_comment(c, run_id, version_no, comment_id)
            if not ok:
                return send_json(self, {'error': 'comment not found'}, status=404)
            send_json(self, {'resolved': True})
        except Exception as e:
            send_json(self, {'error': f'comment_resolve: {e}'}, status=500)

    # Stage 5 - state-machine write handlers.

    def handle_version_save(self, run_id):
        """Create V(n+1) from edited lines_json. Always creates draft."""
        import traceback
        try:
            # Stage 3 Ã¢â‚¬â€ auth first, then DB lookup. This ensures 401
            # is returned for unauthenticated requests even when the
            # run_id doesn't exist.
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            lines_json_str = body.get('lines_json', '')
            if not isinstance(lines_json_str, str) or not lines_json_str:
                return send_json(self, {'error': 'lines_json required'}, status=400)
            try:
                parsed = json.loads(lines_json_str)
                if not isinstance(parsed, list):
                    raise ValueError('must be list')
            except Exception:
                return send_json(self, {'error': 'lines_json invalid'}, status=400)
            change_summary = (body.get('change_summary') or '').strip()
            if not change_summary:
                return send_json(self, {'error': 'change_summary required'}, status=400)
            # Stage 3 Ã¢â‚¬â€ actor identity comes from the session, not the
            # request body. The body `actor` field is now ignored.
            actor = current["username"]
            actor_user_id = int(current["id"])
            with db._conn() as c:
                new_v = versions_io.save_version(
                    c, run_id, lines_json_str, change_summary, actor,
                    author_user_id=actor_user_id,
                )
            # Phase 10 Ã¢â‚¬â€ drop the preview cache for this run. The next
            # /api/preview request will rebuild from the current .docx.
            invalidate_preview_cache(run_id)
            send_json(self, new_v)
        except Exception as e:
            traceback.print_exc()
            send_json(self, {'error': f'version_save: {e}'}, status=500)

    def handle_version_submit(self, run_id, version_no):
        """draft -> in_review. Only if current status == draft.

        Stage 4.11 - autosave-aware: if the request includes
        `from_draft=true` in the body (or version_no is 0/None),
        the current draft (if any) is snapshotted into a new frozen
        `policy_versions` row first, then that new version is submitted.

        Stage 4.12 - autosave-draft aware: with multiple draft rows per
        run, the request may include `draft_id` (the policy_drafts row
        PK) and/or `draft_edit_count` to specify WHICH draft to
        consume. "Submit Only the Currently Viewed Version" semantics.
        Other draft rows remain as orphan history.
        """
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            # Stage 3 - actor identity comes from the session.
            actor = current["username"]
            actor_user_id = int(current["id"])
            from_draft = bool(body.get('from_draft'))
            target_version_no = version_no
            with db._conn() as c:
                # Stage 4.11/4.12 - consume draft if requested. This creates
                # the new frozen row AND removes that specific draft row.
                # The submit then flips that brand-new row from draft to
                # in_review. Other draft rows (if any) remain as orphans.
                if from_draft:
                    draft_edit_count = body.get('draft_edit_count')
                    if draft_edit_count is not None:
                        try:
                            draft_edit_count = int(draft_edit_count)
                        except Exception:
                            draft_edit_count = None
                    new_v = versions_io.consume_draft_into_version(
                        c, run_id,
                        change_summary=body.get('change_summary') or '',
                        actor=actor,
                        actor_user_id=actor_user_id,
                        draft_edit_count=draft_edit_count,
                    )
                    if not new_v:
                        # No autosave draft to consume. Fall back: if
                        # the requested version_no is itself a valid
                        # draft row in policy_versions (e.g. V1, the
                        # initial pipeline draft), submit that row
                        # directly. Otherwise return 409 no_draft.
                        existing = versions_io.get_version(
                            c, run_id, target_version_no
                        )
                        if (
                            existing
                            and existing.get('review_status') == 'draft'
                        ):
                            target_version_no = int(existing['version_no'])
                        else:
                            return send_json(
                                self,
                                {'error': 'no_draft',
                                 'message': (
                                     'No autosave draft to submit. '
                                     'Open the editor and try again.'
                                 )},
                                status=409,
                            )
                    else:
                        target_version_no = int(new_v['version_no'])
                cur = versions_io.get_version(c, run_id, target_version_no)
                if not cur:
                    return send_json(self, {'error': 'version not found'}, status=404)
                if cur.get('review_status') != 'draft':
                    return send_json(
                        self,
                        {
                            'error': 'invalid_state',
                            'message': (
                                f"Cannot submit: current status is "
                                f"{cur.get('review_status')!r}, expected 'draft'."
                            ),
                            'current_status': cur.get('review_status'),
                        },
                        status=409,
                    )
                updated = versions_io.set_review_status(
                    c,
                    run_id,
                    target_version_no,
                    'in_review',
                    actor=actor,
                    reviewer=actor,
                    actor_user_id=actor_user_id,
                    event_type='submitted',
                    details=(
                        f"V{target_version_no} submitted for review by {actor}"
                    ),
                )
            invalidate_preview_cache(run_id)
            send_json(self, updated)
        except Exception as e:
            send_json(self, {'error': f'version_submit: {e}'}, status=500)

    def handle_version_review(self, run_id, version_no):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            action = (body.get('action') or '').strip().lower()
            if action not in ('approve', 'reject'):
                return send_json(
                    self,
                    {'error': 'action must be approve or reject'},
                    status=400,
                )
            # Stage 3 Ã¢â‚¬â€ reviewer identity comes from the session.
            reviewer = current["username"]
            reviewer_user_id = int(current["id"])
            note = (body.get('note') or '').strip()
            if action == 'reject' and not note:
                return send_json(
                    self,
                    {'error': 'rejection note required'},
                    status=400,
                )
            with db._conn() as c:
                cur = versions_io.get_version(c, run_id, version_no)
                if not cur:
                    return send_json(self, {'error': 'version not found'}, status=404)
                if cur.get('review_status') != 'in_review':
                    return send_json(
                        self,
                        {
                            'error': 'invalid_state',
                            'message': (
                                f"Cannot review: current status is "
                                f"{cur.get('review_status')!r}, expected 'in_review'."
                            ),
                            'current_status': cur.get('review_status'),
                        },
                        status=409,
                    )
                new_status = 'approved' if action == 'approve' else 'rejected'
                details = (
                    f"V{version_no} {action}d by {reviewer}"
                    + (f': {note[:200]}' if note else '')
                )
                updated = versions_io.set_review_status(
                    c,
                    run_id,
                    version_no,
                    new_status,
                    actor=reviewer,
                    reviewer=reviewer,
                    actor_user_id=reviewer_user_id,
                    note=note or None,
                    event_type=action + 'd',
                    details=details,
                )
            send_json(self, updated)
        except Exception as e:
            send_json(self, {'error': f'version_review: {e}'}, status=500)

    # Stage 6 - publish handler (approved -> published, generates final docx).

    def handle_version_publish(self, run_id, version_no):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            from api.auth_middleware import require_auth
            current = require_auth(self)
            if current is None:
                return
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            # Stage 3 Ã¢â‚¬â€ actor identity comes from the session.
            actor = current["username"]
            actor_user_id = int(current["id"])
            with db._conn() as c:
                cur = versions_io.get_version(c, run_id, version_no)
                if not cur:
                    return send_json(self, {'error': 'version not found'}, status=404)
                if cur.get('review_status') != 'approved':
                    return send_json(
                        self,
                        {
                            'error': 'invalid_state',
                            'message': (
                                f"Cannot publish: current status is "
                                f"{cur.get('review_status')!r}, expected 'approved'."
                            ),
                            'current_status': cur.get('review_status'),
                        },
                        status=409,
                    )
                # Generate the final docx (reuses the brain layout).
                from api import publish_to_brain
                updated = publish_to_brain.publish_approved_version(
                    c,
                    run_id,
                    version_no,
                    output_dir=RUNS_DIR / run_id,
                    actor=actor,
                    actor_user_id=actor_user_id,
                )
                if not updated:
                    return send_json(
                        self,
                        {'error': 'publish failed (brain verify or docx build)'},
                        status=500,
                    )
            # Phase 10 Ã¢â‚¬â€ published docx path changed, drop the preview cache
            # so the next /api/preview returns the brand-new content.
            invalidate_preview_cache(run_id)
            send_json(self, {
                'version_no': updated['version_no'],
                'review_status': updated['review_status'],
                'docx_path': updated.get('docx_path'),
                'published_at': updated.get('published_at'),
            })
        except Exception as e:
            send_json(self, {'error': f'version_publish: {e}'}, status=500)

    # ------------------------------------------------------------------
    # Stage 4.2 Ã¢â‚¬â€ Flow 1 (share popup), Flow 2 (notifications), Flow 3
    # (reviewer queue + visibility) HTTP handlers.
    # ------------------------------------------------------------------

    def handle_list_shareable_users(self):
        """GET /api/auth/shareable-users Ã¢â‚¬â€ non-admin users available to add
        to a project. Admins excluded (every admin implicitly already has
        access). Search via `?q=<substring>` is a pure client-side filter,
        but the same endpoint powers the type-ahead.
        """
        from api.auth_middleware import require_auth
        current = require_auth(self)
        if current is None:
            return
        with db._conn() as c:
            rows = c.execute(
                """SELECT user_id, username, is_admin FROM users
                   WHERE is_admin = 0 ORDER BY username"""
            ).fetchall()
        return send_json(self, {
            'items': [
                {'id': r['user_id'], 'username': r['username']}
                for r in rows
            ]
        })

    def handle_list_shared_projects(self):
        """GET /api/auth/shared-projects Ã¢â‚¬â€ Flow 2 share-notification feed.

        Returns every project the caller can access, joined with the
        `project_members` row so the bell can detect "you've been added
        since you last opened this" (`is_unread = True`).

        Powers the second section of the header `Notifications (N)`
        dropdown Ã¢â‚¬â€ distinct from the reviewer-queue section above which
        only shows in_review versions assigned to me. Together they
        cover both kinds of activity a project member cares about:
          - "you've been added to <project>" (this endpoint)
          - "<version> is awaiting your review" (reviewer queue)
        """
        from api.auth_middleware import require_auth
        current = require_auth(self)
        if current is None:
            return
        user_id = current['id']
        with db._conn() as c:
            items = users.get_my_shared_projects(c, user_id)
        return send_json(self, {'items': items})

    def handle_list_project_members(self, run_id):
        """GET /api/runs/<id>/members  Ã¢â‚¬â€ Flow 1 popup listing.

        Visible to any member of the project. Returns each member's id,
        username, access_level, the username of the adder, and added_at.
        """
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'viewer')
        if result is None:
            return
        current, your_access = result
        with db._conn() as c:
            items = users.list_project_members(c, run_id)
            run_row = c.execute(
                "SELECT filename FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return send_json(self, {
            'project': (
                {'run_id': run_id, 'filename': run_row['filename']}
                if run_row else {'run_id': run_id}
            ),
            'items': items,
            'your_access': your_access,
        })

    def handle_add_project_member(self, run_id):
        """POST /api/runs/<id>/members  Ã¢â‚¬â€ add or update a member.

        Approver-only. Body: {user_id, access_level}.
        """
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'approver')
        if result is None:
            return
        current, _your_access = result
        length = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}
        try:
            target_user_id = int(body.get('user_id'))
            level = (body.get('access_level') or '').strip()
        except Exception:
            return send_json(
                self, {'error': 'user_id + access_level required'},
                status=400,
            )
        if level not in ('viewer', 'editor', 'approver'):
            return send_json(
                self,
                {'error': 'access_level must be viewer/editor/approver'},
                status=400,
            )
        with db._conn() as c:
            exists = c.execute(
                "SELECT 1 FROM users WHERE user_id = ?",
                (target_user_id,),
            ).fetchone()
            if not exists:
                return send_json(
                    self, {'error': 'target user not found'},
                    status=404,
                )
            run_row = c.execute(
                "SELECT 1 FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not run_row:
                return send_json(
                    self, {'error': 'project not found'},
                    status=404,
                )
            users.add_project_member(
                c, run_id, target_user_id, level,
                added_by_user_id=current['id'],
            )
            # Reset `last_seen_at` so the new member sees the share as
            # "unread" (Flow 2).
            c.execute(
                """UPDATE project_members SET last_seen_at = NULL
                   WHERE run_id = ? AND user_id = ?""",
                (run_id, target_user_id),
            )
            member = c.execute(
                """SELECT pm.access_level,
                          u.username AS added_by_username,
                          pm.added_at
                   FROM project_members pm
                   JOIN users u ON u.user_id = pm.added_by_user_id
                   WHERE pm.run_id = ? AND pm.user_id = ?""",
                (run_id, target_user_id),
            ).fetchone()
        return send_json(self, {
            'user_id': target_user_id,
            'access_level': member['access_level'],
            'added_by': member['added_by_username'],
            'added_at': member['added_at'],
        })

    def handle_remove_project_member(self, run_id, target_user_id):
        """DELETE /api/runs/<id>/members/<user_id>  Ã¢â‚¬â€ revoke membership.

        Approver-only (admins bypass via `require_project_access`).
        """
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'approver')
        if result is None:
            return
        current, _your_access = result
        with db._conn() as c:
            # Block removing the project's creator (they always remain
            # implicit owner).
            creator = c.execute(
                "SELECT created_by_user_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if creator and creator['created_by_user_id'] == target_user_id:
                return send_json(
                    self,
                    {'error': 'cannot remove the project creator'},
                    status=409,
                )
            users.remove_project_member(c, run_id, target_user_id)
        return send_json(self, {'ok': True, 'removed_user_id': target_user_id})

    def handle_mark_project_seen(self, run_id):
        """POST /api/runs/<id>/seen  Ã¢â‚¬â€ mark Flow 2 notification as read."""
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'viewer')
        if result is None:
            return
        current, _your_access = result
        with db._conn() as c:
            ok = users.mark_project_seen(c, run_id, current['id'])
        return send_json(self, {'ok': ok})

    def handle_mark_all_projects_seen(self):
        """POST /api/auth/mark-all-projects-seen — reset the user's
        `last_seen_at` for every project they're a member of. Wipes out
        the backlog of unread Flow 2 notifications in one click.
        """
        from api.auth_middleware import require_auth
        current = require_auth(self)
        if current is None:
            return
        with db._conn() as c:
            n = users.mark_all_projects_seen(c, current['id'])
        return send_json(self, {'ok': True, 'updated': int(n or 0)})

    def handle_dismiss_all_notifications(self):
        """POST /api/auth/dismiss-all-notifications — per-user permanent
        dismissal. Flags every project notification for the current user
        as dismissed. Items will no longer appear in the bell for this
        user; other members are unaffected.
        """
        from api.auth_middleware import require_auth
        current = require_auth(self)
        if current is None:
            return
        with db._conn() as c:
            n = users.dismiss_all_notifications(c, current['id'])
        return send_json(self, {'ok': True, 'dismissed': int(n or 0)})

    # ------------------------------------------------------------------
    # Stage 4.11 Ã¢â‚¬â€ autosave-draft handlers
    # ------------------------------------------------------------------

    def handle_get_draft(self, run_id):
        """GET /api/runs/<id>/draft  Ã¢â‚¬â€ fetch the current autosave draft.

        Returns `{draft: <row> | null}`. Viewers can read; the response
        is identical regardless of access level (the draft is just the
        in-progress edit buffer; viewers don't edit but may want to see
        "last edited at HH:MM by X" in the future).
        """
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'viewer')
        if result is None:
            return
        with db._conn() as c:
            draft = versions_io.get_draft(c, run_id)
        return send_json(self, {'draft': draft})

    def handle_post_draft(self, run_id):
        """POST /api/runs/<id>/draft  Ã¢â‚¬â€ autosave the current edit buffer.

        Body: `{lines_json: string, client_edit_id: string (UUID)}`.

        Idempotent on `client_edit_id` (same UUID Ã¢â€ â€™ same `edit_count`,
        no audit spam). Bumps `edit_count` when the UUID differs.

        Requires `editor` access (viewer blocked).
        """
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'editor')
        if result is None:
            return
        current, _your_access = result
        length = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}
        lines_json_str = body.get('lines_json')
        if not isinstance(lines_json_str, str) or not lines_json_str:
            return send_json(
                self,
                {'error': 'lines_json required (string)'},
                status=400,
            )
        try:
            parsed = json.loads(lines_json_str)
            if not isinstance(parsed, list):
                raise ValueError('must be a list')
        except Exception:
            return send_json(
                self,
                {'error': 'lines_json invalid Ã¢â‚¬â€ must be a JSON list'},
                status=400,
            )
        client_edit_id = body.get('client_edit_id') or ''
        if not isinstance(client_edit_id, str) or not client_edit_id:
            return send_json(
                self,
                {'error': 'client_edit_id required (UUID string)'},
                status=400,
            )
        client_edit_id = client_edit_id.strip()
        # Loose UUID-shape check: 8-4-4-4-12 hex digits, OR a reasonable
        # fallback for clients that send `crypto.randomUUID()` output.
        if not _looks_like_uuid(client_edit_id):
            return send_json(
                self,
                {'error': 'client_edit_id must be a UUID string'},
                status=400,
            )
        actor = current['username']
        actor_user_id = int(current['id'])
        with db._conn() as c:
            # Confirm the run exists. require_project_access already
            # gated 401/403, but a missing run_id would silently upsert
            # an orphan. Defensive.
            if not db.get_run(run_id):
                return send_json(
                    self,
                    {'error': 'unknown run_id'},
                    status=404,
                )
            row = versions_io.upsert_draft(
                c, run_id, lines_json_str, client_edit_id,
                actor=actor, actor_user_id=actor_user_id,
            )
# Stage 4.11: drop the preview cache so the next /api/preview
        # reflects the new autosave (matches save_version's behaviour).
        invalidate_preview_cache(run_id)
        return send_json(self, {
            'edit_id': row['last_edit_id'],
            'edit_count': row['edit_count'],
            'saved_at': row['modified_at'],
            'modified_by': row['modified_by'],
            # Stage 4.14 — V# = latest_frozen + edit_count_position.
            # The draft row's edit_count tells us its position in the
            # ordered list. V# ticks up when the server creates a new
            # draft row (after 60s inactivity or different edit_id).
            'draft_version_no': versions_io.latest_version_no(
                db._conn(), run_id
            ) + int(row['edit_count']),
            'draft_id': row['draft_id'],
        })

    def handle_reviewer_queue(self):
        """GET /api/reviewer/queue  Ã¢â‚¬â€ projects assigned to me as reviewer.

        Used by the Flow 2 notification dropdown. Returns each project's
        latest in-review version (the one waiting for me to act).
        """
        from api.auth_middleware import require_auth
        current = require_auth(self)
        if current is None:
            return
        user_id = current['id']
        is_admin = int(current.get('is_admin', 0)) == 1
        with db._conn() as c:
            if is_admin:
                pv_rows = c.execute(
                    """SELECT pv.run_id, pv.version_no, pv.modified_at,
                              pv.modified_by, pv.assigned_reviewer_user_id,
                              r.filename, r.status AS run_status
                       FROM policy_versions pv
                       JOIN runs r ON r.run_id = pv.run_id
                       WHERE pv.review_status = 'in_review'
                       ORDER BY pv.modified_at DESC LIMIT 50"""
                ).fetchall()
            else:
                pv_rows = c.execute(
                    """SELECT pv.run_id, pv.version_no, pv.modified_at,
                              pv.modified_by, pv.assigned_reviewer_user_id,
                              r.filename, r.status AS run_status
                       FROM policy_versions pv
                       JOIN runs r ON r.run_id = pv.run_id
                       WHERE pv.review_status = 'in_review'
                         AND pv.assigned_reviewer_user_id = ?
                       ORDER BY pv.modified_at DESC LIMIT 50""",
                    (user_id,),
                ).fetchall()
            items = []
            for pv in pv_rows:
                run_id = pv['run_id']
                shared = users.get_my_shared_projects(c, user_id)
                your_access = None
                is_unread = False
                for s in shared:
                    if s['run_id'] == run_id:
                        your_access = s['your_access']
                        is_unread = s['is_unread']
                        break
                items.append({
                    'run_id': run_id,
                    'filename': pv['filename'],
                    'version_no': pv['version_no'],
                    'submitted_at': pv['modified_at'],
                    'submitted_by': pv['modified_by'],
                    'assigned_reviewer_user_id': pv['assigned_reviewer_user_id'],
                    'your_access': your_access,
                    'is_unread': is_unread,
                })
        return send_json(self, {'items': items})

    def handle_assign_reviewer(self, run_id, version_no):
        """POST /api/versions/<id>/<n>/assign  Ã¢â‚¬â€ assign a reviewer (editor+).

        Body: {user_id}. Requires editor access. The submitter cannot
        self-assign themselves; a different editor/approver must review.
        """
        from api.auth_middleware import require_project_access
        result = require_project_access(self, run_id, 'editor')
        if result is None:
            return
        current, _your_access = result
        length = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {}
        try:
            reviewer_id = int(body.get('user_id'))
        except Exception:
            return send_json(
                self, {'error': 'user_id required'},
                status=400,
            )
        with db._conn() as c:
            target = c.execute(
                "SELECT username FROM users WHERE user_id = ?",
                (reviewer_id,),
            ).fetchone()
            if not target:
                return send_json(
                    self, {'error': 'reviewer not found'}, status=404,
                )
            v = versions_io.get_version(c, run_id, version_no)
            if not v:
                return send_json(
                    self, {'error': 'version not found'},
                    status=404,
                )
            existing_assignee = v.get('assigned_reviewer_user_id')
            reviewer_username = target['username']
            c.execute(
                """UPDATE policy_versions
                   SET assigned_reviewer_user_id = ?
                   WHERE run_id = ? AND version_no = ?""",
                (reviewer_id, run_id, version_no),
            )
            # Audit row for the assignment.
            versions_io.add_audit(
                c, run_id, event_type='assign_reviewer',
                actor=current['username'],
                actor_user_id=current['id'],
                version_no=version_no,
                details=f"assigned {reviewer_username} (was {existing_assignee})",
            )
        return send_json(self, {
            'assigned_reviewer_user_id': reviewer_id,
            'assigned_reviewer': reviewer_username,
        })


if __name__ == '__main__':
    db.init_db()
    port = 8000
    print(f"[api] listening on http://localhost:{port}", flush=True)
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
