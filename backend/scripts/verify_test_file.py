#!/usr/bin/env python3
"""Verify the regenerated test file has correct Burmese characters."""
from pathlib import Path

TEST_PATH = Path(__file__).resolve().parents[1] / "tests" / "test_burmese_label_parsing.py"

# Read with explicit UTF-8
content = TEST_PATH.read_text(encoding="utf-8")

# Count Burmese characters
burmese_chars = sum(1 for c in content if 0x1000 <= ord(c) <= 0x109F)
print(f"File size: {len(content)} chars")
print(f"Burmese characters (U+1000-U+109F): {burmese_chars}")

# Check for the expected constants
checks = [
    "TYPE_PHRASE =",
    "PTITLE_PHRASE =",
    "EFFDATE_PHRASE =",
    "REASON_PHRASE =",
]
for check in checks:
    found = check in content
    print(f"  {check}: {'OK' if found else 'MISSING'}")

# Print the constant assignments
for line in content.split("\n"):
    if "PHRASE =" in line and "TYPE" not in line and "#" not in line:
        # Print first 80 chars to avoid terminal issues
        print(f"  CONST: {line[:80]}")
    if "=" in line and "PHRASE" in line and "#" not in line:
        print(f"  FOUND: {line[:80]}")
