"""CLI helpers for the platform.

Subcommands:
    init     - copy Brain into project + write manifest
    verify   - verify Brain matches manifest
    info     - print framework manifest
    process  - run pipeline against a file
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import config
from .framework import brain as brain_loader
from .pipeline import PipelineError, process
from .validator import ValidationFailed


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m policy_platform.cli {init|verify|info|process <file>}", file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "init":
        print(brain_loader.init_or_verify(init=True))
        return 0
    if cmd == "verify":
        print(brain_loader.init_or_verify(init=False))
        return 0
    if cmd == "info":
        print(brain_loader.load_manifest())
        return 0
    if cmd == "process":
        if len(argv) < 3:
            print("process requires a file path", file=sys.stderr)
            return 2
        try:
            r = process(Path(argv[2]))
        except (PipelineError, ValidationFailed) as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        print(f"OK run_id={r.run_id} output={r.output_path} audit_json=<embedded in runs.db>")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
