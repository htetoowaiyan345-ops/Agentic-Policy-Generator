#!/usr/bin/env python3
from pathlib import Path

css_path = Path("D:/Htet Oo Wai Yan/OneDrive - City Holdings Limited/Desktop/agentic-policy-platform - Copy (4)/frontend/web/src/app.css")
css = css_path.read_text(encoding="utf-8")
print(f"Lines: {len(css.splitlines())}")
print(f"Bytes: {len(css)}")
print(f"Has @font-face: {'@font-face' in css}")
print(f"Has Pyidaungsu: {'Pyidaungsu' in css}")
print(f"Has Noto Sans Myanmar: {'Noto Sans Myanmar' in css}")

# Try parsing CSS lightly - look for unbalanced braces
opens = css.count("{")
closes = css.count("}")
print(f"Braces: open={opens} close={closes} balanced={opens == closes}")
