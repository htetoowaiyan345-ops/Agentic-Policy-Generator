"""Cross-cutting types."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentStep:
    no: int
    name: str
    ok: bool
    detail: str = ""


@dataclass
class AuditResult:
    run_id: str
    document_name: str
    processing_time_ms: int
    framework_version: str
    framework_sha256: str
    started_at: str
    finished_at: str
    validation_ok: bool
    output_path: str
    audit_json: str = ""                # JSON-serialized audit (stored in runs.db)
    sections: list[dict] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    integrity_checks: list[dict] = field(default_factory=list)
    fallback_used: bool = False
    total_placed_chars: int = 0
    total_dropped_chars: int = 0
    total_dropped_paragraphs: int = 0
    dropped_paragraphs_sample: list[dict] = field(default_factory=list)
    extraction_path: str = "rules"  # Stage 6: 'rules' | 'spacy' | 'spacy-fallback'
