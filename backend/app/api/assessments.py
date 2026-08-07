"""Job setup review, the unified conversation, and the PPI Assessment Report."""
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
    CandidateTechnicalQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.assessments import (
    CompetencyIn,
    CompetencyOut,
    ConversationMessageIn,
    ConversationOut,
    DimensionOut,
    FrameworkOut,
    FunctionalReportOut,
    JobSetupOut,
    RadarChartOut,
    TranscriptExchangeOut,
    TranscriptOut,
)
from app.services import capabilities as caps
from app.services import (
    answer_classification,
    conversation_guardrails,
    credit_reconciliation,
    hiring_pipeline,
    interview_telemetry,
    interviewer,
    ppi,
    technical_interview,
)
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
from app.services.report_pdf import render_report_pdf
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

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

    CHANGED 2026-08-04: the technical question bank stopped gating anything. It
    used to be the other half of this condition, and a job stayed at
    `questions_pending_review` until a recruiter pressed Finalize on it.

    CHANGED 2026-08-06: the bank stopped EXISTING. Technical questions are
    written per candidate during the conversation, so there is no per-job list
    for anyone to approve.

    The FRAMEWORK review is deliberately KEPT through both changes, and the
    reason is the same one each time. The framework is the fixed evaluation
    criteria every candidate on this job is graded against, it is frozen once
    anyone has been assessed, and a report states a grade against those exact
    criteria. A human confirming it is the product's only comparability
    guarantee. A technical question carried no such promise even when it was
    stored: it is scored against its own rubric, so a weak one costs one item on
    one report rather than making two reports incomparable.

    `questions_approved_at` is left on the model and is now written by NOTHING
    -- the route that stamped it went with the bank. It is deliberately not
    dropped in the same change that stopped using it, so a rollback needs no
    data restore, and `_setup_out` reports the framework's state under that name
    so a client still reading the old field cannot conclude a ready job is
    unready.
    """
    target = READY_FOR_CANDIDATES if job.framework_approved_at is not None else PENDING_REVIEW
    if job.assessment_status != target:
        job.assessment_status = target
    await session.flush()


def _setup_out(job: Job, *, framework_pending: bool = False) -> JobSetupOut:
    approved = job.framework_approved_at is not None
    return JobSetupOut(
        job_id=job.id,
        status=job.assessment_status,
        grade=job.assessment_grade,
        # Deprecated, and deliberately MIRRORS the framework rather than
        # reporting `questions_approved_at`. That column tracked the technical
        # bank, which no longer exists; a client still reading this field would
        # otherwise see False forever on every job and hide the invite control
        # on a job that is perfectly ready.
        questions_approved=approved,
        framework_approved=approved,
        ready_for_candidates=job.assessment_status == READY_FOR_CANDIDATES,
        generated_at=job.framework_generated_at,
        approved_at=job.framework_approved_at,
        framework_pending=framework_pending,
    )


async def _framework_repair_pending(session: AsyncSession, job: Job) -> bool:
    """Whether this job has no usable framework, enqueueing one if so.

    WHY THIS EXISTS
    ---------------
    Framework generation was fire-and-forget at job creation and nothing ever
    checked it landed. Measured on the live database 2026-08-06: 19 of 35 jobs
    carried `framework_generated_at` and had ZERO competency rows. Three whole
    tenants were in that state for every one of their jobs, which is what "the
    portal does not work for other companies" actually was -- a recruiter opened
    the setup screen, saw an empty list, had nothing to approve, and so no
    candidate on any of those jobs could ever be assessed.

    Worse, the stamp made it invisible. `remind_unapproved_technical_questions`
    chases jobs where `framework_generated_at IS NOT NULL`, so a job that never
    produced rows was excluded from the very reminder meant to catch it. A
    timestamp was being treated as evidence that work happened, which is the
    exact failure this repo has a standing rule about.

    So the read path repairs. Enqueueing here is safe and bounded:
    `ppi.generate_framework` is idempotent, and Celery deduplicates nothing but
    the task is a no-op when rows already exist. The recruiter is told the state
    (`framework_pending`) instead of being shown an empty list that looks like a
    finished, empty framework.
    """
    rows = await ppi.load_framework(session, job.id)
    if rows:
        return False
    celery_app.send_task("pickready.generate_ppi_framework", args=[str(job.id)])
    logger.info("assessments.framework_repair_enqueued job_id=%s", job.id)
    return True


@router.get("/jobs/{job_id}/setup", response_model=JobSetupOut)
async def job_setup(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobSetupOut:
    job = await _staff_job(session, user, job_id)
    return _setup_out(job, framework_pending=await _framework_repair_pending(session, job))


# ── The technical question bank: REMOVED 2026-08-06 ──────────────────────────
#
# Five routes lived here: GET/POST/PUT/DELETE `/jobs/{id}/questions` and
# `POST /jobs/{id}/finalize`. They were the Company Portal's preset technical
# bank -- a per-job list of stored strings a company authored, edited and
# finalised, which every applicant to that job then read verbatim.
#
# The feature is withdrawn. Technical questions are written per candidate,
# during the conversation, from the JD, that candidate's resume and everything
# said so far (`services/technical_interview`). There is nothing on a job to
# create, edit, store or assign, so there is no route.
#
# They are DELETED rather than left returning 410. A route that answers is a
# route a client keeps calling, and the frontend screens behind these went in
# the same change; a 404 from an unregistered path is the honest answer to a
# request for a feature that does not exist.
#
# `job.questions_approved_at` is still stamped on nothing and read by nothing.
# It was already inert before this change (2026-08-04 stopped it gating
# anything) and is deliberately not dropped here, so a rollback needs no data
# restore.


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


#: Shown when a job has no framework at all and one has just been enqueued.
#: Distinct from `framework_is_complete`'s reasons, which describe a framework
#: that EXISTS and is short of a category minimum. An empty list means the
#: generator has not landed, and telling a recruiter "add at least 5 Primary
#: Skills" in that state sends them to hand-build 15 competencies the product
#: was supposed to write for them.
FRAMEWORK_PREPARING = (
    "We are still preparing the evaluation criteria for this role. This "
    "normally takes under a minute. Refresh the page shortly."
)


async def _framework_out(
    session: AsyncSession, job: Job, *, pending: bool | None = None
) -> FrameworkOut:
    rows = await ppi.load_framework(session, job.id)
    ok, reason = ppi.framework_is_complete(rows)
    if not rows and pending:
        reason = FRAMEWORK_PREPARING
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
    # Self-healing read. See `_framework_repair_pending`: a job whose generator
    # never landed used to render as an empty framework indistinguishable from a
    # finished one, and nothing anywhere retried.
    pending = await _framework_repair_pending(session, job)
    return await _framework_out(session, job, pending=pending)


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


@router.get("/reports/links/{link_id}/pdf")
async def download_report_pdf(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Download the immutable report with tenant-scoped authorization."""
    report_out = await get_report(link_id=link_id, user=user, session=session)
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Report not found")
    candidate = await session.get(Candidate, link.candidate_id)
    job = await session.get(Job, link.job_id)
    tenant = await session.get(Tenant, link.tenant_id)
    candidate_name = (candidate.full_name if candidate else None) or "Candidate"
    job_title = (job.title if job else None) or "Role"
    tenant_name = (tenant.name if tenant else None) or "PickReady customer"
    payload = render_report_pdf(
        report_out,
        candidate_name=candidate_name,
        job_title=job_title,
        tenant_name=tenant_name,
        generated_at=report_out.synthesized_at,
    )
    safe_name = (
        re.sub(r"[^A-Za-z0-9_-]+", "-", candidate_name).strip("-")
        or "candidate"
    )
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ppi-assessment-report-{safe_name}.pdf"'
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
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


