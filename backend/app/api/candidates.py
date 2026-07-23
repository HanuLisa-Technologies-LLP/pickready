"""Candidate sourcing, review-screen, decisions, pipeline status and
interview scheduling (FR-4.3/4.4, FR-7.x, FR-8.x)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.core.config import get_settings
from app.models.candidate import (
    Candidate,
    Interview,
    JobCandidateLink,
    PipelineStatusEntry,
    Profile,
    VerificationRequest,
)
from app.models.enums import LinkSource, PipelineStatus, VerificationStatus
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.candidates import (
    CandidateOut,
    DecisionIn,
    GrantAccessOut,
    InterviewIn,
    InterviewOut,
    JobLinksOut,
    LinkOut,
    ProfileOut,
    StatusIn,
    StatusOut,
    UploadResumeOut,
    VerificationRequestSummary,
)
from app.services import capabilities as caps
from app.services import rbac
from app.services.audit import audit
from app.workers.celery_app import celery_app

router = APIRouter()

FORWARD_STATUSES = {PipelineStatus.shortlisted, PipelineStatus.offered, PipelineStatus.joined}


async def store_resume(file: UploadFile) -> str | None:
    """Upload the resume to Cloudinary (ESD §2). The upload itself is quick
    I/O and runs in a threadpool; the heavy work (text extraction + LLM
    parsing) is always the `pickready.parse_resume` Celery task.

    Returns None when Cloudinary is not configured (local dev) — the parse
    task tolerates a missing URL."""
    data = await file.read()
    if not get_settings().cloudinary_url:
        return None
    try:
        import cloudinary.uploader  # lazy: not needed at import time

        result = await run_in_threadpool(
            cloudinary.uploader.upload, data, resource_type="raw",
            folder="pickready/resumes",
        )
        return result.get("secure_url")
    except Exception:  # noqa: BLE001 — storage failure must not 500 the upload flow
        return None


async def _get_link(
    session: AsyncSession, user: CurrentUser, link_id: uuid.UUID
) -> JobCandidateLink:
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:  # defense in depth
        raise HTTPException(status_code=404, detail="Link not found")
    return link


async def _latest_status(
    session: AsyncSession, link_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PipelineStatusEntry]:
    if not link_ids:
        return {}
    rows = (
        await session.execute(
            select(PipelineStatusEntry)
            .where(PipelineStatusEntry.job_candidate_link_id.in_(link_ids))
            .order_by(PipelineStatusEntry.at)
        )
    ).scalars().all()
    latest: dict[uuid.UUID, PipelineStatusEntry] = {}
    for row in rows:  # ordered ascending, so the last write wins
        latest[row.job_candidate_link_id] = row
    return latest


@router.post(
    "/jobs/{job_id}/upload-resume",
    response_model=UploadResumeOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_resume(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    email: str = Form(...),
    full_name: str | None = Form(default=None),
    phone: str | None = Form(default=None),
    user: CurrentUser = Depends(require_capability(caps.UPLOAD_RESUMES)),
    session: AsyncSession = Depends(get_tenant_db),
) -> UploadResumeOut:
    """Recruiter uploads a freshly sourced resume (FR-4.3): creates
    candidate + profile + link (source=fresh) and enqueues parsing."""
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.ratified_at is None:
        # ASSUMPTION: sourcing starts once the job has reached HR (FR-3.4).
        raise HTTPException(status_code=409, detail="Job is not ratified yet")

    candidate = (
        await session.execute(select(Candidate).where(Candidate.email == email))
    ).scalars().first()
    if candidate is None:
        candidate = Candidate(
            tenant_id=user.tenant_id, email=email, full_name=full_name, phone=phone
        )
        session.add(candidate)
        await session.flush()

    dup = (
        await session.execute(
            select(JobCandidateLink).where(
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.candidate_id == candidate.id,
            )
        )
    ).scalars().first()
    if dup is not None:
        raise HTTPException(status_code=409, detail="Candidate is already linked to this job")

    resume_url = await store_resume(file)
    profile = Profile(
        candidate_id=candidate.id, source_tenant_id=user.tenant_id, resume_url=resume_url
    )
    session.add(profile)
    await session.flush()

    link = JobCandidateLink(
        tenant_id=user.tenant_id, job_id=job.id, candidate_id=candidate.id,
        profile_id=profile.id, source=LinkSource.fresh,
    )
    session.add(link)
    await session.flush()

    celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="resume_uploaded", target_type="profile", target_id=profile.id,
                metadata={"job_id": str(job.id), "candidate_id": str(candidate.id)})
    return UploadResumeOut(
        candidate_id=candidate.id, profile_id=profile.id, link_id=link.id,
        source=LinkSource.fresh,
    )


@router.get("/{candidate_id}/profile", response_model=ProfileOut)
async def get_profile(
    candidate_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ProfileOut:
    """Full Profile for the HR Review Screen (FR-7.1/7.2).

    # ASSUMPTION: full access to any profile is derived from the HR-exclusive
    # SEND_OUTREACH capability; holders of only VIEW_REVIEW_SCREEN (Hiring
    # Managers) can read a candidate solely when HR has granted access on at
    # least one of that candidate's links (FR-8.1) — capability + data, no
    # role branch.
    """
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    full_access = await rbac.has_capability(
        session, user.tenant_id, user.role, caps.SEND_OUTREACH
    )
    if not full_access:
        granted = (
            await session.execute(
                select(JobCandidateLink).where(
                    JobCandidateLink.candidate_id == candidate_id,
                    JobCandidateLink.tenant_id == user.tenant_id,
                    JobCandidateLink.hm_access_granted.is_(True),
                )
            )
        ).scalars().first()
        if granted is None:
            raise HTTPException(status_code=403, detail="Profile access not granted")

    profile = (
        await session.execute(
            select(Profile).where(Profile.candidate_id == candidate_id)
            .order_by(Profile.created_at.desc())
        )
    ).scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile for this candidate")

    vrs = (
        await session.execute(
            select(VerificationRequest)
            .where(VerificationRequest.profile_id == profile.id)
            .order_by(VerificationRequest.employer_seq)
        )
    ).scalars().all()
    return ProfileOut(
        id=profile.id,
        candidate=CandidateOut.model_validate(candidate),
        resume_url=profile.resume_url,
        aspects_json=profile.aspects_json,
        parsed_fields_json=profile.parsed_fields_json,
        aspects_completed_at=profile.aspects_completed_at,
        verification_requests=[VerificationRequestSummary.model_validate(v) for v in vrs],
    )


@router.post("/links/{link_id}/grant-access", response_model=GrantAccessOut)
async def grant_access(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> GrantAccessOut:
    """HR grants Hiring Manager access to a reviewed profile (FR-8.1).
    # ASSUMPTION: gated by SEND_OUTREACH — the HR-exclusive candidate-management
    # capability — since the PRD matrix has no dedicated grant capability."""
    link = await _get_link(session, user, link_id)
    link.hm_access_granted = True
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="hm_access_granted", target_type="job_candidate_link",
                target_id=link.id)
    return GrantAccessOut(link_id=link.id)


