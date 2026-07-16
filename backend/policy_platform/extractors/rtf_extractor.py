from __future__ import annotations

from pathlib import Path

import hashlib

from striprtf.striprtf import rtf_to_text

from .base import ExtractedDocument


def extract(path: Path) -> ExtractedDocument:
    raw = path.read_bytes()
    text = rtf_to_text(raw.decode("utf-8", errors="replace"))
    paragraphs = [p for p in text.split("\n")]
    sha = hashlib.sha256(raw).hexdigest()
    return ExtractedDocument(
        paragraphs=paragraphs,
        tables=[],
        source_sha256=sha,
        source_format="rtf",
    )
