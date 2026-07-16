# db.py
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

# backend/api/db.py → backend/ (parent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(PROJECT_ROOT / 'data' / 'policy_history.db')
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create schema if it doesn't exist. Adds new columns if upgrading."""
    # Local import to avoid circular dependency (versions_io only needs sqlite3).
    from api import versions_io

    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id            TEXT PRIMARY KEY,
                filename          TEXT NOT NULL,
                file_size_bytes   INTEGER,
                created_at        TEXT NOT NULL,
                status            TEXT NOT NULL,
                sections_filled   INTEGER DEFAULT 0,
                markers_count     INTEGER DEFAULT 0,
                template_sha      TEXT,
                framework_version TEXT,
                source_path       TEXT,
                docx_path         TEXT,
                audit_json        TEXT,
                error_message     TEXT
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_runs_status_created
            ON runs (status, created_at DESC)
        """)
        # Migration for older DBs missing newer columns
        existing = {row[1] for row in c.execute("PRAGMA table_info(runs)").fetchall()}
        if 'template_sha' not in existing:
            c.execute("ALTER TABLE runs ADD COLUMN template_sha TEXT")
        if 'framework_version' not in existing:
            c.execute("ALTER TABLE runs ADD COLUMN framework_version TEXT")
        if 'source_path' not in existing:
            c.execute("ALTER TABLE runs ADD COLUMN source_path TEXT")
        if 'audit_json' not in existing:
            c.execute("ALTER TABLE runs ADD COLUMN audit_json TEXT")

        # Stage 1 - workflow / version-control tables.
        versions_io.init_version_tables(c)


def insert_run(run_id, filename, size, source_path=None):
    with _conn() as c:
        c.execute(
            """INSERT INTO runs
               (run_id, filename, file_size_bytes, created_at, status, source_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, filename, size, datetime.utcnow().isoformat() + 'Z', 'uploaded', source_path)
        )


def update_status(run_id, status, **kwargs):
    """Update run status + any optional fields:
       sections_filled, markers_count, docx_path, result_json, error_message,
       template_sha, framework_version, audit_json.
    """
    allowed = {
        'sections_filled', 'markers_count', 'docx_path', 'result_json',
        'error_message', 'template_sha', 'framework_version', 'audit_json',
    }
    fields = ['status = ?']
    vals = [status]
    for k, v in kwargs.items():
        if k in allowed:
            fields.append(f'{k} = ?')
            vals.append(v)
    vals.append(run_id)
    with _conn() as c:
        c.execute(f"UPDATE runs SET {', '.join(fields)} WHERE run_id = ?", vals)


def get_run(run_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_done_runs():
    with _conn() as c:
        rows = c.execute(
            """SELECT run_id, filename, created_at, status, sections_filled, markers_count
               FROM runs WHERE status='done' ORDER BY created_at DESC LIMIT 50"""
        ).fetchall()
        return [dict(r) for r in rows]