@router.post("/links/{link_id}/decision", response_model=StatusOut)
async def decide_profile(
    link_id: uuid.UUID,
    body: DecisionIn,  # hold without remarks -> 422 (schema validator)
    user: CurrentUser = Depends(require_capability(caps.DECIDE_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StatusOut:
    """Hiring Manager decision: Rejected / Shortlisted / Hold (FR-8.2)."""
    link = await _get_link(session, user, link_id)
    if not link.hm_access_granted:
        raise HTTPException(status_code=403, detail="Profile access not granted (FR-8.1)")

    entry = PipelineStatusEntry(
        tenant_id=user.tenant_id, job_candidate_link_id=link.id,
        status=PipelineStatus(body.status), remarks=body.remarks, set_by=user.user_id,
    )
    session.add(entry)
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="profile_decision", target_type="job_candidate_link",
                target_id=link.id, metadata={"status": body.status, "remarks": body.remarks})
    return StatusOut(link_id=link.id, status=entry.status, remarks=entry.remarks,
                     at=entry.at or datetime.now(timezone.utc))


@router.post("/links/{link_id}/status", response_model=StatusOut)
async def update_pipeline_status(
    link_id: uuid.UUID,
    body: StatusIn,
    user: CurrentUser = Depends(require_capability(caps.UPDATE_PIPELINE_STATUS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StatusOut:
    """Mandatory pipeline status update (FR-8.4). Fresh candidates cannot be
    moved FORWARD until the outreach + all employer verifications are
    submitted or explicitly overridden (FR-5.5)."""
    link = await _get_link(session, user, link_id)
    new_status = PipelineStatus(body.status)

    if new_status in FORWARD_STATUSES and link.source == LinkSource.fresh:
        profile = await session.get(Profile, link.profile_id) if link.profile_id else None
        if profile is None or profile.aspects_completed_at is None:
            raise HTTPException(
                status_code=409,
                detail="Candidate outreach (40 aspects) is not complete (FR-5.5)",
            )
        vrs = (
            await session.execute(
                select(VerificationRequest).where(VerificationRequest.profile_id == profile.id)
            )
        ).scalars().all()
        if not vrs:
            raise HTTPException(
                status_code=409,
                detail="No employer verification requests exist yet (FR-5.2/5.5)",
            )
        if any(v.status == VerificationStatus.pending for v in vrs):
            raise HTTPException(
                status_code=409,
                detail="Employer verification is still pending — submit or override first (FR-5.5)",
            )

    entry = PipelineStatusEntry(
        tenant_id=user.tenant_id, job_candidate_link_id=link.id,
        status=new_status, remarks=body.remarks, set_by=user.user_id,
    )
    session.add(entry)
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="pipeline_status_updated", target_type="job_candidate_link",
                target_id=link.id, metadata={"status": body.status})
    return StatusOut(link_id=link.id, status=entry.status, remarks=entry.remarks,
                     at=entry.at or datetime.now(timezone.utc))


@router.post(
    "/links/{link_id}/interviews",
    response_model=InterviewOut,
    status_code=status.HTTP_201_CREATED,
)
async def schedule_interview(
    link_id: uuid.UUID,
    body: InterviewIn,
    user: CurrentUser = Depends(require_capability(caps.SCHEDULE_INTERVIEWS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> InterviewOut:
    """Interview invite with .ics, sent ONLY from the tenant's verified
    sending domain (FR-8.3 / claude.md rule 5) — never Gmail/Outlook."""
    link = await _get_link(session, user, link_id)
    candidate = await session.get(Candidate, link.candidate_id)
    tenant = await session.get(Tenant, user.tenant_id)
    if candidate is None or tenant is None:
        raise HTTPException(status_code=404, detail="Candidate or tenant missing")

    ics_uid = f"{uuid.uuid4()}@{tenant.domain}"
    sent_from = f"no-reply@{tenant.domain}"
    interview = Interview(
        tenant_id=user.tenant_id, job_candidate_link_id=link.id,
        scheduled_at=body.scheduled_at, sent_from_email=sent_from,
        ics_uid=ics_uid, notes=body.notes,
    )
    session.add(interview)
    await session.flush()

    celery_app.send_task(
        "pickready.send_email",
        args=[
            str(user.tenant_id), candidate.email, "interview_invite",
            {
                "scheduled_at": body.scheduled_at.isoformat(),
                "notes": body.notes,
                "ics_uid": ics_uid,
                "interview_id": str(interview.id),
            },
        ],
    )
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="interview_scheduled", target_type="interview",
                target_id=interview.id, metadata={"link_id": str(link.id)})
    return InterviewOut.model_validate(interview)


@router.get("/jobs/{job_id}", response_model=JobLinksOut)
async def list_job_links(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_DATABANK)),
    session: AsyncSession = Depends(get_tenant_db),
) -> JobLinksOut:
    """All candidate links for a job with score/tier/current status."""
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")

    links = (
        await session.execute(
            select(JobCandidateLink).where(JobCandidateLink.job_id == job.id)
            .order_by(JobCandidateLink.match_score.desc().nulls_last())
        )
    ).scalars().all()
    latest = await _latest_status(session, [l.id for l in links])
    out: list[LinkOut] = []
    for link in links:
        candidate = await session.get(Candidate, link.candidate_id)
        entry = latest.get(link.id)
        out.append(LinkOut(
            link_id=link.id,
            candidate=CandidateOut.model_validate(candidate),
            profile_id=link.profile_id,
            source=link.source,
            match_score=link.match_score,
            tier=link.tier,
            hm_access_granted=link.hm_access_granted,
            current_status=entry.status if entry else None,
            status_remarks=entry.remarks if entry else None,
        ))
    return JobLinksOut(job_id=job.id, links=out)
