"""The override rate, divergence direction, and the no-nudge constraint.

Pure. The HTTP half of calibration lives in `test_dashboard_workflows.py` and
`test_dashboard_rbac_matrix.py`; this file is the arithmetic and the shape.

THE MOST IMPORTANT TEST IN THIS FILE IS THE ONE ABOUT A FIELD SET
------------------------------------------------------------------
spec-doc6 §8.2 and `PRODUCT.md`: measure, never nudge. That is not enforceable
by a comment. What makes it enforceable is that `OverrideRate` carries counts
and a rate and nothing a UI could render as disapproval, and that
`test_the_metric_carries_no_target_and_no_verdict` fails the day somebody adds
an `over_target` convenience flag. A target rendered beside a recruiter's
deviation figure does not improve calibration; it stops the deviation being
reported, which destroys the only signal the metric exists to collect.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services import calibration, rating, team_review
from app.services.miti import dimensions as miti_dimensions

# ── The direction of a disagreement ──────────────────────────────────────────


@pytest.mark.parametrize(
    "verdict,grade,expected",
    [
        # Agreement, in every combination the mapping calls agreement.
        (team_review.VERDICT_PASS, rating.GRADE_HIGHLY, None),
        (team_review.VERDICT_PASS, rating.GRADE_MATCHING, None),
        (team_review.VERDICT_HOLD, rating.GRADE_MODERATELY, None),
        (team_review.VERDICT_REJECT, rating.GRADE_NOT, None),
        # The human is harsher than the machine.
        (team_review.VERDICT_REJECT, rating.GRADE_HIGHLY, calibration.ASSESSMENT_TOO_HIGH),
        (team_review.VERDICT_HOLD, rating.GRADE_MATCHING, calibration.ASSESSMENT_TOO_HIGH),
        # The human is kinder than the machine.
        (team_review.VERDICT_PASS, rating.GRADE_NOT, calibration.ASSESSMENT_TOO_LOW),
        (team_review.VERDICT_HOLD, rating.GRADE_NOT, calibration.ASSESSMENT_TOO_LOW),
    ],
)
def test_the_direction_of_a_divergence(verdict, grade, expected):
    assert calibration.direction_of_divergence(verdict, grade) == expected


def test_an_absent_grade_is_not_a_divergence():
    """A candidate whose profile has not been written cannot be deviated from.

    Counting it as an override would move the rate with the pipeline's
    COVERAGE rather than with anybody's judgment, which is the one thing the
    metric must not measure.
    """
    for verdict in team_review.VERDICTS:
        assert calibration.direction_of_divergence(verdict, None) is None


def test_the_direction_uses_the_one_existing_mapping():
    """`team_review.agrees_with_grade` is the single comparison between the two
    vocabularies, and this module must not grow a second one.

    Asserted by property rather than by inspection: for every (verdict, grade)
    pair, a direction is returned exactly when that function says they
    disagree. A second mapping would have to reproduce that agreement exactly,
    and would eventually not.
    """
    for verdict in team_review.VERDICTS:
        for grade in rating.GRADES:
            agreed = team_review.agrees_with_grade(verdict, grade)
            direction = calibration.direction_of_divergence(verdict, grade)
            assert agreed == (direction is None)


def test_the_written_assessment_values_are_the_columns_own():
    """Migration 0059's `ck_calibration_assessment` accepts accurate /
    too_high / too_low. A fourth value invented here is a row the database
    refuses at write time, which is a 500 for a recruiter saving a remark."""
    migration = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0059_hiring_intelligence.py"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"outcome_assessment IS NULL OR outcome_assessment IN\s*\(([^)]*)\)",
        migration,
    )
    assert match, "the assessment CHECK constraint moved; re-point this test"
    allowed = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert {
        calibration.ASSESSMENT_TOO_HIGH,
        calibration.ASSESSMENT_TOO_LOW,
        calibration.ASSESSMENT_ACCURATE,
    } <= allowed


# ── The override rate ────────────────────────────────────────────────────────


def test_the_metric_carries_no_target_and_no_verdict():
    """MEASURE, NEVER NUDGE, as a property of the type.

    Counts and a rate. No target, no threshold, no severity, no colour, no
    boolean. Asserted on the FIELD SET rather than on the absence of specific
    names, because a future field called `status` would pass a narrower check
    and reach a screen.
    """
    rate = calibration.OverrideRate(comparable=10, diverged=3)
    assert set(rate.as_dict()) == {"comparable", "diverged", "rate"}
    assert set(rate.__dataclass_fields__) == {"comparable", "diverged"}


def test_the_rate_is_divergent_over_comparable():
    assert calibration.OverrideRate(comparable=10, diverged=3).rate == 0.3
    assert calibration.OverrideRate(comparable=4, diverged=4).rate == 1.0


def test_nothing_comparable_reads_as_zero_and_says_so():
    """Zero here means "no reviews with a machine grade yet", NOT perfect
    agreement. `comparable` travels beside the rate so a reader can tell the
    two apart, which a bare 0.0 cannot."""
    empty = calibration.OverrideRate(comparable=0, diverged=0)
    assert empty.rate == 0.0
    assert empty.as_dict()["comparable"] == 0


def test_the_module_names_no_target_anywhere():
    """The Dashboard Specification's "< 15%" is a target for the people who
    maintain the scorecard, and it must not travel with the measurement.

    Grepped for the literal because that is exactly how it would arrive: as a
    constant somebody added "so the UI can colour the number".
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "calibration.py"
    ).read_text(encoding="utf-8")
    code = [
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#") and "0.15" in line
    ]
    assert not code, f"a deviation target reached the measurement: {code}"


