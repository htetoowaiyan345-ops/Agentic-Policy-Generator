"""Copy the 4 latest output .docx files to Downloads and Documents."""
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def _q(t): return '{' + W + '}' + t


def detect_label(p):
    with zipfile.ZipFile(p) as z:
        xml = z.read('word/document.xml')
    root = ET.fromstring(xml)
    chunks = []
    total = 0
    for para in root.iter(_q('p')):
        text = ''.join(t.text or '' for t in para.iter(_q('t')))
        chunks.append(text)
        total += len(text)
        if total > 5000:
            break
    body = ' '.join(chunks)
    if 'Tier 1' in body and 'Spot Award' in body:
        return 'Award'
    if 'Earthquake Emergency Assistance' in body:
        return 'Earthquake'
    if 'Flood Emergency Assistance' in body:
        return 'Flood'
    if 'Coronavirus Disease' in body or 'COVID-19' in body:
        return 'Coronavirus'
    if 'Sexual Harassment' in body and 'New York' in body:
        return 'Sexual Harassment'
    return 'unknown'


def main():
    here = Path(__file__).resolve().parent
    # this script lives at backend/scripts/. The outputs live at
    # backend/data/outputs/, one level up.
    out_dir = here.parent / 'data' / 'outputs'
    paths = sorted(out_dir.glob('*.docx'), key=lambda x: x.stat().st_mtime, reverse=True)
    recent = paths[:4]
    print(f'Detected {len(recent)} recent outputs:')
    for p in recent:
        label = detect_label(p)
        print(f'  {p.name[:8]} -> {label}')
        nice_name = label if label != 'Sexual Harassment' else 'Sexual Harassment'
        fname = f'Policy Framework 5 - {nice_name}.docx'
        home = Path.home()
        for dest_dir in (home / 'Downloads', home / 'Documents'):
            dest = Path(dest_dir) / fname
            try:
                shutil.copy2(p, dest)
                print(f'    -> {dest}')
            except PermissionError:
                # OneDrive may have the file open in Word; skip rather than crash.
                print(f'    [LOCKED -> SKIPPED] {dest}')


if __name__ == '__main__':
    main()