# ── The recruiter's view of what was asked and answered ──────────────────────
#
# A report states a grade. This is the evidence behind it, and until 2026-08-06
# the only way to read it was a psql session against `assessment_messages`.
#
# WHY IT IS SHAPED THIS WAY, given it has to hold for a long time:
#
#   * PAIRED SERVER-SIDE. `assessment_messages` stores speakers in sequence,
#     which is the right shape to write and the wrong shape to read. Pairing in
#     the client means every client re-implements the follow-up and re-ask rules
#     (a probe shares its parent's question_key and is NOT a new question), and
#     they will drift. It is done once, here, by the module that owns those rules.
#
#   * PAGINATED FROM DAY ONE. A non-managerial interview is 45 base questions
#     plus up to 15 probes: 120 messages, several of them long. Returning all of
#     it grows without bound as grades get longer and is the kind of endpoint
#     that works fine until the first CXO pipeline with 200 candidates.
#
#   * KEYED ON THE LINK, not on the report. The transcript exists the moment the
#     candidate answers question one; the report does not exist until they
#     finish. A recruiter chasing a stalled assessment needs the former, and
#     hanging this off the report would make exactly that case unreachable.
#
#   * CRITERION RESOLVED, NOT LEAKED. Each exchange carries the skill or
#     competency it was filed under, as a WORD. That is what makes a transcript
#     readable as evidence. No score, no rubric and no required level crosses
#     this boundary -- the rubric is internal scoring machinery, and the
#     no-numbers rule covers this response like every other.

