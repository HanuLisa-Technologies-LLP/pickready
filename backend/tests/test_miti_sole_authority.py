"""Miti is the SOLE grading authority: one judge per skill (PLAN-p5 section 5).

Before WP5-B a skill's report word came from a rubric scorer while its
contribution to the overall came from the five evaluators' per-competency
bands: two judges, nothing making them agree. What is pinned here:

  1. The category scores, and so the overall, are computed from Miti's SKILL
     GRADES and from nothing else. Moving every evaluator's per-competency band
     from the top of the scale to the bottom moves no score and no word.
  2. The evaluators CHECK the grade instead of making one: a reading two or
     more words from Miti's grade routes the report to a person, named by
     skill and with no number in the reason. One word apart is boundary noise.
  3. A Must-have graded Not Matching, including one left unanswered (O5-1),
     caps the overall at `caps.must_have_ceiling()`, which grades Moderately
     Matching however strong everything else is.
  4. A Must-have Miti could NOT assess is not failed and caps nothing; it
     withholds the overall instead, and no score is stated for it.

Pure values. No database, no model.
"""
from __future__ import annotations

import re

import pytest

from app.services import rating
from app.services.miti import aggregation, caps
from app.services.miti.dimensions import (
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_VERIFIED_COMPETENCE,
    DimensionResult,
)
from tests import miti_fixtures as mf


def _evaluators(band: str, names: tuple[str, ...]) -> list[DimensionResult]:
    """Three judged evaluators, every per-competency reading set to `band`."""
    return [
        DimensionResult(
            dimension=dimension,
            band="solid",
            evidence_refs=(f"e-{dimension}",),
            per_competency={name: band for name in names},
        )
        for dimension in (DIM_VERIFIED_COMPETENCE, DIM_TRACK_RECORD, DIM_ROLE_FIT)
    ]


_SKILLS = (
    mf.skill("Kafka", "must_have", 90, priority=1),
    mf.skill("SQL", "must_have", 70, priority=2),
    mf.skill("Terraform", "nice_to_have", 80),
    mf.skill("Ownership", "behavioural", 85),
)
_NAMES = tuple(grade.name for grade in _SKILLS)


# ── 1 ────────────────────────────────────────────────────────────────────────


def test_the_overall_is_computed_from_the_skill_grades() -> None:
    out = aggregation.aggregate(_evaluators("solid", _NAMES), skill_grades=_SKILLS)
    # Priority-weighted, weight 1 / priority: (90 * 1 + 70 * 0.5) / 1.5.
    assert out.category_scores["must_have"] == pytest.approx((90 + 35) / 1.5)
    assert out.category_scores["nice_to_have"] == pytest.approx(80.0)
    assert out.category_scores["behavioural"] == pytest.approx(85.0)
    assert out.raw_composite == pytest.approx(
        sum(out.category_scores.values()) / len(out.category_scores)
    )


@pytest.mark.parametrize("band", ["strong", "solid", "partial", "weak", "absent"])
def test_no_evaluator_reading_moves_a_score_or_a_word(band: str) -> None:
    """The evaluators still read every skill. Whatever they read, the numbers
    and words Miti's grades produced are the ones delivered."""
    reference = aggregation.aggregate(_evaluators("solid", _NAMES), skill_grades=_SKILLS)
    moved = aggregation.aggregate(_evaluators(band, _NAMES), skill_grades=_SKILLS)
    assert moved.category_scores == reference.category_scores
    assert moved.category_grades == reference.category_grades
    assert moved.delivered_score == reference.delivered_score
    assert moved.overall_grade == reference.overall_grade


# ── 2 ────────────────────────────────────────────────────────────────────────


