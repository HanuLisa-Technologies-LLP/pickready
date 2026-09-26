"""Divergence direction and the no-nudge constraint.

Pure. The HTTP half, a Team Review verdict writing its `CalibrationRecord`,
lives in `test_dashboard_workflows.py`.

The override-rate metric and the raw-numbers calibration view were DELETED
with their routes in the Vivekium release (PLAN-p7 WP-B6): the view returned
the raw D1-D5 numbers to a client and the queue had no screen. What survives
is the RECORD of a divergence and the rule that nothing here may nudge a
reviewer towards agreement.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services import calibration, rating, team_review

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


# ── No target travels with the record ────────────────────────────────────────


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


def test_the_two_calibration_sources_are_named_and_closed():
    assert calibration.CALIBRATION_SOURCES == ("outcome", "team_review_divergence")
    assert calibration.sources_are_exhaustive(["outcome"])
    assert not calibration.sources_are_exhaustive(["something_else"])
