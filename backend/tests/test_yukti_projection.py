"""What a person is shown of a Yukti reading, pinned without a database.

`yukti.projection` is the only path from the stored reading to a recruiter, an
email prompt or a PRISM Report, so its promises are pinned here directly:

* tags are text and a polarity, positives first, skill names resolved from the
  CURRENT skills, and a tag whose skill left the job is dropped, not renamed;
* an email prompt receives evidenced skill NAMES only: never a grade word, a
  model-written tag, a quote or a number;
* provenance is sentences with no part name, no weight and no score;
* the applicant label follows the pipeline status, so it stops the moment a
  sourced candidate applies;
* staleness is derived: the contract moved, or the resume did.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.yukti import config, projection

PYTHON, KAFKA, SQL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NAMES = {str(PYTHON): "Python", str(KAFKA): "Kafka", str(SQL): "SQL"}
VIEW = projection.JobSkillsView(names=NAMES, digest="d-now")


def _skill(skill_id, polarity="positive"):
    tag = {"kind": "skill", "skill_id": str(skill_id), "polarity": polarity}
    if polarity == "positive":
        tag.update({"strength": "strong", "quote": "Built Python services for five years."})
    return tag


# ── Tags ─────────────────────────────────────────────────────────────────────


def test_tags_are_text_and_polarity_positives_first() -> None:
    tags = [
        _skill(KAFKA, "negative"),
        _skill(PYTHON),
        {"kind": "experience", "text": "Senior backend delivery", "polarity": "positive",
         "strength": "some", "quote": "Led the payments team."},
    ]
    assert projection.evidence_tags(tags, NAMES) == [
        {"text": "Python", "polarity": "positive", "shown_in_row": True},
        {"text": "Senior backend delivery", "polarity": "positive", "shown_in_row": True},
        {"text": "Kafka", "polarity": "negative", "shown_in_row": True},
    ]


def test_a_tag_never_carries_its_quote_or_its_strength() -> None:
    for tag in projection.evidence_tags([_skill(PYTHON)], NAMES):
        assert set(tag) == {"text", "polarity", "shown_in_row"}


def test_only_the_first_tags_are_flagged_for_the_row() -> None:
    tags = [
        {"kind": "role_fit", "text": f"Fit {word}", "polarity": "positive"}
        for word in ("one", "two", "three", "four", "five", "six")
    ]
    shown = [t["shown_in_row"] for t in projection.evidence_tags(tags, NAMES)]
    limit = config.MAX_TAGS_SHOWN_IN_ROW
    assert shown == [True] * limit + [False] * (6 - limit)


def test_a_tag_whose_skill_left_the_job_is_dropped_not_renamed() -> None:
    gone = uuid.uuid4()
    assert projection.evidence_tags([_skill(gone), _skill(PYTHON)], NAMES) == [
        {"text": "Python", "polarity": "positive", "shown_in_row": True}
    ]


def test_a_skill_tag_reads_the_current_name() -> None:
    renamed = {**NAMES, str(PYTHON): "Python services"}
    assert projection.evidence_tags([_skill(PYTHON)], renamed)[0]["text"] == "Python services"


@pytest.mark.parametrize("stored", [None, {}, "x", [None, "x", {"kind": "skill"}]])
def test_malformed_tag_storage_reads_as_no_tags(stored) -> None:
    assert projection.evidence_tags(stored, NAMES) == []


# ── Email strengths ──────────────────────────────────────────────────────────


def test_an_email_prompt_receives_evidenced_skill_names_only() -> None:
    tags = [
        _skill(PYTHON),
        _skill(KAFKA, "negative"),
        {"kind": "role_fit", "text": "Payments platform owner", "polarity": "positive"},
        _skill(SQL),
        _skill(PYTHON),
    ]
    assert projection.strengths_for_prompt(tags, NAMES) == ["Python", "SQL"]


def test_the_skill_list_reads_as_prose() -> None:
    assert projection.skill_list_phrase([]) is None
    assert projection.skill_list_phrase(["Python"]) == "Python"
    assert projection.skill_list_phrase(["Python", "SQL"]) == "Python and SQL"
    assert projection.skill_list_phrase(["Python", "SQL", "Kafka"]) == "Python, SQL and Kafka"


def test_the_email_strengths_prose_names_skills_and_nothing_else() -> None:
    from app.api import emails
    from app.services import generation_sufficiency

    link = SimpleNamespace(evidence_tags_json=[_skill(PYTHON), _skill(SQL)])
    assert emails._strengths_prose(link, NAMES) == "- Evidenced on the resume: Python and SQL"
    empty = SimpleNamespace(evidence_tags_json=[])
    assert (
        emails._strengths_prose(empty, NAMES)
        == generation_sufficiency.GENERIC_STRENGTHS_PLACEHOLDER
    )


# ── Words ────────────────────────────────────────────────────────────────────


def test_status_words() -> None:
    assert projection.status_word("pending") == "Not checked yet"
    assert projection.status_word(None) == "Not checked yet"
    assert projection.status_word("not_assessed") == "Not assessed"
    assert projection.status_word("scored") is None
    assert projection.status_word("legacy") is None


@pytest.mark.parametrize(
    "status, source_type, label",
    [
        ("sourced", "databank", "Databank, not an applicant"),
        ("sourced", "sourced", "Sourced, not an applicant"),
        ("applied", "databank", None),
        ("assessment_completed", "sourced", None),
        ("applied", "applied", None),
    ],
)
def test_the_applicant_label_follows_the_pipeline_status(status, source_type, label) -> None:
    assert projection.applicant_label(status, source_type) == label


def test_the_pre_grade_word_is_the_resume_check_alone() -> None:
    assert projection.pre_grade_word("scored", 91.0) == "Highly Matching"
    assert projection.pre_grade_word("legacy", 61.0) == "Moderately Matching"
    assert projection.pre_grade_word("not_assessed", 91.0) is None
    assert projection.pre_grade_word("pending", None) is None


# ── Provenance ───────────────────────────────────────────────────────────────


def _all_lines() -> list[str]:
    record = {
        "contract_digest": "d-now",
        "components_present": list(config.COMPONENTS),
        "validation_parts": {"ctc": "Above range", "notice": "Serving notice",
                             "documents": "Some documents pending"},
        "last_attempt_failed": "model_unavailable",
    }
    lines: list[str] = []
    for status in config.STATUSES:
        for reason in (None, *config.FAILURE_REASONS, "unheard_of"):
            for assessed in (False, True):
                for failed in (False, True):
                    for pct in (0, 50, 70, 100):
                        lines.extend(projection.provenance_words(
                            status=status, failure_reason=reason, provenance=record,
                            assessed=assessed, must_have_failed=failed, weight_pct=pct,
                            applied=assessed,
                        ))
    return lines


def test_provenance_names_no_part_no_weight_and_no_score() -> None:
    lines = set(_all_lines())
    assert lines
    for line in lines:
        assert chr(8212) not in line, line
        for part in config.COMPONENTS:
            assert part not in line, line
        for word in ("score", "weight", "percent", "%"):
            assert word not in line.lower(), line
        digits = [char for char in line if char.isdigit()]
        # The notice buckets are the recruiter column's own words ("within 30
        # days"); nothing else may carry a digit.
        if digits:
            assert "days" in line, line


def test_the_resume_check_line_says_what_was_read_in_plain_words() -> None:
    lines = projection.provenance_words(
        status="scored", failure_reason=None,
        provenance={"components_present": ["must_have_evidenced", "nice_to_have_evidenced",
                                           "experience_level", "validation_fit"]},
        assessed=False, must_have_failed=False, weight_pct=70, applied=True,
    )
    assert "Resume check: skills and experience, read from the resume." in lines
    assert "Application answers: none that could be compared." in lines


def test_the_answers_line_uses_the_recruiter_columns_words() -> None:
    lines = projection.provenance_words(
        status="scored", failure_reason=None,
        provenance={"validation_parts": {"ctc": "Above range", "notice": "Immediate",
                                         "documents": "All documents ready"}},
        assessed=False, must_have_failed=False, weight_pct=70, applied=True,
    )
    assert (
        "Application answers: expected pay above range, available immediately "
        "and all documents ready." in lines
    )


def test_an_assessed_row_says_how_much_the_assessment_decides() -> None:
    def _lines(pct, status="scored"):
        return projection.provenance_words(
            status=status, failure_reason=None, provenance={}, assessed=True,
            must_have_failed=False, weight_pct=pct, applied=True,
        )

    assert "Tatva Assessment: mostly decides this grade." in _lines(70)
    assert "Tatva Assessment: entirely decides this grade." in _lines(100)
    assert "Tatva Assessment: does not move this grade." in _lines(0)
    assert projection.LINE_ASSESSMENT_ALONE in _lines(70, status="pending")


def test_a_kept_prior_result_says_the_latest_attempt_failed() -> None:
    lines = projection.provenance_words(
        status="scored", failure_reason=None,
        provenance={"last_attempt_failed": "model_unavailable"},
        assessed=False, must_have_failed=False, weight_pct=70, applied=True,
    )
    assert projection.LINE_LAST_ATTEMPT_FAILED in lines


def test_every_failure_reason_has_its_own_sentence() -> None:
    assert set(projection.FAILURE_LINES) == set(config.FAILURE_REASONS)


# ── Staleness ────────────────────────────────────────────────────────────────


def test_staleness_is_derived_from_the_digest_and_the_resume() -> None:
    profile = uuid.uuid4()
    fresh = projection.is_stale(
        "scored", {"contract_digest": "d-now"},
        read_profile_id=profile, current_profile_id=profile, current_digest="d-now",
    )
    assert fresh == (False, [])
    moved = projection.is_stale(
        "scored", {"contract_digest": "d-then"},
        read_profile_id=profile, current_profile_id=uuid.uuid4(), current_digest="d-now",
    )
    assert moved == (True, [projection.LINE_SKILLS_CHANGED, projection.LINE_RESUME_CHANGED])
    assert projection.is_stale(
        "legacy", None, read_profile_id=None, current_profile_id=None, current_digest="x"
    ) == (True, [projection.LINE_LEGACY])
    assert projection.is_stale(
        "not_assessed", None, read_profile_id=None, current_profile_id=None,
        current_digest="x",
    ) == (False, [])


# ── The PRISM AI Score summary (Phase 5 reads this) ──────────────────────────


def test_the_prism_summary_is_the_pre_assessment_reading_in_words() -> None:
    profile = uuid.uuid4()
    link = SimpleNamespace(
        yukti_status="scored", yukti_pre_score=86.0, yukti_failure_reason=None,
        yukti_provenance_json={"contract_digest": "d-now",
                               "components_present": ["must_have_evidenced"]},
        yukti_profile_id=profile, profile_id=profile,
        evidence_tags_json=[_skill(PYTHON), _skill(KAFKA, "negative")],
        status="applied", source_type="applied",
    )
    summary = projection.ai_score_summary(link, VIEW)
    assert summary == {
        "grade_word": "Matching",
        "status_word": None,
        "tags": [
            {"text": "Python", "polarity": "positive"},
            {"text": "Kafka", "polarity": "negative"},
        ],
        "provenance": [
            "Resume check: skills, read from the resume.",
            "Application answers: none that could be compared.",
        ],
    }
