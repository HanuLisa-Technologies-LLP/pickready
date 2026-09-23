"""Job setup review, the unified conversation, and the PPI Assessment Report."""
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_tenant_db,
    require_capability,
)
from app.models.assessment import (
    AssessmentAnswer,
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
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.models.job_setup import (
    SWOT_ANALYSIS_SECTIONS,
    JobSwotAnalysis,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.assessments import (
    BulkCompetencyIn,
    CompetencyIn,
    CompetencyOut,
    ClaimEvidenceOut,
    DimensionOut,
    FrameworkOut,
    FunctionalReportOut,
    GapAnalysisOut,
    JobSetupOut,
    MatrixReorderIn,
    RadarChartOut,
    SwotAnalysisGenerateIn,
    SwotAnalysisOut,
    SwotAnalysisSectionsIn,
    TranscriptAnswerDetailOut,
    TranscriptExchangeOut,
    TranscriptOut,
    ValidationPointsOut,
)
# The proctored session's recording start. It lives beside the client-portal
# video schemas rather than with the assessment ones because it is the
# candidate-side half of the SAME artifact those schemas deliver, and both
# halves are bound by the same rule: no bucket name and no object key.
from app.services import capabilities as caps
from app.services import rbac
from app.services.assessment_questions import budget as question_budget
from app.services.hiring import pipeline_halt, scorecard
from app.services import (
    evidence_confidence,
    hiring_pipeline,
    job_assessment_retention,
    ppi,
    ppi_interview,
    reference_code,
    retention_consent,
    swot_analysis,
    job_version,
    tenant_cache,
)
# DUAL-MODE ASSESSMENT (2026-09-05 spec). The video package is reached ONLY by
# these routes and the processing task, never by a scorer; the models carry the
# consent audit and the recording lifecycle.
from app.services.assessment_formats import rendering as format_rendering
from app.services.assessment_formats import scoring as format_scoring
from app.services.assessment_formats import types as question_types
from app.services.audit import audit, record_action, record_agent_action
# PROCTORING IS MANDATORY (proctoring-spec-doc.md, principle P4). The gate is
# this module's only import-time dependency on the proctoring package; the
# behaviour recorder and the report loader are reached inside the handlers
# that need them, so the scoring isolation the package promises stays
# visible in the import graph as gate-plus-report-join and nothing else.
from app.services.functional_assessment import (
    CATEGORY_MATCHING,
    CATEGORY_TECHNICAL,
    RADAR_BANDS,
    RADAR_SERIES,
    build_radar_charts,
    rating_label,
)
from app.services.rating import GRADES, grade_for_percent
from app.workers.dispatch import dispatch

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


def _setup_out(
    job: Job, *, framework_pending: bool = False, swot_analysis_ready: bool = False
) -> JobSetupOut:
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
        swot_analysis_ready=swot_analysis_ready,
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

    Sutra now reads the saved Job SWOT document. A historic intake completion
    stamp is not evidence that the document exists, so check the actual row.
    """
    rows = await ppi.load_framework(session, job.id)
    if rows:
        return False
    # A MATRIX A HUMAN EMPTIED IS NOT A MATRIX THAT WAS NEVER WRITTEN, and the
    # ACTIVE rows alone cannot tell them apart -- deletion here is soft, so both
    # states read as zero. Asked of the active rows only, this told a hiring
    # manager who had just cleared the generated items "we are still preparing
    # the evaluation criteria ... refresh the page shortly" and re-enqueued
    # Sutra, which would have put the items they had deliberately removed back.
    # One row of any kind is proof the generator landed, so from here the real
    # blocker -- "Must-have has no items" -- is what they are told.
    ever = (
        await session.execute(
            select(JobCompetency.id).where(JobCompetency.job_id == job.id).limit(1)
        )
    ).scalars().first()
    if ever is not None:
        return False
    analysis = await swot_analysis.get(session, job)
    if analysis is None or not analysis.has_content:
        return False
    dispatch(
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
    analysis = await swot_analysis.get(session, job)
    return _setup_out(
        job,
        framework_pending=await _framework_repair_pending(session, job),
        swot_analysis_ready=bool(analysis and analysis.has_content),
    )


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
    ok, reason = ppi.matrix_is_complete(
        rows, job.assessment_grade, job.role_classification
    )
    if not rows and pending:
        reason = FRAMEWORK_PREPARING
    return FrameworkOut(
        job_id=job.id,
        status=job.assessment_status,
        approved=job.framework_approved_at is not None,
        competencies=[_competency_out(row) for row in rows],
        maximum_items=question_budget.max_questions(job.assessment_grade, job.role_classification),
        # Computed from what the matrix holds RIGHT NOW rather than read from
        # `job.question_target`, which is stamped at generation. The Hiring
        # Manager is mid-edit on this screen and needs to see what the matrix in
        # front of them would cost a candidate, not what the generated one did.
        question_target=question_budget.resolve_question_target(
            job.assessment_grade, len(rows), job.role_classification
        ),
        question_range=list(
            question_budget.resolve_question_range(
                job.assessment_grade, len(rows), job.role_classification
            )
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


# ── Adding a name back that was removed ─────────────────────────────────────
#
# DELETION HERE IS SOFT AND THE UNIQUE CONSTRAINT IS NOT.
#
# `remove_competency` sets `is_active = False` rather than issuing a DELETE,
# because a generated candidate question may already reference the row.
# `uq_job_competency_name` is on (job_id, category, name) with NO predicate, so
# a name the recruiter removed a minute ago still occupies its slot. Inserting
# it again therefore raised `UniqueViolationError` and the route answered 500.
#
# Measured in pilot on 2026-09-20: a hiring manager cleared the six generated
# Must-have items, pasted their own five, and got "API error 500" twice with
# nothing on the screen explaining it. The paste is the product's normal way in
# and the deleted rows were invisible, so from their seat the form simply did
# not work. The bulk route had NO test of any kind, which is why it shipped.
#
# The row is REVIVED instead. And a name already sitting in the aspect is
# returned UNTOUCHED rather than refused: a paste of thirty skills where two
# are already present must not discard the other twenty-eight, and the caller's
# intent -- "this aspect should contain these names" -- is already satisfied
# for those two. Nothing is silently dropped, because every requested name is
# in the response and in the matrix afterwards; what is deliberately NOT done
# is overwrite an existing item's required level from a paste, since that would
# quietly restate a criterion the reviewer had already set.


async def _rows_by_name(
    session: AsyncSession, job_id: uuid.UUID, category: str, names: list[str]
) -> dict[str, JobCompetency]:
    """Every row holding one of these names in this aspect, ACTIVE OR NOT."""
    if not names:
        return {}
    rows = (
        await session.execute(
            select(JobCompetency).where(
                JobCompetency.job_id == job_id,
                JobCompetency.category == category,
                JobCompetency.name.in_(names),
            )
        )
    ).scalars().all()
    return {row.name: row for row in rows}


def _revive(
    row: JobCompetency,
    *,
    description: str | None,
    required_level: int,
    ordinal: int,
) -> JobCompetency:
    """Bring a soft-deleted row back as the entry the caller just asked for.

    The stages Sutra derived (`dimension`, `weight`, `provenance_json`, ...) are
    deliberately left ALONE. They describe a criterion the pipeline derived, and
    the name coming back is the same name; clearing them would turn a revived
    item into one with no provenance, and inventing new ones would claim a
    derivation that did not run.
    """
    row.is_active = True
    row.description = description
    row.required_level = required_level
    row.ordinal = ordinal
    row.updated_at = datetime.now(timezone.utc)
    return row


def _invalidate_derived_criterion(row: JobCompetency) -> None:
    """A changed human decision needs fresh technical evidence at Save Matrix.

    The draft remains editable and readable. Clearing the derived fields makes
    stale evidence impossible to mistake for evidence of the new name/category.
    Save Matrix derives them in the same transaction as the freeze.

    `swot_origin` IS CLEARED HERE, AND IT IS THE ONE THAT WOULD HAVE LIED.
    It carries the reporting authority's own SWOT sentence, and
    `scorecard.plain_provenance` renders it to the reviewer verbatim as
    `You said: "..."`. It is NOT cleared by `_revive`, and the difference is
    the whole point: a revived row is the SAME name coming back, so the
    sentence still refers to it. A rename is a different criterion wearing the
    row's identity, and carrying the sentence across would attribute
    "Kubernetes operations" to a criterion now called "Incident command" --
    a fabricated citation on the one screen this whole contract exists to make
    trustworthy. Enrichment re-derives from the human's name with no SWOT
    anchor, which is an honest absence rather than a borrowed one.
    """
    row.dimension = None
    row.observable_evidence = None
    row.evidence_sources = None
    row.assessment_method = None
    row.weight = None
    row.threshold_json = None
    row.disqualifier = None
    row.provenance_json = None
    row.anchor_key = None
    row.force_rank = None
    row.swot_origin = None
    # `description` IS THE MIRROR OF `observable_evidence`, so it goes with it.
    #
    # The 2026-09-20 ruling removed the description input from the add and the
    # edit controls, and the edit route echoes the STORED value back so that a
    # field missing from a form is not a field erased from the record. That
    # protects against erasure caused by the form. It does not make the text
    # survive a change of identity: what a competency MEANS is derived from its
    # name, so the sentence describing "Kubernetes operations" is not a
    # description of "Incident command". Leaving the mirror populated while its
    # source is NULL is two fields that must agree, disagreeing, and the one a
    # reviewer actually reads on the card is the stale one. Enrichment writes
    # both again from the human's name at Save Matrix.
    row.description = None


async def _next_ordinal(
    session: AsyncSession, job_id: uuid.UUID, category: str
) -> int:
    return (
        await session.execute(
            select(func.coalesce(func.max(JobCompetency.ordinal), 0)).where(
                JobCompetency.job_id == job_id, JobCompetency.category == category
            )
        )
    ).scalar_one() + 1


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
    existing = (
        await _rows_by_name(session, job.id, body.category, [body.name])
    ).get(body.name)
    if existing is not None and existing.is_active:
        # Already exactly what was asked for. See `_rows_by_name`.
        return _competency_out(existing)
    ordinal = await _next_ordinal(session, job.id, body.category)
    if existing is not None:
        row = _revive(
            existing,
            description=body.description,
            required_level=ppi.required_level_score(body.required_level),
            ordinal=ordinal,
        )
    else:
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
    level = ppi.required_level_score(body.required_level)
    existing = await _rows_by_name(session, job.id, body.category, names)
    ordinal = await _next_ordinal(session, job.id, body.category) - 1
    rows: list[JobCompetency] = []
    for name in names:
        found = existing.get(name)
        if found is not None and found.is_active:
            # Already in this aspect. Returned so the caller sees the whole
            # requested set, and left untouched so a paste cannot restate a
            # level the reviewer set deliberately. See `_rows_by_name`.
            rows.append(found)
            continue
        ordinal += 1
        if found is not None:
            rows.append(
                _revive(found, description=None, required_level=level, ordinal=ordinal)
            )
            continue
        fresh = JobCompetency(
            tenant_id=job.tenant_id,
            job_id=job.id,
            category=body.category,
            name=name,
            required_level=level,
            ordinal=ordinal,
        )
        session.add(fresh)
        rows.append(fresh)
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
    # The same slot `_rows_by_name` exists for: renaming an item onto a name
    # that aspect already holds -- including one only soft-deleted, which the
    # reviewer cannot see -- violates `uq_job_competency_name`. Refused with the
    # reason rather than left to surface as a 500.
    clash = (await _rows_by_name(session, job.id, body.category, [body.name])).get(
        body.name
    )
    if clash is not None and clash.id != row.id:
        raise HTTPException(
            status_code=409,
            detail=(
                f'"{body.name}" is already an entry under '
                f"{ppi.CATEGORY_LABELS[body.category]} on this job."
            ),
        )
    # The identity question is asked BEFORE the assignment, and the
    # invalidation happens AFTER it, so the edit form's own echo of the stored
    # description cannot survive a rename it no longer describes.
    reidentified = row.name != body.name or row.category != body.category
    row.category = body.category
    row.name = body.name
    row.description = body.description
    row.required_level = ppi.required_level_score(body.required_level)
    if reidentified:
        _invalidate_derived_criterion(row)
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
    the user who finalized it, the timestamp and the relevant criteria version.
    All three are written -- two onto the job, one onto the append-only
    scorecard binding -- and then again onto the audit row, because the columns
    answer "what is in force" and the audit row answers "what happened and who
    did it".

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
    # Every column in the one INSERT: the application role has no UPDATE grant
    # on audit_log, so a post-flush attribute write aborts the transaction at
    # commit, after the response has already left (the SWOT false-409 bug).
    await record_action(
        session,
        action="role_definition_finalized",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        new_state={"lifecycle_state": job.lifecycle_state},
        metadata={
            "counts": {
                category: sum(1 for item in rows if item.category == category)
                for category in ppi.CATEGORIES
            },
            # RBAC §20's required facts, in the row as well as the columns.
            "jd_version": job_version.jd_version(job),
            "criteria_version": matrix.version,
            "situation_key": matrix.situation_key,
        },
    )
    await _invalidate_framework(job)
    logger.info(
        "assessments.role_definition_finalized job_id=%s by=%s criteria_version=%d "
        "correlation_id=%s",
        job.id,
        user.user_id,
        matrix.version,
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

    REFUSED ONCE AN ASSESSMENT CONTRACT HAS BEEN ISSUED, AND NOT BEFORE.

    The test is whether any candidate on this job has been INVITED, which is
    what an `assessment_conversations` row is, or has had per-candidate
    questions written against the frozen matrix. Either one means a person is
    being measured against these exact criteria, and a report is immutable, so
    changing the criteria underneath them would make two reports on one job
    incomparable. That is the one property the freeze exists to guarantee.

    IT IS DELIBERATELY NOT "ANY LINKED CANDIDATE". Applying is not being
    assessed: a `job_candidate_links` row is created by an application, a
    sourced upload or a databank import, none of which reads a competency. A
    guard on the link would stop a hiring manager correcting a typo the moment
    the first CV arrives, on a job nobody has been invited to, and there is no
    reopen after that. Somebody who applies before a revision and is invited
    after it is assessed against the revision, which is the currently approved
    contract and the only one that was ever used on them.

    AND IT IS DELIBERATELY NOT "ANY REPORT", WHICH IS WHAT IT USED TO BE. A
    report exists only at the END of an assessment, so between invitation and
    synthesis the matrix was reopenable underneath a candidate who was already
    answering questions derived from it. Their questions came from one version
    and their grade would have been written against another, with nothing
    recording that it had happened.
    """
    job = await _staff_job(session, user, job_id)
    contracted = (
        await session.execute(
            select(
                select(func.count())
                .select_from(AssessmentConversation)
                .where(AssessmentConversation.job_id == job.id)
                .scalar_subquery()
                + select(func.count())
                .select_from(CandidateQuestion)
                .where(CandidateQuestion.job_id == job.id)
                .scalar_subquery()
            )
        )
    ).scalar_one()
    if contracted:
        raise HTTPException(
            status_code=409,
            detail=(
                "Candidates on this job have already been invited to an "
                "assessment against these criteria, so the matrix can no "
                "longer be changed. A report states a grade against the exact "
                "criteria it was written from and is never rewritten."
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
                _invalidate_derived_criterion(row)
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


# ── The AI-assisted Job SWOT Analysis (2026-09-13 spec, sections 23 to 33) ──
#
# FOUR ROUTES, ONE AUTHORIZATION MODEL, NO NEW PERMISSION SYSTEM (section 27)
# ---------------------------------------------------------------------------
# Reading takes `view_company_jobs`, which is what every other read of a job
# already takes. Writing takes `edit_swot`, which RBAC 24 already defines as a
# Hiring-Manager-controlled field and which the intake routes above already
# use. Both go through `rbac.require_authorized`, so the whole RBAC 3 chain
# runs on every one of them: tenant, then the 24 ceiling, then the grant, then
# assignment scope, then lifecycle state. There is deliberately no SWOT-only
# authorization anywhere in this block.
#
# THE GET IS SEPARATELY AUTHORIZED FROM THE WRITES, AND THAT IS THE FEATURE
# --------------------------------------------------------------------------
# Section 27 asks for a real VIEW / EDIT split: a user with view access sees
# the SWOT and no edit controls, and a user with edit access sees the controls
# and no read-only notice. Two different capabilities on two different routes
# is what makes that split true at the API rather than only in the interface.
#
# WHY THE READ USES `require_capability` AND THE WRITES USE `require_authorized`
# ------------------------------------------------------------------------------
# `require_authorized` adds resource SCOPE to the capability question, and for
# three of the five client roles RBAC 24 marks `view_company_jobs` SCOPED,
# meaning "the jobs you are assigned to". This product does not yet write
# `job_assignments` rows from any workflow, so a scope check on the READ would
# refuse a Recruiter the SWOT of a job whose JD they are looking at on the same
# page, which is a stricter answer than the job itself gives and a worse one
# than no answer.
#
# So the read asks the flat question every sibling read in this router asks
# ("may this person work on jobs at all"), with the tenant boundary enforced by
# RLS and by `_staff_job`. The WRITES keep the full chain, because a wrong
# answer there is a wrong answer in the direction that matters: it changes the
# document. The day assignments are written, the read can tighten to the same
# gate with no other change; the writes will already be correct.


async def _swot_analysis_out(
    session: AsyncSession,
    user: CurrentUser,
    job: Job,
    row: JobSwotAnalysis,
) -> SwotAnalysisOut:
    """Serialize one analysis, resolving `can_edit` on this job for this user.

    The answer is computed with the SAME call the write routes enforce with
    (`rbac.authorize` over `edit_swot` and this job's resource facts), which is
    what stops the interface and the API disagreeing: there is one rule, asked
    twice, not two rules that happen to match today.
    """
    editor_name = None
    if row.last_modified_by is not None:
        editor = await session.get(User, row.last_modified_by)
        editor_name = editor.full_name if editor is not None else None

    resource = await rbac.load_job_resource(session, job.id)
    decision = await rbac.authorize(
        session,
        rbac.Principal(
            user_id=user.user_id, tenant_id=user.tenant_id, role=user.role
        ),
        caps.EDIT_SWOT,
        resource,
    )

    previous = dict(row.previous_json or {})
    return SwotAnalysisOut(
        job_id=job.id,
        status=row.status,
        strengths=row.strengths,
        weaknesses=row.weaknesses,
        opportunities=row.opportunities,
        threats=row.threats,
        generated_by=row.generated_by,
        last_generated_at=row.last_generated_at,
        generation_error=row.generation_error,
        human_edited=row.human_edited,
        last_modified_at=row.last_modified_at,
        last_modified_by_name=editor_name,
        version=row.version,
        can_restore_previous=any(
            str(previous.get(name) or "").strip()
            for name in SWOT_ANALYSIS_SECTIONS
        ),
        can_edit=decision.allowed,
    )


async def _enqueue_matrix_from_swot(session: AsyncSession, job: Job, row: JobSwotAnalysis) -> None:
    """Start Sutra when a saved SWOT exists and no matrix has been written."""
    if not row.has_content or job.framework_approved_at is not None:
        return
    if await ppi.load_framework(session, job.id):
        return
    dispatch(
        "pickready.compile_tatva_matrix",
        args=[str(job.id)],
        kwargs={"correlation_id": job.correlation_id or ""},
    )


@router.get("/jobs/{job_id}/swot-analysis", response_model=SwotAnalysisOut)
async def get_swot_analysis(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_COMPANY_JOBS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """The SWOT document, in whatever state it is in.

    An empty document is the `not_generated` STATE and not a 404: the tab
    exists for every job, and a reader who may see the job must see the empty
    state rather than an error that reads as "this job is broken".
    """
    job = await _staff_job(session, user, job_id)
    row = await swot_analysis.get_or_create(session, job)
    return await _swot_analysis_out(session, user, job, row)


@router.post("/jobs/{job_id}/swot-analysis/generate", response_model=SwotAnalysisOut)
async def generate_swot_analysis(
    job_id: uuid.UUID,
    body: SwotAnalysisGenerateIn,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """Draft the SWOT with the model. Section 28: generation is the same
    authority as editing, because both decide what the document says."""
    job = await _staff_job(session, user, job_id)
    try:
        row = await swot_analysis.generate(
            session, job, confirm_overwrite=body.confirm_overwrite
        )
    except swot_analysis.HumanEditsWouldBeLost as exc:
        # 409, not 403: the caller is authorized and the request is well
        # formed. What is wrong is the STATE, and the client resolves it by
        # confirming rather than by acquiring a permission.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except swot_analysis.SwotAnalysisError as exc:
        # The row now carries `status=failed` and the reason; that write is
        # part of this transaction and must survive the error response, so the
        # failure is returned as a document rather than raised past it.
        failed = await swot_analysis.get_or_create(session, job)
        out = await _swot_analysis_out(session, user, job, failed)
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="job_swot_analysis_generation_failed",
            target_type="job",
            target_id=job.id,
            metadata={"reason": str(exc)},
        )
        return out

    row_out = await _swot_analysis_out(session, user, job, row)
    # ONE INSERT, never an UPDATE. The audit columns (job_id, actor_role,
    # correlation_id, agent_name) must be set BEFORE the row is flushed:
    # `audit()` flushes on return, so mutating the returned row afterwards
    # emits `UPDATE audit_log`, which the app role has REVOKED (0001/0014,
    # binding since the 2026-09-11 credential split). That UPDATE failed the
    # COMMIT after the 200 had already been sent, the whole transaction rolled
    # back, and the client's copy of `version` ran one ahead of the row --
    # which is exactly the false "Someone else saved this SWOT" 409 on the
    # next save. `record_agent_action` writes every column in the one INSERT.
    # RBAC 34: an AI-initiated mutation is attributable to BOTH the human who
    # asked for it and the agent that executed it. `actor_user_id` stays the
    # human, always.
    await record_agent_action(
        session,
        action="job_swot_analysis_generated",
        agent_name="bodha",
        principal_user_id=user.user_id,
        principal_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        metadata={
            "version": row.version,
            "replaced_human_edits": bool(body.confirm_overwrite and row.human_edited),
        },
    )
    await _enqueue_matrix_from_swot(session, job, row)
    return row_out


@router.put("/jobs/{job_id}/swot-analysis", response_model=SwotAnalysisOut)
async def save_swot_analysis(
    job_id: uuid.UUID,
    body: SwotAnalysisSectionsIn,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """Persist the team's edits. This is the write the whole feature exists for."""
    job = await _staff_job(session, user, job_id)
    try:
        row = await swot_analysis.save(
            session,
            job,
            {
                "strengths": body.strengths,
                "weaknesses": body.weaknesses,
                "opportunities": body.opportunities,
                "threats": body.threats,
            },
            editor_id=user.user_id,
            expected_version=body.expected_version,
        )
    except swot_analysis.VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # One INSERT carrying every audit column: see the generate route for why a
    # post-flush mutation of the audit row is an UPDATE the app role cannot run.
    await record_action(
        session,
        action="job_swot_analysis_edited",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        metadata={"version": row.version},
    )
    await _enqueue_matrix_from_swot(session, job, row)
    return await _swot_analysis_out(session, user, job, row)


@router.post("/jobs/{job_id}/swot-analysis/restore", response_model=SwotAnalysisOut)
async def restore_swot_analysis(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(rbac.require_authorized(caps.EDIT_SWOT)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SwotAnalysisOut:
    """Undo the one destructive action in this feature (section 32)."""
    job = await _staff_job(session, user, job_id)
    try:
        row = await swot_analysis.restore_previous(session, job)
    except swot_analysis.SwotAnalysisError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # One INSERT carrying every audit column: see the generate route for why a
    # post-flush mutation of the audit row is an UPDATE the app role cannot run.
    await record_action(
        session,
        action="job_swot_analysis_restored",
        actor_user_id=user.user_id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        metadata={"version": row.version},
    )
    await _enqueue_matrix_from_swot(session, job, row)
    return await _swot_analysis_out(session, user, job, row)



# ── The PPI Assessment Report (spec §9) ──────────────────────────────────────


async def _require_assessment_records_readable(
    session: AsyncSession, user: CurrentUser, link: JobCandidateLink
) -> None:
    """THE JOB-CLOSURE ACCESS GATE (change request 22, 2026-09-22).

    Closing a job no longer deletes its assessment data on the spot; it
    WITHHOLDS it for thirty days and then a sweep deletes it. That makes
    inaccessibility something the read path has to enforce rather than
    something the absence of a row enforced for free, so every staff-facing
    read of a report, a PDF or a transcript asks here.

    Called AFTER the tenant check and BEFORE the artifact is loaded, the
    ordering `services/tools` states: a refusal that ran the handler first has
    already read the row it was refusing to show.

    The CANDIDATE's own routes deliberately do not call this. The employer
    closing a requisition withholds the record from the employer; it was never
    a reason to take a person's own assessment away from them.
    """
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_assessment_retention.require_readable(session, user, job)


@router.get("/reports/links/{link_id}", response_model=FunctionalReportOut)
async def get_report(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FunctionalReportOut:
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Report not found")
    await _require_assessment_records_readable(session, user, link)
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
            # EVIDENCE CONFIDENCE (0107). The stored code becomes a word HERE,
            # server side, for the same reason `score` becomes a grade here: a
            # code that crossed the boundary is a code somebody eventually
            # renders directly. A row written before 0107 carries None and
            # `display_word` returns None for it rather than inventing one.
            evidence_confidence=evidence_confidence.display_word(
                row.evidence_confidence
            ),
            evidence_sources=list(
                evidence_confidence.source_labels(row.evidence_sources)
            ),
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

    # The Proctoring Report, appended as the final, informational section.
    # It moves nothing above it: no grade in this payload is read from it.
    from app.services.proctoring import report as proctoring_report  # noqa: PLC0415

    proctoring = await proctoring_report.load_report_out(session, link.id)

    # Data-retention consent (Consent & Privacy spec, 2026-09-05): whether the
    # Download control may be offered at all. Read here so the UI and the PDF
    # route agree, and a refused download is never a dead button.
    report_candidate = await session.get(Candidate, link.candidate_id)

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
        proctoring=proctoring,
        gap_analysis=GapAnalysisOut.model_validate(report.gap_analysis_json or {}),
        # An EMPTY model, not None, when the column is NULL. The two sections
        # were added in 0107 and every report written before it has neither;
        # a client walking the section order must find an empty section it can
        # skip rather than a missing key it has to guard.
        claim_evidence=ClaimEvidenceOut.model_validate(
            report.claim_evidence_json or {}
        ),
        validation_points=ValidationPointsOut.model_validate(
            report.validation_points_json or {}
        ),
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
        report_download_allowed=retention_consent.assessment_download_allowed(
            report_candidate
        ),
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
    # Asked again rather than relied on through `get_report` above, the same
    # defensiveness the tenant check two lines up already applies: a gate that
    # only holds because of the order two calls happen in is a gate the next
    # reordering deletes without a diff that looks like a deletion. The Job is
    # in the session identity map by now, so the second ask is free.
    await _require_assessment_records_readable(session, user, link)
    candidate = await session.get(Candidate, link.candidate_id)
    # The candidate decides whether their assessment is retained beyond this
    # job (Consent & Privacy spec, 2026-09-05): Download enabled only on an
    # explicit Yes; No and never-asked both stay view-only. The on-screen
    # report route above is deliberately not gated.
    if not retention_consent.assessment_download_allowed(candidate):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The candidate consented to view-only access for this assessment.",
        )
    job = await session.get(Job, link.job_id)
    tenant = await session.get(Tenant, link.tenant_id)
    candidate_name = (candidate.full_name if candidate else None) or "Candidate"
    job_title = (job.title if job else None) or "Role"
    tenant_name = (tenant.name if tenant else None) or "Vivekium customer"
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
    session: AsyncSession, link: JobCandidateLink, questions: list[CandidateQuestion]
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

    for question in questions:
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


def _answer_key(row: CandidateQuestion) -> dict[str, Any]:
    """What the recruiter sees BESIDE the candidate's choice: the correct
    option ids, the accepted answers per blank, the approach a coding answer
    was read against. Prose and identifiers; never a score."""
    payload = dict(row.payload_json or {})
    if row.question_type == question_types.MCQ_SINGLE:
        return {"correct_option_id": payload.get("correct_option_id")}
    if row.question_type == question_types.MCQ_MULTI:
        return {"correct_option_ids": list(payload.get("correct_option_ids") or [])}
    if row.question_type == question_types.FILL_BLANK:
        return {
            "accepted": [
                list(blank.get("accepted") or [])
                for blank in sorted(payload.get("blanks") or [], key=lambda item: item.get("index", 0))
            ]
        }
    if row.question_type == question_types.CODING:
        return {"expected_approach": payload.get("expected_approach")}
    return {}


def _answer_detail(
    row: CandidateQuestion, record: AssessmentAnswer | None
) -> TranscriptAnswerDetailOut | None:
    """The per-format view of one answer (assessment-spec-doc 7).

    Correctness is a WORD, time spent is a PHRASE, and the AI evaluation is
    its reasoning and citations. `auto_score`, the per-criterion numbers and
    the seconds all stay on the row. None for a pre-format short-answer row
    with no structured record, which is every exchange written before 0076.
    """
    if record is None and row.question_type == question_types.SHORT_ANSWER:
        return None
    evaluation = dict(getattr(record, "ai_evaluation_json", None) or {})
    detail = TranscriptAnswerDetailOut(
        time_spent=format_rendering.time_spent_phrase(
            record.time_spent_seconds if record is not None else None
        ),
    )
    if question_types.is_structured(row.question_type):
        detail.payload = question_types.candidate_view(row.id, row.question_type, row.payload_json)
        detail.answer = dict(record.answer_json or {}) if record is not None else {}
        detail.answer_key = _answer_key(row)
    if row.question_type in question_types.OBJECTIVE_TYPES:
        detail.correctness = format_scoring.correctness_word(
            record.auto_score if record is not None else None
        )
        detail.blank_results = [str(item) for item in evaluation.get("blank_results") or []]
    if row.question_type in (question_types.EVIDENCE_BASED, question_types.CODING):
        detail.evaluation_reasoning = evaluation.get("reasoning") or None
        detail.evaluation_citations = [str(item) for item in evaluation.get("citations") or []]
        detail.not_executed_note = evaluation.get("not_executed_note") or None
    return detail


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
    await _require_assessment_records_readable(session, user, link)
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
    questions = await ppi_interview.load_for_link(session, link.id)
    labels = await _criterion_labels(session, link, questions)
    rows_by_key = {str(question.id): question for question in questions}
    records = {
        str(record.question_id): record
        for record in (
            await session.execute(
                select(AssessmentAnswer).where(
                    AssessmentAnswer.conversation_id == conversation.id
                )
            )
        ).scalars().all()
    }

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
        row = rows_by_key.get(key)
        exchanges.append(
            TranscriptExchangeOut(
                ordinal=len(exchanges) + 1,
                domain=pending.domain,
                question=pending.content,
                answer=message.content,
                criterion=labels.get(key),
                follow_up=follow_up,
                asked_at=pending.created_at,
                question_type=row.question_type if row is not None else None,
                resume_anchor=row.resume_anchor if row is not None else None,
                # The structured record belongs to the BASE exchange; a
                # follow-up is more evidence for the same question and shows
                # the anchor, never a second copy of the detail.
                detail=(
                    _answer_detail(row, records.get(key))
                    if row is not None and not follow_up
                    else None
                ),
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
