"""Initialize and verify the frozen Brain manifest.

Run:
    python -m policy_platform.cli init
    python -m policy_platform.cli verify
    python -m policy_platform.cli info
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from policy_platform import config


class BrainManifestTampered(RuntimeError):
    """Raised when the project's Brain file does not match the manifest."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_media(path: Path) -> list[str]:
    import zipfile

    with zipfile.ZipFile(path) as z:
        return sorted(n for n in z.namelist() if n.startswith("word/media/"))


def write_manifest(brain_path: Path, manifest_path: Path) -> dict[str, Any]:
    sha = _sha256(brain_path)
    media = _list_media(brain_path)
    manifest = {
        "version": config.FRAMEWORK_VERSION,
        "source_filename": brain_path.name,
        "sha256": sha,
        "embedded_media": media,
        "section_count": 15,
        "frozen": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_manifest(manifest_path: Path = config.MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def init_or_verify(*, init: bool) -> dict[str, Any]:
    """If init=True: ensure Brain exists in project and write manifest.
    Otherwise verify the Brain matches the manifest.
    """
    if init:
        config.BRAIN_DIR.mkdir(parents=True, exist_ok=True)
        if not config.BRAIN_PATH.exists():
            if not config.SOURCE_BRAIN_HINT.exists():
                raise FileNotFoundError(
                    f"Brain source not found at {config.SOURCE_BRAIN_HINT}. "
                    "Place 'Policy Framework 5.docx' there or update SOURCE_BRAIN_HINT in config.py."
                )
            shutil.copy2(config.SOURCE_BRAIN_HINT, config.BRAIN_PATH)
        return write_manifest(config.BRAIN_PATH, config.MANIFEST_PATH)

    if not config.MANIFEST_PATH.exists():
        raise FileNotFoundError("Manifest missing. Run init first.")
    manifest = load_manifest()
    actual = _sha256(config.BRAIN_PATH)
    if actual != manifest["sha256"]:
        raise BrainManifestTampered(
            "Brain file hash mismatch. Re-run init to refresh the manifest "
            "if the Brain was intentionally updated."
        )
    return manifest


def main(argv: list[str]) -> int:
    import sys
    cmd = argv[1] if len(argv) > 1 else "verify"
    if cmd == "init":
        m = init_or_verify(init=True)
    elif cmd == "verify":
        m = init_or_verify(init=False)
    elif cmd == "info":
        m = load_manifest()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
