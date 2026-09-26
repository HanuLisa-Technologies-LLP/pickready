"""The grading ORCHESTRATOR, and the PRISM Report's read shape.

THE ONE-WAY PIPELINE (PLAN-p5 WP5-D, the Vivekium release)
-----------------------------------------------------------
This module used to be a 2,700 line LangGraph whose nodes scored, wrote
prose, ran the gate and UPDATEd the report in place. It is now ONLY the
orchestrator, and every step is a stage module under
`services/assessment_pipeline`:

    1 evidence.load_inputs        what the grading and the report are written from
    2 grading.grade               MITI: every per-skill grade and the overall
    3 composition.compose         SIDDHI: remarks, citations, gaps, the gate
    4 persistence.write_report    insert-only report + rows, evaluation upsert

A stage imports the stages before it and never a later one, and none imports
this module. `tests/test_assessment_pipeline_direction.py` walks module-level
AND function-level imports, so a lazy import cannot hide a back edge.

`run_assessment` keeps its name because `pickready.run_functional_assessment`
and `locks.SCORING = "functional_assessment.scoring"` are persisted names.

THE BOUNDED RETRY OF A NOT-ASSESSED RUN (P5-D4)
------------------------------------------------
A skill Miti could not assess (every substantive answer's evaluation failed,
a coding result the sandbox never produced) is a fact about the PLATFORM, not
the candidate. Before the final attempt the run writes NO report: the live
evaluation row records `not_assessed` and the attempt count, the task
returns, and the hourly `release_held_assessments` sweep re-dispatches it. On
attempt `settings.miti_not_assessed_attempts` the report IS written, those
skills stated "Not assessed", the report routed to a person, and
`miti.not_assessed_final_report` logged at ERROR, which a CloudWatch alarm
watches. A report is permanent, so a two-minute outage must not become a
permanent "Not assessed"; the bound stops a permanent state from re-paying
the other skills' evaluations every hour for ever.

THE READ SHAPE
--------------
The category names, the radar builder and `rating_label` below are read by
the report route and the PDF. They are the report's SHAPE, not grading, and
none of them imports a stage. They move to the report read model with WP5-F.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentConversation
from app.models.candidate import JobCandidateLink
from app.models.job import Job
from app.services import cost_telemetry, ppi, ppi_interview
from app.services.assessment_pipeline import composition, grading, persistence
from app.services.assessment_pipeline import evidence as stage_evidence
from app.services.assessment_pipeline.types import (
    MODE_MITI,
    RUN_NOT_ASSESSED,
    RUN_WRITTEN,
    ProvenanceRecorder,
)
from app.services.assessment_questions import budget as question_budget
from app.services.miti.items import UNANSWERED_SCORE
from app.services.rating import GRADES, band_index_for, grade_for_percent
from app.services.siddhi import remarks as siddhi_remarks

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORY_MATCHING",
    "CATEGORY_TECHNICAL",
    "GRADES",
    "MODE_MITI",
    "PPI_REMARK_WORDS",
    "PROBE_REMARK_WORDS",
    "RADAR_BANDS",
    "RADAR_SERIES",
    "REPORT_CATEGORIES",
    "RunResult",
    "UNANSWERED_SCORE",
    "band_index_for",
    "build_radar_charts",
    "infer_grade_fallback",
    "rating_label",
    "run_assessment",
    "word_count",
]

#: The four grades. How many questions a grade is asked is
#: `assessment_questions.budget`.
GRADE_NAMES: tuple[str, ...] = question_budget.GRADES

#: LEGACY. Reports written before the Vivekium release carry AI Score rows
#: under this category (four matching parameters, 25 to 30 word remarks). The
#: AI Score is now Yukti's frozen snapshot on `ai_score_json`; nothing writes
#: this category any more, and it is read so a historic report still renders.
CATEGORY_MATCHING = "matching"

#: LEGACY. Reports written before Draft v4 carry rows scored against the
#: standalone technical bank that no longer exists; read, never written.
CATEGORY_TECHNICAL = "technical"

#: Report section order. The two legacy categories trail it because only a
#: historic report carries them.
REPORT_CATEGORIES: tuple[str, ...] = (
    CATEGORY_MATCHING,
    ppi.CATEGORY_MUST_HAVE,
    ppi.CATEGORY_NICE_TO_HAVE,
    ppi.CATEGORY_BEHAVIOURAL,
    CATEGORY_TECHNICAL,
)

#: Word contracts, owned by Siddhi's writer and re-exported for the readers
#: that still import them from here.
PPI_REMARK_WORDS = siddhi_remarks.SKILL_REMARK_WORDS
PROBE_REMARK_WORDS = siddhi_remarks.PROBE_REMARK_WORDS

#: Ordered best-to-worst grade labels, for the radar legend and colour ramp.
RADAR_BANDS: tuple[str, ...] = GRADES


def rating_label(score: int | float | None) -> str | None:
    """The client-facing grade for an internal 0-100 score.

    Thin alias over `services.rating.grade_for_percent`: the scale lives in
    one module so the assessment and the ranking cannot drift apart.
    """
    return grade_for_percent(score)


def word_count(value: str) -> int:
    return siddhi_remarks.word_count(value)


# ── Radar charts (spec 9.4) ──────────────────────────────────────────────────
# Each chart plots TWO shapes on the same axes (what the job requires, what
# the candidate demonstrated). No number appears on an axis, a data label or a
# tooltip: `*_index` is a RENDERING COORDINATE (1..4), because a radar has no
# geometry without a radius and the four grades ARE the radial axis.

RADAR_CHART_KEYS: tuple[str, ...] = ("overall", *ppi.CATEGORIES)

RADAR_CHART_TITLES: dict[str, str] = {
    "overall": "Overall",
    **{category: ppi.CATEGORY_LABELS[category] for category in ppi.CATEGORIES},
}

#: The legend below every chart, by word only (spec 9.4).
RADAR_SERIES: tuple[str, ...] = ("Job Requirement", "Candidate Assessment")

#: One Overall spoke per aspect, both shapes derived from the same rows the
#: sections render, so a chart can never disagree with the text beside it.
OVERALL_AXES: tuple[tuple[str, str], ...] = tuple(
    (category, ppi.CATEGORY_LABELS[category]) for category in ppi.CATEGORIES
)


def _mean(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else UNANSWERED_SCORE


def _axis(name: str, candidate_score: int, required: int | None) -> dict[str, Any]:
    candidate_band = grade_for_percent(candidate_score) or GRADES[-1]
    requirement_band = grade_for_percent(
        required if required is not None else ppi.DEFAULT_REQUIRED_LEVEL
    ) or GRADES[-1]
    return {
        "axis": name,
        "requirement_band": requirement_band,
        "requirement_index": band_index_for(requirement_band),
        "candidate_band": candidate_band,
        "candidate_index": band_index_for(candidate_band),
    }


def build_radar_charts(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The radar charts, built from the SAME dimension rows the sections render.

    A row with no score (a skill stated "Not assessed", 0130) has NO axis: a
    candidate shape drawn for it would plot a grade nobody made, and drawing
    it at the bottom band would state Not Matching. Pure; unit-tested.
    """
    scored = [row for row in dimensions if row.get("score") is not None]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in scored:
        by_category.setdefault(row["category"], []).append(row)

    charts: list[dict[str, Any]] = []
    overall_axes = []
    for category, label in OVERALL_AXES:
        rows = by_category.get(category) or []
        if not rows:
            continue
        overall_axes.append(
            _axis(
                label,
                _mean([row["score"] for row in rows]),
                _mean([row["required_level"] for row in rows if row.get("required_level")])
                if any(row.get("required_level") for row in rows)
                else None,
            )
        )
    charts.append({"key": "overall", "title": RADAR_CHART_TITLES["overall"], "axes": overall_axes})

    for category in ppi.CATEGORIES:
        rows = sorted(by_category.get(category) or [], key=lambda item: item["ordinal"])
        charts.append(
            {
                "key": category,
                "title": RADAR_CHART_TITLES[category],
                "axes": [
                    _axis(row["name"], row["score"], row.get("required_level")) for row in rows
                ],
            }
        )
    return charts


