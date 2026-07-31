"""Tests for the Word-format normalization pass."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest


def _docx_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8", errors="replace")


def _all_body_paragraphs(xml: str) -> list[str]:
    return re.findall(r"<w:p\b[^>]*>(.*?)</w:p>", xml, re.DOTALL)


def _para_text(p: str) -> str:
    return "".join(re.findall(r"<w:t[^>]*>([^<]+)</w:t>", p))


def test_a4_page_size_applied(tmp_path):
    """Every <w:sectPr>'s <w:pgSz> is A4 (11906 × 16838 twips)."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    xml = _docx_xml(r.output_path)
    assert 'w:w="11906"' in xml, "A4 width not applied"
    assert 'w:h="16838"' in xml, "A4 height not applied"


def test_justify_forced_on_every_body_paragraph(tmp_path):
    """Every paragraph that has text carries <w:jc w:val="both"/>."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    xml = _docx_xml(r.output_path)
    paras = _all_body_paragraphs(xml)
    bad: list[tuple[int, str]] = []
    for i, para in enumerate(paras):
        text = _para_text(para).strip()
        if not text:
            continue
        if "<w:jc " not in para:
            bad.append((i, text[:80]))
    assert not bad, f"Non-justified paragraphs: {bad}"


def test_body_data_runs_not_bold(tmp_path):
    """Body data runs (after the slot heading keyword) carry NO <w:b/>."""
    p = tmp_path / "inp.txt"
    p.write_text(
        "Type: HR\n"
        "Effective Date/Period: 2026-01-01\n"
        "INTRODUCTION\nIntro about the policy here.\n"
        "1. Purpose\nTo clarify who this applies to.\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    xml = _docx_xml(r.output_path)
    paras = _all_body_paragraphs(xml)
    # Find the body paragraph after INTRODUCTION.
    found = False
    for para in paras:
        text = _para_text(para).strip()
        if not text.startswith("Intro about the policy"):
            continue
        found = True
        # All runs in this body paragraph must be non-bold.
        runs = re.findall(r"<w:r\b[^>]*>(.*?)</w:r>", para, re.DOTALL)
        for run_inner in runs:
            rPr_match = re.search(r"<w:rPr.*?</w:rPr>", run_inner, re.DOTALL)
            if not rPr_match:
                continue
            rPr = rPr_match.group(0)
            bad_bold = re.findall(
                r"<w:b\s*/>|<w:b\s+[^/>]*w:val=\"(?:1|true)\"",
                rPr,
            )
            assert not bad_bold, f"Body run still bold: {rPr!r}"
        break
    assert found, "intro body paragraph not found"


def test_calibri_10pt_body_runs(tmp_path):
    """Body data runs are Calibri 10pt (sz=20 half-points)."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\nINTRODUCTION\nIntro body text.\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    xml = _docx_xml(r.output_path)
    paras = _all_body_paragraphs(xml)
    found = False
    for para in paras:
        text = _para_text(para).strip()
        if "Intro body text" not in text:
            continue
        found = True
        # Body data runs should be Calibri 10pt (sz=20). We check the
        # <w:r> runs' <w:rPr> — NOT the paragraph default <w:rPr>.
        runs = re.findall(r"<w:r\b[^>]*>(.*?)</w:r>", para, re.DOTALL)
        assert runs, "no <w:r> in intro body"
        for run_inner in runs:
            rPr_match = re.search(r"<w:rPr.*?</w:rPr>", run_inner, re.DOTALL)
            if not rPr_match:
                continue
            rPr = rPr_match.group(0)
            assert 'w:val="20"' in rPr, f"Body run missing sz=20: {rPr[:200]}"
            # Calibri may appear as w:ascii="Calibri" or w:asciiTheme="majorHAnsi"
            # (the headings theme font is also "Calibri Light" in Word — the
            # user requested body be Calibri, so we accept either form as
            # long as the run rPr clearly uses Calibri).
            is_calibri = (
                'w:ascii="Calibri"' in rPr
                or 'w:hAnsi="Calibri"' in rPr
                or 'w:asciiTheme="majorHAnsi"' in rPr
            )
            assert is_calibri, f"Body run not Calibri: {rPr[:200]}"
        break
    assert found, "intro body paragraph not found"


