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

# backend/api/server.py → backend/ (parent)
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR
sys.path.insert(0, str(BACKEND_DIR))

from api import db
from api import pipeline_runner
from api import versions_io

DATA_DIR = PROJECT_ROOT / 'data'
RUNS_DIR = DATA_DIR / 'runs'
RUNS_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXT = {'.pdf', '.docx', '.txt'}
MAX_SIZE = 50 * 1024 * 1024

CORS_METHODS = 'GET, POST, OPTIONS'
CORS_HEADERS_ALLOWED = 'Content-Type, Accept'


# ---------------------------------------------------------------------------
# Phase 10 — preview-cache memoization
#
# `build_preview_from_docx` walks the published .docx via python-docx and
# paragraph-normalises each one — a cold call costs ~2–3s, a warm call
# ~25ms. We memoize the result keyed on (run_id, docx_path, docx_mtime) in
# an LRU so that subsequent Step-03 opens for the same run are instant.
#
# Invalidation rules:
#   * Cache entry is **invalidated** when the run's `docx_path` changes or
#     when `runs.audit_json` is rewritten (Phase 6/8 hook).
#   * TTL: 5 minutes. Even without explicit invalidation, the entry
#     expires and gets rebuilt on the next request.
#   * Size: max 64 entries — old ones evicted (LRU).
# ---------------------------------------------------------------------------
PREVIEW_CACHE_TTL_SEC = 5 * 60
PREVIEW_CACHE_MAX = 64