def infer_grade_fallback(job: Job) -> str:
    """Keyword grade inference. Mirrored exactly by migration 0014's SQL CASE."""
    title = f"{job.title} {job.level or ''}".lower()
    if any(term in title for term in ("chief", "cxo", "ceo", "cto", "cfo", "coo")):
        return "cxo"
    if any(term in title for term in ("director", "head", "vice president", "vp", "leader")):
        return "leadership"
    if any(term in title for term in ("manager", "lead", "supervisor")):
        return "managerial"
    return "non_managerial"


# ── The orchestrator ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunResult:
    """What one scoring run did. `report_id` is set exactly when it wrote one."""

    status: str
    attempts: int
    report_id: uuid.UUID | None = None


_NOTE_PATH = ("siddhi", "ready_pick_note", "sentence")


def _note(gap_analysis_json: dict[str, Any]) -> str | None:
    value: Any = gap_analysis_json
    for key in _NOTE_PATH:
        value = value.get(key) if isinstance(value, dict) else None
    return str(value) if value else None


async def run_assessment(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> RunResult:
    """Grade one application with Miti and write its PRISM Report. One way.

    The caller (the scoring task) holds the advisory lock and has already
    checked that no report exists, so a report found by the insert is a
    defect and raises. The caller commits.

    * **No conversation, no contract, no report.** Gate G1 reads the snapshot
      the conversation is bound to, so `ScorecardUnavailable`.
    * **No questions is a refusal, never a generation**: a completed
      conversation always has its questions, so none is a defect to surface.
    """
    from app.services.miti import live as miti_live  # noqa: PLC0415

    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()
    if conversation is None:
        raise miti_live.ScorecardUnavailable(
            f"no assessment conversation exists for link {link.id}, so there is "
            "no locked contract to grade against"
        )
    questions = await ppi_interview.load_for_link(session, link.id)
    if not questions:
        raise miti_live.ScorecardUnavailable(
            f"link {link.id} has an assessment conversation and no issued "
            "questions, so there is nothing the candidate can be graded on"
        )
    grade = job.assessment_grade if job.assessment_grade in GRADE_NAMES else infer_grade_fallback(job)

    live = await persistence.live_evaluation(session, link.id)
    previous = (
        live.attempts
        if live is not None and live.status == persistence.EVALUATION_NOT_ASSESSED
        else 0
    )
    attempt = previous + 1
    final_attempt = attempt >= get_settings().miti_not_assessed_attempts

    provenance = ProvenanceRecorder()
    # EVERYTHING THE RUN SPENDS IS BILLED TO THIS APPLICATION, and the scope
    # wraps every stage so a run that dies part way still reports its spend.
    async with cost_telemetry.record_for(
        session, tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
    ):
        inputs = await stage_evidence.load_inputs(
            session,
            job=job,
            link=link,
            conversation=conversation,
            questions=questions,
            grade=grade,
        )
        miti = await grading.grade(
            session, inputs, allow_incomplete=final_attempt, provenance=provenance
        )
        if not miti.complete and not final_attempt:
            await persistence.upsert_evaluation(
                session,
                inputs,
                miti,
                status=persistence.EVALUATION_NOT_ASSESSED,
                attempts=attempt,
                report_id=None,
                needs_human_review=True,
            )
            logger.warning(
                "functional_assessment.not_assessed_retry link_id=%s attempt=%d of=%d skills=%d",
                link.id, attempt, get_settings().miti_not_assessed_attempts,
                len(miti.not_assessed_skills),
            )
            return RunResult(status=RUN_NOT_ASSESSED, attempts=attempt)
        if miti.aggregate is None:
            raise miti_live.ScorecardUnavailable(
                f"Miti produced no aggregate for link {link.id}: "
                f"{'; '.join(miti.outcome.blocking_reasons) if miti.outcome else 'no outcome'}"
            )
        if not miti.complete:
            # THE ALARM. A CloudWatch metric filter counts this token
            # (`infra/modules/observability`, `miti_not_assessed_final`).
            logger.error(
                "miti.not_assessed_final_report link_id=%s attempts=%d skills=%d",
                link.id, attempt, len(miti.not_assessed_skills),
            )
        uncertainty = await grading.evidence_uncertainty(session, inputs)
        composed = await composition.compose(
            session, inputs, miti, provenance=provenance, uncertainty=uncertainty
        )
        report_id = await persistence.write_report(
            session,
            inputs,
            fields=composed.fields,
            dimensions=composed.dimensions,
            provenance=provenance,
        )
        await persistence.upsert_evaluation(
            session,
            inputs,
            miti,
            status=persistence.EVALUATION_COMPLETE,
            attempts=attempt,
            report_id=report_id,
            needs_human_review=bool(composed.fields["needs_human_review"]),
            note=_note(composed.fields["gap_analysis_json"]),
        )
        await persistence.mark_assessment_completed(session, inputs)
    logger.info(
        "functional_assessment.report_written link_id=%s report_id=%s mode=%s attempts=%d",
        link.id, report_id, composed.fields["scoring_mode"], attempt,
    )
    return RunResult(status=RUN_WRITTEN, attempts=attempt, report_id=report_id)
