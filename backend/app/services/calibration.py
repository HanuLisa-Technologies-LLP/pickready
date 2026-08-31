"""Calibration: the override rate, divergence routing, and the audited view.

THREE THINGS LIVE HERE AND THEY ARE RELATED BY ONE IDEA
--------------------------------------------------------
A reviewer disagreeing with the Ready Pick Score is DATA. It is either a sign
that the scorecard needs recalibration or a sign that the reviewer saw
something the assessment missed, and nothing in the system can tell which. So
all three of these exist to RECORD that disagreement and none of them exists to
discourage it:

  1. `override_rate` measures how often a Team Review verdict differs from the
     machine grade (the Dashboard Specification's success metric).
  2. `raise_divergence` writes a `CalibrationRecord` when one does, and audits
     it so it surfaces in the Super Admin activity view.
  3. `calibration_view` exposes the raw D1-D5 numbers, evaluator outputs and
     aggregation internals that D8 keeps off every other surface, to the two
     roles D8 names, and logs every read.

MEASURE, NEVER NUDGE. THIS IS A HARD CONSTRAINT, NOT A PREFERENCE.
-------------------------------------------------------------------
spec-doc6 §8.2 and `PRODUCT.md`: implement the measurement, and implement NO
nudge, warning, friction or visual discouragement when a reviewer disagrees
with the score. The Dashboard Specification targets deviation at under 15%, and
a target that quietly pushes recruiters toward agreement destroys the very
signal it is measuring: a recruiter who stops disagreeing has not become better
calibrated, they have stopped reporting.

So nothing in this module returns a warning, a threshold verdict, a colour, a
severity or a boolean a UI could render as disapproval. `OverrideRate` carries
counts and a rate and NOTHING ELSE, and the target is deliberately absent from
the payload: a number rendered beside a target is a scoreboard.
`tests/test_calibration.py` asserts that field set, so a future "over_target"
convenience flag fails the suite rather than reaching a screen.

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
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import audit, rating, team_review
from app.services.miti import dimensions as miti_dimensions

__all__ = [
    "SOURCE_OUTCOME",
    "SOURCE_TEAM_REVIEW_DIVERGENCE",
    "CALIBRATION_SOURCES",
    "ASSESSMENT_TOO_HIGH",
    "ASSESSMENT_TOO_LOW",
    "ASSESSMENT_ACCURATE",
    "CALIBRATION_DIVERGENCE_RAISED",
    "CALIBRATION_INTERNALS_VIEWED",
    "OverrideRate",
    "direction_of_divergence",
    "raise_divergence",
    "override_rate",
    "divergences",
    "calibration_view",
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
CALIBRATION_INTERNALS_VIEWED = "calibration_internals_viewed"


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
    """Record a Team Review verdict that disagreed with the Ready Pick Score.

    Returns the calibration record's id, or None when the verdict AGREED and
    there was nothing to record. Agreement is not a calibration event: a table
    that recorded both would make "how many divergences" a question you answer
    by filtering rather than by counting, and the metric that matters is the
    rate, which `override_rate` computes from the reviews themselves.

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
                    f"against Ready Pick grade {machine_grade}."
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


# ── The override rate ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OverrideRate:
    """Counts and a rate. Deliberately nothing else.

    No target, no threshold verdict, no severity, no colour. The Dashboard
    Specification's "< 15%" is a target for the people who build the scorecard,
    not a judgment to render beside a recruiter's name, and a payload carrying
    it would be one component away from becoming a scoreboard.
    """

    #: Reviews that had a machine grade to be compared against.
    comparable: int
    #: Of those, the ones whose verdict differed.
    diverged: int

    @property
    def rate(self) -> float:
        """Divergent reviews as a fraction of comparable reviews.

        Zero when nothing is comparable, and note what that means: no reviews
        with a machine grade yet, NOT perfect agreement. `comparable` travels
        beside it so a reader can tell those apart.
        """
        if not self.comparable:
            return 0.0
        return self.diverged / self.comparable

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rate": round(self.rate, 4)}


