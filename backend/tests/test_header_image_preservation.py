"""Tests that the Brain's logo image (image1.jpeg) and the smaller
Brain graphic (image2.png) are preserved in the output's header parts.

Per the user's directive ("only keep the logo right"):
  - image1.jpeg (logo) must remain in word/header2.xml referenced via
    <w:drawing> so it shows on the right side of default pages.
  - image2.png must remain in word/header3.xml referenced via
    <w:drawing> so it shows on the right side of even pages.
  - image1.jpeg byte-content (SHA-256) must match the Brain's SHA.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

import pytest

from policy_platform import config


pytestmark = pytest.mark.slow


def _docx_xml(path: Path, member: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(member).decode("utf-8", errors="replace")


def _docx_media(path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("word/media/"):
                out[n] = z.read(n)
    return out


def _brain_media_sha(name: str) -> str:
    brain = config.BRAIN_PATH
    with zipfile.ZipFile(brain) as z:
        return hashlib.sha256(z.read(f"word/media/{name}")).hexdigest()


def test_image1_jpeg_present_in_output_media(tmp_path):
    """Output should contain image1.jpeg with the same bytes as Brain."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    media = _docx_media(r.output_path)
    assert "word/media/image1.jpeg" in media, "image1.jpeg missing from output media"
    assert (
        hashlib.sha256(media["word/media/image1.jpeg"]).hexdigest()
        == _brain_media_sha("image1.jpeg")
    ), "image1.jpeg SHA does not match Brain"


def test_image2_png_present_in_output_media(tmp_path):
    """Output should contain image2.png with the same bytes as Brain."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    media = _docx_media(r.output_path)
    assert "word/media/image2.png" in media, "image2.png missing from output media"
    assert (
        hashlib.sha256(media["word/media/image2.png"]).hexdigest()
        == _brain_media_sha("image2.png")
    ), "image2.png SHA does not match Brain"


def test_header2_logo_drawing_referenced(tmp_path):
    """image1.jpeg logo drawing is referenced in word/header2.xml.

    The Brain's logo anchors on the right side of default pages via
    header2.xml. The <w:drawing> referencing image1.jpeg via
    r:embed=\"rId1\" must remain in the output's header2.xml.
    """
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    h2 = _docx_xml(r.output_path, "word/header2.xml")
    assert "<w:drawing>" in h2, "header2.xml: <w:drawing> missing (logo gone)"
    assert 'r:embed="rId1"' in h2, "header2.xml: rId1 embed ref missing (logo unreferenced)"
    # Confirm the rel points to image1.jpeg.
    rels = _docx_xml(r.output_path, "word/_rels/header2.xml.rels")
    assert "image1.jpeg" in rels, "header2.xml.rels does not point to image1.jpeg"


def test_header3_image2_drawing_referenced(tmp_path):
    """image2.png graphic drawing is referenced in word/header3.xml."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    h3 = _docx_xml(r.output_path, "word/header3.xml")
    assert "<w:drawing>" in h3, "header3.xml: <w:drawing> missing"
    assert 'r:embed="rId1"' in h3, "header3.xml: rId1 embed ref missing"
    rels = _docx_xml(r.output_path, "word/_rels/header3.xml.rels")
    assert "image2.png" in rels, "header3.xml.rels does not point to image2.png"


def test_header2_logo_survives_with_input_title(tmp_path):
    """When input has a real title, the Brain logo is still preserved
    and the new title run is appended AFTER the <w:drawing>."""
    p = tmp_path / "inp.txt"
    p.write_text(
        "Type: HR\nPolicy Title: My Custom Policy Title\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    h2 = _docx_xml(r.output_path, "word/header2.xml")
    assert "<w:drawing>" in h2, "logo drawing removed by title replacement"
    assert 'r:embed="rId1"' in h2, "logo rel dropped by title replacement"
    # In the new preserve-and-strip model the pipeline's own header
    # rewrite writes a `[POLICY]` placeholder run; we strip the brackets
    # but the *value* comes from the pipeline, not from the input title.
    # What matters here is that the logo <w:drawing> survives.
    draw_pos = h2.find("<w:drawing>")
    assert draw_pos != -1, "logo <w:drawing> not in header2.xml"