#: Bounded so one request cannot ask for an entire tenant's interview history.
#: 200 comfortably holds the longest single interview the product can produce
#: (45 base + 15 probes), so the common case is one page.
TRANSCRIPT_MAX_LIMIT = 200
TRANSCRIPT_DEFAULT_LIMIT = 100


async def _criterion_labels(
    session: AsyncSession, link: JobCandidateLink
) -> dict[str, str]:
    """{question_key: human label} for every key this candidate could produce.

    Both scorers key on a row id, so the raw transcript is a wall of UUIDs. This
    resolves them in two queries rather than one per exchange -- the N+1 here
    would be 60 round trips on an ordinary interview.
    """
    labels: dict[str, str] = {}
    for row in await technical_interview.load_for_link(session, link.id):
        labels[str(row.id)] = row.skill
    competencies = (
        await session.execute(
            select(JobCompetency.id, JobCompetency.name).where(
                JobCompetency.job_id == link.job_id
            )
        )
    ).all()
    for competency_id, name in competencies:
        labels[str(competency_id)] = name
    return labels


@router.get("/transcripts/links/{link_id}", response_model=TranscriptOut)
async def get_transcript(
    link_id: uuid.UUID,
    limit: int = TRANSCRIPT_DEFAULT_LIMIT,
    offset: int = 0,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TranscriptOut:
    """Every question this candidate was asked and every answer they gave.

    Gated on `view_review_screen`, the same capability that opens the report:
    someone who may read the grade may read the evidence for it, and someone who
    may not read the grade certainly may not read the candidate's raw answers.
    """
    limit = max(1, min(TRANSCRIPT_MAX_LIMIT, limit))
    offset = max(0, offset)

    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Application not found")
    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()
    candidate_name = (
        await session.execute(select(Candidate.full_name).where(Candidate.id == link.candidate_id))
    ).scalar_one_or_none()
    job_title = (
        await session.execute(select(Job.title).where(Job.id == link.job_id))
    ).scalar_one_or_none()

    if conversation is None:
        # Invited but never opened, or not invited at all. An empty transcript
        # is the correct answer and a 404 is not: the recruiter asked a
        # reasonable question about a real application and "nothing yet" is the
        # true answer to it.
        return TranscriptOut(
            job_candidate_link_id=link.id,
            candidate_name=candidate_name,
            job_title=job_title,
            status="not_started",
            exchanges=[],
            total=0,
            limit=limit,
            offset=offset,
        )

    messages = (
        await session.execute(
            select(AssessmentMessage)
            .where(AssessmentMessage.conversation_id == conversation.id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).scalars().all()
    labels = await _criterion_labels(session, link)

    # Pair each agent line with the candidate line that answered it. Walking the
    # ordinals rather than zipping alternate rows: the last question of an
    # abandoned assessment has no answer, and zipping would silently pair it
    # with someone else's.
    exchanges: list[TranscriptExchangeOut] = []
    seen_keys: set[str] = set()
    pending: AssessmentMessage | None = None
    for message in messages:
        if message.speaker == "agent":
            pending = message
            continue
        if pending is None:
            continue
        key = pending.question_key or ""
        # A follow-up or re-ask reuses its parent's key by design, which is
        # exactly how the scorers file it as more evidence for one question. It
        # is therefore also how we recognise one here, with no extra column.
        follow_up = key in seen_keys
        if key:
            seen_keys.add(key)
        exchanges.append(
            TranscriptExchangeOut(
                ordinal=len(exchanges) + 1,
                domain=pending.domain,
                question=pending.content,
                answer=message.content,
                criterion=labels.get(key),
                follow_up=follow_up,
                asked_at=pending.created_at,
            )
        )
        pending = None

    total = len(exchanges)
    return TranscriptOut(
        job_candidate_link_id=link.id,
        candidate_name=candidate_name,
        job_title=job_title,
        status=conversation.status,
        completed_at=conversation.completed_at,
        exchanges=exchanges[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
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
    # The review gate (spec §5, amended 2026-08-04): no candidate enters the
    # conversation until the recruiter has saved the PPI FRAMEWORK. The
    # technical bank stopped gating anything in the same change, so it is no
    # longer named here or in the message the candidate reads.
    if job.assessment_status != READY_FOR_CANDIDATES:
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment is not open yet. The hiring team is still "
                "confirming how this role will be evaluated, and you will be "
                "emailed as soon as it is ready."
            ),
        )
    return link, job


# REMOVED 2026-08-05: `_CONNECTORS`, eight canned openers ("Great.",
# "Understood.", "Thanks for that.", "Appreciate the detail." ...) prepended to
# every question by POSITION.
#
# They were the literal thing the brief forbids: templated acknowledgment
# strings, chosen by `position % 8` and therefore blind to what the candidate
# had just said. An answer of "I do not know" was met with "Appreciate the
# detail." That is worse than saying nothing, because it tells the candidate
# the interviewer is not reading -- and it is the single most visible reason
# the assessment read as a form rather than a conversation.
#
# The job they were doing (making the blended technical/PPI sequence read as
# ONE conversation, spec §8) is now done properly by
# `interviewer.compose_next_question`, which writes the transition against the
# actual transcript, or writes nothing when there is no real connection to
# draw. A bare question is always better than a false acknowledgment.


async def _conversation_prompts(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> list[tuple[str, str, str]]:
    """The single blended sequence: this candidate's technical slots + their PPI
    questions, round-robin interleaved.

    The candidate never sees or interacts with two bots and is never told which
    engine scores which answer (spec §8).

    `question_key` carries the CandidateTechnicalQuestion id and the
    JobCompetency id respectively -- the two scorers key on exactly these.

    CHANGED 2026-08-06: the technical half reads per-CANDIDATE rows rather than
    the job's preset bank. The sequence length and the interleaving are
    identical, and so is the key contract: a stable row id from the first turn,
    so no scorer ever sees a key appear mid-conversation.
    """
    technical = await technical_interview.load_for_link(session, link.id)
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

    # Returned BARE. The conversational join between one question and the next
    # is written per turn by `interviewer.compose_next_question` against the
    # real transcript, so it can only claim a connection that actually exists.
    # Anything canned here would be prepended before the candidate has said
    # anything for it to respond to.
    return blended


async def _ensure_conversation_ready(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> None:
    """Transient prep, never a human gate.

    The technical half is created INLINE and the PPI half is not, and the
    asymmetry is deliberate rather than an inconsistency:

      * `technical_interview.ensure_slots` writes rows from a PURE function of
        the job's JD (`skill_plan`). No model call, no network, microseconds.
        Deferring that to a worker would make a candidate wait and retry for
        work the request could have finished before the response was written.
        The QUESTIONS are still generated by a model, one at a time, later --
        this only reserves the slots and their skills.

      * PPI questions ARE a model call, per candidate, against the job's
        framework, so they stay in Celery (claude.md rule 4) and the candidate
        is asked to retry in a moment.
    """
    await technical_interview.ensure_slots(session, job, link)
    has_ppi = (
        await session.execute(
            select(func.count())
            .select_from(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
        )
    ).scalar_one()
    if has_ppi:
        return
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

    # THE FIRST QUESTION IS WRITTEN HERE, and it has to be.
    #
    # Every other base question is written on the request that answers its
    # predecessor, so by the time it is shown `delivered_prompt` is populated.
    # Question one has no predecessor. Before 2026-08-06 that did not matter --
    # the first question was a preset technical string, which is what a preset
    # bank is for -- but the blend puts a technical slot first, and a technical
    # slot now starts life holding the deterministic fallback probe. Without
    # this every candidate's opening question would be the generic
    # "Describe a demanding situation where you applied X", which is precisely
    # the scripted feel the whole change exists to remove.
    #
    # Guarded on `delivered_prompt is None` so re-opening a part-finished
    # assessment does not rewrite the question the candidate is already looking
    # at, and skipped entirely when a probe is outstanding.
    if (
        conversation.pending_prompt is None
        and conversation.delivered_prompt is None
        and index < len(prompts)
    ):
        await _write_next_question(session, job, link, conversation, prompts, index)

    # A pending follow-up outranks the next base question: the candidate left
    # mid-probe and must come back to the probe, not skip past it.
    # `delivered_prompt` is the wording written for this base question; it is
    # NULL only when generation was unavailable, and the stored text is the
    # correct fallback then.
    prompt = conversation.pending_prompt or (
        (conversation.delivered_prompt or prompts[index][2])
        if index < len(prompts)
        else None
    )
    return ConversationOut(
        conversation_id=conversation.id,
        status=conversation.status,
        prompt=prompt,
        progress_label=f"Question {index + 1} of {len(prompts)}" if index < len(prompts) else "Conversation complete",
    )



async def _resume_excerpt(session: AsyncSession, link: JobCandidateLink) -> str:
    """This candidate's resume text, or empty.

    Read per turn rather than cached on the conversation: a candidate may
    replace the resume on their profile, and the questions should be grounded in
    what the application actually carries now.
    """
    if link.profile_id is None:
        return ""
    return (
        await session.execute(
            select(Profile.resume_text).where(Profile.id == link.profile_id)
        )
    ).scalar_one_or_none() or ""


async def _write_next_question(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    prompts: list[tuple[str, str, str]],
    index: int,
) -> None:
    """Write the base question at `index` for THIS candidate at THIS point, and
    stash it on `conversation.delivered_prompt`.

    BOTH HALVES ARE NOW GENERATED, BY DIFFERENT AGENTS, FOR DIFFERENT REASONS
    -------------------------------------------------------------------------
      ppi        `interviewer.compose_next_question` in MODE_GENERATE. A PPI
                 answer is scored against its COMPETENCY across every answer
                 filed under it, so the question may be written fresh.

      technical  `technical_interview.write_question`. Until 2026-08-06 this was
                 MODE_REWORD -- the phrasing could move but the substance could
                 not -- because the answer was scored against a preset question's
                 stored rubric, and a fresh question would have been graded
                 against a rubric for a question nobody was asked. That objection
                 is answered by generating the RUBRIC WITH THE QUESTION and
                 persisting both before the candidate reads either, which is
                 exactly what `write_question` does. The rubric now always
                 belongs to the question actually asked.

    Both paths degrade to the stored text, which is always a correct thing to
    ask: for PPI it is the question pre-generated for this competency from this
    resume, and for technical it is the deterministic probe `ensure_slots` wrote
    from the job's own skill plan.
    """
    if index >= len(prompts):
        return
    try:
        await _write_next_question_inner(session, job, link, conversation, prompts, index)
    except Exception as exc:  # noqa: BLE001
        # WRITING THE NEXT QUESTION MUST NEVER COST THE CANDIDATE THIS TURN.
        #
        # By the time this runs the candidate's answer is already recorded and
        # the index already advanced. Everything here is an ENHANCEMENT of the
        # question they will read next, and the stored text is always a correct
        # thing to ask, so any failure has exactly one right answer: leave
        # `delivered_prompt` NULL and move on.
        #
        # It is not hypothetical. `llm_router` used to commit the CALLER's
        # transaction when it condemned a key, which closed the request's
        # transaction and made the next read raise -- so a provider outage 500ed
        # a candidate mid-assessment. That root cause is fixed
        # (`llm_router._persist_key_health`), and this stays as the guard that
        # makes the whole step non-load-bearing whatever the next such bug is.
        logger.warning(
            "assessments.next_question_unavailable conversation_id=%s error=%s",
            conversation.id, type(exc).__name__,
        )


async def _write_next_question_inner(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    prompts: list[tuple[str, str, str]],
    index: int,
) -> None:
    domain, key, stored = prompts[index]
    # Read AFTER the caller's flush so the turn just written is part of the
    # memory this question is conditioned on. Reading a stale transcript would
    # have the interviewer talk as though the last answer had not been given.
    memory = await _transcript_rows(session, conversation.id)
    asked_before = [row["content"] for row in memory if row.get("speaker") == "agent"]
    resume = await _resume_excerpt(session, link)

    if domain == "technical":
        row = None
        try:
            row = await session.get(CandidateTechnicalQuestion, uuid.UUID(str(key)))
        except (ValueError, TypeError):
            row = None
        if row is None:
            # The slot vanished under us, which should be impossible. Showing
            # the stored text is the honest degradation; refusing the turn would
            # cost the candidate their assessment over a bookkeeping fault.
            logger.info("assessments.technical_slot_missing key=%s", key)
            written = stored
        else:
            result = await technical_interview.write_question(
                session=session,
                job=job,
                row=row,
                resume_excerpt=resume,
                transcript=memory,
                asked_before=asked_before,
            )
            written = result.value["question"]
    else:
        # `key` carries the JobCompetency id for a PPI question: the criterion
        # this turn must probe and the key its answer will be filed under.
        competency = None
        try:
            competency = await session.get(JobCompetency, uuid.UUID(str(key)))
        except (ValueError, TypeError):
            competency = None
        written = await interviewer.compose_next_question(
            session=session,
            question=stored,
            transcript=memory,
            mode=(
                interviewer.MODE_GENERATE if competency else interviewer.MODE_REWORD
            ),
            competency=competency.name if competency else "",
            competency_hint=(competency.description or "") if competency else "",
            jd_excerpt=job.jd_markdown or "",
            resume_excerpt=resume,
            asked_before=asked_before,
        )

    # OUTBOUND GUARD, on the way IN to storage rather than on the way out, so
    # the transcript records exactly the text the candidate will read. A
    # generated question is written by a model that has just been shown a JD, a
    # resume and a criterion -- precisely the context from which a grade or a
    # required level could leak into interviewer speech.
    conversation.delivered_prompt = conversation_guardrails.inspect_agent_output(written)
    await session.flush()


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
    # Wall clock for the turn, for the telemetry line at the end. monotonic
    # rather than wall time: this measures a duration, and a clock adjustment
    # mid-request would otherwise produce a negative latency.
    turn_started = time.monotonic()
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
        # Log what the candidate actually READ, not what was stored for them.
        # `delivered_prompt` is the wording composed on the previous turn; when
        # it is NULL (first question, or the rewrite was unavailable) the two
        # are the same string. Getting this wrong would make the transcript a
        # record of a conversation that never happened, and the transcript is
        # both the scorers' input and this agent's own memory.
        prompt = conversation.delivered_prompt or prompt
        conversation.delivered_prompt = None
        conversation.next_question_index += 1

    ordinal = (
        await session.execute(select(func.coalesce(func.max(AssessmentMessage.ordinal), 0)).where(AssessmentMessage.conversation_id == conversation.id))
    ).scalar_one()
    # INBOUND GUARD, before this text is stored or reaches any prompt.
    #
    # A candidate's answer is DATA, never instructions to the interviewer, and
    # it goes straight into a model prompt. `sanitized` keeps their real content
    # and defangs attack framing, so an answer that legitimately DISCUSSES
    # prompt injection is still an answer. It also redacts anything shaped like
    # a pasted credential, which protects the candidate rather than us: prompts
    # are traced, and a key pasted by mistake must not be persisted or sent on.
    #
    # Note the contract: `violation is not None` does NOT mean refused. Only
    # `allowed` decides, and it goes False only when the framing was a directive
    # AND nothing resembling an answer is left underneath it.
    guard = conversation_guardrails.inspect_answer(body.answer)
    answer_text = guard.sanitized.strip()
    session.add_all([
        AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 1, speaker="agent", domain=domain, question_key=key, content=prompt),
        AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 2, speaker="candidate", domain=domain, question_key=key, content=answer_text),
    ])
    await session.flush()

    # Decide how to react to this answer. Only after a BASE question: two
    # consecutive reactions on one point reads as cross-examination, and it is
    # also what would let one evasive candidate consume the whole budget.
    if not pending:
        transcript = await _transcript_rows(session, conversation.id)
        reaction = None
        action = "advanced"
        answer_label = "substantive"
        degraded = False

        if not guard.allowed:
            # A refused turn is still transcribed above: the record of what was
            # said is what a dispute is settled from. Classifying it would spend
            # a model call grading an attack, so this short-circuits to the same
            # re-ask mechanism a non-answer uses -- same question_key, no
            # follow-up budget, bounded to one per question.
            reaction = guard.candidate_message
            action = "rechallenged"
            answer_label = f"guarded_{guard.violation}"
        else:
            # ONE classification decides what happens next. Gibberish and empty
            # are settled deterministically (no model call, because the model
            # being down is exactly when the guard matters); off_topic and
            # evasive need the model, because they are well-formed prose that
            # simply does not answer the question, and nothing deterministic
            # can see that.
            #
            # Observed live on 2026-08-05: four consecutive keyboard-mash
            # answers were each met with the next scripted question, because
            # the follow-up path deliberately refuses to spend a probe on
            # gibberish. Sound for probing, and the worst behaviour overall --
            # the one case a human interviewer certainly reacts to became the
            # one case this agent never did.
            verdict = await answer_classification.classify(
                session=session,
                question=prompt,
                answer=answer_text,
                transcript=transcript,
            )
            answer_label = verdict.label
            degraded = verdict.confidence == "low"

            if verdict.needs_rechallenge:
                # A re-ask costs no follow-up budget and changes no scoring:
                # the answer is already recorded, and gibberish already grades
                # Not Matching through the existing unanswered path.
                reaction = await interviewer.challenge_non_answer(
                    session=session,
                    question=prompt,
                    answer=answer_text,
                    transcript=transcript,
                    label=verdict.label,
                )
                action = "rechallenged" if reaction else "advanced"

            if reaction is None:
                reaction = await interviewer.next_follow_up(
                    session=session,
                    question=prompt,
                    answer=answer_text,
                    transcript=transcript,
                    follow_ups_used=conversation.follow_ups_used,
                    already_followed_up=False,
                    # Scaled to this interview's length. A flat five probes
                    # across 45 questions left 89% of the conversation unable
                    # to react to anything the candidate said.
                    budget=interviewer.follow_up_budget(len(prompts)),
                )
                # Only a real probe draws down the budget. A re-ask does not:
                # asking someone to actually answer is not a probe, and
                # spending the budget on it would starve the thin-but-real
                # answers later in the interview that a probe is worth more on.
                if reaction:
                    conversation.follow_ups_used += 1
                    action = "followed_up"

        if reaction:
            # OUTBOUND GUARD. Last thing before any interviewer line is stored
            # as what the candidate will read.
            conversation.pending_prompt = conversation_guardrails.inspect_agent_output(reaction)
            conversation.pending_question_key = key
            conversation.pending_domain = domain

        # One structured line per turn. Labels, keys and timings only, never
        # answer or question text: an ordinary log is far more widely readable
        # than a LangSmith trace, and prompts carry a real candidate's answers.
        interview_telemetry.record_turn(
            interview_telemetry.TurnEvent(
                conversation_id=str(conversation.id),
                turn_index=index,
                question_key=key,
                domain=domain,
                answer_label=answer_label,
                action=action,
                generated=conversation.delivered_prompt is not None,
                degraded=degraded,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
        )

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

    # Write the next BASE question for THIS candidate at THIS point.
    #
    # Skipped entirely when a probe or re-ask is outstanding: that IS the next
    # thing the candidate sees, and writing a question nobody will read would
    # spend a second sequential model call on a request a candidate is waiting
    # on. Also skipped once the questions run out, for the same reason.
    if not conversation.pending_prompt and next_index < len(prompts):
        await _write_next_question(
            session, job, link, conversation, prompts, next_index
        )

    # A pending follow-up is what the candidate sees next. The progress label
    # deliberately keeps counting BASE questions, so a probe does not make the
    # interview look longer than it is or push the count past its own total.
    #
    # `prompts[next_index][2]` is deliberately re-read from the ORIGINAL list
    # rather than refetched: for a technical slot `write_question` has just
    # overwritten the row's prompt in place, and the delivered text is what the
    # candidate reads anyway. This branch is only reached when delivery was
    # unavailable, and then the pre-generation text is exactly the right thing
    # to show.
    next_prompt = conversation.pending_prompt or (
        (conversation.delivered_prompt or prompts[next_index][2])
        if next_index < len(prompts)
        else None
    )
    return ConversationOut(
        conversation_id=conversation.id,
        status=conversation.status,
        prompt=next_prompt,
        progress_label=f"Question {next_index + 1} of {len(prompts)}" if next_index < len(prompts) else "Conversation complete",
    )
