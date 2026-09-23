"""The seven-column derivations: words or an honest None, never a guess.

Vivekium feature 3 (owner-ruled 2026-09-18). Every function here is pure, so
the whole truth table runs with no database and no clock. The assertions to
protect hardest are the None cases: a missing job range, an unparseable CTC
and an unstated education must all yield NO word, because the alternative is
a fabricated comparison sitting beside a hiring decision.
"""
from __future__ import annotations

import pytest

from app.services import recruiter_columns as rc


# ── parse_ctc_rupees ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4,00,000", 400_000),          # the form's own worked example
        ("400000", 400_000),            # plain rupees
        ("4 LPA", 400_000),             # lakhs per annum
        ("4.5lpa", 450_000),            # no space, fractional
        ("12L", 1_200_000),             # the L suffix
        ("12 lakhs", 1_200_000),
        ("Rs. 6,50,000 per annum", 650_000),  # prose around the number
        ("18", 1_800_000),              # bare small number reads as lakhs
        ("999", 99_900_000),            # still under the 1000 threshold
        ("1000", 1000),                 # at 1000, rupees as typed
    ],
)
def test_ctc_parsing_reads_what_candidates_actually_type(raw, expected):
    assert rc.parse_ctc_rupees(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "negotiable", "N/A", "-"])
def test_ctc_parsing_refuses_a_non_answer(raw):
    assert rc.parse_ctc_rupees(raw) is None


# ── ctc_match ────────────────────────────────────────────────────────────────

_RANGE = {"ctc_min": 400_000, "ctc_max": 1_200_000}


def test_ctc_within_above_below():
    assert rc.ctc_match("6,00,000", _RANGE) == rc.CTC_WITHIN
    assert rc.ctc_match("15 LPA", _RANGE) == rc.CTC_ABOVE
    assert rc.ctc_match("2,00,000", _RANGE) == rc.CTC_BELOW


def test_ctc_boundaries_are_inclusive_ties_go_to_the_candidate():
    """House rule 8: exactly at either end is WITHIN the range."""
    assert rc.ctc_match("4,00,000", _RANGE) == rc.CTC_WITHIN
    assert rc.ctc_match("12,00,000", _RANGE) == rc.CTC_WITHIN


def test_ctc_match_is_none_when_either_side_is_silent():
    assert rc.ctc_match("6,00,000", None) is None
    assert rc.ctc_match("6,00,000", {}) is None
    assert rc.ctc_match("6,00,000", {"range_lpa": "15-22"}) is None  # legacy shape
    assert rc.ctc_match(None, _RANGE) is None
    assert rc.ctc_match("negotiable", _RANGE) is None
    # An inverted or non-positive range is a data defect, not a comparison.
    assert rc.ctc_match("6,00,000", {"ctc_min": 9, "ctc_max": 2}) is None


# ── notice_period_bucket ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("option", "bucket"),
    [
        ("Immediate", "Immediate"),
        ("15 days", "Within 30 days"),
        ("30 days", "Within 30 days"),
        ("45 days", "30 to 60 days"),
        ("60 days", "30 to 60 days"),
        ("90 days", "60 to 90 days"),
        ("Serving notice period", "Serving notice"),
    ],
)
def test_every_form_option_has_a_bucket(option, bucket):
    """Keyed by the form's own options, so the two lists cannot drift."""
    from app.services.application_validation import NOTICE_PERIOD_OPTIONS

    assert option in NOTICE_PERIOD_OPTIONS
    assert rc.notice_period_bucket(option) == bucket


def test_the_form_option_list_is_fully_covered():
    """A NEW option added to the form must land in a bucket or this fails."""
    from app.services.application_validation import NOTICE_PERIOD_OPTIONS

    for option in NOTICE_PERIOD_OPTIONS:
        assert rc.notice_period_bucket(option) is not None, option


def test_free_text_day_counts_bucket_inclusively():
    assert rc.notice_period_bucket("120 days") == "Above 90 days"
    assert rc.notice_period_bucket("one month, 30 days really") == "Within 30 days"
    assert rc.notice_period_bucket(None) is None
    assert rc.notice_period_bucket("depends") is None


# ── education_match ──────────────────────────────────────────────────────────


