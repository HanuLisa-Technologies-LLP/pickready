"""Job setup review, the unified conversation, and the PPI Assessment Report."""
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_optional_candidate,
    get_public_db,
    get_tenant_db,
    require_capability,
)
from app.models.assessment import (
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    # LEGACY, read-only: a transcript written before Draft v4 keyed its
    # technical exchanges on this table, and the recruiter's transcript view
    # still has to resolve those keys to a label.
    CandidateTechnicalQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.job import Job
from app.models.job_setup import SWOT_AREAS, JobSwotIntake
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.assessments import (
    BulkCompetencyIn,
    CompetencyIn,
    CompetencyOut,
    ConversationAnswerEditIn,
    ConversationMessageIn,
    ConversationOut,
    DimensionOut,
    FrameworkOut,
    FunctionalReportOut,
    GapAnalysisOut,
    InvitationResolveOut,
    JobSetupOut,
    MatrixReorderIn,
    RadarChartOut,
    SwotAnswerIn,
    SwotIntakeOut,
    TranscriptExchangeOut,
    TranscriptOut,
)
from app.services import capabilities as caps
from app.services import rbac
from app.services.hiring import pipeline_halt, scorecard, situations, swot_quality
from app.services import (
    answer_classification,
    assessment_invite,
    conversation_guardrails,
    credit_reconciliation,
    hiring_pipeline,
    interview_telemetry,
    interviewer,
    job_posting,
    ppi,
    ppi_interview,
    reference_code,
    retake,
    swot_intake,
    tenant_cache,
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
from app.workers.celery_app import celery_app
from app.services.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

READY_FOR_CANDIDATES = "ready_for_candidates"
PENDING_REVIEW = "questions_pending_review"
MAX_REASKS_PER_QUESTION = 2


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
    #: CHANGED (Draft v4): the setup session has TWO halves and the job is open
    #: to candidates only when both are finalised (spec §10). The PPI matrix is
    #: what every candidate is graded against; the Matching category list is what
    #: every sourced resume is ranked against, and a job whose categories were
    #: never confirmed would rank its whole pipeline against a list nobody read.
    #:
    #: The SWOT intake is deliberately NOT a third condition. It is an INPUT to
    #: the matrix, so an intake nobody completed shows up here as a matrix nobody
    #: approved; making it its own gate would give one problem two error messages
    #: and two places to fix it.
    ready = (
        job.framework_approved_at is not None
        and job.matching_categories_finalized_at is not None
    )
    target = READY_FOR_CANDIDATES if ready else PENDING_REVIEW
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
        matching_categories_finalized=job.matching_categories_finalized_at is not None,
        swot_complete=job.swot_completed_at is not None,
        ready_for_candidates=job.assessment_status == READY_FOR_CANDIDATES,
        generated_at=job.framework_generated_at,
        approved_at=job.framework_approved_at,
        framework_pending=framework_pending,
    )


async def _framework_repair_pending(session: AsyncSession, job: Job) -> bool:
    """Whether this job has no usable matrix, enqueueing Sutra if it can run.

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

    CHANGED 2026-08-29. It no longer enqueues unconditionally. Sutra refuses to
    compile without a completed SWOT session, so a job whose intake is still
    running has no matrix for a perfectly good reason, and enqueueing a task
    that is certain to refuse would fill the log with a normal waiting state.
    The enqueue is now conditioned on the one input this layer can see; every
    other refusal is Sutra's and is surfaced by `_setup_out`'s own fields.
    """
    rows = await ppi.load_framework(session, job.id)
    if rows:
        return False
    if job.swot_completed_at is None:
        # Nothing to enqueue. The setup screen says the SWOT session is
        # outstanding, which is the actionable half of this state.
        return True
    celery_app.send_task(
        "pickready.compile_tatva_matrix",
        args=[str(job.id)],
        kwargs={"correlation_id": job.correlation_id or ""},
    )
    logger.info("assessments.matrix_compile_enqueued job_id=%s", job.id)
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
    """One matrix item as the review screen reads it.

    THE SEVEN STAGES TRAVEL; THE ARITHMETIC DOES NOT. `weight`, the four
    multiplier terms and `threshold` stay on the row. What crosses is the
    plain-language provenance and the force-ranking POSITION, which is an order
    rather than a score. spec-doc6 §4.3 asks for the traceability to be shown
    "in plain language before finalisation", and a table of multipliers is not
    plain language: a hiring manager confirming "1.4850" is confirming that the
    arithmetic looks plausible.
    """
    item = scorecard.item_from_row(row)
    return CompetencyOut(
        id=row.id,
        category=row.category,
        name=row.name,
        description=row.description,
        # A number never crosses this boundary, not even the job's own
        # requirement level: the client reads and writes the same four words.
        required_level=grade_for_percent(row.required_level) or GRADES[1],
        ordinal=row.ordinal,
        observable_evidence=row.observable_evidence,
        assessment_method=row.assessment_method,
        disqualifier=row.disqualifier,
        swot_origin=row.swot_origin,
        force_rank=row.force_rank,
        provenance=scorecard.plain_provenance(item) if item is not None else [],
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
    ok, reason = ppi.matrix_is_complete(rows, job.assessment_grade)
    if not rows and pending:
        reason = FRAMEWORK_PREPARING
    return FrameworkOut(
        job_id=job.id,
        status=job.assessment_status,
        approved=job.framework_approved_at is not None,
        competencies=[_competency_out(row) for row in rows],
        maximum_items=ppi.max_questions(job.assessment_grade),
        # Computed from what the matrix holds RIGHT NOW rather than read from
        # `job.question_target`, which is stamped at generation. The Hiring
        # Manager is mid-edit on this screen and needs to see what the matrix in
        # front of them would cost a candidate, not what the generated one did.
        question_target=ppi.resolve_question_target(job.assessment_grade, len(rows)),
        question_range=list(
            ppi.resolve_question_range(job.assessment_grade, len(rows))
        ),
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
    cache_key = f"pickready:tenant:{job.tenant_id}:job_competencies:{job.id}"
    cached = await tenant_cache.get_json(cache_key)
    if cached is not None:
        return FrameworkOut.model_validate(cached)
    pending = await _framework_repair_pending(session, job)
    output = await _framework_out(session, job, pending=pending)
    await tenant_cache.set_json(cache_key, output.model_dump(mode="json"), ttl=120)
    return output


async def _invalidate_framework(job: Job) -> None:
    await tenant_cache.delete(
        f"pickready:tenant:{job.tenant_id}:job_competencies:{job.id}"
    )


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
    await _invalidate_framework(job)
    return _competency_out(row)


@router.post(
    "/jobs/{job_id}/framework/bulk",
    response_model=list[CompetencyOut],
    status_code=status.HTTP_201_CREATED,
)
async def add_competencies_bulk(
    job_id: uuid.UUID,
    body: BulkCompetencyIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[CompetencyOut]:
    """Add a pasted list atomically, preserving order and removing duplicates."""
    job = await _staff_job(session, user, job_id)
    _reject_frozen(job)
    names = list(dict.fromkeys(name.strip() for name in body.names if name.strip()))
    if not names:
        raise HTTPException(status_code=422, detail="Add at least one name.")
    if body.category == ppi.CATEGORY_BEHAVIOURAL:
        for name in names:
            _reject_culture(name)
    ordinal = (
        await session.execute(
            select(func.coalesce(func.max(JobCompetency.ordinal), 0)).where(
                JobCompetency.job_id == job.id,
                JobCompetency.category == body.category,
            )
        )
    ).scalar_one()
    rows = [
        JobCompetency(
            tenant_id=job.tenant_id,
            job_id=job.id,
            category=body.category,
            name=name,
            required_level=ppi.required_level_score(body.required_level),
            ordinal=ordinal + index,
        )
        for index, name in enumerate(names, start=1)
    ]
    session.add_all(rows)
    await session.flush()
    await _invalidate_framework(job)
    return [_competency_out(row) for row in rows]


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
    await _invalidate_framework(job)
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
    await _invalidate_framework(job)


@router.post("/jobs/{job_id}/framework/finalize", response_model=FrameworkOut)
async def finalize_framework(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.FINALIZE_ROLE_DEFINITION)
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> FrameworkOut:
    """The Hiring Manager finalises the role definition (RBAC §12, §20).

    THIS IS THE ONE HUMAN ACT THE WHOLE PIPELINE TURNS ON. From here the matrix
    is what EVERY candidate on this job is graded against, `_reject_frozen`
    refuses to reopen it once anyone has been, and gate G1 starts answering yes.

    RBAC §20 makes it an EXPLICIT transition and says what it has to record:
    the user who finalized it, the timestamp, the relevant version and the
    relevant hiring-criteria version. All four are written -- two onto the job,
    two onto the append-only Company DNA binding -- and then again onto the
    audit row, because the columns answer "what is in force" and the audit row
    answers "what happened and who did it".

    Authorised through `rbac.require_authorized` rather than
    `require_capability`, and the difference is the point: this route names a
    resource, so tenant, job assignment (RBAC §10.2: each job has exactly one
    Hiring Manager) and lifecycle state all apply. The check runs BEFORE the
    handler, so a refusal has not already read the criteria it was refusing to
    show.
    """
    job = await _staff_job(session, user, job_id)
    try:
        matrix = await scorecard.freeze(
            session,
            job,
            actor_user_id=user.user_id,
            correlation_id=job.correlation_id,
        )
    except scorecard.ScorecardInputMissing as missing:
        raise HTTPException(status_code=422, detail=missing.detail) from missing
    except pipeline_halt.PipelineHalted as halt:
        raise HTTPException(
            status_code=503, detail=pipeline_halt.http_detail(halt)
        ) from halt

    # RBAC §17's explicit transition. IN_REVIEW -> FINALIZED, and it is the only
    # way into FINALIZED, which is what makes §21's publish precondition
    # checkable: `rbac._state_rules` refuses PUBLISH_JOB in any earlier state.
    job.lifecycle_state = hiring_pipeline.JobLifecycleState.FINALIZED.value
    job.finalized_by = user.user_id
    job.finalized_at = matrix.approved_at
    job.criteria_version = matrix.version
    await _refresh_setup_status(session, job)
    rows = await ppi.load_framework(session, job.id)
    row = await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="role_definition_finalized",
        target_type="job",
        target_id=job.id,
        metadata={
            "counts": {
                category: sum(1 for item in rows if item.category == category)
                for category in ppi.CATEGORIES
            },
            # RBAC §20's four required facts, in the row as well as the columns.
            "jd_version": swot_intake.jd_version(job),
            "criteria_version": matrix.version,
            "company_dna_version": matrix.company_dna_version,
            "situation_key": matrix.situation_key,
        },
    )
    row.job_id = job.id
    row.actor_role = user.role.value
    row.correlation_id = job.correlation_id
    row.new_state = {"lifecycle_state": job.lifecycle_state}
    await _invalidate_framework(job)
    logger.info(
        "assessments.role_definition_finalized job_id=%s by=%s criteria_version=%d "
        "dna_version=%s correlation_id=%s",
        job.id,
        user.user_id,
        matrix.version,
        matrix.company_dna_version,
        job.correlation_id,
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
    await _invalidate_framework(job)
    return await _framework_out(session, job)


# ── Drag-and-drop reordering of the matrix (spec §5.3) ───────────────────────


@router.post("/jobs/{job_id}/framework/reorder", response_model=FrameworkOut)
async def reorder_framework(
    job_id: uuid.UUID,
    body: MatrixReorderIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FrameworkOut:
    """Apply one drag-and-drop gesture: reorder within an aspect, or move an
    item between Must-have and Nice-to-have.

    ONE route for both, because to the person dragging they are one gesture, and
    a UI that had to guess which of two endpoints a drop belonged to would guess
    wrong at exactly the boundary the feature exists to cross.

    The client sends each changed aspect's WHOLE ordered list. That is
    idempotent, it always describes a state a human actually looked at, and it
    means a dropped or retried request cannot leave the matrix in an order
    nobody chose. An aspect the client did not send is left untouched.

    Behavioural is deliberately not a valid destination for a move. §5.3 offers
    moving items "between Must-have and Nice-to-have"; a skill dragged into
    Behavioural would be scored by judgement instead of against a rubric, which
    silently changes how every candidate on the job is assessed on it.
    """
    job = await _staff_job(session, user, job_id)
    _reject_frozen(job)
    rows = {row.id: row for row in await ppi.load_framework(session, job.id)}

    moved = 0
    for group in body.groups:
        if group.category == ppi.CATEGORY_BEHAVIOURAL and any(
            rows[competency_id].category != ppi.CATEGORY_BEHAVIOURAL
            for competency_id in group.competency_ids
            if competency_id in rows
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "An item can be moved between Must-have and Nice-to-have, but "
                    "not into Behavioural Competencies. A skill assessed by "
                    "judgement rather than against a rubric would change how every "
                    "candidate on this job is graded on it."
                ),
            )
        for ordinal, competency_id in enumerate(group.competency_ids, 1):
            row = rows.get(competency_id)
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="One of the items in this list is not on this job.",
                )
            if row.category != group.category:
                # No culture check here: the only reachable destinations are
                # Must-have and Nice-to-have, and the refusal above is what
                # keeps it that way. A check on a branch that cannot be taken
                # reads as protection and provides none.
                row.category = group.category
                moved += 1
            row.ordinal = ordinal
            row.updated_at = datetime.now(timezone.utc)

    await session.flush()
    await _invalidate_framework(job)
    logger.info(
        "assessments.matrix_reordered job_id=%s groups=%d moved=%d",
        job.id, len(body.groups), moved,
    )
    return await _framework_out(session, job)


# ── Bodha: the Hiring Manager SWOT session (Runbook §18) ───────────────────
# The Layer 3 intake, run once per job before Sutra can compile anything. Four
# quadrants, §18.3's seven high-value probes, §18.2's force-ranking and
# disqualifier confirmation, §18.5's best-performer test, and §18.4's situation
# classification read back for explicit confirmation.
#
# RBAC §10.4 makes the SWOT a Hiring-Manager-controlled field, so these routes
# authorise on EDIT_SWOT through the full RBAC chain (tenant, assignment,
# lifecycle state) rather than on CREATE_JOB. RBAC §9.4 names "job-role SWOT
# analysis" among the things a Recruiter "MUST NOT be able to authoritatively
# modify", and §11 is separately clear that the Hiring Manager cannot REJECT the
# JD -- Bodha handing a SWOT back for rework is a different act and is what
# §18.5 requires.


def _swot_out(job: Job, intake: JobSwotIntake, prompt: str | None) -> SwotIntakeOut:
    area = swot_intake.current_area(intake)
    quality = dict(intake.quality_json or {})
    return SwotIntakeOut(
        job_id=job.id,
        status=intake.status,
        complete=swot_intake.is_complete(intake),
        current_area=area,
        current_area_label=swot_intake.AREA_LABELS.get(area) if area else None,
        prompt=prompt,
        captured=intake.captured(),
        areas_total=len(SWOT_AREAS),
        areas_done=min(intake.area_index, len(SWOT_AREAS)),
        phase=intake.phase,
        phase_label=swot_intake.PHASE_LABELS.get(intake.phase),
        situation_key=intake.situation_key,
        situation_label=(
            situations.SITUATIONS[intake.situation_key].label
            if situations.is_valid(intake.situation_key)
            else None
        ),
        returned_for_rework=intake.phase == swot_intake.PHASE_REWORK,
        # The §18.5 rules currently refusing, by NAME. The sentence to say is
        # `prompt`; a screen that rendered both would say the same thing twice.
        outstanding_rules=[
            str(entry.get("rule"))
            for entry in (quality.get("rejections") or [])
            if entry.get("rule")
        ],
        instruments_asked=[str(key) for key in (intake.probes_asked or [])],
    )


@router.get("/jobs/{job_id}/swot", response_model=SwotIntakeOut)
async def get_swot_intake(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotIntakeOut:
    job = await _staff_job(session, user, job_id)
    intake = await swot_intake.get_or_create(session, job, conducted_by=user.user_id)
    prompt = await swot_intake.open_question(session, job, intake)
    return _swot_out(job, intake, prompt)


@router.post("/jobs/{job_id}/swot/respond", response_model=SwotIntakeOut)
async def respond_swot_intake(
    job_id: uuid.UUID,
    body: SwotAnswerIn,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotIntakeOut:
    """Record one answer and return the next question.

    CLOSING THE SESSION IS WHAT ACTIVATES SUTRA. §18.5's six rejection rules are
    the only exit: an intake that trips one is handed back with the sentence
    that says what is wanted, and nothing is enqueued. An intake that passes
    them publishes Bodha's `swot_evidence` artifact and enqueues the seven-stage
    compile.

    The compile is a CELERY TASK, never inline. It is a model call plus a dozen
    table lookups, and a hiring manager who has just finished a ninety-minute
    session should not watch it run.

    Regeneration is refused once the matrix is frozen, so a late intake cannot
    move the criteria underneath a report that already states a grade against
    them; `scorecard.compile_matrix` raises rather than overwriting.
    """
    job = await _staff_job(session, user, job_id)
    intake = await swot_intake.get_or_create(session, job, conducted_by=user.user_id)
    if swot_intake.is_complete(intake):
        return _swot_out(job, intake, None)

    try:
        prompt = await swot_intake.submit_answer(session, job, intake, body.answer)
    except pipeline_halt.PipelineHalted as halt:
        raise HTTPException(
            status_code=503, detail=pipeline_halt.http_detail(halt)
        ) from halt

    if swot_intake.is_complete(intake):
        row = await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="swot_session_completed",
            target_type="job",
            target_id=job.id,
            metadata={
                "situation_key": intake.situation_key,
                "captured": {
                    area: len(points) for area, points in intake.captured().items()
                },
                "instruments": list(intake.probes_asked or []),
                "best_performer_excluded": intake.best_performer_excluded,
            },
        )
        row.job_id = job.id
        row.actor_role = user.role.value
        row.correlation_id = job.correlation_id
        # RBAC §34: an AI-initiated mutation is attributable to BOTH the human
        # principal and the agent that executed it. `actor_user_id` stays the
        # human, always.
        row.agent_name = "bodha"
        if job.framework_approved_at is None:
            celery_app.send_task(
                "pickready.compile_tatva_matrix",
                args=[str(job.id)],
                kwargs={
                    "replace": True,
                    "correlation_id": job.correlation_id or "",
                },
            )
            await _invalidate_framework(job)
            logger.info(
                "assessments.swot_complete_matrix_enqueued job_id=%s situation=%s",
                job.id,
                intake.situation_key,
            )
    return _swot_out(job, intake, prompt)


# ── The PPI Assessment Report (spec §9) ──────────────────────────────────────


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
        # The same COMPANY-JOB-CANDIDATE code the candidate table shows under
        # the name, so a report and a table row can be matched up by eye.
        # Recomputed rather than joined: it is a pure function of three ids that
        # never change, so there is no stored value to disagree with.
        reference_code=reference_code.reference_code(
            link.tenant_id, link.job_id, link.candidate_id
        ),
        grade=report.grade,
        ai_score=grouped.get(CATEGORY_MATCHING, []),
        overall_grade=grade_for_percent(overall) or GRADES[-1],
        overall_summary=report.overall_summary,
        must_have=grouped.get(ppi.CATEGORY_MUST_HAVE, []),
        nice_to_have=grouped.get(ppi.CATEGORY_NICE_TO_HAVE, []),
        behavioural=grouped.get(ppi.CATEGORY_BEHAVIOURAL, []),
        # Empty on every report written from Draft v4 onward. A report written
        # before it still carries rows here and still renders them.
        technical=grouped.get(CATEGORY_TECHNICAL, []),
        validation=report.validation_json,
        gap_analysis=GapAnalysisOut.model_validate(report.gap_analysis_json or {}),
        # Populated only where Gap Analysis is not: a pre-Draft-v4 report shows
        # what it was actually written with rather than an empty section.
        suggested_interview_questions=(
            list(report.suggested_probes_json or [])
            if not (report.gap_analysis_json or {})
            else []
        ),
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
    tenant_name = (tenant.name if tenant else None) or "ReadyPick customer"
    # ReportLab is heavy and PDF downloads are infrequent; keep it off the API
    # startup path so ordinary requests do not pay its import cost.
    from app.services.report_pdf import render_report_pdf

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
                # USER-VISIBLE, so it follows the copy rename rather than the
                # code names. Everything else in this file still says `ppi`
                # deliberately (a route is quoted in links already sitting in
                # inboxes), but a downloaded file lands on somebody's desktop
                # under whatever we call it, and that name should match the
                # header printed inside it.
                f'attachment; filename="prism-report-{safe_name}.pdf"'
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
#: (28 base questions at non-managerial, plus follow-ups), so the common case is
#: one page. Left at 200 rather than lowered with the question counts: the bound
#: exists to stop an unbounded read, and a historic 45-question interview must
#: still page the same way.
TRANSCRIPT_MAX_LIMIT = 200
TRANSCRIPT_DEFAULT_LIMIT = 100


async def _criterion_labels(
    session: AsyncSession, link: JobCandidateLink
) -> dict[str, str]:
    """{question_key: human label} for every key this candidate could produce.

    The scorer keys on a row id, so the raw transcript is a wall of UUIDs. This
    resolves them in a fixed number of queries rather than one per exchange --
    the N+1 here would be 60 round trips on an ordinary interview.

    THREE key shapes, because a transcript outlives the design that wrote it:

      CandidateQuestion.id           what the unified conversation stamps today
      JobCompetency.id               what PPI questions were keyed on before
                                     Draft v4
      CandidateTechnicalQuestion.id  what the separate technical track was keyed
                                     on, for conversations that ran before it
                                     was folded into Must-have

    All three are resolved rather than only the current one. A recruiter opening
    a report written last month is the exact case this screen exists for, and an
    unresolved key renders as a UUID beside the answer it labels.
    """
    labels: dict[str, str] = {}
    competencies = (
        await session.execute(
            select(JobCompetency.id, JobCompetency.name).where(
                JobCompetency.job_id == link.job_id
            )
        )
    ).all()
    by_competency = {competency_id: name for competency_id, name in competencies}
    for competency_id, name in by_competency.items():
        labels[str(competency_id)] = name

    for question in await ppi_interview.load_for_link(session, link.id):
        name = by_competency.get(question.competency_id)
        if name:
            labels[str(question.id)] = name

    legacy = (
        await session.execute(
            select(CandidateTechnicalQuestion.id, CandidateTechnicalQuestion.skill).where(
                CandidateTechnicalQuestion.job_candidate_link_id == link.id
            )
        )
    ).all()
    for question_id, skill in legacy:
        labels[str(question_id)] = skill
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


# ── The assessment invitation link (2026-08-11) ──────────────────────────────
#
# The email now carries a signed token rather than a raw application id, and it
# lands on a PUBLIC page whose only job is to route. This endpoint is what that
# page asks. It is deliberately the one place that knows the order the checks
# run in, because the order is the whole design:
#
#   token validity -> is the application still real -> is anyone signed in ->
#   is it the RIGHT person -> is the posting window still open -> has this
#   already been submitted -> has the recruiter actually invited them
#
# Signature before world, identity before state. Checking "already submitted"
# before "wrong account" would tell a stranger holding the link whether a
# particular candidate had finished their assessment.

_INVITE_STATE_MESSAGES = {
    "expired": (
        "This assessment link has expired. Ask the hiring team to send a new "
        "invitation and it will work straight away."
    ),
    "invalid": (
        "This assessment link could not be read. It may have been broken by "
        "your email client, so try copying the whole address from the message."
    ),
    "window_closed": (
        "This job posting has closed, so the assessment is no longer open. "
        "Nothing you have already sent is lost."
    ),
    "not_invited": (
        "This assessment is not open to you yet. The hiring team invites "
        "candidates individually, and you will be emailed if they invite you."
    ),
    "needs_auth": (
        "Sign in to your candidate account to open this assessment."
    ),
    "ready": "Your assessment is ready.",
    "in_progress": (
        "You have already started this assessment. Your saved answers are "
        "still there and you will pick up where you left off."
    ),
    "completed": (
        "You have already submitted this assessment. There is nothing more to "
        "answer."
    ),
    "gone": (
        "That application is no longer available. If you believe this is a "
        "mistake, reply to the invitation email and the hiring team can check."
    ),
}


def _invite_out(
    state: str,
    *,
    redirect_to: str | None = None,
    message: str | None = None,
    **extra: Any,
) -> InvitationResolveOut:
    return InvitationResolveOut(
        state=state,
        redirect_to=redirect_to,
        message=message or _INVITE_STATE_MESSAGES.get(state, "This link could not be opened."),
        **extra,
    )


# Abuse control, not authorization (services/rate_limit). This endpoint is
# unauthenticated and does real database work per call. 30/min is far above
# anyone clicking a link from an email, and well below what probing for valid
# tokens would need -- which is the only other reason to call it in volume.
@router.get("/invitations/{token}", response_model=InvitationResolveOut,
    dependencies=[Depends(rate_limit("assessment_invitation", limit=30, window=60))],
)
async def resolve_invitation(
    token: str,
    user: CurrentUser | None = Depends(get_optional_candidate),
    session: AsyncSession = Depends(get_public_db),
) -> InvitationResolveOut:
    """Resolve an emailed assessment link into exactly one next step.

    Answers 200 for every outcome, including the refusals. That is deliberate:
    the caller is a landing page that must render a specific explanation for
    each state, and a 401/404/410 would collapse five different situations into
    "something went wrong" -- which is the generic-error failure this codebase
    keeps having to fix. The refusal is in the `state` field, and no state
    carries a `redirect_to` it has not earned.
    """
    try:
        payload = assessment_invite.verify(token)
    except assessment_invite.InviteTokenError as exc:
        return _invite_out(exc.reason)

    link_id = uuid.UUID(str(payload["link_id"]))
    invited_email = str(payload.get("email") or "")

    link = await session.get(JobCandidateLink, link_id)
    if link is None:
        return _invite_out("gone")
    job = await session.get(Job, link.job_id)
    if job is None:
        return _invite_out("gone")
    tenant = await session.get(Tenant, link.tenant_id)
    context = {
        "job_title": job.title,
        "company_name": tenant.name if tenant else None,
    }

    # Signed out. The page sends them to /login carrying this same URL as
    # `next`, which is the whole point of the change: the candidate comes back
    # HERE after signing in, not to the jobs board.
    if user is None:
        return _invite_out(
            "needs_auth",
            invited_email_masked=assessment_invite.mask_email(invited_email),
            **context,
        )

    account = await session.get(User, user.user_id)
    signed_in_email = account.email if account else None
    if not assessment_invite.emails_match(invited_email, signed_in_email):
        # Never silently attach the assessment to whoever happens to be signed
        # in. Both addresses are reported (one masked) because "wrong account"
        # with no way to tell which account is right is an unactionable error.
        return _invite_out(
            "wrong_account",
            invited_email_masked=assessment_invite.mask_email(invited_email),
            signed_in_email=signed_in_email,
            message=(
                "This invitation was sent to a different email address. Sign "
                "out and sign back in with the account it was sent to."
            ),
            **context,
        )

    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()

    # Already submitted wins over the window: a candidate who finished on the
    # last day should be shown their submission, not told the job has closed.
    if conversation is not None and conversation.completed_at is not None:
        return _invite_out(
            "completed",
            redirect_to=f"/portal/applications?application={link.id}",
            **context,
        )

    if not job_posting.can_edit_application(
        applied_at=link.created_at,
        posting_start=job.posting_start_date,
        posting_end_date=job.posting_end_date,
        grace_period_end_date=job.grace_period_end_date,
    ):
        return _invite_out("window_closed", **context)

    if conversation is None or conversation.invitation_sent_at is None:
        return _invite_out("not_invited", **context)

    # The six-month classification. Under PPI nothing is portable between jobs
    # (services/retake), so this never skips the assessment -- it only supplies
    # the sentence explaining why the candidate is answering questions again.
    recent_prior = False
    try:
        decision = await retake.decide(session, link.candidate_id, link.job_id)
        recent_prior = decision.decision == retake.DECISION_REUSE
    except Exception:  # pragma: no cover - classification is never load-bearing
        logger.exception("invitation.retake_classification_failed link_id=%s", link.id)

    started = conversation.started_at is not None
    return _invite_out(
        "in_progress" if started else "ready",
        redirect_to=f"/portal/assessments/{link.id}",
        recent_prior_report=recent_prior,
        **context,
    )


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


async def _dimension_coverage(
    session: AsyncSession, link: JobCandidateLink
) -> tuple[int, int]:
    """(matrix items with substantive evidence, matrix items probed at all).

    This is what Vaada's stopping rule reads. Two properties are doing the work.

    COUNTED PER COMPETENCY, NOT PER QUESTION. A matrix item can be probed by
    more than one question, and a follow-up is filed under its parent question's
    key. Counting questions would let three questions against one competency
    look like three covered dimensions, and the conversation would close having
    asked about a third of the matrix.

    ONLY A SUBSTANTIVE ANSWER COUNTS. `answer_label` is the classifier's own
    verdict (services/answer_classification), and empty, gibberish, off-topic and
    evasive replies are all excluded. This is the direction that matters: if a
    non-answer counted as coverage, a candidate could end their own assessment
    early by not answering, which is precisely backwards. A NULL label is
    treated as substantive, because every degradation path in the classifier
    returns "substantive" by design and a provider outage must not silently
    start withholding coverage.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT
                    COUNT(DISTINCT q.competency_id) FILTER (
                        WHERE m.id IS NOT NULL
                          AND COALESCE(m.answer_label, 'substantive') = 'substantive'
                    ) AS covered,
                    COUNT(DISTINCT q.competency_id) AS total
                FROM candidate_questions q
                LEFT JOIN assessment_messages m
                       ON m.question_key = CAST(q.id AS text)
                      AND m.speaker = 'candidate'
                WHERE q.job_candidate_link_id = :link_id
                """
            ),
            {"link_id": str(link.id)},
        )
    ).first()
    if row is None:
        return 0, 0
    return int(row[0] or 0), int(row[1] or 0)


async def _coverage_rows(
    session: AsyncSession, link: JobCandidateLink
) -> list[dict[str, Any]]:
    """Per-matrix-item evidence counts, for Vaada's explicit conversation state.

    A SECOND READ BESIDE `_dimension_coverage`, AND DELIBERATELY SO. That
    function is the stopping rule's own input and its exact SQL is pinned by
    `tests/test_conversation_key_contract.py` -- the join key, the DISTINCT
    competency count and the substantive filter are each pinned because getting
    any of them wrong grades every candidate on the job Not Matching with no
    error anywhere. Folding it into this richer read would put the product's
    only comparability guarantee behind a refactor whose breakage is silent.
    Two small indexed aggregates on one turn is the cheaper mistake.

    Grouped by competency, never by question: several questions can probe one
    matrix item and a follow-up is filed under its parent's key, so counting
    questions would let a third of the matrix look like full coverage.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT c.name AS dimension,
                       COUNT(m.id) AS answers,
                       COUNT(m.id) FILTER (
                           WHERE COALESCE(m.answer_label, 'substantive') = 'substantive'
                       ) AS substantive,
                       COUNT(m.id) FILTER (WHERE m.evidence_gap) AS gaps
                  FROM candidate_questions q
                  JOIN job_competencies c ON c.id = q.competency_id
                  LEFT JOIN assessment_messages m
                         ON m.question_key = CAST(q.id AS text)
                        AND m.speaker = 'candidate'
                 WHERE q.job_candidate_link_id = :link_id
                 GROUP BY c.id, c.name, c.category, c.ordinal
                 ORDER BY c.category, c.ordinal
                """
            ),
            {"link_id": str(link.id)},
        )
    ).all()
    return [
        {
            "dimension": str(row[0]),
            "answers": int(row[1] or 0),
            "substantive": int(row[2] or 0),
            "gaps": int(row[3] or 0),
        }
        for row in rows
    ]


