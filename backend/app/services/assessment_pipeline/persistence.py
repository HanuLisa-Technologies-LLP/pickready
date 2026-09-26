"""Stage 4 of the assessment pipeline: the ONE writer of a report and its working.

    evidence (1)  ->  grading (2)  ->  composition (3)  ->  PERSISTENCE (4)

THE REPORT IS INSERT-ONLY, IN THREE PLACES
-------------------------------------------
1. `write_report` is an `INSERT ... ON CONFLICT DO NOTHING` on
   `uq_functional_report_link`. No row back means a report already exists,
   which the scoring task's exists-check under the advisory lock makes a
   defect signal, never an expected branch: it RAISES `ReportAlreadyWritten`.
2. Migration 0130's BEFORE UPDATE trigger refuses any UPDATE on
   `functional_skills_reports` and `report_dimensions`.
3. UPDATE is REVOKED from `pickready_app` on both.

`tests/test_report_insert_only.py` asserts by AST that this module issues no
UPDATE and no DELETE, and reads the trigger's refusal back from Postgres.

THE EVALUATION IS ONE LIVE ROW PER APPLICATION
------------------------------------------------
`evaluations` is the WORKING and is legitimately replaced by a rescore, so it
is an UPSERT onto the partial unique index `uq_evaluations_live_link`
(`superseded_at IS NULL`). A run that could not assess a skill writes the row
with `status="not_assessed"` and the attempt count, and NO report: the hourly
`pickready.release_held_assessments` sweep re-dispatches it, and the attempts
counter bounds how often (`miti_not_assessed_attempts`).

THE APPLICATION MOVES TO `assessment_completed` HERE. That stage promises a
report exists, so the report's writer is the one thing that may claim it; a
hand move is refused (`hiring_pipeline.SYSTEM_ONLY_TARGETS`).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import FunctionalSkillsReport, ReportDimension
from app.models.hiring import Evaluation
from app.services.assessment_pipeline.types import AssessmentInputs, ProvenanceRecorder

logger = logging.getLogger(__name__)

__all__ = [
    "EVALUATION_COMPLETE",
    "EVALUATION_NOT_ASSESSED",
    "ReportAlreadyWritten",
    "live_evaluation",
    "mark_assessment_completed",
    "report_exists",
    "upsert_evaluation",
    "write_report",
]

EVALUATION_COMPLETE = "complete"
EVALUATION_NOT_ASSESSED = "not_assessed"

#: `evaluations.scoring_mode` is CHECKed to full | degraded | stub (0059).
_EVALUATION_MODE_FULL = "full"
_EVALUATION_MODE_DEGRADED = "degraded"

_REPORT_COLUMNS = frozenset(column.name for column in FunctionalSkillsReport.__table__.columns)
_DIMENSION_COLUMNS = frozenset(column.name for column in ReportDimension.__table__.columns)


class ReportAlreadyWritten(RuntimeError):
    """The insert found a report for this application. A defect: the scoring
    task checks for one under the advisory lock before it spends anything."""


async def report_exists(session: AsyncSession, link_id: uuid.UUID) -> bool:
    """Whether this application already has its (immutable) report."""
    found = (
        await session.execute(
            select(func.count())
            .select_from(FunctionalSkillsReport)
            .where(FunctionalSkillsReport.job_candidate_link_id == link_id)
        )
    ).scalar_one()
    return bool(found)


async def live_evaluation(session: AsyncSession, link_id: uuid.UUID) -> Evaluation | None:
    """The application's one live evaluation row, or None."""
    return (
        await session.execute(
            select(Evaluation).where(
                Evaluation.link_id == link_id, Evaluation.superseded_at.is_(None)
            )
        )
    ).scalars().first()


async def write_report(
    session: AsyncSession,
    inputs: AssessmentInputs,
    *,
    fields: Mapping[str, Any],
    dimensions: Sequence[Mapping[str, Any]],
    provenance: ProvenanceRecorder,
) -> uuid.UUID:
    """INSERT the report and its rows. Never an UPDATE; see the module docstring.

    Provenance is read HERE, after every model call of the run returned, so
    `model_id` and `prompt_version` name exactly the calls whose output was
    accepted, and a report no model wrote carries NULL for both.
    """
    unknown = set(fields) - _REPORT_COLUMNS
    if unknown:
        raise ValueError(f"not report columns: {sorted(unknown)}")
    values = {
        **fields,
        "id": uuid.uuid4(),
        "tenant_id": inputs.job.tenant_id,
        "job_id": inputs.job.id,
        "job_candidate_link_id": inputs.link.id,
        "synthesized_at": datetime.now(timezone.utc),
        "model_id": provenance.model_id(),
        "prompt_version": provenance.prompt_version(),
        "generation_provenance_json": provenance.as_json(),
    }
    report_id = (
        await session.execute(
            pg_insert(FunctionalSkillsReport)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_functional_report_link")
            .returning(FunctionalSkillsReport.id)
        )
    ).scalar_one_or_none()
    if report_id is None:
        raise ReportAlreadyWritten(
            f"link {inputs.link.id} already has a report; reports are immutable "
            "and a second scoring run must have returned under the lock"
        )
    rows = [
        {
            **{key: value for key, value in row.items() if key in _DIMENSION_COLUMNS},
            "id": uuid.uuid4(),
            "tenant_id": inputs.job.tenant_id,
            "report_id": report_id,
        }
        for row in dimensions
    ]
    if rows:
        await session.execute(insert(ReportDimension), rows)
    return report_id


