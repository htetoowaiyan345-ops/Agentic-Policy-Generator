import re, sys
html = sys.stdin.read()
for m in re.finditer(r'id="(step-[0-9]|generate-btn|next-[0-9]|gen-filename|gen-status-badge|status-processing|status-done|status-failed|review-btn)"', html):
    print(m.group(1))
