"""Tests for Stage 6: audit JSON includes `extraction_path` field.

After the simplification, audit is a single JSON string returned by
`write_audit(result)` and stored in runs.db. No files are written."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from policy_platform import pipeline
from policy_platform.audit import write_audit
from policy_platform.pipeline_types import AuditResult, AgentStep


def _make_audit(extraction_path: str) -> AuditResult:
    """Build a minimal AuditResult for audit-write tests."""
    return AuditResult(
        run_id="test-run-id",
        document_name="test.pdf",
        processing_time_ms=42,
        framework_version="Brain-PF5-v1.1.0",
        framework_sha256="bc30b324fc9ab9bce4c90599895591e4566bc0e9acc43e97e5fc1e56e7dcc946",
        started_at="2026-07-05T00:00:00Z",
        finished_at="2026-07-05T00:00:01Z",
        validation_ok=True,
        output_path="out.docx",
        sections=[],
        steps=[AgentStep(no=1, name="Validate", ok=True, detail="ok")],
        integrity_checks=[],
        extraction_path=extraction_path,
    )


def test_audit_json_includes_extraction_path():
    """write_audit returns a JSON string with top-level extraction_path."""
    result = _make_audit("spacy-fallback")
    audit_json_str = write_audit(result)
    payload = json.loads(audit_json_str)
    assert payload.get("extraction_path") == "spacy-fallback"


def test_audit_default_extraction_path_is_rules():
    """When AuditResult sets 'rules', JSON reflects that."""
    result = _make_audit("rules")
    audit_json_str = write_audit(result)
    payload = json.loads(audit_json_str)
    assert payload["extraction_path"] == "rules"


def test_audit_contains_required_keys():
    """Audit JSON contains run_id, sections, integrity_checks, steps."""
    result = _make_audit("rules")
    payload = json.loads(write_audit(result))
    for key in ("run_id", "sections", "integrity_checks", "steps", "framework_sha256"):
        assert key in payload


def test_pipeline_records_extraction_path_in_audit():
    """End-to-end: pipeline.process() returns a result whose audit JSON contains extraction_path."""
    p = Path("C:/Users/htetoowaiyan/Downloads/Policy For Coronavirus Disease.pdf")
    if not p.exists():
        pytest.skip("Source PDF not available")
    r = pipeline.process(p)
    audit_str = write_audit(r)
    payload = json.loads(audit_str)
    assert "extraction_path" in payload
    assert payload["extraction_path"] in (
        "rules",
        "rules+sentence",
        "rules+narrative",
        "rules+sentence+narrative",
        "spacy",
        "spacy-fallback",
        "spacy+sentence",
        "spacy-fallback+sentence",
        "spacy+narrative",
        "spacy-fallback+narrative",
        "spacy+sentence+narrative",
        "spacy-fallback+sentence+narrative",
    )