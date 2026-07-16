"""Project-wide configuration. No behavior, only constants and paths."""
from __future__ import annotations

import os
from pathlib import Path

# backend/policy_platform/config.py → PROJECT_ROOT = parents[1] = backend/
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

# Brain template (locked)
DATA_DIR: Path = PROJECT_ROOT / "data"
BRAIN_DIR: Path = DATA_DIR / "brain_template"
BRAIN_FILENAME: str = "Policy_Framework_5.docx"
BRAIN_PATH: Path = BRAIN_DIR / BRAIN_FILENAME
MANIFEST_PATH: Path = BRAIN_DIR / "framework_manifest.json"
SOURCE_BRAIN_HINT: Path = Path.home() / "Downloads" / BRAIN_FILENAME

# Data folders
OUTPUTS_DIR: Path = DATA_DIR / "outputs"
AUDIT_DIR: Path = DATA_DIR / "audit"
SAMPLES_DIR: Path = DATA_DIR / "samples"
RUNS_DIR: Path = DATA_DIR / "runs"

FRAMEWORK_VERSION: str = "Brain-PF5-v1.1.0"
MAX_UPLOAD_MB: int = int(os.environ.get("MAX_UPLOAD_MB", "50"))

SKIPPED_STATUS: str = os.environ.get("SKIPPED_STATUS", "Skipped - Section Not Found")
FOUND_EMPTY_STATUS: str = "Found but Empty"

# Brain's Reason for Policy slot can physically hold this many paragraphs
# before layout breaks. Configurable via BRAIN_REASON_CAPACITY env var.
BRAIN_REASON_CAPACITY: int = int(os.environ.get("BRAIN_REASON_CAPACITY", "100"))

# ---------------------------------------------------------------------------
# RAG-Hybrid retrieval tunables (Stage X fix: moved from module-level
# constants in rag/retrieval_pipeline.py so they can be tuned at runtime
# without code edits). All defaults match the previous hardcoded values
# exactly; no behavior change unless the env var is explicitly set.
# ---------------------------------------------------------------------------

# Hard cap (seconds) for the per-document RAG pipeline. On timeout the
# pipeline returns its partial result with whatever slots have been
# collected so far. Default raised from 60s to 120s so dense PDFs like
# Earthquake PDF (which has 30 paragraphs and requires BM25 + FAISS +
# cross-encoder for every Tier-3 slot) can finish RAG for critical
# (tier-1) slots like HISTORY without being marked "timeout".
# Tight-scope callers can still override via the RAG_TIMEOUT_SECONDS
# env var.
RAG_TIMEOUT_SECONDS: float = float(os.environ.get("RAG_TIMEOUT_SECONDS", "120.0"))

# Hybrid scoring weights: alpha for vector (FAISS), 1 - alpha for keyword (BM25).
# 0.7 = 70% semantic, 30% keyword. Valid range: [0.0, 1.0].
RAG_ALPHA: float = float(os.environ.get("RAG_ALPHA", "0.7"))

# Top-k candidates to retrieve from each backend (FAISS, BM25) before
# merging and reranking.
RAG_TOP_K_PER_BACKEND: int = int(os.environ.get("RAG_TOP_K_PER_BACKEND", "5"))

# Final number of candidates reranked per slot by the cross-encoder.
RAG_RERANK_POOL: int = int(os.environ.get("RAG_RERANK_POOL", "5"))

# Minimum hybrid score for a RAG match to be accepted. Below this the
# slot is reported as "no_match" rather than a random wrong paragraph.
RAG_MIN_CONFIDENCE: float = float(os.environ.get("RAG_MIN_CONFIDENCE", "0.05"))

# ---------------------------------------------------------------------------
# Service / E2E-test knobs (all optional; defaults preserve prior behavior).
# ---------------------------------------------------------------------------

# Backend HTTP bind/connect parameters. Tests should read these instead
# of typing a URL.
API_HOST: str = os.environ.get("API_HOST", "127.0.0.1")
API_PORT: int = int(os.environ.get("API_PORT", "8765"))
API_BASE_URL: str = os.environ.get("API_BASE_URL", f"http://{API_HOST}:{API_PORT}")

# Production DB lives at DATA_DIR / "policy_history.db" by default.
# Tests can override via TEST_DB_PATH; this avoids polluting the real
# history panel during CI runs.
DB_PATH: Path = Path(os.environ.get("DB_PATH", str(DATA_DIR / "policy_history.db")))
TEST_DB_PATH: Path = Path(os.environ.get("TEST_DB_PATH", str(DATA_DIR / "policy_history_e2e.db")))

# E2E test defaults — actor/reviewer names sent to the API.
TEST_ACTOR: str = os.environ.get("TEST_ACTOR", "test-author")
TEST_REVIEWER: str = os.environ.get("TEST_REVIEWER", "test-reviewer")

# Gate the slow end-to-end test behind an explicit env var so it doesn't
# run during normal `pytest`. Set RUN_E2E=1 to enable.
RUN_E2E: bool = os.environ.get("RUN_E2E", "0") == "1"

# Gate the RAG-side label-chunking contract. Default-on since the
# M1 unification: the RAG layer treats every section-heading label
# occurrence as its own paragraph boundary by default. This
# suppresses slot-bleed on dense single-paragraph sources like
# Earthquake_Full_Policy_One_Paragraph.pdf (slot 12/14). Set the env
# var to "0" to opt out and revert to legacy single-paragraph
# routing (matches pre-M1 behaviour).
RAG_LABEL_CHUNKING: bool = os.environ.get(
    "AGENTIC_POLICY_RAG_LABEL_CHUNKING", "1"
) != "0"