async def override_rate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    job_id: uuid.UUID | str | None = None,
) -> OverrideRate:
    """Team Review verdicts that differed from the machine grade, over the
    verdicts that had a grade to differ from.

    A review on a candidate with no evaluation is NOT counted as agreement and
    NOT counted as divergence: it is excluded, because there was no machine
    opinion to deviate from. Counting it either way would move the rate with
    the pipeline's coverage rather than with anybody's judgment.

    Comparison uses `team_review.agrees_with_grade` and no second mapping.
    """
    clauses = ["tr.tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": str(tenant_id)}
    if job_id is not None:
        clauses.append("link.job_id = :job_id")
        params["job_id"] = str(job_id)

    rows = (
        await session.execute(
            text(
                f"""
                SELECT tr.rating AS verdict,
                       eval.aggregate_json->>'overall_grade' AS machine_grade
                FROM candidate_team_reviews tr
                JOIN job_candidate_links link
                     ON link.id = tr.job_candidate_link_id
                LEFT JOIN LATERAL (
                    SELECT e.aggregate_json
                    FROM evaluations e
                    WHERE e.link_id = link.id
                    ORDER BY e.created_at DESC, e.id DESC
                    LIMIT 1
                ) eval ON true
                WHERE {' AND '.join(clauses)}
                """
            ),
            params,
        )
    ).mappings().all()

    comparable = 0
    diverged = 0
    for row in rows:
        grade = row["machine_grade"]
        if not grade or grade not in rating.GRADES:
            continue
        verdict = row["verdict"]
        if verdict not in team_review.GRADES_FOR_VERDICT:
            continue
        comparable += 1
        if not team_review.agrees_with_grade(verdict, grade):
            diverged += 1
    return OverrideRate(comparable=comparable, diverged=diverged)


