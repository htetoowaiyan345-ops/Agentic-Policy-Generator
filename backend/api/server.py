# server.py
import os
import sys
import json
import uuid
import threading
import re
import zipfile
import io
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
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
            run = db.get_run(run_id)
            if not run or not run.get('docx_path'):
                return send_json(self, {'error': 'no docx'}, status=404)
            # Stage 6 - require a published version before download.
            with db._conn() as c:
                published_no = versions_io.latest_published_version_no(c, run_id)
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
            send_file(self, run['docx_path'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', download_name='Policy_Output.docx')
        except Exception as e:
            send_json(self, {'error': f'download: {e}'}, status=500)

    def handle_download_all(self, run_id):
        """Bundle every file produced for this run (source + docx) into
        a single zip and stream it to the browser.

        Looks at the run's directory (`data/runs/<run_id>/`) and adds
        every regular file under it. The zip is named
        `<run_id>_all_files.zip`.
        """
        try:
            run_dir = RUNS_DIR / run_id
            if not run_dir.exists():
                return send_json(self, {'error': 'run not found'}, status=404)
            # Stage 6 - require a published version before all-files download.
            with db._conn() as c:
                published_no = versions_io.latest_published_version_no(c, run_id)
            if published_no is None:
                return send_json(
                    self,
                    {
                        'error': 'no_published_version',
                        'message': (
                            'No published version. Approve a version, then click '
                            'Publish & Generate DOCX to enable download.'
                        ),
                    },
                    status=409,
                )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(run_dir.iterdir()):
                    if p.is_file():
                        zf.write(p, arcname=p.name)
            data = buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Content-Disposition', f'attachment; filename="{run_id}_all_files.zip"')
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
        was actually written to the docx)."""
        try:
            run = db.get_run(run_id)
            if not run:
                return send_json(self, {'error': 'unknown run_id'}, status=404)
            if run['status'] != 'done':
                return send_json(self, {'error': 'result not ready', 'status': run['status']}, status=400)
            docx_path = run.get('docx_path')
            if not docx_path or not Path(docx_path).exists():
                return send_json(self, {'error': 'no docx on disk'}, status=404)
            from api.api_preview import build_preview_from_docx
            data = build_preview_from_docx(docx_path)
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