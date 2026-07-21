"""versions_io.py - DB helpers for the workflow / version-control feature.

Adds 3 tables to policy_history.db (created if not exist):

    policy_versions  - one row per version (V1, V2, V3...) of a run
    review_comments  - comments anchored to a slot / paragraph or general
    audit_log        - immutable event trail per run

The existing `runs` table is NOT modified. All writes here go through the
shared DB connection helper. Existing data and tests are unaffected.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    """ISO-8601 UTC timestamp with Z suffix."""
    return datetime.now(timezone.utc).isoformat()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS policy_versions (
    version_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    version_no      INTEGER NOT NULL,
    lines_json      TEXT NOT NULL,
    change_summary  TEXT,
    modified_by     TEXT NOT NULL DEFAULT 'system',
    modified_at     TEXT NOT NULL,
    review_status   TEXT NOT NULL DEFAULT 'draft',
    reviewer        TEXT,
    review_note     TEXT,
    reviewed_at     TEXT,
    published_at    TEXT,
    docx_path       TEXT,
    source          TEXT NOT NULL DEFAULT 'pipeline',
    actor_user_id           INTEGER,
    assigned_reviewer_user_id INTEGER,
    UNIQUE(run_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_versions_run_status
    ON policy_versions (run_id, review_status);

CREATE TABLE IF NOT EXISTS review_comments (
    comment_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    version_no      INTEGER NOT NULL,
    anchor_kind     TEXT,
    anchor_key      TEXT,
    body            TEXT NOT NULL,
    author          TEXT NOT NULL DEFAULT 'user',
    author_user_id  INTEGER,
    created_at      TEXT NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_comments_run_version
    ON review_comments (run_id, version_no);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    version_no      INTEGER,
    event_type      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    actor_user_id   INTEGER,
    details         TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_run_created
    ON audit_log (run_id, created_at DESC);
"""


def init_version_tables(conn) -> None:
    """Create the 3 workflow tables + their indexes. Idempotent."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def create_initial_version(
    conn,
    run_id: str,
    lines_json: str,
    document_name: str = '',
    actor: str = 'system',
) -> dict | None:
    """Insert V1 for `run_id`. Idempotent: if V1 already exists, no-op.

    `lines_json` must be a JSON string (list of [kind, payload] pairs).
    Returns the inserted row as dict, or None if V1 already existed.
    """
    if not run_id or not lines_json:
        return None
    existing = conn.execute(
        "SELECT version_id FROM policy_versions WHERE run_id = ? AND version_no = 1",
        (run_id,),
    ).fetchone()
    if existing:
        return None
    summary = f"Initial version for {document_name}" if document_name else "Initial version (auto-created by pipeline)"
    cur = conn.execute(
        """INSERT INTO policy_versions
           (run_id, version_no, lines_json, change_summary, modified_by, modified_at,
            review_status, source)
           VALUES (?, 1, ?, ?, ?, ?, 'draft', 'pipeline')""",
        (run_id, lines_json, summary, actor, _now()),
    )
    conn.execute(
        """INSERT INTO audit_log (run_id, version_no, event_type, actor, details, created_at)
           VALUES (?, 1, 'created', ?, ?, ?)""",
        (run_id, actor, f"V1 created for document {document_name!r}", _now()),
    )
    conn.commit()
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM policy_versions WHERE version_id = ?", (cur.lastrowid,)
        ).fetchone()
    )


def save_version(
    conn,
    run_id: str,
    lines_json: str,
    change_summary: str,
    actor: str = 'user',
    author_user_id: int | None = None,
) -> dict:
    """Create V(n+1) where n is the current max version_no for `run_id`.

    The new version starts with review_status='draft' regardless of prior status.
    Source='user_edit'. Audit 'edited' event is logged.
    Returns the inserted row as dict.

    Stage 3 (multi-user): `author_user_id` is the logged-in user id and is
    recorded in both `policy_versions.actor_user_id` (new column) and
    `audit_log.actor_user_id` for attribution.
    """
    if not change_summary or not change_summary.strip():
        raise ValueError("change_summary is required")
    row = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS m FROM policy_versions WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    next_no = (row["m"] if row else 0) + 1
    cur = conn.execute(
        """INSERT INTO policy_versions
           (run_id, version_no, lines_json, change_summary, modified_by, modified_at,
            review_status, source, actor_user_id)
           VALUES (?, ?, ?, ?, ?, ?, 'draft', 'user_edit', ?)""",
        (run_id, next_no, lines_json, change_summary.strip(), actor, _now(),
         author_user_id),
    )
    conn.execute(
        """INSERT INTO audit_log (run_id, version_no, event_type, actor, actor_user_id, details, created_at)
           VALUES (?, ?, 'edited', ?, ?, ?, ?)""",
        (
            run_id,
            next_no,
            actor,
            author_user_id,
            f"V{next_no} saved: {change_summary.strip()[:200]}",
            _now(),
        ),
    )
    conn.commit()
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM policy_versions WHERE version_id = ?", (cur.lastrowid,)
        ).fetchone()
    )


def latest_version_no(conn, run_id: str) -> int:
    """Max version_no for run. 0 if none."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS m FROM policy_versions WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["m"]) if row else 0


