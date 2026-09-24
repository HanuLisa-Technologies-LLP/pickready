"""Calibration: divergence routing, recorded as audit data.

A reviewer disagreeing with the Vivekium Score is DATA. It is either a sign
that the scorecard needs recalibration or a sign that the reviewer saw
something the assessment missed, and nothing in the system can tell which. So
`raise_divergence` RECORDS that disagreement, as a `CalibrationRecord` and an
audit row, and nothing here exists to discourage it.

WHAT WENT, AND WHY (Vivekium release, PLAN-p7 WP-B6)
----------------------------------------------------
Two read surfaces lived here and both are DELETED with their routes: the
audited raw-numbers view (D8), which returned the raw D1-D5 numbers to a
client and so broke the no-numbers rule with no exception left to cover it,
and the Standards Board queue with its override-rate metric, which had no
screen. The records are still
written: `calibration_records` is the audit trail of every divergence, read
by whoever maintains the scorecard, not by a product surface.

MEASURE, NEVER NUDGE. THIS IS STILL A HARD CONSTRAINT.
-------------------------------------------------------
spec-doc6 8.2 and `PRODUCT.md`: no nudge, warning, friction or visual
discouragement when a reviewer disagrees with the score. Nothing in this
module returns a warning, a threshold verdict, a colour or a severity.

WHY THE RECORD IS A `CalibrationRecord` AND NOT A NEW TABLE
------------------------------------------------------------
Runbook §59 already names the object and this schema already has it: "did the
grade turn out to be right?". A reviewer's contrary verdict is exactly a
judgment about the prediction, entered by a person, after the fact. Migration
0069 widens the table with `source` and `team_review_id` rather than adding a
second table, because two tables holding "somebody thinks the grade was wrong"
would be two places to look and two things to keep in step.
"""
from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import audit, team_review

__all__ = [
    "SOURCE_OUTCOME",
    "SOURCE_TEAM_REVIEW_DIVERGENCE",
    "CALIBRATION_SOURCES",
    "ASSESSMENT_TOO_HIGH",
    "ASSESSMENT_TOO_LOW",
    "ASSESSMENT_ACCURATE",
    "CALIBRATION_DIVERGENCE_RAISED",
    "direction_of_divergence",
    "raise_divergence",
]


# ── `calibration_records.source` ─────────────────────────────────────────────

#: The original meaning of the table: a hiring outcome observed later.
SOURCE_OUTCOME = "outcome"
#: A Team Review verdict that disagreed with the machine grade.
SOURCE_TEAM_REVIEW_DIVERGENCE = "team_review_divergence"

CALIBRATION_SOURCES: tuple[str, ...] = (SOURCE_OUTCOME, SOURCE_TEAM_REVIEW_DIVERGENCE)


# ── `calibration_records.outcome_assessment`, for a divergence ───────────────
#
# The column's own meaning is "was the grade right? too high? too low?", and a
# divergent verdict answers exactly that. The direction is derived from the two
# vocabularies' ORDER, never from a second opinion: `team_review.VERDICTS` runs
# pass -> hold -> reject and `rating.GRADES` runs best -> worst, so comparing
# the position of the verdict the machine implies against the verdict the human
# gave says which way they disagreed.

# The VALUES ARE THE COLUMN'S OWN, from migration 0059's
# `ck_calibration_assessment`: accurate / too_high / too_low. Not a new
# vocabulary invented for this surface. A divergence only ever writes the two
# directional ones, because "accurate" is what an AGREEING verdict would mean
# and an agreeing verdict writes no row at all.
ASSESSMENT_TOO_HIGH = "too_high"
ASSESSMENT_TOO_LOW = "too_low"
#: Written by the outcome path, never by a divergence. Named here so a reader
#: comparing this module against the CHECK constraint finds all three.
ASSESSMENT_ACCURATE = "accurate"


# ── Audit actions ────────────────────────────────────────────────────────────
#
# Written through `audit.record_action`, whose `action` is a free string. These
# are named here rather than in `services/audit.py` because they belong to this
# surface, and `ACTIVITY_ACTIONS` over there is a presentation list for the
# Super Admin activity view rather than a filter on what is written: every
# audit row is written and readable whether or not it appears in that tuple.
CALIBRATION_DIVERGENCE_RAISED = "calibration_divergence_raised"