def _competency_scores(miti: Any) -> dict[str, Any]:
    """Per SKILL, from Miti's grades: words and statuses, the working only."""
    return {
        grade.name: {
            "bucket": grade.bucket,
            "status": grade.status,
            "grade": grade.grade,
            "partially_assessed": grade.partially_assessed,
            "methods": sorted({item.method for item in grade.items}),
        }
        for grade in miti.skills
    }


async def upsert_evaluation(
    session: AsyncSession,
    inputs: AssessmentInputs,
    miti: Any,
    *,
    status: str,
    attempts: int,
    report_id: uuid.UUID | None,
    needs_human_review: bool,
    note: str | None = None,
) -> uuid.UUID:
    """Write Miti's working onto the application's ONE live evaluation row.

    `INSERT ... ON CONFLICT (link_id) WHERE superseded_at IS NULL DO UPDATE`:
    a second run replaces the working in place, and no row is ever deleted
    (review dispositions keep their `evaluation_id`). The versions are COPIED,
    never joined: the contract may be revised after this was written.
    """
    from app.services.siddhi import synthesis as siddhi_synthesis

    outcome = miti.outcome
    aggregate = miti.aggregate
    payload = aggregate.as_dict() if aggregate is not None else {}
    if note:
        payload[siddhi_synthesis.READY_PICK_NOTE_KEY] = note
    values = {
        "tenant_id": inputs.job.tenant_id,
        "job_id": inputs.job.id,
        "link_id": inputs.link.id,
        "report_id": report_id,
        "scorecard_version": int(miti.contract_version),
        "contract_digest": miti.contract_digest,
        # No situation type exists on a contract, so none is claimed.
        "situation_type": None,
        "dimension_scores": (
            {result.dimension: result.as_dict() for result in outcome.results}
            if outcome is not None
            else {}
        ),
        "competency_scores": _competency_scores(miti),
        "aggregate_json": payload,
        "triangulation_json": (
            outcome.triangulation.as_dict()
            if outcome is not None and outcome.triangulation
            else {}
        ),
        "gate_results_json": (
            [gate.as_dict() for gate in outcome.gate_results] if outcome is not None else []
        ),
        "scoring_mode": (
            _EVALUATION_MODE_FULL if miti.complete else _EVALUATION_MODE_DEGRADED
        ),
        "confidence": aggregate.confidence if aggregate is not None else None,
        "needs_human_review": needs_human_review,
        "status": status,
        "attempts": attempts,
        "completed_at": datetime.now(timezone.utc),
    }
    statement = pg_insert(Evaluation).values(id=uuid.uuid4(), **values)
    statement = statement.on_conflict_do_update(
        index_elements=[Evaluation.link_id],
        index_where=text("superseded_at IS NULL"),
        set_={key: statement.excluded[key] for key in values if key != "link_id"},
    ).returning(Evaluation.id)
    return (await session.execute(statement)).scalar_one()


async def mark_assessment_completed(session: AsyncSession, inputs: AssessmentInputs) -> bool:
    """Move the application to `assessment_completed` now that its report exists.

    Only from `assessment_in_progress`: an application a recruiter has already
    moved on (on hold, rejected, shortlisted) keeps the stage a person chose.
    Written through `apply_transition`, the one chokepoint that writes the
    history, the mirror and the candidate's Updates feed together.
    """
    from app.services import hiring_pipeline

    current = (
        await session.execute(
            text("SELECT status FROM job_candidate_links WHERE id = :lid"),
            {"lid": str(inputs.link.id)},
        )
    ).scalar_one_or_none()
    if hiring_pipeline.normalize(current) != hiring_pipeline.ASSESSMENT_IN_PROGRESS:
        return False
    await hiring_pipeline.apply_transition(
        session,
        link_id=inputs.link.id,
        tenant_id=inputs.job.tenant_id,
        target=hiring_pipeline.ASSESSMENT_COMPLETED,
    )
    return True