def get_versions(conn, run_id: str) -> list[dict]:
    """List versions for run (metadata only, NO lines_json)."""
    rows = conn.execute(
        """SELECT version_id, run_id, version_no, change_summary, modified_by,
                  modified_at, review_status, reviewer, review_note, reviewed_at,
                  published_at, source
           FROM policy_versions WHERE run_id = ? ORDER BY version_no ASC""",
        (run_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_version(conn, run_id: str, version_no: int) -> dict | None:
    """One version's full row including lines_json parsed back to object."""
    row = conn.execute(
        "SELECT * FROM policy_versions WHERE run_id = ? AND version_no = ?",
        (run_id, version_no),
    ).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    try:
        d["lines_json"] = json.loads(d.get("lines_json") or "[]")
    except Exception:
        d["lines_json"] = []
    return d


def get_previous_version(conn, run_id: str, version_no: int) -> dict | None:
    """Return the immediately-prior version (`version_no - 1`) for diffing.

    Phase 6 — used by `publish_to_brain.publish_approved_version` to
    build a per-slot before/after audit xlsx.

    Returns `None` when `version_no` is 1 or when the prior row does not
    exist; callers should treat that as "no previous version" (the
    diff rows will have empty `before_text` in that case).
    """
    if version_no is None or version_no <= 1:
        return None
    return get_version(conn, run_id, version_no - 1)


def latest_published_version_no(conn, run_id: str) -> int | None:
    """Max version_no with review_status='published'. None if no published version."""
    row = conn.execute(
        """SELECT COALESCE(MAX(version_no), 0) AS m FROM policy_versions
           WHERE run_id = ? AND review_status = 'published'""",
        (run_id,),
    ).fetchone()
    val = int(row["m"]) if row else 0
    return val if val > 0 else None


def set_review_status(
    conn,
    run_id: str,
    version_no: int,
    new_status: str,
    actor: str = 'user',
    reviewer: str | None = None,
    note: str | None = None,
    event_type: str = 'status_changed',
    details: str | None = None,
    actor_user_id: int | None = None,
) -> dict | None:
    """Update review_status (and related reviewer/note/timestamps).

    Writes audit row. Returns updated version dict or None if not found.

    Stage 3 (multi-user): `actor_user_id` is recorded in the audit row
    for attribution. The `reviewer` field stays as free-text for legacy
    compatibility (it duplicates `actor` in most cases).
    """
    existing = conn.execute(
        "SELECT * FROM policy_versions WHERE run_id = ? AND version_no = ?",
        (run_id, version_no),
    ).fetchone()
    if not existing:
        return None
    reviewed_at = _now() if new_status in ('in_review', 'approved', 'rejected', 'published') else existing['reviewed_at']
    published_at = _now() if new_status == 'published' else existing['published_at']
    conn.execute(
        """UPDATE policy_versions
           SET review_status = ?, reviewer = ?, review_note = ?,
               reviewed_at = ?, published_at = ?
           WHERE run_id = ? AND version_no = ?""",
        (new_status, reviewer, note, reviewed_at, published_at, run_id, version_no),
    )
    if not details:
        details = f"V{version_no} -> {new_status}" + (f" ({note[:200]})" if note else '')
    conn.execute(
        """INSERT INTO audit_log (run_id, version_no, event_type, actor, actor_user_id, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, version_no, event_type, actor, actor_user_id, details, _now()),
    )
    conn.commit()
    return get_version(conn, run_id, version_no)


def set_published(
    conn,
    run_id: str,
    version_no: int,
    docx_path: str,
    actor: str = 'user',
    actor_user_id: int | None = None,
) -> dict | None:
    """Mark version as published and record docx path. Writes audit row.

    Stage 3 (multi-user): `actor_user_id` is recorded in audit log.
    """
    existing = conn.execute(
        "SELECT * FROM policy_versions WHERE run_id = ? AND version_no = ?",
        (run_id, version_no),
    ).fetchone()
    if not existing:
        return None
    conn.execute(
        """UPDATE policy_versions
           SET review_status = 'published', docx_path = ?, published_at = ?
           WHERE run_id = ? AND version_no = ?""",
        (docx_path, _now(), run_id, version_no),
    )
    conn.execute(
        """INSERT INTO audit_log (run_id, version_no, event_type, actor, actor_user_id, details, created_at)
           VALUES (?, ?, 'published', ?, ?, ?, ?)""",
        (
            run_id,
            version_no,
            actor,
            actor_user_id,
            f"V{version_no} published; docx={docx_path}",
            _now(),
        ),
    )
    conn.commit()
    return get_version(conn, run_id, version_no)


def add_comment(
    conn,
    run_id: str,
    version_no: int,
    body: str,
    anchor_kind: str | None = None,
    anchor_key: str | None = None,
    author: str = 'user',
    author_user_id: int | None = None,
) -> dict:
    """Add a comment to (run_id, version_no). Returns new comment dict.

    Stage 3 (multi-user): `author_user_id` is the logged-in user id
    and is recorded in `review_comments.author_user_id` and
    `audit_log.actor_user_id` for attribution.
    """
    if not body or not body.strip():
        raise ValueError("comment body is required")
    cur = conn.execute(
        """INSERT INTO review_comments
           (run_id, version_no, anchor_kind, anchor_key, body, author, author_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, version_no, anchor_kind, anchor_key, body.strip(), author, author_user_id, _now()),
    )
    conn.execute(
        """INSERT INTO audit_log (run_id, version_no, event_type, actor, actor_user_id, details, created_at)
           VALUES (?, ?, 'comment_added', ?, ?, ?, ?)""",
        (
            run_id,
            version_no,
            author,
            author_user_id,
            f"Comment added on {anchor_kind or 'general'}: {body.strip()[:120]}",
            _now(),
        ),
    )
    conn.commit()
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM review_comments WHERE comment_id = ?", (cur.lastrowid,)
        ).fetchone()
    )


