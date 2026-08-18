"""Tests for Burmese label-row parsing (mirroring English tests)."""
import pytest
from policy_platform.framework.brain_fields import canonical_label, _LABEL_LINE_RE
from policy_platform.i18n.burmese_synonyms import (
    get_burmese_synonyms,
    get_burmese_label_synonyms,
    get_all_burmese_synonyms,
    get_all_burmese_labels,
    get_canonical_for_burmese_label,
    reset_cache,
)


# Real Burmese phrases loaded from data/i18n/burmese_synonyms.yaml
TYPE_PHRASE = "အမျိုးအစား"
PTITLE_PHRASE = "မူဝါဒအမည်"
EFFDATE_PHRASE = "အသက်ဝင်သည့်နေ့"
REASON_PHRASE = "မူဝါဒချမှတ်ရခြင်းအကြောင်းရင်း"
INTRO_PHRASE = "နိဒါန်း"
POLSTM_PHRASE = "မူဝါဒ"
DEFS_PHRASE = "အဓိပ္ပါယ်ဖွင့်ဆိုချက်များ"


@pytest.fixture(autouse=True)
def clear_cache():
    reset_cache()
    yield
    reset_cache()


class TestBurmeseHeadings:
    """Test heading synonyms for slots 5-14."""

    def test_headings_slots_5_through_14_exist(self):
        all_syns = get_all_burmese_synonyms()
        expected_slots = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14}
        assert set(all_syns.keys()) >= expected_slots

    def test_slot_5_introduction_has_phrases(self):
        syns = get_burmese_synonyms(5)
        assert len(syns) >= 5
        assert INTRO_PHRASE in syns

    def test_slot_6_policy_statement_has_phrases(self):
        syns = get_burmese_synonyms(6)
        assert len(syns) >= 4
        assert POLSTM_PHRASE in syns

    def test_slot_12_definitions_has_phrases(self):
        syns = get_burmese_synonyms(12)
        assert len(syns) >= 6
        assert DEFS_PHRASE in syns


class TestBurmeseLabels:
    """Test label-row synonyms for slots 1, 2, 3, 4, 11."""

    def test_all_label_keys_exist(self):
        all_labels = get_all_burmese_labels()
        expected_keys = {
            "type",
            "policy_title",
            "policy_number",
            "applicable_sectors",
            "functional_areas",
            "brief_description",
            "effective_date",
            "approved_by",
            "prepared_by",
            "responsible_functions",
            "responsible_function_officers",
            "supersedes",
            "last_reviewed",
            "applies_to",
            "reason_for_policy",
            "policy_review_note",
        }
        assert set(all_labels.keys()) == expected_keys

    def test_type_label_phrases(self):
        syns = get_burmese_label_synonyms("type")
        assert len(syns) == 6
        assert TYPE_PHRASE in syns

    def test_policy_title_label_phrases(self):
        syns = get_burmese_label_synonyms("policy_title")
        assert len(syns) == 8
        assert PTITLE_PHRASE in syns

    def test_effective_date_label_phrases(self):
        syns = get_burmese_label_synonyms("effective_date")
        assert len(syns) == 8
        assert EFFDATE_PHRASE in syns

    def test_reason_for_policy_label_phrases(self):
        syns = get_burmese_label_synonyms("reason_for_policy")
        assert len(syns) == 8
        assert REASON_PHRASE in syns


class TestBurmeseReverseLookup:
    """Test get_canonical_for_burmese_label returns English canonical."""

    def test_type_reverse_lookup(self):
        assert get_canonical_for_burmese_label(TYPE_PHRASE) == "Type:"

    def test_policy_title_reverse_lookup(self):
        assert get_canonical_for_burmese_label(PTITLE_PHRASE) == "Policy Title:"

    def test_effective_date_reverse_lookup(self):
        assert get_canonical_for_burmese_label(EFFDATE_PHRASE) == "Effective Date/Period:"

    def test_reason_for_policy_reverse_lookup(self):
        assert get_canonical_for_burmese_label(REASON_PHRASE) == "Reason for Policy:"

    def test_unknown_returns_none(self):
        assert get_canonical_for_burmese_label("xyzunknownphrase") is None


class TestCanonicalLabelBurmese:
    """Test canonical_label() resolves Burmese labels to English canonical."""

    def test_type_burmese(self):
        assert canonical_label(TYPE_PHRASE) == "Type:"

    def test_policy_title_burmese(self):
        assert canonical_label(PTITLE_PHRASE) == "Policy Title:"

    def test_effective_date_burmese(self):
        assert canonical_label(EFFDATE_PHRASE) == "Effective Date/Period:"

    def test_reason_for_policy_burmese(self):
        assert canonical_label(REASON_PHRASE) == "Reason for Policy:"

    def test_english_still_works(self):
        assert canonical_label("Type") == "Type:"
        assert canonical_label("Policy Title") == "Policy Title:"
        assert canonical_label("Effective Date/Period") == "Effective Date/Period:"

    def test_unknown_returns_none(self):
        assert canonical_label("xyzunknownburmesephrase") is None


class TestRegexBurmese:
    """Test _LABEL_LINE_RE accepts Burmese characters."""

    def test_english_label_colon(self):
        m = _LABEL_LINE_RE.match("Type: HR Policy")
        assert m is not None
        assert m.group(1) == "Type"
        assert m.group(2) == "HR Policy"

    def test_burmese_label_colon(self):
        line = TYPE_PHRASE + ": HR Policy"
        m = _LABEL_LINE_RE.match(line)
        assert m is not None
        assert m.group(1) == TYPE_PHRASE
        assert m.group(2) == "HR Policy"

    def test_burmese_policy_title(self):
        line = PTITLE_PHRASE + ": My Policy Title"
        m = _LABEL_LINE_RE.match(line)
        assert m is not None
        assert m.group(1) == PTITLE_PHRASE
        assert m.group(2) == "My Policy Title"


class TestFieldMapBurmese:
    """Test field_map() with Burmese label rows."""

    def test_field_map_single_burmese_label(self):
        from policy_platform.framework.brain_fields import field_map
        # Use policy_title (no English-vocab stripping heuristic)
        paras = [PTITLE_PHRASE + ": Employee Health Benefit"]
        result = field_map(paras)
        assert result.get("Policy Title:") == "Employee Health Benefit"

    def test_field_map_multiple_burmese_labels(self):
        from policy_platform.framework.brain_fields import field_map
        # All fields use simple nouns without English-vocab heuristics
        paras = [
            PTITLE_PHRASE + ": Employee Health Benefit",
            EFFDATE_PHRASE + ": 2024-01-01",
            REASON_PHRASE + ": To provide guidance",
        ]
        result = field_map(paras)
        assert result.get("Policy Title:") == "Employee Health Benefit"
        assert result.get("Effective Date/Period:") == "2024-01-01"
        assert result.get("Reason for Policy:") == "To provide guidance"

    def test_field_map_mixed_english_burmese(self):
        from policy_platform.framework.brain_fields import field_map
        paras = [
            "Policy Title: HR Policy",
            EFFDATE_PHRASE + ": 2024-01-01",
            "Approved by: Director",
        ]
        result = field_map(paras)
        assert result.get("Policy Title:") == "HR Policy"
        assert result.get("Effective Date/Period:") == "2024-01-01"
        assert result.get("Approved by:") == "Director"