def direction_of_divergence(verdict: str, grade: str | None) -> str | None:
    """Which way a reviewer disagreed with the machine grade.

    None when they agreed, which includes the case where there is no grade to
    disagree with. `team_review.agrees_with_grade` is the single mapping
    between the two vocabularies and this function does not add a second one:
    it asks that function first and only then works out the direction.
    """
    if team_review.agrees_with_grade(verdict, grade):
        return None
    implied = team_review.verdict_for_grade(grade)
    if implied is None:
        return None
    verdicts = team_review.VERDICTS
    human = verdicts.index(verdict)
    machine = verdicts.index(implied)
    if human > machine:
        # The human's verdict is further down a list that runs strongest
        # first, so the human is harsher: the machine graded too high.
        return ASSESSMENT_TOO_HIGH
    if human < machine:
        return ASSESSMENT_TOO_LOW
    # UNREACHABLE while both vocabularies are total orders: equal positions
    # mean the verdicts agree, and `agrees_with_grade` returned above. It
    # raises rather than defaulting because the alternative is writing a
    # direction nobody derived into the one dataset that must not contain
    # invented rows.
    raise ValueError(
        f"verdict {verdict!r} neither agrees with grade {grade!r} nor "
        "differs in a direction the two vocabularies can express"
    )


async def raise_divergence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    job_id: uuid.UUID | str,
    evaluation_id: uuid.UUID | str | None,
    team_review_id: uuid.UUID | str,
    reviewer_user_id: uuid.UUID | str,
    reviewer_role: str | None,
    verdict: str,
    machine_grade: str | None,
    machine_confidence: str | None = None,
    link_id: uuid.UUID | str | None = None,
    candidate_id: uuid.UUID | str | None = None,
) -> uuid.UUID | None:
    """Record a Team Review verdict that disagreed with the Vivekium Score.

    Returns the calibration record's id, or None when the verdict AGREED and
    there was nothing to record. Agreement is not a calibration event: a table
    that recorded both would make "how many divergences" a question you answer
    by filtering rather than by counting, and an override rate is computed
    from the reviews themselves rather than from this table.

    IDEMPOTENT PER REVIEW. A reviewer may refine their verdict, so the record
    is keyed on `team_review_id` and upserted. Without that, a reviewer who
    changed their mind twice would appear in the substrate three times and
    inflate the Standards Board's queue with one person's single opinion.

    Nothing here blocks, warns or returns anything the caller could render as
    disapproval. It writes a row and audits it.
    """
    direction = direction_of_divergence(verdict, machine_grade)
    if direction is None:
        # The reviewer agreed, or there is no machine grade to differ from.
        # A previously recorded divergence for this same review is withdrawn:
        # leaving it would assert a disagreement the reviewer no longer holds.
        await session.execute(
            text(
                "DELETE FROM calibration_records "
                "WHERE team_review_id = :trid AND source = :source"
            ),
            {"trid": str(team_review_id), "source": SOURCE_TEAM_REVIEW_DIVERGENCE},
        )
        return None

    record_id = (
        await session.execute(
            text(
                """
                INSERT INTO calibration_records (
                    tenant_id, job_id, evaluation_id, evaluation_ref,
                    source, team_review_id, predicted_grade,
                    predicted_confidence, outcome_assessment, recorded_by, note
                )
                VALUES (
                    :tenant_id, :job_id, :evaluation_id, :evaluation_id,
                    :source, :team_review_id, :predicted_grade,
                    :predicted_confidence, :assessment, :recorded_by, :note
                )
                ON CONFLICT (team_review_id)
                    WHERE source = 'team_review_divergence'
                DO UPDATE SET
                    outcome_assessment = EXCLUDED.outcome_assessment,
                    predicted_grade = EXCLUDED.predicted_grade,
                    predicted_confidence = EXCLUDED.predicted_confidence,
                    note = EXCLUDED.note
                RETURNING id
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "job_id": str(job_id),
                "evaluation_id": None if evaluation_id is None else str(evaluation_id),
                "source": SOURCE_TEAM_REVIEW_DIVERGENCE,
                "team_review_id": str(team_review_id),
                "predicted_grade": machine_grade,
                "predicted_confidence": machine_confidence,
                "assessment": direction,
                "recorded_by": str(reviewer_user_id),
                # The reviewer's own remark is NOT copied here. RBAC 29 makes a
                # remark the author's, and duplicating it into a second table
                # creates a copy that can drift from the one they can edit.
                # `team_review_id` is the pointer to it.
                "note": (
                    f"Team Review verdict {team_review.VERDICT_LABELS[verdict]} "
                    f"against Vivekium grade {machine_grade}."
                ),
            },
        )
    ).scalar_one()

    await audit.record_action(
        session,
        action=CALIBRATION_DIVERGENCE_RAISED,
        actor_user_id=reviewer_user_id,
        actor_role=reviewer_role,
        tenant_id=tenant_id,
        resource_type="calibration_record",
        resource_id=record_id,
        job_id=job_id,
        application_id=link_id,
        candidate_id=candidate_id,
        new_state={
            "verdict": verdict,
            "predicted_grade": machine_grade,
            "outcome_assessment": direction,
        },
    )
    return uuid.UUID(str(record_id))


def sources_are_exhaustive(values: Sequence[str]) -> bool:
    """Every stored `source` is one this module knows about."""
    return set(values) <= set(CALIBRATION_SOURCES)
