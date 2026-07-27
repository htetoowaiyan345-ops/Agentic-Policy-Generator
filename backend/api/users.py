"""users.py — Multi-user authentication and session management.

Adds the following tables (created via `init_user_tables`):
  - users             : login identity (id, username, password_hash, is_admin,
                         password_source, created_at)
  - user_sessions     : opaque session tokens (token, user_id, created_at,
                         expires_at)
  - project_members   : per-project access (run_id, user_id, access_level,
                         added_by_user_id, added_at)

The `admin` user has its password stored with `password_source='env'`, meaning
verification reads `os.getenv('ADMIN_PASSWORD')` directly. This is by user
request (admin password kept in `.env`, not hashed). Regular users have
`password_source='hash'` and their passwords are stored as bcrypt hashes.

Session tokens are random 32-byte URL-safe strings (NOT JWT — the backend
uses stdlib only). Tokens are sent by the client in the
`Authorization: Bearer <token>` header.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def _hash_password_bcrypt(plain: str) -> str:
    """Hash a password with bcrypt (cost factor 12)."""
    import bcrypt
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def _verify_password_bcrypt(plain: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash. Constant-time compare."""
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _verify_password_env(plain: str) -> bool:
    """Verify against ADMIN_PASSWORD env var. Constant-time compare."""
    expected = os.getenv("ADMIN_PASSWORD", "")
    if not expected:
        return False
    return hmac.compare_digest(plain.encode("utf-8"), expected.encode("utf-8"))


