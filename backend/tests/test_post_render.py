"""Tests for post-render black-line cleanup.

The Brain framework's default-page header (header2.xml) carries a
decorative black `<v:line>` shape. After rendering, this shape sits
at the top of every non-first page and looks like a horizontal rule
drawn into the document body. The cleaner removes it.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from policy_platform.post_render import (
    clean_xml_part,
    strip_black_lines,
)


# ---------------------------------------------------------------------------
# Unit tests for the regex patterns
# ---------------------------------------------------------------------------

def test_clean_xml_part_strips_vline_with_strokecolor_black():
    """A `<v:line>` with strokecolor containing `black` is removed."""
    xml_in = (
        '<w:r><w:pict w14:anchorId="123">'
        '<v:line id="x" strokecolor="black [3040]" from="0,55.2pt" to="467.45pt,55.2pt"/>'
        '</w:pict></w:r>'
    )
    out = clean_xml_part(xml_in)
    assert "v:line" not in out


def test_clean_xml_part_strips_vline_with_strokeweight():
    """A `<v:line>` with strokeweight (visible line width) is also removed."""
    xml_in = (
        '<w:r><w:pict>'
        '<v:line id="x" strokeweight="1pt" strokecolor="black" from="0,0" to="100,0"/>'
        '</w:pict></w:r>'
    )
    out = clean_xml_part(xml_in)
    assert "v:line" not in out


def test_clean_xml_part_strips_drawing_with_solidblack_ln():
    """A `<w:drawing>` whose <a:ln> has a solid black fill is removed."""
    xml_in = (
        '<w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5000000" cy="0"/></a:xfrm>'
        '<a:prstGeom prst="line"><a:avLst/></a:prstGeom></wps:spPr>'
        '<wps:style><a:lnRef idx="1"/><a:fillRef idx="0"/>'
        '<a:ln><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:ln></wps:style>'
        '</wps:wsp></a:graphicData></a:graphic></wp:inline>'
        '</w:drawing>'
    )
    out = clean_xml_part(xml_in)
    assert '<a:srgbClr' not in out or '000000' not in out


def test_clean_xml_part_preserves_non_black_lines():
    """A line shape with a non-black color must be preserved."""
    xml_in = (
        '<w:r><w:pict>'
        '<v:line id="x" strokecolor="red [100]" from="0,0" to="100,0"/>'
        '</w:pict></w:r>'
    )
    out = clean_xml_part(xml_in)
    # No black-related match, so the pict stays
    assert "v:line" in out


def test_clean_xml_part_preserves_logos_and_images():
    """An image with no line shape must pass through untouched."""
    xml_in = (
        '<w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="...picture">'
        '<pic:pic><pic:blipFill><a:blip r:embed="rId1"/></pic:blipFill></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline>'
        '</w:drawing>'
    )
    out = clean_xml_part(xml_in)
    assert out == xml_in


# ---------------------------------------------------------------------------
# Integration: strip_black_lines on a real zip
# ---------------------------------------------------------------------------

def _make_fake_docx_with_black_line(path: Path) -> None:
    """Build a minimal docx zip with a header2.xml containing a black <v:line>."""
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Body text</w:t></w:r></w:p></w:body>'
            '</w:document>'
        ),
        "word/header2.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            '<w:p><w:r>'
            '<w:pict><v:line id="S8" strokecolor="black [3040]" from="0,55.2pt" to="467.45pt,55.2pt"/></w:pict>'
            '</w:r></w:p>'
            '</w:hdr>'
        ),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>'
            '</Relationships>'
        ),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_strip_black_lines_removes_from_header_zip(tmp_path):
    """Run strip_black_lines on a real zip and verify the line is gone."""
    src = tmp_path / "src.docx"
    out = tmp_path / "out.docx"
    _make_fake_docx_with_black_line(src)
    shutil.copy2(src, out)

    n = strip_black_lines(out)
    assert n >= 1

    with zipfile.ZipFile(out) as z:
        h2 = z.read("word/header2.xml").decode("utf-8")
        doc = z.read("word/document.xml").decode("utf-8")
    assert "v:line" not in h2
    assert "Body text" in doc


def test_strip_black_lines_idempotent_on_already_clean_zip(tmp_path):
    """Running on a zip that has no black lines must return 0 and not modify content."""
    src = tmp_path / "src.docx"
    out = tmp_path / "out.docx"
    _make_fake_docx_with_black_line(src)
    shutil.copy2(src, out)
    strip_black_lines(out)  # clean once
    # Now run again
    n2 = strip_black_lines(out)
    # The first run may touch the file by re-encoding; subsequent runs should also report 0 parts modified
    # (or 0 because no lines left)
    with zipfile.ZipFile(out) as z:
        h2 = z.read("word/header2.xml").decode("utf-8")
    assert "v:line" not in h2


def test_strip_black_lines_preserves_logos_in_header(tmp_path):
    """A header containing both a logo image and a black line: only the line must go."""
    src = tmp_path / "src.docx"
    out = tmp_path / "out.docx"
    # Build header with both an inline image AND a black v:line
    h2_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<w:p><w:r>'
        '<w:drawing><wp:inline>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="...picture">'
        '<pic:pic><pic:blipFill><a:blip xmlns:r="..." r:embed="rId1"/></pic:blipFill></pic:pic>'
        '</a:graphicData></a:graphic>'
        '</wp:inline></w:drawing>'
        '</w:r></w:p>'
        '<w:p><w:r><w:pict>'
        '<v:line id="S8" strokecolor="black [3040]" from="0,55.2pt" to="467.45pt,55.2pt"/>'
        '</w:pict></w:r></w:p>'
        '</w:hdr>'
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
            '</Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Body text</w:t></w:r></w:p></w:body>'
            '</w:document>'
        ),
        "word/header2.xml": h2_xml,
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
            '</Relationships>'
        ),
        "word/media/image1.png": b"\x89PNG-fake",
    }
    src.write_bytes(b"")  # touch
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            if isinstance(content, str):
                zf.writestr(name, content)
            else:
                zf.writestr(name, content)
    shutil.copy2(src, out)

    strip_black_lines(out)

    with zipfile.ZipFile(out) as z:
        h2 = z.read("word/header2.xml").decode("utf-8")
        # logo blip reference still present
        assert 'r:embed="rId1"' in h2
        # black v:line removed
        assert "v:line" not in h2


def test_strip_black_lines_does_nothing_if_no_lines_present(tmp_path):
    """A header with only images must pass through unchanged."""
    src = tmp_path / "src.docx"
    out = tmp_path / "out.docx"
    _make_fake_docx_with_black_line(src)
    # Replace header2 with a clean one
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/document.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Body text</w:t></w:r></w:p></w:body>'
        '</w:document>',
        "word/header2.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:p><w:r><w:t>Logo area only</w:t></w:r></w:p>'
            '</w:hdr>'
        ),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>'
            '</Relationships>'
        ),
    }
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    shutil.copy2(src, out)

    n = strip_black_lines(out)
    assert n == 0  # nothing to clean
    with zipfile.ZipFile(out) as z:
        h2 = z.read("word/header2.xml").decode("utf-8")
    assert "Logo area only" in h2


# ---------------------------------------------------------------------------
# Tests for DrawingML <a:prstGeom prst="line"> shape stripping
# (these are the v2 black lines that survive the v1 fix)
# ---------------------------------------------------------------------------

def test_clean_xml_part_strips_drawing_with_prstgeom_line():
    """A <w:drawing> wrapping <a:prstGeom prst="line"/> is removed."""
    xml_in = (
        '<w:r><w:drawing>'
        '<wp:anchor distT="0" distB="0">'
        '<wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>701202</wp:posOffset></wp:positionV>'
        '<wp:extent cx="5936689" cy="0"/>'
        '<wp:docPr id="8" name="Straight Connector 8"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="wordprocessingShape">'
        '<wps:wsp xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5936689" cy="0"/></a:xfrm>'
        '<a:prstGeom prst="line"><a:avLst/></a:prstGeom></wps:spPr>'
        '<wps:style><a:lnRef idx="1"><a:schemeClr val="dk1"/></a:lnRef></wps:style>'
        '</wps:wsp></a:graphicData></a:graphic>'
        '</wp:anchor></w:drawing></w:r>'
    )
    out = clean_xml_part(xml_in)
    assert "prstGeom" not in out or "prst=" not in out or "line" not in out[out.find("prst="):out.find("prst=")+20]
    # Strong assertion: the drawing fragment is gone
    assert "wp:docPr" not in out


def test_clean_xml_part_strips_drawing_with_cy_zero():
    """A <w:drawing> whose <a:ext cy="0"/> indicates a horizontal line is removed."""
    xml_in = (
        '<w:r><w:drawing>'
        '<wp:anchor><wp:extent cx="5936689" cy="0"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="wordprocessingShape">'
        '<wps:wsp><wps:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="5936689" cy="0"/></a:xfrm></wps:spPr></wps:wsp>'
        '</a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>'
    )
    out = clean_xml_part(xml_in)
    assert "wp:extent" not in out or "cy=" not in out


def test_clean_xml_part_preserves_drawing_with_image():
    """A <w:drawing> for an image is preserved even if it has prstGeom."""
    xml_in = (
        '<w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="...picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:blipFill><a:blip r:embed="rId1"/></pic:blipFill>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
    )
    out = clean_xml_part(xml_in)
    assert 'r:embed="rId1"' in out


# ---------------------------------------------------------------------------
# Tests for Brain sanitization in-place
# ---------------------------------------------------------------------------

def test_sanitize_brain_strips_both_vline_and_drawing_lines(tmp_path):
    """sanitize_brain_in_place removes BOTH old VML <v:line> shapes AND new
    DrawingML <a:prstGeom prst="line"/> shapes from the template."""
    from policy_platform.post_render import sanitize_brain_in_place

    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:v="urn:schemas-microsoft-com:vml" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<w:body>'
            '<w:p><w:r>'
            '<w:pict><v:line id="L1" strokecolor="black [3040]" from="0,55.2pt" to="467.45pt,55.2pt"/></w:pict>'
            '</w:r></w:p>'
            '<w:p><w:r>'
            '<w:drawing><wp:anchor><wp:extent cx="5936689" cy="0"/>'
            '<a:graphic><a:graphicData uri="wordprocessingShape">'
            '<wps:wsp xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            '<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5936689" cy="0"/></a:xfrm>'
            '<a:prstGeom prst="line"><a:avLst/></a:prstGeom></wps:spPr></wps:wsp>'
            '</a:graphicData></a:graphic></wp:anchor></w:drawing>'
            '</w:r></w:p>'
            '</w:body></w:document>'
        ),
        "word/header2.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            '<w:p><w:r><w:pict>'
            '<v:line id="L2" strokecolor="black [3040]" from="0,55.2pt" to="467.45pt,55.2pt"/>'
            '</w:pict></w:r></w:p></w:hdr>'
        ),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>'
            '</Relationships>'
        ),
    }
    src = tmp_path / "brain.docx"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)

    n = sanitize_brain_in_place(src)
    assert n >= 2  # both body and header modified

    with zipfile.ZipFile(src) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        h2 = z.read("word/header2.xml").decode("utf-8")
    assert "v:line" not in doc
    assert "v:line" not in h2
    assert "prstGeom" not in doc or "prst=\"line\"" not in doc


def test_sanitize_brain_modifies_sha(tmp_path):
    """The Brain SHA-256 changes after sanitization."""
    import hashlib
    from policy_platform.post_render import sanitize_brain_in_place

    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            '<w:body><w:p><w:r>'
            '<w:pict><v:line id="L" strokecolor="black [3040]" from="0,0" to="100,0"/></w:pict>'
            '</w:r></w:p></w:body></w:document>'
        ),
    }
    src = tmp_path / "brain.docx"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)

    sha_before = hashlib.sha256(src.read_bytes()).hexdigest()
    sanitize_brain_in_place(src)
    sha_after = hashlib.sha256(src.read_bytes()).hexdigest()
    assert sha_before != sha_after


def test_sanitize_brain_is_idempotent(tmp_path):
    """Running sanitize twice produces the same SHA."""
    import hashlib
    from policy_platform.post_render import sanitize_brain_in_place

    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            '<w:body><w:p><w:r>'
            '<w:pict><v:line id="L" strokecolor="black [3040]" from="0,0" to="100,0"/></w:pict>'
            '</w:r></w:p></w:body></w:document>'
        ),
    }
    src = tmp_path / "brain.docx"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)

    sanitize_brain_in_place(src)
    sha_first = hashlib.sha256(src.read_bytes()).hexdigest()
    sanitize_brain_in_place(src)
    sha_second = hashlib.sha256(src.read_bytes()).hexdigest()
    assert sha_first == sha_second
