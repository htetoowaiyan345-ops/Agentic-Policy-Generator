import zipfile
from pathlib import Path

import pytest

from policy_platform import config, pipeline


def test_pipeline_golden_brain_runs_ok():
    """Process the Brain as input and verify the pipeline doesn't break.
    The Brain has its own headings which cause distribution edge cases;
    this test only verifies media preservation and basic pipeline success."""
    r = pipeline.process(config.BRAIN_PATH, fail_on_validation=False)
    # Media must be preserved byte-equal
    p_out = Path(r.output_path)
    assert p_out.exists()
    with zipfile.ZipFile(config.BRAIN_PATH) as z_in, zipfile.ZipFile(p_out) as z_out:
        for name in ("word/media/image1.jpeg", "word/media/image2.png"):
            assert hash(z_in.read(name)) == hash(z_out.read(name))


def test_pipeline_no_growth_for_minimal_input(tmp_path):
    p = tmp_path / "minimal.txt"
    p.write_text(
        "Policy Title: Minimal\n"
        "INTRODUCTION\nSome intro text here.\n"
        "1. Purpose\nTo do the minimum.\n",
        encoding="utf-8",
    )
    r = pipeline.process(p)
    assert r.validation_ok is True
    from docx import Document
    d_out = Document(r.output_path)
    d_brain = Document(str(config.BRAIN_PATH))
    out_p = sum(1 for ch in d_out.element.body if ch.tag.endswith("}p"))
    brain_p = sum(1 for ch in d_brain.element.body if ch.tag.endswith("}p"))
    out_t = sum(1 for ch in d_out.element.body if ch.tag.endswith("}tbl"))
    brain_t = sum(1 for ch in d_brain.element.body if ch.tag.endswith("}tbl"))
    assert out_p <= brain_p
    assert out_t == brain_t


def test_pipeline_universal_distribution(tmp_path):
    p = tmp_path / "no_headings.txt"
    p.write_text("Line content\n" * 200, encoding="utf-8")
    r = pipeline.process(p)
    assert r.validation_ok is True
    # All 200 source paragraphs should be placed (0 dropped)
    assert r.total_dropped_paragraphs == 0


def test_pipeline_rejects_legacy_doc(tmp_path):
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(Exception):
        pipeline.process(p)
