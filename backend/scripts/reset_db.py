"""reset_db.py — Wipe all runs, versions, comments, audit, and existing
.docx files. Keeps the 3 seed users. Re-seeds users if users table empty.

Run from the backend/ directory:
    python -m scripts.reset_db

This is destructive and intended for use only when migrating to the
multi-user schema. Existing data is permanently deleted.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# backend/scripts/reset_db.py → backend/ (parent)
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api import db  # noqa: E402


def main() -> None:
    if not sys.stdin.isatty():
        # Non-interactive (e.g., piped) — auto-confirm
        confirmed = True
    else:
        print("This will DELETE all runs, versions, comments, audit logs,")
        print(f"and the data/runs/ directory contents at {BACKEND_DIR / 'data'}.")
        print("The 3 seed users (admin/user1/user2) will be preserved.\n")
        resp = input("Type 'yes' to continue: ").strip().lower()
        confirmed = resp in ("yes", "y")

    if not confirmed:
        print("Aborted.")
        return

    # Wipe data/runs/ (all per-run files) — best effort. Some files may
    # be locked by OneDrive sync or other processes; we skip those and
    # let the DB wipe below make the orphaned files harmless.
    runs_dir = BACKEND_DIR / "data" / "runs"
    if runs_dir.exists():
        count = 0
        skipped = 0
        for child in runs_dir.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                count += 1
            except (PermissionError, OSError) as e:
                skipped += 1
                print(f"[reset_db] skipped {child.name}: {e}")
        print(f"[reset_db] removed {count} entries from {runs_dir} ({skipped} skipped)")

    # Wipe DB tables (keep users). Run init_db first so all tables exist.
    db.init_db()
    with db._conn() as c:
        c.execute("DELETE FROM runs")
        c.execute("DELETE FROM policy_versions")
        c.execute("DELETE FROM review_comments")
        c.execute("DELETE FROM audit_log")
        c.execute("DELETE FROM project_members")
        c.commit()
    print("[reset_db] cleared runs, policy_versions, review_comments, audit_log, project_members")

    # Re-init the schema (this also re-seeds users if users table is empty)
    db.init_db()
    print("[reset_db] re-initialized DB schema")
    print("[reset_db] DONE")


if __name__ == "__main__":
    main()