class PreviewCache:
    """Thread-safe LRU cache for `/api/preview/<run_id>` responses.

    Each entry is `(value, expire_at_ts, signature_str)` where
    `signature_str` is the (docx_path, docx_mtime, audit_json_len) tuple
    stringified — used to detect stale entries without DB read."""

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
        if path == '/api/upload':
            return self.handle_upload()
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

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/history':
            return self.handle_history()
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
            db.insert_run(run_id, filename, len(data), source_path=str(src_path))
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
            run = db.get_run(run_id)
            if not run or not run.get('docx_path'):
                return send_json(self, {'error': 'no docx'}, status=404)

            # Optional `version_no` query param — when provided, serve
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
                        # Approved but not yet published → build on the fly
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
                                # Phase 8 — apply the Word-style header /
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
        """Bundle every version's `.docx` from this run into a single
        zip and stream it to the browser.

        Per the user's spec ("Download all files — each file's currently
        viewing version"):
          - NO source file is bundled. The run's original upload is
            excluded unconditionally (the user must not get the source
            back via this endpoint).
          - ALL versions that appear in the Result dropdown are bundled:
            one `.docx` per `policy_versions` row. Versions that already
            have a per-version `<run_id>_approved_v<n>.docx` on disk are
            added as-is. Approved-but-not-published versions are built
            on the fly from their `lines_json` and bundled. Draft /
            in-review / rejected versions with no `.docx` are skipped
            but noted in `manifest.txt`.
          - The version the user is CURRENTLY VIEWING (passed by the
            frontend as the `version_no` query param, matching the
            Result dropdown selection) is marked with `_CURRENT.docx`
            suffix and listed first inside the zip. A `manifest.txt`
            summarises every entry with version + status + current flag.

        Zip filename: `<run_id>_all_v<N>.zip` where N is the
        currently-viewing version (or `_all.zip` when none was passed).
        """
        try:
            run_dir = RUNS_DIR / run_id
            if not run_dir.exists():
                return send_json(self, {'error': 'run not found'}, status=404)
            # Allow download once at least one version exists (any status).
            with db._conn() as c:
                versions = versions_io.get_versions(c, run_id)
                latest_pub = versions_io.latest_published_version_no(c, run_id)
            if not versions:
                return send_json(
                    self,
                    {
                        'error': 'no_versions',
                        'message': 'No versions exist for this run yet.',
                    },
                    status=404,
                )

            # Parse `version_no` from the query string. When the
            # frontend doesn't send one, fall back to the latest
            # published version (or the highest-numbered version).
            qs = parse_qs(urlparse(self.path).query)
            raw_no = (qs.get('version_no') or qs.get('version') or [None])[0]
            try:
                requested_no = int(raw_no) if raw_no is not None else None
            except (TypeError, ValueError):
                requested_no = None
            if requested_no is None:
                requested_no = latest_pub if latest_pub is not None else max(
                    v.get('version_no', 0) for v in versions
                )

            files_to_zip: list[tuple[Path, str]] = []
            manifest_lines: list[str] = []
            # Sort versions ascending; build on-the-fly for missing ones.
            for v in sorted(versions, key=lambda x: x.get('version_no', 0)):
                vno = int(v.get('version_no') or 0)
                status_v = v.get('review_status') or 'draft'
                is_current = (vno == requested_no)
                current_tag = '_CURRENT' if is_current else ''

                # 1. Prefer per-version file already on disk.
                per_version_main = run_dir / f'{run_id}_approved_v{vno}.docx'
                per_version_preview = run_dir / f'{run_id}_approved_v{vno}_preview.docx'
                chosen_path: Path | None = None
                if per_version_main.exists():
                    chosen_path = per_version_main
                elif per_version_preview.exists():
                    chosen_path = per_version_preview
                else:
                    # 2. Build on-the-fly if approved/published but no file.
                    if status_v in ('approved', 'published'):
                        try:
                            from policy_platform.pipeline import run_from_lines_json
                            from api.lines_json_extractor import normalise_lines_json
                            from api.docx_approved_export import _apply_header_footer
                            tmp_out = per_version_preview
                            run_from_lines_json(
                                lines_json=normalise_lines_json(v.get('lines_json') or []),
                                output_path=tmp_out,
                                run_id=run_id,
                                document_name=f'{run_id}_v{vno}_preview',
                                fail_on_validation=False,
                            )
                            try:
                                from docx import Document as _Doc2
                                doc2 = _Doc2(str(tmp_out))
                                _apply_header_footer(
                                    doc2, v.get('lines_json') or [],
                                )
                                doc2.save(str(tmp_out))
                            except Exception as ee:
                                print(f'[handle_download_all] header/footer apply failed: {ee}', flush=True)
                            chosen_path = tmp_out
                        except Exception as e:
                            print(f'[handle_download_all] on-the-fly build for v{vno} failed: {e}', flush=True)

                if chosen_path is None:
                    manifest_lines.append(
                        f'v{vno}\t{status_v}\t(no .docx generated — skipped)'
                    )
                    continue

                arcname = f'v{vno}_{status_v}{current_tag}.docx'
                files_to_zip.append((chosen_path, arcname))
                marker = ' [CURRENT VIEW]' if is_current else ''
                manifest_lines.append(
                    f'v{vno}\t{status_v}\t{arcname}{marker}'
                )

            if not files_to_zip:
                return send_json(
                    self,
                    {
                        'error': 'no_docx_available',
                        'message': (
                            'No .docx output is available for any version '
                            'in this run. Approve and publish at least one '
                            'version first.'
                        ),
                    },
                    status=404,
                )

            # Source file is EXPLICITLY excluded — see header docstring.
            # Do not add any path matching `source.*` to files_to_zip.

            # Re-order: CURRENT entry first, rest in version order.
            files_to_zip.sort(
                key=lambda t: (not t[1].endswith('_CURRENT.docx'), t[1])
            )

            # Build manifest header.
            manifest_header = (
                f'Run: {run_id}\n'
                f'Current view version: v{requested_no}\n'
                f'Generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}\n'
                f'Entries ({len(files_to_zip)}):\n'
            )
            manifest_text = manifest_header + '\n'.join(manifest_lines) + '\n'

            zip_name = f'{run_id}_all_v{requested_no}.zip'
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Manifest goes FIRST so users see it on extraction.
                zf.writestr('manifest.txt', manifest_text)
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
            rows = db.list_done_runs()
            send_json(self, rows)
        except Exception as e:
            send_json(self, {'error': f'history: {e}'}, status=500)

    # Stage 2 - workflow / version-control read handlers (READ-ONLY).

    def handle_versions_list(self, run_id):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                items = versions_io.get_versions(c, run_id)
            send_json(self, {'items': items})
        except Exception as e:
            send_json(self, {'error': f'versions_list: {e}'}, status=500)

    def handle_version_get(self, run_id, version_no):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            with db._conn() as c:
                v = versions_io.get_version(c, run_id, version_no)
            if not v:
                return send_json(self, {'error': 'version not found'}, status=404)
            send_json(self, v)
        except Exception as e:
            send_json(self, {'error': f'version_get: {e}'}, status=500)

    def handle_comments_list(self, run_id, version_no):
        try:
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
            author = body.get('author') or 'user'
            with db._conn() as c:
                comment = versions_io.add_comment(
                    c, run_id, version_no, text, anchor_kind, anchor_key, author
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
        try:
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
            actor = (body.get('actor') or 'user').strip() or 'user'
            with db._conn() as c:
                new_v = versions_io.save_version(
                    c, run_id, lines_json_str, change_summary, actor
                )
            # Phase 10 — drop the preview cache for this run. The next
            # /api/preview request will rebuild from the current .docx.
            invalidate_preview_cache(run_id)
            send_json(self, new_v)
        except Exception as e:
            send_json(self, {'error': f'version_save: {e}'}, status=500)

    def handle_version_submit(self, run_id, version_no):
        """draft -> in_review. Only if current status == draft."""
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            actor = (body.get('actor') or 'user').strip() or 'user'
            with db._conn() as c:
                cur = versions_io.get_version(c, run_id, version_no)
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
                    version_no,
                    'in_review',
                    actor=actor,
                    reviewer=actor,
                    event_type='submitted',
                    details=(
                        f"V{version_no} submitted for review by {actor}"
                    ),
                )
            send_json(self, updated)
        except Exception as e:
            send_json(self, {'error': f'version_submit: {e}'}, status=500)

    def handle_version_review(self, run_id, version_no):
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
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
            reviewer = (body.get('reviewer') or '').strip() or 'reviewer'
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
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            actor = (body.get('actor') or 'user').strip() or 'user'
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
                )
                if not updated:
                    return send_json(
                        self,
                        {'error': 'publish failed (brain verify or docx build)'},
                        status=500,
                    )
            # Phase 10 — published docx path changed, drop the preview cache
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


if __name__ == '__main__':
    db.init_db()
    port = 8000
    print(f"[api] listening on http://localhost:{port}", flush=True)
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()