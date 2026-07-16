"""Brain framework: identifier-based slot lookup.

The Brain template defines 14 content slots (slot 15 is the logo/image).
Each slot has a recognizable heading string. We can find any slot's body
in any docx by:
  1. Walk the body children sequentially.
  2. Match paragraph text against canonical heading strings.
  3. A slot starts at its heading paragraph and ends at the next slot's heading.

This index-free lookup works correctly even when paragraphs are added/removed
anywhere in the document.
"""
from __future__ import annotations

# Canonical heading strings as they appear in the Brain template.
SLOT_HEADINGS: dict[int, str] = {
    1:  "Type",
    2:  "Brief Description",
    3:  "Effective Date/Period",
    4:  "Reason for Policy",
    5:  "INTRODUCTION",
    6:  "POLICY STATEMENT",
    7:  "1. Purpose",
    8:  "2. Scope & Beneficiaries",
    9:  "3. Exclusions",
    10: "4. Award Structure & Payout Tiers",
    11: "Policy Review Note",
    12: "DEFINITIONS",
    13: "RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES",
    14: "HISTORY",
}


SLOT_NAMES: dict[int, str] = {
    1: "Header",
    2: "Brief Description",
    3: "Approval & Governance",
    4: "Reason for Policy",
    5: "INTRODUCTION",
    6: "POLICY STATEMENT",
    7: "1. Purpose",
    8: "2. Scope & Beneficiaries",
    9: "3. Exclusions",
    10: "4. Award Structure & Payout Tiers",
    11: "Policy Review Note",
    12: "DEFINITIONS",
    13: "RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES",
    14: "HISTORY",
}


SLOT_HAS_TABLE: dict[int, bool] = {
    10: True,
    14: True,
}


# Original body-child indices for each Brain slot. Captured at init.
# body_items includes the heading paragraph + all body paragraphs + table (if any).
# Renderer uses these to find slot elements by index at render time.
# Index ranges verified against find_slot_boundaries() output.
BRAIN_SLOT_RANGES: dict[int, dict] = {
    1:  {"body_items": [2, 3, 4, 5, 6, 7]},
    2:  {"body_items": [8]},
    3:  {"body_items": [9, 10, 11, 12, 13, 14, 15, 16, 17]},
    4:  {"body_items": [18, 19, 20, 21, 22]},
    5:  {"body_items": [23, 24, 25]},
    6:  {"body_items": [26, 27, 28]},
    7:  {"body_items": [29, 30, 31]},
    8:  {"body_items": [32, 33, 34]},
    9:  {"body_items": [35, 36, 37, 38, 39]},
    10: {"body_items": [40, 41, 42, 43, 44]},
    11: {"body_items": [45, 46, 47, 48, 49, 50, 51, 52, 53, 54]},
    12: {"body_items": [55, 56, 57, 58, 59, 60, 61, 62]},
    13: {"body_items": [63, 64, 65, 66, 67, 68, 69]},
    14: {"body_items": [70, 71, 72, 73, 74, 75]},
}


def find_slot_boundaries(doc) -> dict[int, dict]:
    """Walk the body of `doc` and return {sec_id: {'start': i, 'end': j}}."""
    from docx.oxml.ns import qn

    body_children = list(doc.element.body)
    heading_idx: dict[int, int] = {}
    for sid, h in SLOT_HEADINGS.items():
        for i, ch in enumerate(body_children):
            tag = ch.tag.split("}")[-1]
            if tag != "p":
                continue
            txt = "".join((t.text or "") for t in ch.iter(qn("w:t"))).strip()
            if txt == h or txt.startswith(h + ":") or txt.startswith(h + " ") or txt.startswith(h + "."):
                heading_idx[sid] = i
                break

    ordered = sorted(heading_idx.items(), key=lambda kv: kv[1])
    bounds: dict[int, dict] = {}
    for i, (sid, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(body_children)
        bounds[sid] = {"start": start, "end": end, "elements": body_children[start:end]}
    return bounds