async def _ledger_dimension_flags(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> tuple[set[str], set[str]]:
    """(dimensions the ledger contradicts, dimensions it only inferred).

    THE MITI SIDE OF THE LOOP. Whether two readings of a dimension disagree is a
    question about the evidence ledger, not about the transcript, so it is read
    from the ledger rather than guessed at from what the candidate typed. That
    is what makes this a loop rather than two agents talking past each other:
    Miti records what it read, and Vaada asks about what does not add up while
    there is still a candidate to ask.

    NEVER LOAD-BEARING. A ledger that is unavailable, empty, or has not been
    written for this application yet returns two empty sets, and the
    conversation behaves exactly as it did before the ledger existed. The
    opposite direction would be far worse: a ledger outage that made every
    conversation refuse to close would strand every candidate in the product
    mid-assessment.
    """
    from app.services.evidence import ledger

    try:
        claims = await ledger.load_claims(
            session, tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
        )
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.info("assessments.evidence_unavailable link_id=%s", link.id)
        return set(), set()
    conflicting = {
        claim.dimension
        for claim in claims
        if claim.status == ledger.CLAIM_CONTRADICTED
    }
    # `inferred_only` is the product agreeing with itself: everything behind the
    # dimension was concluded rather than stated or confirmed. That is exactly
    # the shape a conversation can fix, by asking.
    inferred = {
        claim.dimension
        for claim in claims
        if claim.status == ledger.CLAIM_INFERRED_ONLY
    }
    return conflicting, inferred


async def _conversation_state(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    total_written: int,
) -> "interviewer.ConversationState":
    """Where this conversation has got to, as one explicit value.

    INTERNAL ONLY. It is logged for an operator and read by the stop decision;
    no field of it reaches a response schema, and the no-numbers rule covers its
    counts exactly as it covers a score.
    """
    rows = await _coverage_rows(session, link)
    conflicting, inferred = await _ledger_dimension_flags(session, job, link)
    return interviewer.conversation_state(
        dimensions=[
            interviewer.DimensionEvidence(
                dimension=row["dimension"],
                answers=row["answers"],
                substantive=row["substantive"],
                gaps=row["gaps"],
                conflicting=row["dimension"] in conflicting,
                weak=row["dimension"] in inferred,
            )
            for row in rows
        ],
        asked=conversation.next_question_index,
        total_written=total_written,
        floor=ppi.min_questions(job.assessment_grade),
        probe_outstanding=conversation.pending_prompt is not None,
    )


async def _conversation_prompts(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> list[tuple[str, str, str]]:
    """This candidate's questions, in one sequence (spec §7).

    ONE LIST, from ONE table. Until Draft v4 this function round-robin
    interleaved two: a technical slot list and a PPI question list, produced by
    two generators and consumed by two scorers. There is one PPI matrix now and
    technical depth lives inside its Must-have items, so there is one stream and
    no seam to hide -- the candidate never interacts with separate technical and
    behavioural bots and never sees the scoring methods behind the conversation.

    The order is the matrix's own: Must-have, Nice-to-have, Behavioural, which
    is `ppi.generate_candidate_questions`' allocation order, held in `ordinal`.
    That is deterministic per job, which is what keeps two candidates' reports
    comparable.

    `question_key` carries the JobCompetency id, because that is what the scorer
    files an answer under. The DOMAIN carries the aspect, so the recruiter's
    transcript view can say which part of the matrix an exchange belonged to
    without the candidate ever having been told.
    """
    rows = (
        await session.execute(
            select(CandidateQuestion, JobCompetency)
            .join(JobCompetency, JobCompetency.id == CandidateQuestion.competency_id)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).all()

    # Returned BARE. The conversational join between one question and the next
    # is written per turn by the question writer against the real transcript, so
    # it can only claim a connection that actually exists. Anything canned here
    # would be prepended before the candidate has said anything for it to
    # respond to.
    return [
        (competency.category, str(question.id), question.prompt)
        for question, competency in rows
    ]


async def _ensure_conversation_ready(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> None:
    """Transient prep, never a human gate.

    There used to be two halves here and an asymmetry worth explaining: the
    technical slots were created inline because they came from a pure function
    of the JD, while the PPI questions went to Celery because they were a model
    call. Draft v4 left one half. Every question is generated per candidate
    against the saved matrix, so the whole preparation is a model call, it stays
    in Celery (CLAUDE.md rule 4), and the candidate is asked to retry in a
    moment rather than made to wait on a request.
    """
    has_questions = (
        await session.execute(
            select(func.count())
            .select_from(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
        )
    ).scalar_one()
    if has_questions:
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
        answered_questions=index,
        total_questions=len(prompts),
        is_reask=conversation.pending_kind == "reask",
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
    """Write the question at `index` for THIS candidate at THIS point.

    ONE writer for all three aspects (`ppi_interview.write_question`), and the
    method it uses inside varies by aspect rather than by caller: a Must-have or
    Nice-to-have question is written together with the rubric its answer will be
    graded against, and a Behavioural question is written without one because
    there is no single correct answer to weigh a behavioural account against.

    Degrades to the stored text, which is always a correct thing to ask: it is
    the question `ppi.generate_candidate_questions` already wrote for this item
    from this candidate's own resume.
    """
    _aspect, key, stored = prompts[index]
    # Read AFTER the caller's flush so the turn just written is part of the
    # memory this question is conditioned on. Reading a stale transcript would
    # have the interviewer talk as though the last answer had not been given.
    memory = await _transcript_rows(session, conversation.id)
    asked_before = [row["content"] for row in memory if row.get("speaker") == "agent"]
    resume = await _resume_excerpt(session, link)

    row = None
    try:
        row = await session.get(CandidateQuestion, uuid.UUID(str(key)))
    except (ValueError, TypeError):
        row = None
    competency = (
        await session.get(JobCompetency, row.competency_id) if row is not None else None
    )
    if row is None or competency is None:
        # The row vanished under us, which should be impossible. Showing the
        # stored text is the honest degradation; refusing the turn would cost
        # the candidate their assessment over a bookkeeping fault.
        logger.info("assessments.question_row_missing key=%s", key)
        written = stored
    else:
        result = await ppi_interview.write_question(
            session=session,
            job=job,
            row=row,
            competency=competency,
            resume_excerpt=resume,
            transcript=memory,
            asked_before=asked_before,
        )
        written = result.value["question"]
        # LAST CHECK BEFORE THE CANDIDATE READS IT, and deterministic. The
        # writer is told what has already been asked and usually respects it;
        # "usually" is not a guarantee, and the one turn it gets wrong is the
        # turn the candidate notices. Falling back to the stored text costs
        # nothing here: it is the question already written for this item from
        # this candidate's own resume, and it is by construction not one of the
        # lines above.
        if interviewer.is_semantic_repeat(written, asked_before):
            logger.info(
                "assessments.question_rejected reason=repeat conversation_id=%s",
                conversation.id,
            )
            written = stored

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
    # Rows created before 0045 have NULL here; every historical pending prompt
    # was a probe, so that is the safe compatibility meaning.
    pending_kind = (conversation.pending_kind or "probe") if pending else None
    answering_reask = pending_kind == "reask"
    answering_probe = pending_kind == "probe"
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
        conversation.pending_kind = None
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
    candidate_message = AssessmentMessage(
        tenant_id=job.tenant_id,
        conversation_id=conversation.id,
        ordinal=ordinal + 2,
        speaker="candidate",
        domain=domain,
        question_key=key,
        content=answer_text,
    )
    session.add_all([
        AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 1, speaker="agent", domain=domain, question_key=key, content=prompt),
        candidate_message,
    ])
    await session.flush()

    # A probe is supplemental evidence for an already accepted base answer, so
    # it never advances the base counter. A base answer and a relevance re-ask
    # are classified: only an accepted one advances the counter.
    if not answering_probe:
        transcript = await _transcript_rows(session, conversation.id)
        reaction = None
        action = "advanced"
        answer_label = "substantive"
        degraded = False
        needs_rechallenge = False

        if not guard.allowed:
            # A refused turn is still transcribed above: the record of what was
            # said is what a dispute is settled from. Classifying it would spend
            # a model call grading an attack, so this short-circuits to the same
            # re-ask mechanism a non-answer uses -- same question_key, no
            # follow-up budget, bounded to one per question.
            answer_label = f"guarded_{guard.violation}"
            needs_rechallenge = True
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
            needs_rechallenge = verdict.needs_rechallenge

        candidate_message.answer_label = answer_label

        # Re-asks are bounded per base question. The counter stays still while
        # one is outstanding. Once the cap is exhausted the answer remains in
        # the transcript with evidence_gap=true and the interview moves on, so
        # a candidate cannot be trapped in an infinite loop.
        if (
            needs_rechallenge
            and conversation.reasks_used < MAX_REASKS_PER_QUESTION
        ):
            if not guard.allowed:
                reaction = guard.candidate_message
            else:
                reaction = await interviewer.challenge_non_answer(
                    session=session,
                    question=prompt,
                    answer=answer_text,
                    transcript=transcript,
                    label=answer_label,
                )
            if reaction:
                conversation.reasks_used += 1
                action = "rechallenged"

        if reaction is None:
            # Either the answer was relevant, the re-ask cap was reached, or a
            # challenge could not be composed. Every one of those outcomes
            # closes exactly one base slot. Invalid outcomes are explicit gaps.
            conversation.next_question_index += 1
            conversation.reasks_used = 0
            if needs_rechallenge:
                candidate_message.evidence_gap = True
                action = "advanced_with_gap"
            elif not answering_reask:
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
                    # Every interviewer line so far, so a probe that merely
                    # re-asks something already asked is dropped rather than
                    # spending a scarce budget to prove nobody was listening.
                    # The check inside is deterministic: it matters most during
                    # an outage, which is when a model cannot answer it.
                    asked_before=[
                        row["content"]
                        for row in transcript
                        if row.get("speaker") == "agent"
                    ],
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
            conversation.pending_kind = (
                "reask" if action == "rechallenged" else "probe"
            )

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

    # Vaada decides WHERE IN SUTRA'S RANGE this conversation ends.
    #
    # Two ways to finish, and they are not the same event. The first is running
    # out of written questions, which is what this has always done. The second
    # is new on 2026-08-23 and is the specification's own stopping rule: the
    # conversation ends "when Vaada determines sufficient evidence has been
    # gathered across all matrix dimensions". Sutra fixes the RANGE per job so
    # two candidates stay comparable; the candidate's own answer depth decides
    # where inside it they stop.
    #
    # The floor inside `conversation_may_close` is the load-bearing half. Without
    # it a fluent candidate is assessed on fewer criteria than a hesitant one and
    # the two reports stop being comparable, which is the one thing the matrix
    # exists to guarantee. And a dimension is only "covered" by a SUBSTANTIVE
    # answer: an evasive reply is not evidence, and treating it as coverage would
    # let a candidate shorten their own assessment by not answering.
    #
    # A pending follow-up still holds completion open, exactly as before. The
    # candidate is mid-sentence; charging the customer and dispatching scoring
    # there would score an assessment that is still being written.
    evidence_complete = False
    if not conversation.pending_prompt:
        covered, total_dimensions = await _dimension_coverage(session, link)
        # The explicit state, beside the tuple the stopping rule reads. It is
        # what turns "18 of 20 covered" into something an operator can act on:
        # which dimensions are unprobed, which produced nothing usable, and
        # which the evidence ledger already holds two readings of.
        coverage = await _conversation_state(
            session, job, link, conversation, len(prompts)
        )
        logger.info(
            "assessments.conversation_state conversation_id=%s state=%s",
            conversation.id, coverage.as_log(),
        )
        evidence_complete = ppi.conversation_may_close(
            grade=job.assessment_grade,
            asked=conversation.next_question_index,
            total_written=len(prompts),
            covered_dimensions=covered,
            total_dimensions=total_dimensions,
        ) and (
            # AN ADDITIONAL CONDITION, NEVER A REPLACEMENT.
            # `conversation_may_close` stays the only thing that decides whether
            # an assessment may end early, floor included; this can only ever
            # make it stricter. A MATERIAL contradiction obliges `ask_follow_up`
            # while a conversation is still running, and stopping with one
            # outstanding throws away the only chance anyone will get to ask.
            # Completion by exhausting the written questions is untouched below,
            # so this can never strand a candidate in an endless interview.
            interviewer.STOP_NO_CONFLICT_OUTSTANDING in coverage.stop_conditions
        )

    if (
        conversation.next_question_index >= len(prompts) or evidence_complete
    ) and not conversation.pending_prompt:
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
    if (
        not conversation.pending_prompt
        and next_index < len(prompts)
        and conversation.completed_at is None
    ):
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
        answered_questions=next_index,
        total_questions=len(prompts),
        is_reask=conversation.pending_kind == "reask",
        answer_message_id=candidate_message.id,
    )


@router.patch(
    "/conversations/{conversation_id}/answers/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def edit_latest_answer(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: ConversationAnswerEditIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> Response:
    """Edit the most recent saved answer while the assessment is active.

    Earlier answers are already inputs to later adaptive questions, so changing
    one would make the transcript disagree with the conversation that occurred.
    The latest answer is safe to correct, but it must still pass the same
    relevance guard that allowed the counter to advance.
    """
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Completed assessment responses are permanent and cannot be edited.",
        )
    await _candidate_link(session, user, conversation.job_candidate_link_id)
    message = await session.get(AssessmentMessage, message_id)
    if (
        message is None
        or message.conversation_id != conversation.id
        or message.speaker != "candidate"
    ):
        raise HTTPException(status_code=404, detail="Response not found")
    latest_id = (
        await session.execute(
            select(AssessmentMessage.id)
            .where(
                AssessmentMessage.conversation_id == conversation.id,
                AssessmentMessage.speaker == "candidate",
            )
            .order_by(AssessmentMessage.ordinal.desc())
            .limit(1)
        )
    ).scalar_one()
    if latest_id != message.id:
        raise HTTPException(
            status_code=409,
            detail="Only your most recent response can be edited.",
        )

    guard = conversation_guardrails.inspect_answer(body.answer)
    edited = guard.sanitized.strip()
    if not guard.allowed:
        raise HTTPException(status_code=422, detail=guard.candidate_message)
    question = (
        await session.execute(
            select(AssessmentMessage.content).where(
                AssessmentMessage.conversation_id == conversation.id,
                AssessmentMessage.speaker == "agent",
                AssessmentMessage.ordinal == message.ordinal - 1,
            )
        )
    ).scalar_one_or_none()
    verdict = await answer_classification.classify(
        session=session,
        question=question or "",
        answer=edited,
        transcript=await _transcript_rows(session, conversation.id),
    )
    if verdict.needs_rechallenge:
        detail = {
            "gibberish": "That edit did not come through as an answer.",
            "empty": "Please enter an answer before saving.",
            "off_topic": "That edit answers a different question.",
            "shallow": "That edit still needs the specific example or detail requested.",
            "evasive": "That edit still does not address the specific question.",
        }.get(verdict.label, "That edit does not answer the question.")
        raise HTTPException(status_code=422, detail=detail)

    message.content = edited
    message.answer_label = verdict.label
    message.evidence_gap = False
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
