#!/usr/bin/env python3
"""Start vite dev server using direct vite.js invocation."""
import subprocess
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
project_root = backend.parent
frontend = project_root / "frontend" / "web"
node = Path("D:/node-portable/node-v20.18.0-win-x64/node.exe")
vite_js = frontend / "node_modules" / "vite" / "bin" / "vite.js"
logs = project_root / "logs"
logs.mkdir(parents=True, exist_ok=True)

print(f"Node: {node}")
print(f"Vite.js: {vite_js}")
print(f"Frontend dir: {frontend}")
print(f"Logs dir: {logs}")

# Write a Windows batch file that quotes everything properly
bat_path = logs / "_run_vite.bat"
bat_content = (
    "@echo off\r\n"
    "chcp 65001 > nul\r\n"
    f'cd /d "{frontend}"\r\n'
    f'"{node}" "{vite_js}" dev\r\n'
)
bat_path.write_text(bat_content, encoding="utf-8")
print(f"Wrote batch: {bat_path}")
print(f"Content:\n{bat_content}")