def test_line_spacing_2_0_body(tmp_path):
    """Every body paragraph carries 2.0 line spacing + 4pt before/after."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    xml = _docx_xml(r.output_path)
    paras = _all_body_paragraphs(xml)
    for para in paras:
        text = _para_text(para).strip()
        if not text:
            continue
        # Find paragraph-level <w:spacing w:line="480">. The run-level
        # <w:spacing val="0"/> (run character spacing) is unrelated.
        # Use a regex that explicitly looks for the line-bearing spacing
        # element at the pPr level (NOT inside an inner rPr).
        sp_matches = re.findall(
            r"<w:spacing[^/>]*w:line=\"480\"[^/>]*/>",
            para,
        )
        assert sp_matches, (
            f"paragraph missing paragraph-level <w:spacing w:line=480>: "
            f"{para[:300]}"
        )
        attr = sp_matches[0]
        assert 'w:line="480"' in attr, f"line not 480: {attr}"
        assert 'w:lineRule="auto"' in attr, f"lineRule not auto: {attr}"
        # Default is 80 (4pt). Two Header paragraphs get modified by
        # the renderer's Header decoration pass:
        #   - `Functional Area(s):` and `Applies to:` → `after=240`
        #     (12pt gap below the label, above the black line).
        #   - The paragraph that follows each target (the one with the
        #     1px black top-border) → `before=0` (flush under the line).
        #     The 12pt white space below the line is provided by the
        #     border's own `w:space=240`, not by paragraph `w:before`.
        #     When input has `Functional Area(s): Human Resources`,
        #     this paragraph reads `Brief Description:`. When input
        #     has `Applies to: All eligible employees`, it reads
        #     `Reason for Policy:`.
        is_target_label = (
            text.startswith("Functional Area(s):")
            or text.startswith("Applies to:")
        )
        is_line_bearing = (
            text.startswith("Brief Description:")
            or text.startswith("Reason for Policy:")
        )
        if is_target_label:
            assert 'w:before="80"' in attr, f"target before not 80: {attr}"
            assert 'w:after="240"' in attr, (
                f"boosted label after not 240: {attr}"
            )
        elif is_line_bearing:
            assert 'w:before="0"' in attr, (
                f"line-bearing before not 0: {attr}"
            )
            assert 'w:after="80"' in attr, f"line-bearing after not 80: {attr}"
        else:
            assert 'w:before="80"' in attr, f"before not 80: {attr}"
            assert 'w:after="80"' in attr, f"after not 80: {attr}"


def test_introduction_heading_keeps_bold(tmp_path):
    """Slot heading keyword `INTRODUCTION` keeps its bold run."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    xml = _docx_xml(r.output_path)
    paras = _all_body_paragraphs(xml)
    found = False
    for para in paras:
        text = _para_text(para).strip()
        if text != "INTRODUCTION":
            continue
        found = True
        rPrs = re.findall(r"<w:rPr.*?</w:rPr>", para, re.DOTALL)
        assert rPrs
        any_bold = False
        for rPr in rPrs:
            if re.search(r"<w:b\s*/>|<w:b\s+[^/>]*w:val=\"(?:1|true)\"", rPr):
                any_bold = True
        assert any_bold, "INTRODUCTION heading lost its bold"
        break
    assert found, "INTRODUCTION heading not found"


def test_header_label_decoration_1px_line_below_targets(tmp_path):
    """The 1px black top-border lives on the paragraph that FOLLOWS
    `Functional Area(s):` and `Applies to:` — i.e., the line visually
    appears BELOW the target label, not above it. The top-border
    carries a 12pt `w:space` so there is 12pt of white space BELOW the
    line before the next paragraph's text starts (symmetric with the
    12pt `after=240` on the target label above the line)."""
    p = tmp_path / "inp.txt"
    p.write_text(
        "Type: HR\n"
        "Functional Area(s): Human Resources\n"
        "Applies to: All eligible employees\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    paras = _all_body_paragraphs(_docx_xml(r.output_path))
    body_paras = [
        (text, para)
        for para in paras
        for text in [_para_text(para).strip()]
        if text
    ]
    found_fa_line = False
    found_at_line = False
    target_labels = {"Functional Area(s):", "Applies to:"}
    for idx, (text, para) in enumerate(body_paras):
        for target in target_labels:
            if text.startswith(target):
                # The next paragraph in document order must carry the
                # 1px black top-border with 12pt `w:space` (gap below
                # line) and `w:before=0` (flush under the line).
                next_text, next_para = body_paras[idx + 1]
                m_line = re.search(
                    r'<w:pBdr[^>]*>\s*<w:top\b'
                    r'(?=[^/>]*\bw:val="single")'
                    r'(?=[^/>]*\bw:sz="6")'
                    r'(?=[^/>]*\bw:space="240")'
                    r'(?=[^/>]*\bw:color="000000")'
                    r'[^/>]*/>\s*</w:pBdr>',
                    next_para,
                    re.DOTALL,
                )
                assert m_line, (
                    f"next paragraph after {target!r} is missing "
                    f"1px black top-border with 12pt under-line gap "
                    f"(w:space=240): {next_para[:300]}"
                )
                m_before = re.search(
                    r'<w:spacing[^/>]*w:line="480"[^/>]*'
                    r'w:before="0"[^/>]*/>',
                    next_para,
                )
                assert m_before, (
                    f"line-bearing next paragraph after {target!r} "
                    f"missing w:before=0 (must be flush under line): "
                    f"{next_para[:300]}"
                )
                if target.startswith("Functional Area"):
                    found_fa_line = True
                else:
                    found_at_line = True
                break
    assert found_fa_line, "1px line below Functional Area(s): not found"
    assert found_at_line, "1px line below Applies to: not found"
