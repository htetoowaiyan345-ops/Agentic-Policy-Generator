#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copy the user's PDF to tests/fixtures/ with a sanitized filename."""
import shutil
from pathlib import Path

src = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
dst = Path(r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\fixtures\HR_00002_redacted.pdf")

dst.parent.mkdir(parents=True, exist_ok=True)
if not src.exists():
    print(f"NOT_FOUND: {src}")
    raise SystemExit(1)

shutil.copy2(src, dst)
print(f"Copied {src.stat().st_size} bytes -> {dst}")
print(f"SHA256: {__import__('hashlib').sha256(dst.read_bytes()).hexdigest()}")