def _profile(*rows: str) -> dict:
    return {"education": {row: {"course": "x"} for row in rows}}


def test_education_match_partial_and_no_match():
    graduate = _profile("class_x", "class_xii", "graduation")
    assert rc.education_match(graduate, "Bachelor's degree in engineering") == rc.EDUCATION_MATCH
    # One rung short: diploma against a bachelor's bar.
    diploma = _profile("class_xii", "diploma")
    assert rc.education_match(diploma, "B.Tech or equivalent degree") == rc.EDUCATION_PARTIAL
    # Two rungs short: school against a bachelor's bar.
    school = _profile("class_x", "class_xii")
    assert rc.education_match(school, "Bachelor's degree required") == rc.EDUCATION_NO_MATCH


def test_education_exceeding_the_bar_is_a_match():
    pg = _profile("graduation", "post_graduation")
    assert rc.education_match(pg, "Bachelor's degree") == rc.EDUCATION_MATCH


def test_the_higher_stated_requirement_wins():
    """'Master's preferred, Bachelor's required' reads as the master's bar."""
    graduate = _profile("graduation")
    assert (
        rc.education_match(graduate, "MBA preferred; bachelor's degree required")
        == rc.EDUCATION_PARTIAL
    )


def test_education_is_none_when_either_side_is_silent():
    assert rc.education_match(None, "Bachelor's degree") is None
    assert rc.education_match({}, "Bachelor's degree") is None
    assert rc.education_match(_profile("certifications"), "Bachelor's degree") is None
    assert rc.education_match(_profile("graduation"), None) is None
    assert rc.education_match(_profile("graduation"), "  ") is None
    # A requirement sentence with no recognisable level asserts no bar.
    assert rc.education_match(_profile("graduation"), "strong fundamentals") is None


def test_word_boundaries_hold():
    """'iti' must not fire inside 'ambition'; the disqualifier lesson."""
    assert rc.required_education_level("We value ambition and drive") is None
    assert rc.required_education_level("ITI certificate holders welcome") == 1


# ── bgv_status_word ──────────────────────────────────────────────────────────


def test_bgv_words_cover_every_derivable_status():
    from app.models.bgv_verification import CANDIDATE_BGV_STATUSES

    for status in CANDIDATE_BGV_STATUSES:
        word = rc.bgv_status_word(status)
        assert word, status
        assert not any(char.isdigit() for char in word)
    assert rc.bgv_status_word("verified") == "Done"
    assert rc.bgv_status_word("pending") == "Pending"
    assert rc.bgv_status_word("not_started") == "Not Started"
    # The two states the brief's vocabulary does not name, rendered honestly.
    assert rc.bgv_status_word("not_required") == "Not Required"
    assert rc.bgv_status_word("not_verified") == "Not Confirmed"
    assert rc.bgv_status_word(None) == "Not Started"


# ── the row payload carries the columns ──────────────────────────────────────


def test_the_row_payload_serves_the_seven_column_fields():
    """`_row_payload` on a bare fixture row: every new field present, every
    derived word None (nothing stated), and match_percent an int when the
    score exists."""
    from app.services import job_candidates

    row = {
        "link_id": "l", "candidate_id": "c", "profile_id": None,
        "source": None, "tier": None, "status": "applied",
        "status_updated_at": None, "application_source": None,
        "source_type": None, "archived_at": None, "breakdown": None,
        "validation": None, "tenant_id": "t", "full_name": "A", "email": "a@x",
        "profile_form": None, "resume_url": None, "resume_filename": None,
        "resume_mime_type": None, "report_id": None, "report_ready_at": None,
        "profile_age": "new_profile", "is_new_candidate": False,
        "review_charged": False, "match_score": 87.4,
    }
    payload = job_candidates._row_payload(row, "Non-managerial")
    assert payload["match_percent"] == 87
    assert payload["ctc_match_label"] is None
    assert payload["notice_period_label"] is None
    assert payload["education_match_label"] is None
    # No employment declaration: verification is not required, honestly.
    assert payload["bgv_status"] == "not_required"
    assert payload["bgv_status_label"] == "Not Required"

    row["match_score"] = None
    assert job_candidates._row_payload(row, "Non-managerial")["match_percent"] is None
