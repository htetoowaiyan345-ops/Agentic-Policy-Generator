from __future__ import annotations

import hashlib
from pathlib import Path

from .base import ExtractedDocument


def extract(path: Path) -> ExtractedDocument:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"TXT file is not valid UTF-8: {e}") from None
    # Preserve content verbatim line-for-line. CRLF preserved as-is in the strings.
    paragraphs = text.splitlines()
    sha = hashlib.sha256(raw).hexdigest()
    return ExtractedDocument(
        paragraphs=paragraphs,
        tables=[],
        source_sha256=sha,
        source_format="txt",
    )