# ── The audited raw-numbers view ─────────────────────────────────────────────


def test_the_calibration_view_carries_the_numbers_and_the_profile_panel_does_not():
    """spec-doc6 D8, from both sides in one test.

    The panel and this view read the SAME evaluation. What separates them is
    which fields each one is built from, and asserting only that the view has
    the numbers would leave the panel free to have them too.
    """
    from app.services import dashboard

    evaluation = {
        "id": "11111111-1111-4111-8111-111111111111",
        "dimension_scores": {
            "verified_competence": {"band": "strong", "evidence_refs": ["ev-1"]},
            "authenticity_consistency": {"band": "partial", "evidence_refs": []},
        },
        "competency_scores": {"kafka": {"band": "solid"}},
        "aggregate_json": {
            "raw_composite": 81.0,
            "adjusted_composite": 74.5,
            "authenticity_factor": 0.92,
            "category_scores": {"must_have": 78.0},
            "category_grades": {"must_have": rating.GRADE_MATCHING},
            "overall_grade": rating.GRADE_MATCHING,
            "confidence": "medium",
        },
        "gate_results_json": [],
        "triangulation_json": {},
        "confidence": "medium",
    }

    view = calibration.calibration_view(evaluation)
    assert view["raw_composite"] == 81.0
    assert view["adjusted_composite"] == 74.5
    assert view["authenticity_factor"] == 0.92
    assert view["category_scores"] == {"must_have": 78.0}
    # The raw per-dimension score, which is the field D8 keeps off every other
    # surface. It is the representative score the aggregator itself used.
    strong = next(d for d in view["dimensions"] if d["band"] == "strong")
    assert strong["raw_score"] == miti_dimensions.band_for("strong")

    panel = dashboard.profile_panel(
        evaluation=evaluation,
        candidate_name="Test Candidate Zero",
        system_id="AAAA-BBBB-CCCC",
        under_integrity_review=False,
    )
    flat = repr(panel)
    for number in ("81.0", "74.5", "0.92", "78.0", str(miti_dimensions.band_for("strong"))):
        assert number not in flat, f"{number} reached the Ready Pick Profile panel"


def test_every_dimension_appears_in_the_view_even_when_unrated():
    """A dimension the evaluators never reached reports a null band and a null
    score, not a zero. Somebody auditing the engine needs to see the gap."""
    view = calibration.calibration_view(
        {"id": "x", "dimension_scores": {}, "aggregate_json": {}}
    )
    assert [d["dimension"] for d in view["dimensions"]] == list(
        miti_dimensions.DIMENSIONS
    )
    assert all(d["raw_score"] is None for d in view["dimensions"])


def test_an_unknown_band_reports_no_number_rather_than_a_guess():
    """This view exists for people auditing the engine. A fabricated score here
    would be a fabrication inside the audit itself."""
    view = calibration.calibration_view(
        {
            "id": "x",
            "dimension_scores": {"verified_competence": {"band": "excellent"}},
            "aggregate_json": {},
        }
    )
    entry = next(
        d for d in view["dimensions"] if d["dimension"] == "verified_competence"
    )
    assert entry["band"] == "excellent"
    assert entry["raw_score"] is None


def test_the_two_calibration_sources_are_named_and_closed():
    assert calibration.CALIBRATION_SOURCES == ("outcome", "team_review_divergence")
    assert calibration.sources_are_exhaustive(["outcome"])
    assert not calibration.sources_are_exhaustive(["something_else"])