def list_comments(conn, run_id: str, version_no: int) -> list[dict]:
    """All comments for a given (run_id, version_no)."""
    rows = conn.execute(
        """SELECT * FROM review_comments
           WHERE run_id = ? AND version_no = ?
           ORDER BY created_at ASC""",
        (run_id, version_no),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def resolve_comment(conn, run_id: str, version_no: int, comment_id: int) -> bool:
    """Mark a comment resolved. Returns True on success."""
    cur = conn.execute(
        """UPDATE review_comments SET resolved = 1
           WHERE run_id = ? AND version_no = ? AND comment_id = ?""",
        (run_id, version_no, comment_id),
    )
    conn.commit()
    return cur.rowcount > 0


def add_audit(
    conn,
    run_id: str,
    event_type: str,
    actor: str = 'system',
    version_no: int | None = None,
    details: str | None = None,
) -> int:
    """Append to audit_log manually. Returns new audit_id."""
    cur = conn.execute(
        """INSERT INTO audit_log (run_id, version_no, event_type, actor, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, version_no, event_type, actor, details, _now()),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def list_audit(conn, run_id: str) -> list[dict]:
    """All audit rows for run, most recent first."""
    rows = conn.execute(
        """SELECT * FROM audit_log WHERE run_id = ?
           ORDER BY created_at DESC""",
        (run_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    return {k: row[k] for k in row.keys()}
