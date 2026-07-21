"""auth_middleware.py — Request authentication helpers.

Tiny stdlib-only helpers for the `BaseHTTPRequestHandler` based backend
in `api/server.py`. Reads the `Authorization: Bearer <token>` header,
looks up the session, and returns the user record. Sends 401 / 403 JSON
responses directly via the handler's `send_json` method.

Usage in server.py handlers:

    from api.auth_middleware import require_auth, require_admin, require_project_access

    def handle_something(self, run_id):
        user = require_auth(self)          # 401 if no/invalid token
        if user is None:
            return                          # require_auth already sent the response
        ... continue ...

    def handle_admin_only(self):
        user = require_admin(self)         # 401 if no token, 403 if not admin
        if user is None:
            return

    def handle_project(self, run_id):
        user, access = require_project_access(self, run_id, 'editor')
        if user is None:
            return
        # `access` is the user's level: 'viewer' | 'editor' | 'approver' | None
        ...
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from api import db, users

if TYPE_CHECKING:
    pass


def _extract_bearer_token(handler) -> str | None:
    """Read the `Authorization: Bearer <token>` header. Returns the token
    string, or None if missing/malformed."""
    auth = handler.headers.get("Authorization", "")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts[0].strip(), parts[1].strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def get_current_user(handler) -> dict | None:
    """Return the user dict for the current request, or None if no valid session.
    Does NOT send a response — use `require_auth` for that."""
    token = _extract_bearer_token(handler)
    if not token:
        return None
    with db._conn() as c:
        return users.get_user_by_token(c, token)


def require_auth(handler) -> dict | None:
    """Ensure the request has a valid session. On success returns the user
    dict. On failure sends a 401 JSON response and returns None."""
    user = get_current_user(handler)
    if user is None:
        # Defer the import to avoid a circular import at module load
        from api.server import send_json
        send_json(handler, {"error": "unauthorized", "message": "Authentication required."}, status=401)
        return None
    return user


def require_admin(handler) -> dict | None:
    """Ensure the request has a valid session AND the user is admin.
    On failure sends 401 (no token) or 403 (not admin). Returns user dict or None."""
    user = require_auth(handler)
    if user is None:
        return None
    if not user.get("is_admin"):
        from api.server import send_json
        send_json(handler, {"error": "forbidden", "message": "Admin access required."}, status=403)
        return None
    return user


def require_project_access(
    handler, run_id: str, min_level: str
) -> tuple[dict | None, str | None]:
    """Ensure the user is authenticated AND has at least `min_level` access
    to the project. Returns (user, access_level) on success or (None, None)
    on failure (after sending 401/403/404).

    `min_level` is one of: 'viewer', 'editor', 'approver'.

    Admin users (is_admin=1) automatically have 'approver' access to every
    project (handled by `get_user_project_access`).
    """
    user = require_auth(handler)
    if user is None:
        return None, None
    with db._conn() as c:
        access = users.get_user_project_access(c, int(user["id"]), run_id)
    if access is None:
        from api.server import send_json
        send_json(
            handler,
            {
                "error": "forbidden",
                "message": (
                    f"You do not have access to this project. "
                    f"Ask the project owner to share it with you."
                ),
            },
            status=403,
        )
        return None, None
    if not users.meets_access(access, min_level):
        from api.server import send_json
        send_json(
            handler,
            {
                "error": "forbidden",
                "message": (
                    f"This action requires {min_level!r} access. "
                    f"You have {access!r} access."
                ),
            },
            status=403,
        )
        return None, None
    return user, access
