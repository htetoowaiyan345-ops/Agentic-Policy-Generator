"""Myanmar text extraction debug logging.

Gated by MYANMAR_EXTRACTION_DEBUG environment variable (default: off).
When enabled, writes checkpoint text to backend/logs/myanmar_debug/.

Each run creates timestamped files for side-by-side diff comparison.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

DEBUG_ENABLED = os.environ.get("MYANMAR_EXTRACTION_DEBUG", "0") == "1"
MAX_CHARS_PER_CHECKPOINT = 20_000
MAX_LINE_LENGTH = 500

_LOG_DIR = (
    Path(__file__).resolve().parent.parent.parent / "logs" / "myanmar_debug"
)


def _truncate_text(text: str) -> str:
    """Truncate text to MAX_CHARS_PER_CHECKPOINT and wrap long lines."""
    if len(text) > MAX_CHARS_PER_CHECKPOINT:
        text = (
            text[:MAX_CHARS_PER_CHECKPOINT]
            + f"\n[... TRUNCATED at {MAX_CHARS_PER_CHECKPOINT} chars, full length: {len(text)}]"
        )
    out_lines = []
    for line in text.split("\n"):
        if len(line) > MAX_LINE_LENGTH:
            line = (
                line[:MAX_LINE_LENGTH]
                + f" ... [line truncated, full: {len(line)} chars]"
            )
        out_lines.append(line)
    return "\n".join(out_lines)


def log_checkpoint(name: str, text: str, run_id: str | None = None) -> None:
    """Write a checkpoint's text to disk when debugging is enabled.

    Files written:
      logs/myanmar_debug/<run_id>_<checkpoint>.txt

    Each call overwrites the file (so reruns don't accumulate).
    """
    if not DEBUG_ENABLED:
        return
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"{run_id}_" if run_id else ""
    timestamp = int(time.time() * 1000)
    out_path = _LOG_DIR / f"{suffix}{name}_{timestamp}.txt"
    out_path.write_text(_truncate_text(text), encoding="utf-8")