async def divergences(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    job_id: uuid.UUID | str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The Standards Board's queue: divergence records, newest first.

    Paginated in SQL. Carries the reviewer, the job, the candidate, the machine
    grade and the direction, and does NOT carry the reviewer's remark: the
    remark belongs to its author and is read on the Team Review panel, where
    the author's name is attached to it.
    """
    clauses = ["cr.tenant_id = :tenant_id", "cr.source = :source"]
    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "source": SOURCE_TEAM_REVIEW_DIVERGENCE,
        "limit": max(1, min(int(limit), 200)),
        "offset": max(0, int(offset)),
    }
    if job_id is not None:
        clauses.append("cr.job_id = :job_id")
        params["job_id"] = str(job_id)

    rows = (
        await session.execute(
            text(
                f"""
                SELECT cr.id,
                       cr.job_id,
                       job.title            AS job_title,
                       cr.predicted_grade,
                       cr.predicted_confidence,
                       cr.outcome_assessment,
                       cr.created_at,
                       tr.rating            AS verdict,
                       tr.reviewer_user_id,
                       reviewer.email       AS reviewer_email,
                       reviewer.role        AS reviewer_role,
                       link.id              AS link_id,
                       cand.id              AS candidate_id,
                       cand.full_name       AS candidate_name
                FROM calibration_records cr
                LEFT JOIN candidate_team_reviews tr ON tr.id = cr.team_review_id
                LEFT JOIN users reviewer ON reviewer.id = cr.recorded_by
                LEFT JOIN job_candidate_links link
                       ON link.id = tr.job_candidate_link_id
                LEFT JOIN candidates cand ON cand.id = link.candidate_id
                LEFT JOIN jobs job ON job.id = cr.job_id
                WHERE {' AND '.join(clauses)}
                ORDER BY cr.created_at DESC, cr.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


# ── The audited calibration view (spec-doc6 D8) ──────────────────────────────


def calibration_view(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Raw D1-D5 numbers, evaluator outputs and aggregation internals.

    THE ONLY PLACE IN THE PRODUCT THAT RETURNS THESE. D8: they are internal
    engine state, not a product surface, and they are exposed only through an
    authenticated calibration view restricted to Super Admin and HR Manager and
    always logged when viewed. The route enforces the first half; the caller
    writing `CALIBRATION_INTERNALS_VIEWED` enforces the second.

    Built by CONSTRUCTION from the evaluation's own internal projections rather
    than by filtering the dashboard payload, for the same reason
    `Aggregate.client_projection` is built that way and in the opposite
    direction: a projection assembled field by field cannot leak a field that
    was added elsewhere, and a projection assembled by removal will.
    """
    aggregate = dict(evaluation.get("aggregate_json") or {})
    dimension_scores = evaluation.get("dimension_scores") or {}
    return {
        "artifact": "calibration_internals",
        "evaluation_id": evaluation.get("id"),
        "scorecard_version": evaluation.get("scorecard_version"),
        "company_dna_version": evaluation.get("company_dna_version"),
        "situation_type": evaluation.get("situation_type"),
        "scoring_mode": evaluation.get("scoring_mode"),
        "dimensions": [
            {
                "dimension": key,
                "label": miti_dimensions.DIMENSION_LABELS[key],
                "band": (dimension_scores.get(key) or {}).get("band"),
                # THE RAW NUMBER. `band_for` is the representative internal
                # score for the band, which is what the aggregator actually
                # arithmetic'd over.
                "raw_score": _band_score(dimension_scores.get(key) or {}),
                "insufficient_evidence": bool(
                    (dimension_scores.get(key) or {}).get("insufficient_evidence")
                ),
                "evidence_refs": list(
                    (dimension_scores.get(key) or {}).get("evidence_refs") or []
                ),
            }
            for key in miti_dimensions.DIMENSIONS
        ],
        "competency_scores": dict(evaluation.get("competency_scores") or {}),
        "category_scores": dict(aggregate.get("category_scores") or {}),
        "raw_composite": aggregate.get("raw_composite"),
        "adjusted_composite": aggregate.get("adjusted_composite"),
        "authenticity_factor": aggregate.get("authenticity_factor"),
        "authenticity_reason": aggregate.get("authenticity_reason"),
        "must_have_cap_applied": bool(aggregate.get("must_have_cap_applied")),
        "confidence": aggregate.get("confidence") or evaluation.get("confidence"),
        "insufficient_dimensions": list(aggregate.get("insufficient_dimensions") or []),
        "review_reasons": list(aggregate.get("review_reasons") or []),
        "gate_results": list(evaluation.get("gate_results_json") or []),
        "triangulation": dict(evaluation.get("triangulation_json") or {}),
    }


def _band_score(entry: Mapping[str, Any]) -> int | None:
    band = entry.get("band")
    if band is None:
        return None
    try:
        return miti_dimensions.band_for(str(band))
    except ValueError:
        # An unknown band is reported as such rather than as a number. This
        # view exists for people auditing the engine, and a fabricated score
        # here would be a fabrication inside the audit itself.
        return None


async def log_calibration_view(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | str,
    actor_role: str | None,
    evaluation_id: uuid.UUID | str,
    job_id: uuid.UUID | str | None,
    link_id: uuid.UUID | str | None,
    candidate_id: uuid.UUID | str | None,
    exceptional: bool = False,
) -> None:
    """D8's "always logged when viewed", as a write that must succeed.

    `audit.record_action` raises on failure and the caller's transaction does
    not commit, which is the right direction here: a read of the engine's
    internals that left no trace is exactly the read the logging requirement
    exists to prevent.
    """
    await audit.record_action(
        session,
        action=CALIBRATION_INTERNALS_VIEWED,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        resource_type="evaluation",
        resource_id=evaluation_id,
        job_id=job_id,
        application_id=link_id,
        candidate_id=candidate_id,
        exceptional=exceptional,
    )


def sources_are_exhaustive(values: Sequence[str]) -> bool:
    """Every stored `source` is one this module knows about."""
    return set(values) <= set(CALIBRATION_SOURCES)