def _verify_password_for_user(user_row: sqlite3.Row, plain: str) -> bool:
    """Verify a password attempt against the user's stored credentials."""
    source = (user_row["password_source"] if user_row else "") or "hash"
    if source == "env":
        return _verify_password_env(plain)
    if not user_row["password_hash"]:
        return False
    return _verify_password_bcrypt(plain, user_row["password_hash"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT NOT NULL UNIQUE,
    password_hash    TEXT,
    is_admin         INTEGER NOT NULL DEFAULT 0,
    password_source  TEXT NOT NULL DEFAULT 'hash',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON user_sessions (user_id);

CREATE TABLE IF NOT EXISTS project_members (
    run_id           TEXT NOT NULL,
    user_id          INTEGER NOT NULL,
    access_level     TEXT NOT NULL,
    added_by_user_id INTEGER,
    added_at         TEXT NOT NULL,
    last_seen_at     TEXT,
    PRIMARY KEY (run_id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_project_members_user
    ON project_members (user_id);
"""


def init_user_tables(conn) -> None:
    """Create user / session / project_members tables + indexes. Idempotent."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Seed users (3 users, on first init only)
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SEED_USERS = [
    {
        "username": "admin",
        "password": None,  # uses ADMIN_PASSWORD env var
        "is_admin": True,
        "password_source": "env",
    },
    {
        "username": "user1",
        "password": "user123",
        "is_admin": False,
        "password_source": "hash",
    },
    {
        "username": "user2",
        "password": "user123",
        "is_admin": False,
        "password_source": "hash",
    },
]


def seed_users_if_empty(conn) -> int:
    """Insert the 3 seed users if the users table is empty.

    Returns the number of users inserted (0 if already seeded).
    """
    existing = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    if existing and int(existing["n"]) > 0:
        return 0
    inserted = 0
    for spec in SEED_USERS:
        if spec["password_source"] == "hash":
            password_hash = _hash_password_bcrypt(spec["password"])
        else:
            password_hash = None
        conn.execute(
            """INSERT INTO users
               (username, password_hash, is_admin, password_source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                spec["username"],
                password_hash,
                1 if spec["is_admin"] else 0,
                spec["password_source"],
                _now(),
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def _row_to_user_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    return {
        "id": int(row["user_id"]),
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
    }


def get_user_by_username(conn, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()


def get_user(conn, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return _row_to_user_dict(row)


def list_users(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM users ORDER BY user_id ASC"""
    ).fetchall()
    return [_row_to_user_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _new_token() -> str:
    """Generate a 32-byte URL-safe random token."""
    return secrets.token_urlsafe(32)


def create_session(conn, user_id: int) -> dict:
    """Create a new session for the user. Returns {token, user, expires_at}."""
    token = _new_token()
    now = _now()
    expires = _now()  # we re-use the helper; session expiry is approximate
    # Compute expires_at properly
    from datetime import timedelta
    expires_dt = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)
    expires = expires_dt.isoformat()
    conn.execute(
        """INSERT INTO user_sessions (token, user_id, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (token, int(user_id), now, expires),
    )
    conn.commit()
    user = get_user(conn, user_id)
    return {"token": token, "user": user, "expires_at": expires}


def delete_session(conn, token: str) -> bool:
    cur = conn.execute(
        "DELETE FROM user_sessions WHERE token = ?", (token,)
    )
    conn.commit()
    return cur.rowcount > 0


def get_user_by_token(conn, token: str) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT u.*
           FROM user_sessions s
           JOIN users u ON u.user_id = s.user_id
           WHERE s.token = ?""",
        (token,),
    ).fetchone()
    if not row:
        return None
    return _row_to_user_dict(row)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(conn, username: str, password: str) -> dict | None:
    """Verify credentials and create a session. Returns {token, user, expires_at}
    on success, or None on failure."""
    if not username or not password:
        return None
    user_row = get_user_by_username(conn, username)
    if not user_row:
        # Still hash a dummy password to avoid timing-based username enumeration
        _verify_password_bcrypt(password, "$2b$12$" + "0" * 53)
        return None
    if not _verify_password_for_user(user_row, password):
        return None
    return create_session(conn, int(user_row["user_id"]))


# ---------------------------------------------------------------------------
# Project membership
# ---------------------------------------------------------------------------

VALID_ACCESS_LEVELS = ("viewer", "editor", "approver")
ACCESS_RANK = {"viewer": 1, "editor": 2, "approver": 3}


def add_project_member(
    conn,
    run_id: str,
    user_id: int,
    access_level: str,
    added_by_user_id: int | None = None,
) -> dict:
    """Add or update a project member. Returns the row."""
    if access_level not in VALID_ACCESS_LEVELS:
        raise ValueError(f"Invalid access_level: {access_level!r}")
    conn.execute(
        """INSERT INTO project_members
           (run_id, user_id, access_level, added_by_user_id, added_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(run_id, user_id)
           DO UPDATE SET access_level = excluded.access_level,
                         added_by_user_id = excluded.added_by_user_id,
                         added_at = excluded.added_at""",
        (run_id, int(user_id), access_level, added_by_user_id, _now()),
    )
    conn.commit()
    return {
        "run_id": run_id,
        "user_id": int(user_id),
        "access_level": access_level,
        "added_by_user_id": added_by_user_id,
    }


def remove_project_member(conn, run_id: str, user_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM project_members WHERE run_id = ? AND user_id = ?",
        (run_id, int(user_id)),
    )
    conn.commit()
    return cur.rowcount > 0


def list_project_members(conn, run_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT pm.run_id, pm.user_id, pm.access_level,
                  pm.added_by_user_id, pm.added_at,
                  u.username
           FROM project_members pm
           LEFT JOIN users u ON u.user_id = pm.user_id
           WHERE pm.run_id = ?
           ORDER BY pm.added_at ASC""",
        (run_id,),
    ).fetchall()
    return [
        {
            "run_id": r["run_id"],
            "user_id": int(r["user_id"]),
            "username": r["username"],
            "access_level": r["access_level"],
            "added_by_user_id": r["added_by_user_id"],
            "added_at": r["added_at"],
        }
        for r in rows
    ]


def get_user_project_access(
    conn, user_id: int, run_id: str
) -> str | None:
    """Return the user's access level on the run, or None if not a member.

    Admin users (`is_admin=1`) get 'approver' access to every run, regardless
    of project membership.
    """
    user = conn.execute(
        "SELECT is_admin FROM users WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    if not user:
        return None
    if int(user["is_admin"]) == 1:
        return "approver"
    row = conn.execute(
        """SELECT access_level FROM project_members
           WHERE run_id = ? AND user_id = ?""",
        (run_id, int(user_id)),
    ).fetchone()
    if not row:
        return None
    return row["access_level"]


def meets_access(user_level: str | None, required: str) -> bool:
    """True if `user_level` meets or exceeds `required` per ACCESS_RANK."""
    if user_level is None:
        return False
    return ACCESS_RANK.get(user_level, 0) >= ACCESS_RANK.get(required, 0)


# ---------------------------------------------------------------------------
# Stage 4.1.2 — Flow 2 helper queries
# ---------------------------------------------------------------------------

def get_my_shared_projects(conn, user_id: int) -> list[dict]:
    """Return every run `user_id` can access, joined with `project_members`
    metadata for Flow 2's header notification + Flow 3's access badge.

    Each row contains:
      - run_id, filename, created_at, status (from `runs`)
      - your_access  : access_level on this project
      - shared_by    : username of the user who added `user_id` to the
                       project (NULL if `user_id` is the project creator)
      - added_at     : timestamp when `user_id` was added to the project
      - last_seen_at : last time `user_id` opened this project (NULL if never)

    Admin users (`is_admin=1`) get every run, with `your_access='approver'`
    and `shared_by=NULL`.
    """
    user_row = conn.execute(
        "SELECT is_admin FROM users WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    if not user_row:
        return []
    if int(user_row["is_admin"]) == 1:
        rows = conn.execute(
            """SELECT r.run_id, r.filename, r.created_at, r.status,
                      'approver' AS your_access,
                      u_creator.username AS shared_by,
                      NULL AS added_at,
                      NULL AS last_seen_at
               FROM runs r
               LEFT JOIN users u_creator ON u_creator.user_id = r.created_by_user_id
               ORDER BY r.created_at DESC LIMIT 50"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT r.run_id, r.filename, r.created_at, r.status,
                      pm.access_level AS your_access,
                      CASE
                        WHEN pm.added_by_user_id = pm.user_id
                          THEN NULL
                        ELSE u_added.username
                      END AS shared_by,
                      pm.added_at,
                      pm.last_seen_at
               FROM runs r
               JOIN project_members pm ON pm.run_id = r.run_id
               LEFT JOIN users u_added ON u_added.user_id = pm.added_by_user_id
               WHERE pm.user_id = ?
                 AND pm.dismissed_at IS NULL
               ORDER BY r.created_at DESC LIMIT 50""",
            (int(user_id),),
        ).fetchall()
    return [
        {
            "run_id": r["run_id"],
            "filename": r["filename"],
            "created_at": r["created_at"],
            "status": r["status"],
            "your_access": r["your_access"],
            # `shared_by` is NULL when the current user is the creator
            # (their own project); otherwise it is the username of the
            # user who added them.
            "shared_by": r["shared_by"],
            "added_at": r["added_at"],
            "last_seen_at": r["last_seen_at"],
            # True when `last_seen_at` is NULL or older than `added_at`
            # (i.e. there has been a new share since the user last looked).
            "is_unread": (
                r["last_seen_at"] is None
                or (r["added_at"] is not None and r["last_seen_at"] < r["added_at"])
            ),
        }
        for r in rows
    ]


def get_unread_count(conn, user_id: int) -> int:
    """Return the count of shared projects that have NOT been opened (or
    have new activity) since the user's `last_seen_at`. Used by the Flow 2
    header notification badge.
    """
    rows = get_my_shared_projects(conn, user_id)
    return sum(1 for r in rows if r.get("is_unread"))


def mark_project_seen(conn, run_id: str, user_id: int) -> bool:
    """Mark `user_id`'s view of `run_id` as 'just now'. Returns True if the
    row was updated, False if the user is not a member of the project.
    """
    cur = conn.execute(
        """UPDATE project_members SET last_seen_at = ?
           WHERE run_id = ? AND user_id = ?""",
        (_now(), run_id, int(user_id)),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_all_projects_seen(conn, user_id: int) -> int:
    """Reset `last_seen_at` for every project the user is a member of.
    Clears out the backlog of unread Flow 2 notifications in one shot.
    Returns the number of rows updated.
    """
    cur = conn.execute(
        """UPDATE project_members SET last_seen_at = ?
           WHERE user_id = ?""",
        (_now(), int(user_id)),
    )
    conn.commit()
    return cur.rowcount


def dismiss_notification(conn, run_id: str, user_id: int) -> bool:
    """Per-user dismissal: flag a single project notification as
    dismissed for `user_id`. Returns True if the row was updated.

    The `project_members` row is preserved (access is unaffected) — only
    the `dismissed_at` flag is set, which excludes the row from
    `get_my_shared_projects` so the bell stops surfacing it.
    """
    cur = conn.execute(
        """UPDATE project_members SET dismissed_at = ?
           WHERE run_id = ? AND user_id = ?""",
        (_now(), run_id, int(user_id)),
    )
    conn.commit()
    return cur.rowcount > 0


def dismiss_all_notifications(conn, user_id: int) -> int:
    """Bulk-dismiss every project notification for `user_id` in one
    shot. Returns the number of rows flagged.
    """
    cur = conn.execute(
        """UPDATE project_members SET dismissed_at = ?
           WHERE user_id = ? AND dismissed_at IS NULL""",
        (_now(), int(user_id)),
    )
    conn.commit()
    return cur.rowcount

