"""Usage: python diag_slots.py <input.pdf|docx|txt>

Diagnoses which slot each paragraph ends up in for the given input.
Resolves the file path from CLI args (no hardcoded names)."""
import sys
from pathlib import Path

USAGE = "Usage: python diag_slots.py <input_file>"

if len(sys.argv) < 2:
    print(USAGE)
    sys.exit(2)

from policy_platform.extractors import dispatch
from policy_platform.rag import RetrievalPipeline

input_path = Path(sys.argv[1])
if not input_path.exists():
    print(f"ERROR: file not found: {input_path}")
    sys.exit(2)

doc = dispatch(input_path)
pipeline = RetrievalPipeline()
result = pipeline.run(
    doc.paragraphs,
    tables=doc.tables,
    table_paragraph_indices=list(doc.table_paragraph_indices) if doc.table_paragraph_indices else None,
)

print("=== All paragraphs ===")
for i, p in enumerate(doc.paragraphs):
    print(f"[{i}] {p!r}")

print("=== Classification slots (full chunk_text) ===")
for sid in sorted(result.slots.keys()):
    s = result.slots[sid]
    print(f"--- slot {sid}: source_idx={s.source_idx} score={s.score:.3f} backend={s.backend!r}")
    print(f"    chunk_text: {(s.chunk_text or '')!r}")
    if s.table is not None:
        print(f"    table: {s.table!r}")
