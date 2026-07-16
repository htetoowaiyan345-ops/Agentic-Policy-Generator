"""Usage: python verify_slot_separation.py <input_file> [chunking=0|1]

Verifies that slot 12 (Definitions) and slot 14 (History) do not bleed
into each other on the given input. Default chunking flag = 0."""
import os
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python verify_slot_separation.py <input_file> [chunking=0|1]")
    sys.exit(2)

chunking = sys.argv[2] if len(sys.argv) >= 3 else "0"
os.environ["AGENTIC_POLICY_RAG_LABEL_CHUNKING"] = chunking

from policy_platform.extractors import dispatch
from policy_platform.rag import RetrievalPipeline

input_path = Path(sys.argv[1])
doc = dispatch(input_path)
pipeline = RetrievalPipeline()
result = pipeline.run(
    doc.paragraphs,
    tables=doc.tables,
    table_paragraph_indices=list(doc.table_paragraph_indices) if doc.table_paragraph_indices else None,
)

s12 = result.slots.get(12)
s14 = result.slots.get(14)

print(f"chunking flag = {chunking}")
print(f"slot 12 backend: {s12.backend if s12 else None!r}")
print(f"slot 12 chunk_text (full): {s12.chunk_text!r}")
print()
print(f"slot 14 backend: {s14.backend if s14 else None!r}")
print(f"slot 14 chunk_text (full): {s14.chunk_text!r}")
print()

VERIFICATION = "Version FY26-27"
in_s12 = s12.chunk_text and VERIFICATION in s12.chunk_text
in_s14 = s14.chunk_text and VERIFICATION in s14.chunk_text
print(f"'{VERIFICATION}' in slot 12? {in_s12}")
print(f"'{VERIFICATION}' in slot 14? {in_s14}")
print()
if in_s12:
    print("FAIL: slot 12 (Definitions) still contains the History line.")
elif in_s14:
    print("PASS: slot 12 is clean and slot 14 has the History value.")
elif not in_s14:
    print("PARTIAL: slot 12 is clean but slot 14 is also empty (timeout).")
