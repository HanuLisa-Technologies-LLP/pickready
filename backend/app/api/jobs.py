"""Job creation + multi-level approval FSM endpoints (FR-3.x, FR-4.1)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_db, require_capability
from app.models.company import Company
from app.models.enums import ApprovalDecision, JobStatus
from app.models.job import Job, JobApproval
from app.schemas.jobs import (
    ApprovalOut,
    ApproveIn,
    CompensationIn,
    JDUpdateIn,
    JobCreateIn,
    JobDetailOut,
    JobOut,
)
from app.services import approval_fsm as fsm
from app.services import capabilities as caps
from app.services import rbac
from app.services.audit import audit
from app.workers.celery_app import celery_app

router = APIRouter()


async def _approval_config(session: AsyncSession, tenant_id: uuid.UUID) -> dict | None:
    company = (
        await session.execute(select(Company).where(Company.tenant_id == tenant_id))
    ).scalars().first()
    return company.approval_levels_config if company else None


async def _can_see_pre_ratified(session: AsyncSession, user: CurrentUser) -> bool:
    """# ASSUMPTION: visibility of pre-ratified jobs is capability-derived,
    not role-derived (claude.md rule 3): actors who create jobs or sit in the
    approval chain see the whole lifecycle; everyone else (HR/Recruiter) sees
    a job only once ratified (FR-3.4)."""
    return (
        await rbac.has_capability(session, user.tenant_id, user.role, caps.CREATE_JOB)
        or await rbac.has_capability(session, user.tenant_id, user.role, caps.APPROVE_JOB)
        or await rbac.has_capability(session, user.tenant_id, user.role, caps.CONFIGURE_APPROVAL_LEVELS)
    )


async def _get_visible_job(
    session: AsyncSession, user: CurrentUser, job_id: uuid.UUID
) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:  # defense in depth; RLS is the boundary
        raise HTTPException(status_code=404, detail="Job not found")
    # Terminal/HR-visibility marker is ratified_at, NOT the status value: a job
    # pending at an active "ratified" level also carries status ratified.
    if job.ratified_at is None and not await _can_see_pre_ratified(session, user):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    job = Job(
        tenant_id=user.tenant_id,
        title=body.title,
        department=body.department,
        level=body.level,
        requirement_period=body.requirement_period,
        jd_json=body.jd.model_dump(mode="json"),
        status=JobStatus.draft,
        created_by=user.user_id,
    )
    session.add(job)
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_created", target_type="job", target_id=job.id,
                metadata={"title": body.title})
    return JobOut.model_validate(job)


@router.get("", response_model=list[JobOut])
async def list_jobs(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[JobOut]:
    stmt = select(Job).where(Job.tenant_id == user.tenant_id).order_by(Job.created_at.desc())
    if not await _can_see_pre_ratified(session, user):
        stmt = stmt.where(Job.ratified_at.isnot(None))  # FR-3.4 (terminal marker)
    rows = (await session.execute(stmt)).scalars().all()
    return [JobOut.model_validate(j) for j in rows]


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    job = await _get_visible_job(session, user, job_id)
    return JobDetailOut.model_validate(job)


@router.post("/{job_id}/submit", response_model=JobOut)
async def submit_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.CREATE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """draft -> first active approval level; leading inactive levels are
    logged as explicitly skipped."""
    job = await _get_visible_job(session, user, job_id)
    if job.status != JobStatus.draft:
        raise HTTPException(status_code=409, detail="Job has already been submitted")

    config = await _approval_config(session, user.tenant_id)
    try:
        result = await fsm.apply_submit(session, job, config)
    except fsm.ApprovalConfigError as exc:
        raise HTTPException(
            status_code=409,
            detail="Approval levels are not configured for this company (FR-2.3)",
        ) from exc

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_submitted", target_type="job", target_id=job.id,
                metadata={"new_status": result.new_status.value})
    if result.ratified:
        celery_app.send_task("pickready.run_matching", args=[str(job.id)])  # FR-4.2
    return JobOut.model_validate(job)


@router.post("/{job_id}/approve", response_model=JobOut)
async def approve_job(
    job_id: uuid.UUID,
    body: ApproveIn,
    user: CurrentUser = Depends(require_capability(caps.APPROVE_JOB)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Approve/reject at the job's current level. The FSM re-validates that
    the actor is the assigned approver of exactly this level."""
    job = await _get_visible_job(session, user, job_id)
    config = await _approval_config(session, user.tenant_id)
    try:
        result = await fsm.apply_transition(
            session, job, config,
            acting_user_id=user.user_id,
            decision=ApprovalDecision(body.decision),
            remarks=body.remarks,
        )
    except fsm.NotAssignedApprover as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except fsm.PriorLevelPending as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (fsm.AlreadyTerminal, fsm.NotSubmitted) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except fsm.ApprovalConfigError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_approval_decision", target_type="job", target_id=job.id,
                metadata={"decision": body.decision, "new_status": result.new_status.value,
                          "remarks": body.remarks})
    if result.ratified:
        # The moment a job reaches HR, Databank matching runs (FR-4.2) —
        # asynchronously, never inline.
        celery_app.send_task("pickready.run_matching", args=[str(job.id)])
    return JobOut.model_validate(job)


@router.get("/{job_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[ApprovalOut]:
    job = await _get_visible_job(session, user, job_id)
    rows = (
        await session.execute(
            select(JobApproval).where(JobApproval.job_id == job.id)
            .order_by(JobApproval.decided_at, JobApproval.id)
        )
    ).scalars().all()
    return [ApprovalOut.model_validate(r) for r in rows]


@router.put("/{job_id}/compensation", response_model=JobDetailOut)
async def set_compensation(
    job_id: uuid.UUID,
    body: CompensationIn,
    user: CurrentUser = Depends(require_capability(caps.ADD_COMPENSATION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """HR adds compensation post-ratification (FR-4.1)."""
    job = await _get_visible_job(session, user, job_id)
    if job.ratified_at is None:
        raise HTTPException(status_code=409, detail="Compensation is added after ratification (FR-4.1)")
    job.compensation_json = body.compensation
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_compensation_set", target_type="job", target_id=job.id)
    return JobDetailOut.model_validate(job)


@router.put("/{job_id}/jd", response_model=JobDetailOut)
async def edit_jd(
    job_id: uuid.UUID,
    body: JDUpdateIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_JOB_DESCRIPTION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobDetailOut:
    """HR JD-ambiguity fixes, post-ratification only (FR-4.1)."""
    job = await _get_visible_job(session, user, job_id)
    if job.ratified_at is None:
        raise HTTPException(status_code=409, detail="HR edits the JD only after ratification (FR-4.1)")
    job.jd_json = body.jd.model_dump(mode="json")
    if body.title is not None:
        job.title = body.title
    if body.department is not None:
        job.department = body.department
    if body.level is not None:
        job.level = body.level
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="job_jd_edited", target_type="job", target_id=job.id)
    return JobDetailOut.model_validate(job)
