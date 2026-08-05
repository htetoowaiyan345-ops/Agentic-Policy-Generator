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

        # Notification: shared_notifications table — records every "X shared
        # project Y with Z" event so the sender can later see their own
        # outgoing shares in the bell ("you shared Project A with bob")
        # alongside the incoming-shares feed ("alice shared Project B
        # with you"). One row per (sender, recipient, run) per share event;
        # re-sharing bumps `created_at`. Per-user dismissal via
        # `sender_dismissed_at` (only the sender's view is suppressed;
        # the recipient still sees the incoming notification as before).
        c.execute("""
            CREATE TABLE IF NOT EXISTS shared_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                sender_user_id INTEGER NOT NULL,
                recipient_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                sender_dismissed_at TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id),
                FOREIGN KEY (sender_user_id) REFERENCES users(user_id),
                FOREIGN KEY (recipient_user_id) REFERENCES users(user_id)
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_notifications_sender
            ON shared_notifications (sender_user_id, created_at DESC)
        """)
        # Per-recipient uniqueness: one share row per (sender, run, recipient).
        # Re-sharing the same project with the same person updates the
        # existing row's `created_at` instead of duplicating.
        sn_cols = {row[1] for row in c.execute("PRAGMA table_info(shared_notifications)").fetchall()}
        if not sn_cols:
            pass  # fresh table created above; nothing to migrate yet.

        c.commit()


def insert_run(run_id, filename, size, source_path=None, created_by_user_id=None):
    """Insert a new run. If `created_by_user_id` is provided, also auto-add
    that user to `project_members` with `access_level='approver'` so they
    have full access to their own run.

    Stage 4.x — Run History storage cap (per user spec "30 files max"):
    before inserting, count rows in the `runs` table. If the count is at
    or above `MAX_RUN_HISTORY`, evict the oldest runs (and all related
    rows + on-disk .docx) until there's room for the new one. This keeps
    Run History bounded so the DB + disk don't grow without limit.
    Eviction is FIFO by `created_at` ascending — the oldest run is
    removed first, regardless of status. Per spec, all 30 slots count
    (uploaded, processing, done, failed) so Run History always shows
    the 30 most recent files regardless of status.
    """
    with _conn() as c:
        # Evict oldest runs until there's room for the new one. We evict
        # BEFORE inserting so the count check uses the post-evict count.
        existing = c.execute(
            "SELECT run_id FROM runs ORDER BY created_at ASC"
        ).fetchall()
        # How many do we need to evict? `existing` will hold the post-
        # evict list once we DELETE rows from it. We need to leave at
        # most MAX_RUN_HISTORY - 1 rows so the new INSERT keeps us at
        # the cap.
        evict_count = max(0, len(existing) - (MAX_RUN_HISTORY - 1))
        for i in range(evict_count):
            oldest_id = existing[i]['run_id']
            _evict_run(c, oldest_id)
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


# Run History storage cap. Per user spec: at most 30 files kept; the
# 31st upload auto-deletes the oldest. Applies to ALL statuses so Run
# History always shows the 30 most recent files (uploaded + processing
# + done + failed). The constant is module-level so tests can patch it
# if they need a smaller cap for fixtures.
MAX_RUN_HISTORY = 30


def _evict_run(c, run_id: str) -> None:
    """Delete a single run and everything attached to it: per-run
    metadata in `runs`, version history (`policy_versions`), draft
    buffer (`policy_drafts`), review comments, audit entries, project
    membership rows, and share notifications. The on-disk .docx file
    (if any) is also removed. Called from `insert_run` to enforce the
    MAX_RUN_HISTORY cap.

    Order matters: child rows first, then `runs`, so no FK constraints
    are violated even though `PRAGMA foreign_keys` is OFF in this DB.
    """
    # Read the docx_path so we can delete the file from disk after the
    # DB rows are gone. Errors here are non-fatal — the DB rows are
    # the canonical record; missing files are tolerated by the
    # preview/download endpoints.
    docx_path = None
    row = c.execute(
        "SELECT docx_path FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row:
        docx_path = row['docx_path']
    # Delete child rows first. We don't trust CASCADE because
    # `PRAGMA foreign_keys = 0` and most FKs don't declare ON DELETE.
    c.execute("DELETE FROM policy_versions WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM policy_drafts WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM review_comments WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM audit_log WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM project_members WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM shared_notifications WHERE run_id = ?", (run_id,))
    # Finally the run row itself.
    c.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    # Remove the on-disk .docx if it exists. Defensive: Path may be
    # missing or already gone; we just log and move on.
    if docx_path:
        try:
            p = Path(docx_path)
            if p.exists():
                p.unlink()
        except Exception:
            # Don't let a filesystem hiccup abort the eviction. The
            # run row is already gone; the orphaned file will be
            # garbage-collected on next disk-cleanup pass.
            pass


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