def test_an_evaluator_two_grades_away_routes_the_report_to_a_person() -> None:
    graded = (mf.skill("Kafka", "must_have", 92), mf.skill("Ownership", "behavioural", 92))
    out = aggregation.aggregate(
        [
            DimensionResult(
                dimension=DIM_VERIFIED_COMPETENCE,
                band="weak",
                evidence_refs=("e1",),
                per_competency={"Kafka": "weak"},
            )
        ],
        skill_grades=graded,
    )
    divergence = [reason for reason in out.review_reasons if "differs from the graded word" in reason]
    assert divergence and "Kafka" in divergence[0]
    assert "Ownership" not in divergence[0]
    assert not re.search(r"\d", divergence[0]), "a review reason carries no number"
    assert out.needs_human_review
    # It routes; it does not grade.
    assert out.category_grades["must_have"] == rating.GRADE_HIGHLY


def test_one_grade_apart_is_boundary_noise_and_routes_nothing() -> None:
    graded = (mf.skill("Kafka", "must_have", 92), mf.skill("Ownership", "behavioural", 92))
    out = aggregation.aggregate(
        [
            DimensionResult(
                dimension=DIM_VERIFIED_COMPETENCE,
                band="solid",
                evidence_refs=("e1",),
                per_competency={"Kafka": "solid"},
            )
        ],
        skill_grades=graded,
    )
    assert not any("differs from the graded word" in reason for reason in out.review_reasons)


def test_an_insufficient_evaluator_checks_nothing() -> None:
    graded = (mf.skill("Kafka", "must_have", 92), mf.skill("Ownership", "behavioural", 92))
    out = aggregation.aggregate(
        [
            DimensionResult(
                dimension=DIM_VERIFIED_COMPETENCE,
                band="partial",
                evidence_refs=(),
                insufficient_evidence=True,
                per_competency={"Kafka": "absent"},
            )
        ],
        skill_grades=graded,
    )
    assert not any("differs from the graded word" in reason for reason in out.review_reasons)


# ── 3 ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "failed",
    [mf.skill("Kafka", "must_have", 40), mf.unanswered("Kafka")],
    ids=["graded_not_matching", "unanswered"],
)
def test_a_failed_must_have_caps_the_overall_at_moderately_matching(failed) -> None:
    out = aggregation.aggregate(
        [],
        skill_grades=(
            failed,
            mf.skill("SQL", "must_have", 100, priority=2),
            mf.skill("Terraform", "nice_to_have", 100),
            mf.skill("Ownership", "behavioural", 100),
        ),
    )
    assert out.must_have_failed
    assert out.must_have_cap_applied
    assert out.delivered_score <= caps.must_have_ceiling()
    assert out.overall_grade == rating.GRADE_MODERATELY
    assert rating.grade_for_percent(caps.must_have_ceiling()) == rating.GRADE_MODERATELY


def test_the_cap_is_a_min_and_never_lifts_a_weaker_candidate() -> None:
    out = aggregation.aggregate(
        [],
        skill_grades=(
            mf.unanswered("Kafka"),
            mf.skill("Ownership", "behavioural", 30),
        ),
    )
    # The control FIRED (it is recorded) and BOUND nothing: the candidate was
    # already below the ceiling, and a `min` leaves them exactly where they were.
    assert caps.CONTROL_COMPETENCY_THRESHOLD in {cap.control for cap in out.applied_caps}
    assert not out.must_have_cap_applied
    assert out.delivered_score == pytest.approx(out.adjusted_composite)
    assert out.delivered_score < caps.must_have_ceiling()
    assert out.overall_grade == rating.GRADE_NOT


# ── 4 ────────────────────────────────────────────────────────────────────────


def test_a_not_assessed_must_have_caps_nothing_and_withholds_the_overall() -> None:
    out = aggregation.aggregate(
        [],
        skill_grades=(
            mf.skill("Kafka", "must_have", None),
            mf.skill("Ownership", "behavioural", 95),
        ),
    )
    assert not out.must_have_failed
    assert caps.CONTROL_COMPETENCY_THRESHOLD not in {cap.control for cap in out.applied_caps}
    assert out.overall_status == aggregation.OVERALL_NOT_ASSESSED
    assert out.overall_grade == ""
    assert out.stated_score is None
    assert "Kafka" in out.insufficient_skills
