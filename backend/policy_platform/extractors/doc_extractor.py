from __future__ import annotations


class UnsupportedFormatError(ValueError):
    """Raised when an input format is not supported in this version."""


def extract(_path):  # pragma: no cover - intentionally unsupported
    raise UnsupportedFormatError(
        "Legacy .doc files are not supported in v1. Please save the source "
        "document as .docx or .pdf and resubmit."
    )
