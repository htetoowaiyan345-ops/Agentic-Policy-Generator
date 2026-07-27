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
    from api import versions_io, users

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
        # Stage 1.4 (multi-user): runs.created_by_user_id (nullable, for
        # orphaned data; new runs fill this from the session's user id).
        if 'created_by_user_id' not in existing:
            c.execute("ALTER TABLE runs ADD COLUMN created_by_user_id INTEGER")

        # Stage 1 - workflow / version-control tables.
        versions_io.init_version_tables(c)

        # Stage 4.11 - mutable draft table (one row per run). The draft
        # is the in-progress edit buffer; on submit it's snapshotted
        # into a frozen `policy_versions` row and the draft is removed.
        versions_io.init_drafts_table(c)

        # Stage 1.4 (multi-user): project membership + reviewer assignment
        # + actor attribution. Idempotent.
        users.init_user_tables(c)
        seeded = users.seed_users_if_empty(c)
        if seeded:
            print(f"[init_db] seeded {seeded} user(s)", flush=True)

        # Stage 1.4 migrations for policy_versions + audit_log + review_comments.
        # policy_versions: assigned_reviewer_user_id + actor_user_id
        pv_cols = {row[1] for row in c.execute("PRAGMA table_info(policy_versions)").fetchall()}
        if 'assigned_reviewer_user_id' not in pv_cols:
            c.execute("ALTER TABLE policy_versions ADD COLUMN assigned_reviewer_user_id INTEGER")
        if 'actor_user_id' not in pv_cols:
            c.execute("ALTER TABLE policy_versions ADD COLUMN actor_user_id INTEGER")
        # audit_log: actor_user_id
        al_cols = {row[1] for row in c.execute("PRAGMA table_info(audit_log)").fetchall()}
        if 'actor_user_id' not in al_cols:
            c.execute("ALTER TABLE audit_log ADD COLUMN actor_user_id INTEGER")
        # review_comments: author_user_id
        rc_cols = {row[1] for row in c.execute("PRAGMA table_info(review_comments)").fetchall()}
        if 'author_user_id' not in rc_cols:
            c.execute("ALTER TABLE review_comments ADD COLUMN author_user_id INTEGER")

        # Stage 4.1.1 — project_members.last_seen_at (nullable; used by Flow 2
        # to count unread shared projects for the header bell). Idempotent.
        pm_cols = {row[1] for row in c.execute("PRAGMA table_info(project_members)").fetchall()}
        if 'last_seen_at' not in pm_cols:
            c.execute("ALTER TABLE project_members ADD COLUMN last_seen_at TEXT")

        # Stage 4.x — project_members.dismissed_at (nullable; per-user
        # dismissal flag for Flow 2 notifications). When set, the row is
        # excluded from `get_my_shared_projects` so the bell no longer
        # surfaces it for this user. Idempotent.
        if 'dismissed_at' not in pm_cols:
            c.execute("ALTER TABLE project_members ADD COLUMN dismissed_at TEXT")

        c.commit()


def insert_run(run_id, filename, size, source_path=None, created_by_user_id=None):
    """Insert a new run. If `created_by_user_id` is provided, also auto-add
    that user to `project_members` with `access_level='approver'` so they
    have full access to their own run."""
    with _conn() as c:
        c.execute(
            """INSERT INTO runs
               (run_id, filename, file_size_bytes, created_at, status, source_path,
                created_by_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, filename, size, datetime.utcnow().isoformat() + 'Z', 'uploaded',
             source_path, created_by_user_id)
        )
        if created_by_user_id is not None:
            from api import users as _users
            _users.add_project_member(
                c, run_id, int(created_by_user_id), 'approver',
                added_by_user_id=int(created_by_user_id),
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