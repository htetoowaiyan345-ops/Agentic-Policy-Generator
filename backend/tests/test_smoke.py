from policy_platform.audit import new_run_id
from policy_platform.framework.section_map import FROZEN_SECTIONS


def test_section_count():
    assert len(FROZEN_SECTIONS) == 15


def test_synonym_for_aim_matches_purpose():
    from policy_platform.framework.section_map import SECTION_HEADING_SYNONYMS
    assert any("aim" in s for s in SECTION_HEADING_SYNONYMS[7])


def test_run_id_is_unique():
    a = new_run_id()
    b = new_run_id()
    assert a != b
    assert len(a) == 32
