"""Job setup review, the unified conversation, and the PPI Assessment Report."""
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_tenant_db,
    require_capability,
)
from app.models.assessment import (
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
    TechnicalQuestion,
)
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.schemas.assessments import (
    CompetencyIn,
    CompetencyOut,
    ConversationMessageIn,
    ConversationOut,
    DimensionOut,
    FrameworkOut,
    FunctionalReportOut,
    JobSetupOut,
    QuestionBankOut,
    RadarChartOut,
    TechnicalQuestionIn,
    TechnicalQuestionOut,
)
from app.services import capabilities as caps
from app.services import credit_reconciliation, hiring_pipeline, interviewer, ppi
from app.services.audit import audit
from app.services.functional_assessment import (
    CATEGORY_MATCHING,
    CATEGORY_TECHNICAL,
    RADAR_BANDS,
    RADAR_SERIES,
    build_radar_charts,
    rating_label,
)
from app.services.rating import GRADES, grade_for_percent
from app.workers.celery_app import celery_app

router = APIRouter()

READY_FOR_CANDIDATES = "ready_for_candidates"
PENDING_REVIEW = "questions_pending_review"


async def _staff_job(session: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _refresh_setup_status(session: AsyncSession, job: Job) -> None:
    """A job is open to candidates once the PPI FRAMEWORK is approved.

    CHANGED 2026-08-04, client decision: the technical question bank no longer
    gates anything. It used to be the other half of this condition, and a job
    stayed at `questions_pending_review` until a recruiter pressed Finalize on
    it. That step is removed -- generated questions are usable immediately.

    The FRAMEWORK review is deliberately KEPT, and the two are not the same
    thing. The framework is the fixed evaluation criteria every candidate on
    this job is graded against, it is frozen once anyone has been assessed, and
    a report states a grade against those exact criteria. A human confirming it
    is the product's comparability guarantee. The technical bank carries no such
    promise: each question is scored against its own rubric, and a weak question
    costs one item on one report rather than making two reports incomparable.

    `questions_approved_at` is left on the model and still stamped by the
    finalize route, which survives so an existing client and the historic rows
    that carry a timestamp both keep working. It simply no longer decides
    anything -- deliberately not dropped in the same change that stops reading
    it, so a rollback needs no data restore.
    """
    target = READY_FOR_CANDIDATES if job.framework_approved_at is not None else PENDING_REVIEW
    if job.assessment_status != target:
        job.assessment_status = target
    await session.flush()


def _setup_out(job: Job) -> JobSetupOut:
    return JobSetupOut(
        job_id=job.id,
        status=job.assessment_status,
        grade=job.assessment_grade,
        questions_approved=job.questions_approved_at is not None,
        framework_approved=job.framework_approved_at is not None,
        ready_for_candidates=job.assessment_status == READY_FOR_CANDIDATES,
        generated_at=job.questions_generated_at or job.framework_generated_at,
        approved_at=(
            max(job.questions_approved_at, job.framework_approved_at)
            if job.questions_approved_at and job.framework_approved_at
            else None
        ),
    )


@router.get("/jobs/{job_id}/setup", response_model=JobSetupOut)
async def job_setup(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobSetupOut:
    return _setup_out(await _staff_job(session, user, job_id))


# ── Technical question bank ──────────────────────────────────────────────────


def _question_out(row: TechnicalQuestion) -> TechnicalQuestionOut:
    return TechnicalQuestionOut(
        id=row.id,
        ordinal=row.ordinal,
        skill=row.skill,
        prompt=row.prompt,
        rubric=row.rubric_json,
        is_active=row.is_active,
    )


def _bank_out(job: Job, rows: list[TechnicalQuestion]) -> QuestionBankOut:
    return QuestionBankOut(
        job_id=job.id,
        status=job.assessment_status,
        grade=job.assessment_grade,
        questions=[_question_out(row) for row in rows],
        approved=job.questions_approved_at is not None,
    )


@router.get("/jobs/{job_id}/questions", response_model=QuestionBankOut)
async def question_bank(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> QuestionBankOut:
    job = await _staff_job(session, user, job_id)
    rows = (
        await session.execute(
            select(TechnicalQuestion).where(TechnicalQuestion.job_id == job.id).order_by(TechnicalQuestion.ordinal)
        )
    ).scalars().all()
    return _bank_out(job, list(rows))


@router.post("/jobs/{job_id}/questions", response_model=TechnicalQuestionOut, status_code=status.HTTP_201_CREATED)
async def add_question(
    job_id: uuid.UUID,
    body: TechnicalQuestionIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TechnicalQuestionOut:
    job = await _staff_job(session, user, job_id)
    ordinal = (
        await session.execute(select(func.coalesce(func.max(TechnicalQuestion.ordinal), 0)).where(TechnicalQuestion.job_id == job.id))
    ).scalar_one() + 1
    row = TechnicalQuestion(tenant_id=job.tenant_id, job_id=job.id, ordinal=ordinal, skill=body.skill, prompt=body.prompt, rubric_json=body.rubric)
    session.add(row)
    await session.flush()
    return _question_out(row)


@router.put("/jobs/{job_id}/questions/{question_id}", response_model=TechnicalQuestionOut)
async def update_question(
    job_id: uuid.UUID,
    question_id: uuid.UUID,
    body: TechnicalQuestionIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TechnicalQuestionOut:
    job = await _staff_job(session, user, job_id)
    row = await session.get(TechnicalQuestion, question_id)
    if row is None or row.job_id != job.id:
        raise HTTPException(status_code=404, detail="Question not found")
    row.skill, row.prompt, row.rubric_json = body.skill, body.prompt, body.rubric
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return _question_out(row)


@router.delete("/jobs/{job_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_question(
    job_id: uuid.UUID,
    question_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    job = await _staff_job(session, user, job_id)
    row = await session.get(TechnicalQuestion, question_id)
    if row is None or row.job_id != job.id:
        raise HTTPException(status_code=404, detail="Question not found")
    row.is_active = False
    await session.flush()


@router.post("/jobs/{job_id}/finalize", response_model=QuestionBankOut)
async def finalize_questions(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> QuestionBankOut:
    """Approve the technical bank. Half of the manual step (spec §5, §11)."""
    job = await _staff_job(session, user, job_id)
    rows = (
        await session.execute(
            select(TechnicalQuestion).where(TechnicalQuestion.job_id == job.id, TechnicalQuestion.is_active.is_(True)).order_by(TechnicalQuestion.ordinal)
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=422, detail="At least one active technical question is required")
    job.questions_approved_at = datetime.now(timezone.utc)
    await _refresh_setup_status(session, job)
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id, action="technical_questions_finalized", target_type="job", target_id=job.id, metadata={"question_count": len(rows)})
    return _bank_out(job, list(rows))


# ── The PPI framework (spec §6.2, §6.3) ──────────────────────────────────────


def _competency_out(row: JobCompetency) -> CompetencyOut:
    return CompetencyOut(
        id=row.id,
        category=row.category,
        name=row.name,
        description=row.description,
        # A number never crosses this boundary, not even the job's own
        # requirement level: the client reads and writes the same four words.
        required_level=grade_for_percent(row.required_level) or GRADES[1],
        ordinal=row.ordinal,
    )


async def _framework_out(session: AsyncSession, job: Job) -> FrameworkOut:
    rows = await ppi.load_framework(session, job.id)
    ok, reason = ppi.framework_is_complete(rows)
    return FrameworkOut(
        job_id=job.id,
        status=job.assessment_status,
        approved=job.framework_approved_at is not None,
        competencies=[_competency_out(row) for row in rows],
        minimum_per_category=ppi.MINIMUM_PER_CATEGORY,
        blocking_reason=None if ok else reason,
    )


@router.get("/jobs/{job_id}/framework", response_model=FrameworkOut)
async def get_framework(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FrameworkOut:
    job = await _staff_job(session, user, job_id)
    return await _framework_out(session, job)


def _reject_culture(name: str) -> None:
    if ppi.is_forbidden_competency(name):
        raise HTTPException(status_code=422, detail=ppi.FORBIDDEN_COMPETENCY_DETAIL)


def _reject_frozen(job: Job) -> None:
    """A saved framework is the job's fixed evaluation criteria (spec §6.3).

    Editing it after candidates have been graded against it would make two
    reports on the same job incomparable, which is the one property the
    framework exists to guarantee. Reopening is a deliberate act: unfinalize,
    which is refused once any candidate has been assessed.
    """
    if job.framework_approved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This framework has been saved and is now the fixed evaluation "
                "criteria for the job. Reopen it for editing before making changes."
            ),
        )


@router.post("/jobs/{job_id}/framework", response_model=CompetencyOut, status_code=status.HTTP_201_CREATED)
async def add_competency(
    job_id: uuid.UUID,
    body: CompetencyIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompetencyOut:
    job = await _staff_job(session, user, job_id)
    _reject_frozen(job)
    if body.category == ppi.CATEGORY_BEHAVIOURAL:
        _reject_culture(body.name)
    ordinal = (
        await session.execute(
            select(func.coalesce(func.max(JobCompetency.ordinal), 0)).where(
                JobCompetency.job_id == job.id, JobCompetency.category == body.category
            )
        )
    ).scalar_one() + 1
    row = JobCompetency(
        tenant_id=job.tenant_id,
        job_id=job.id,
        category=body.category,
        name=body.name,
        description=body.description,
        required_level=ppi.required_level_score(body.required_level),
        ordinal=ordinal,
    )
    session.add(row)
    await session.flush()
    return _competency_out(row)


@router.put("/jobs/{job_id}/framework/{competency_id}", response_model=CompetencyOut)
async def update_competency(
    job_id: uuid.UUID,
    competency_id: uuid.UUID,
    body: CompetencyIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompetencyOut:
    job = await _staff_job(session, user, job_id)
    _reject_frozen(job)
    row = await session.get(JobCompetency, competency_id)
    if row is None or row.job_id != job.id:
        raise HTTPException(status_code=404, detail="Competency not found")
    if body.category == ppi.CATEGORY_BEHAVIOURAL:
        _reject_culture(body.name)
    row.category = body.category
    row.name = body.name
    row.description = body.description
    row.required_level = ppi.required_level_score(body.required_level)
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return _competency_out(row)


@router.delete("/jobs/{job_id}/framework/{competency_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_competency(
    job_id: uuid.UUID,
    competency_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    job = await _staff_job(session, user, job_id)
    _reject_frozen(job)
    row = await session.get(JobCompetency, competency_id)
    if row is None or row.job_id != job.id:
        raise HTTPException(status_code=404, detail="Competency not found")
    # Soft delete: a generated candidate question may already reference it.
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()


@router.post("/jobs/{job_id}/framework/finalize", response_model=FrameworkOut)
async def finalize_framework(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FrameworkOut:
    """Save the framework as the job's fixed evaluation criteria (spec §6.3)."""
    job = await _staff_job(session, user, job_id)
    rows = await ppi.load_framework(session, job.id)
    ok, reason = ppi.framework_is_complete(rows)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)
    job.framework_approved_at = datetime.now(timezone.utc)
    await _refresh_setup_status(session, job)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="ppi_framework_finalized",
        target_type="job",
        target_id=job.id,
        metadata={
            "counts": {
                category: sum(1 for row in rows if row.category == category)
                for category in ppi.CATEGORIES
            }
        },
    )
    return await _framework_out(session, job)


@router.post("/jobs/{job_id}/framework/reopen", response_model=FrameworkOut)
async def reopen_framework(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FrameworkOut:
    """Reopen a saved framework for editing, and close the job to new
    conversations while it is open.

    Refused once any candidate has been graded against it: those reports state
    a grade against criteria that would silently change underneath them, and a
    report is immutable.
    """
    job = await _staff_job(session, user, job_id)
    assessed = (
        await session.execute(
            select(func.count())
            .select_from(FunctionalSkillsReport)
            .where(FunctionalSkillsReport.job_id == job.id)
        )
    ).scalar_one()
    if assessed:
        raise HTTPException(
            status_code=409,
            detail=(
                "Candidates have already been assessed against this framework, so "
                "it can no longer be changed. Reports state a grade against these "
                "exact criteria and are never rewritten."
            ),
        )
    job.framework_approved_at = None
    await _refresh_setup_status(session, job)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="ppi_framework_reopened",
        target_type="job",
        target_id=job.id,
    )
    return await _framework_out(session, job)


# ── The PPI Assessment Report (spec §10) ─────────────────────────────────────


@router.get("/reports/links/{link_id}", response_model=FunctionalReportOut)
async def get_report(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FunctionalReportOut:
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Report not found")
    report = (
        await session.execute(select(FunctionalSkillsReport).where(FunctionalSkillsReport.job_candidate_link_id == link.id))
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="The PPI Assessment Report is not ready")
    rows = (
        await session.execute(
            select(ReportDimension)
            .where(ReportDimension.report_id == report.id)
            .order_by(ReportDimension.ordinal)
        )
    ).scalars().all()

    def _out(row: ReportDimension) -> DimensionOut:
        return DimensionOut(
            name=row.name,
            description=row.description,
            grade=rating_label(row.score) or GRADES[-1],
            required_level=grade_for_percent(row.required_level),
            remark=row.remark,
        )

    grouped: dict[str, list[DimensionOut]] = {}
    for row in rows:
        grouped.setdefault(row.category, []).append(_out(row))

    # Charts are built from the SAME rows the sections render, so a chart can
    # never disagree with the text beside it.
    charts = build_radar_charts(
        [
            {
                "category": row.category,
                "name": row.name,
                "score": row.score,
                "required_level": row.required_level,
                "ordinal": row.ordinal,
            }
            for row in rows
        ]
    )

    overall = report.overall_score
    if overall is None:
        # Written before migration 0030. Recompute rather than showing nothing.
        assessed = [row.score for row in rows if row.category != CATEGORY_MATCHING]
        overall = round(sum(assessed) / len(assessed)) if assessed else 0

    return FunctionalReportOut(
        id=report.id,
        job_candidate_link_id=link.id,
        grade=report.grade,
        ai_score=grouped.get(CATEGORY_MATCHING, []),
        overall_grade=grade_for_percent(overall) or GRADES[-1],
        overall_summary=report.overall_summary,
        primary_skills=grouped.get(ppi.CATEGORY_PRIMARY, []),
        secondary_skills=grouped.get(ppi.CATEGORY_SECONDARY, []),
        behavioural=grouped.get(ppi.CATEGORY_BEHAVIOURAL, []),
        technical=grouped.get(CATEGORY_TECHNICAL, []),
        validation=report.validation_json,
        suggested_interview_questions=report.suggested_probes_json,
        radar_charts=[RadarChartOut(**chart) for chart in charts],
        radar_bands=list(RADAR_BANDS),
        radar_series=list(RADAR_SERIES),
        synthesized_at=report.synthesized_at,
        immutable=True,
    )


# ── Report immutability ──────────────────────────────────────────────────────
# A generated report is a permanent record: it is what a hiring decision was
# made from, and a retake produces a NEW report ALONGSIDE the old one rather
# than overwriting it. There is no edit or delete affordance in the UI, and
# these handlers make the backend say so explicitly.
#
# Declaring them is deliberate. Without a registered handler, FastAPI answers a
# DELETE on this path with 405 Method Not Allowed -- which reads as "the route
# doesn't do that yet" rather than "this is forbidden by design", and would
# leave a future edit free to add one without anyone noticing the rule.

_IMMUTABLE_DETAIL = (
    "PPI Assessment Reports are immutable. A report records the assessment a "
    "hiring decision was made from, so it is never edited or deleted; a retake "
    "after six months generates a new report alongside this one."
)


@router.patch("/reports/links/{link_id}", status_code=status.HTTP_403_FORBIDDEN)
async def patch_report_forbidden(link_id: uuid.UUID) -> None:
    raise HTTPException(status_code=403, detail=_IMMUTABLE_DETAIL)


@router.put("/reports/links/{link_id}", status_code=status.HTTP_403_FORBIDDEN)
async def put_report_forbidden(link_id: uuid.UUID) -> None:
    raise HTTPException(status_code=403, detail=_IMMUTABLE_DETAIL)


@router.delete("/reports/links/{link_id}", status_code=status.HTTP_403_FORBIDDEN)
async def delete_report_forbidden(link_id: uuid.UUID) -> None:
    raise HTTPException(status_code=403, detail=_IMMUTABLE_DETAIL)


# ── The unified candidate conversation (spec §8) ─────────────────────────────


async def _candidate_link(session: AsyncSession, user: CurrentUser, link_id: uuid.UUID) -> tuple[JobCandidateLink, Job]:
    candidate = (
        await session.execute(select(Candidate).where(Candidate.user_id == user.user_id))
    ).scalars().first()
    link = await session.get(JobCandidateLink, link_id)
    if candidate is None or link is None or link.candidate_id != candidate.id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # The review gate (spec §5): no candidate enters the conversation until the
    # recruiter has finalised BOTH the technical bank and the PPI framework.
    if job.assessment_status != READY_FOR_CANDIDATES:
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment is not open yet. The hiring team is still "
                "reviewing the questions for this role, and you will be emailed "
                "as soon as it is ready."
            ),
        )
    return link, job


# Short conversational connectors so the blended sequence reads as ONE
# conversation rather than two interleaved engines (spec §8). Cycled by
# position; the first prompt is always delivered bare.
_CONNECTORS: tuple[str, ...] = (
    "Thanks for that.",
    "Good, moving on.",
    "That's helpful.",
    "Understood.",
    "Noted, thank you.",
    "Great.",
    "Appreciate the detail.",
    "Right, next one.",
)


async def _conversation_prompts(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> list[tuple[str, str, str]]:
    """The single blended sequence: the job's technical bank + this candidate's
    PPI questions, round-robin interleaved and conversationally connected.

    The candidate never sees or interacts with two bots and is never told which
    engine scores which answer (spec §8).

    `question_key` carries the TechnicalQuestion id and the JobCompetency id
    respectively -- the two scorers key on exactly these.
    """
    technical = (
        await session.execute(
            select(TechnicalQuestion)
            .where(TechnicalQuestion.job_id == job.id, TechnicalQuestion.is_active.is_(True))
            .order_by(TechnicalQuestion.ordinal)
        )
    ).scalars().all()
    candidate_questions = (
        await session.execute(
            select(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).scalars().all()

    tech = [("technical", str(row.id), row.prompt) for row in technical]
    ppi_prompts = [("ppi", str(row.competency_id), row.prompt) for row in candidate_questions]

    blended: list[tuple[str, str, str]] = []
    longest = max(len(tech), len(ppi_prompts), 0)
    for index in range(longest):
        for group in (tech, ppi_prompts):
            if index < len(group):
                blended.append(group[index])

    def _connect(position: int, prompt: str) -> str:
        # The first prompt, and any prompt that already opens with its own
        # discourse marker, are delivered bare -- stacking two reads oddly.
        if position == 0 or prompt.split(" ", 1)[0].rstrip(",") in ("Finally", "To", "Before", "Right", "And"):
            return prompt
        return f"{_CONNECTORS[position % len(_CONNECTORS)]} {prompt}"

    return [
        (domain, key, _connect(position, prompt))
        for position, (domain, key, prompt) in enumerate(blended)
    ]


async def _ensure_conversation_ready(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> None:
    """Transient prep, never a human gate.

    The job has already passed the recruiter's review by the time this runs; if
    the technical bank or this candidate's PPI questions are somehow missing,
    generation is enqueued (Celery, never inline -- claude.md rule 4) and the
    candidate is asked to retry in a moment.
    """
    has_technical = (
        await session.execute(
            select(func.count())
            .select_from(TechnicalQuestion)
            .where(TechnicalQuestion.job_id == job.id, TechnicalQuestion.is_active.is_(True))
        )
    ).scalar_one()
    has_ppi = (
        await session.execute(
            select(func.count())
            .select_from(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
        )
    ).scalar_one()
    if has_technical and has_ppi:
        return
    if not has_technical:
        celery_app.send_task("pickready.generate_technical_questions", args=[str(job.id)])
    if not has_ppi:
        celery_app.send_task("pickready.generate_candidate_questions", args=[str(link.id)])
    raise HTTPException(
        status_code=409,
        detail="We are preparing your assessment. Please try again in a moment.",
    )


@router.post("/conversations/links/{link_id}/start", response_model=ConversationOut)
async def start_conversation(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ConversationOut:
    link, job = await _candidate_link(session, user, link_id)
    conversation = (
        await session.execute(select(AssessmentConversation).where(AssessmentConversation.job_candidate_link_id == link.id))
    ).scalars().first()
    # ── The invitation gate ──────────────────────────────────────────────────
    # Not every applicant is assessed. The conversation row IS the invitation:
    # a recruiter creates it via POST /pipeline/jobs/{id}/select-candidates, and
    # only then may the candidate open the questions. Checked BEFORE any
    # generation is enqueued, so an uninvited candidate cannot make the product
    # do work by hitting the URL.
    if conversation is None or conversation.invitation_sent_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This assessment is not open to you yet. The hiring team invites "
                "candidates individually, and you will be emailed if they invite you."
            ),
        )
    await _ensure_conversation_ready(session, job, link)
    # First open of an invited assessment moves it along the pipeline.
    if conversation.started_at is None:
        conversation.started_at = datetime.now(timezone.utc)
        await session.flush()
        if hiring_pipeline.can_transition(
            link.status, hiring_pipeline.ASSESSMENT_IN_PROGRESS
        ):
            await hiring_pipeline.apply_transition(
                session,
                link_id=link.id,
                tenant_id=link.tenant_id,
                target=hiring_pipeline.ASSESSMENT_IN_PROGRESS,
            )
    prompts = await _conversation_prompts(session, job, link)
    index = min(conversation.next_question_index, len(prompts))
    return ConversationOut(
        conversation_id=conversation.id,
        status=conversation.status,
        prompt=prompts[index][2] if index < len(prompts) else None,
        progress_label=f"Question {index + 1} of {len(prompts)}" if index < len(prompts) else "Conversation complete",
    )



async def _transcript_rows(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The conversation so far, oldest first.

    This is the agent's MEMORY. Without it every turn would be judged on the
    single answer in front of it, which is how the interview came to repeat
    ground the candidate had already covered.

    Read back from `assessment_messages` rather than accumulated in memory
    because `respond` is one stateless HTTP request per turn -- there is no
    process holding the conversation between them.
    """
    rows = (
        await session.execute(
            select(AssessmentMessage.speaker, AssessmentMessage.content)
            .where(AssessmentMessage.conversation_id == conversation_id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).all()
    return [{"speaker": speaker, "content": content} for speaker, content in rows]


@router.post("/conversations/{conversation_id}/respond", response_model=ConversationOut)
async def respond(
    conversation_id: uuid.UUID,
    body: ConversationMessageIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ConversationOut:
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    link, job = await _candidate_link(session, user, conversation.job_candidate_link_id)
    prompts = await _conversation_prompts(session, job, link)
    index = conversation.next_question_index
    pending = conversation.pending_prompt
    # A pending follow-up means the candidate is answering the PROBE, not the
    # next scripted question. Completion therefore cannot be decided by the
    # index alone: the last base question may still have a follow-up
    # outstanding, and finishing there would charge the customer and start
    # scoring while the candidate is still typing.
    if index >= len(prompts) and not pending:
        raise HTTPException(status_code=409, detail="Conversation is already complete")

    if pending:
        # Answered under the key of the question that PRODUCED the follow-up,
        # so `answers_by_key` files it with that question's other answers and
        # every scorer sees one richer answer rather than an unknown key.
        # The index is deliberately NOT advanced: a follow-up is extra evidence
        # for a question already counted, never an extra question.
        domain = conversation.pending_domain or "technical"
        key = conversation.pending_question_key or ""
        prompt = pending
        conversation.pending_prompt = None
        conversation.pending_question_key = None
        conversation.pending_domain = None
    else:
        domain, key, prompt = prompts[index]
        conversation.next_question_index += 1

    ordinal = (
        await session.execute(select(func.coalesce(func.max(AssessmentMessage.ordinal), 0)).where(AssessmentMessage.conversation_id == conversation.id))
    ).scalar_one()
    answer_text = body.answer.strip()
    session.add_all([
        AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 1, speaker="agent", domain=domain, question_key=key, content=prompt),
        AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 2, speaker="candidate", domain=domain, question_key=key, content=answer_text),
    ])
    await session.flush()

    # Decide whether to press on this answer. Only after a BASE question: two
    # consecutive probes on one point reads as cross-examination, and it is
    # also what would let one evasive candidate consume the whole budget.
    if not pending:
        transcript = await _transcript_rows(session, conversation.id)
        follow_up = await interviewer.next_follow_up(
            session=session,
            question=prompt,
            answer=answer_text,
            transcript=transcript,
            follow_ups_used=conversation.follow_ups_used,
            already_followed_up=False,
        )
        if follow_up:
            conversation.pending_prompt = follow_up
            conversation.pending_question_key = key
            conversation.pending_domain = domain
            conversation.follow_ups_used += 1

    if conversation.next_question_index >= len(prompts) and not conversation.pending_prompt:
        conversation.status = "completed"
        conversation.completed_at = datetime.now(timezone.utc)
        # One full credit, charged at the moment completed_at is set. Idempotent,
        # and deliberately NOT deferred to the scoring task: the customer should
        # see the deduction at the same time they see the report, and a failed
        # scoring run must not make the work free.
        await credit_reconciliation.charge_completed(
            session,
            conversation_id=conversation.id,
            tenant_id=job.tenant_id,
            job_candidate_link_id=link.id,
        )
        celery_app.send_task("pickready.run_functional_assessment", args=[str(link.id)])
    await session.flush()
    next_index = conversation.next_question_index
    # A pending follow-up is what the candidate sees next. The progress label
    # deliberately keeps counting BASE questions, so a probe does not make the
    # interview look longer than it is or push the count past its own total.
    next_prompt = conversation.pending_prompt or (
        prompts[next_index][2] if next_index < len(prompts) else None
    )
    return ConversationOut(
        conversation_id=conversation.id,
        status=conversation.status,
        prompt=next_prompt,
        progress_label=f"Question {next_index + 1} of {len(prompts)}" if next_index < len(prompts) else "Conversation complete",
    )
