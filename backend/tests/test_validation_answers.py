"""The Validation column must show all 38 profile answers, not just the 6
application fields (product owner report, 2026-08-16).

`job_candidates.validation_answers` renders the customer-portal ranking
table's Validation Q&A modal. It used to read only
`application_validation.VALIDATION_FIELDS` from `job_candidate_links.
validation_json`; the candidate's own 38-item profile form
(`candidate_profile_form.ALL_FIELDS`, stored on `candidates.profile_form_json`)
never reached it.
"""
from __future__ import annotations

from app.services.application_validation import VALIDATION_FIELDS
from app.services.candidate_profile_form import ALL_FIELDS
from app.services.job_candidates import validation_answers


def test_the_application_fields_and_the_full_profile_form_both_appear() -> None:
    submitted = {"current_ctc": "18 LPA", "role_interest": "Growth."}
    profile = {"current_city": "Pune", "total_experience": "4 Years"}

    answers = validation_answers(submitted, profile)

    assert len(answers) == len(VALIDATION_FIELDS) + len(ALL_FIELDS)
    keys = [item["key"] for item in answers]
    assert len(keys) == len(set(keys)), "no two answers may share a key"

    by_key = {item["key"]: item for item in answers}
    assert by_key["current_ctc"]["answer"] == "18 LPA"
    assert by_key["current_ctc"]["group"] == "Application"
    assert by_key["profile:current_city"]["answer"] == "Pune"
    assert by_key["profile:current_city"]["group"] == "Personal Details"
    # A profile field the candidate never answered still appears.
    assert by_key["profile:last_company_name"]["answer"] is None


def test_a_missing_profile_form_still_renders_every_profile_question() -> None:
    """A candidate who has not filled their profile yet, or a link predating
    the profile form, must not lose the questionnaire -- only the answers."""
    answers = validation_answers({}, None)
    profile_answers = [a for a in answers if a["group"] != "Application"]
    assert len(profile_answers) == len(ALL_FIELDS)
    assert all(item["answer"] is None for item in profile_answers)
