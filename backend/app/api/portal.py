"""Candidate portal (FR-6.x, FR-9.x / ESD §13). Authenticated endpoints use
the candidate JWT audience; the outreach link endpoints are public, gated by
the signed outreach token."""
import json
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    decode_outreach_token,
    get_candidate_db,
    get_current_candidate,
    get_public_db,
)
from app.models.candidate import (
    Candidate,
    JobCandidateLink,
    PipelineStatusEntry,
    Profile,
    VerificationRequest,
)
from app.models.enums import LinkSource, VerificationStatus
from app.models.job import Job
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.portal import (
    ApplicationOut,
    ApplicationsOut,
    ApplyOut,
    AspectOut,
    OutreachInfoOut,
    OutreachSubmitOut,
    PortalJobOut,
    PortalJobsOut,
)
from app.workers.celery_app import celery_app

router = APIRouter()

MAX_EMPLOYER_EMAILS = 3  # FR-5.2

# ── The 40-aspect questionnaire ─────────────────────────────────────────────
# ASSUMPTION: the PRD references "the 40-aspect questionnaire" but does not
# enumerate the aspects (business content to be supplied by Hanulisa). Known
# from the PRD: aspects 1-4 duplicate the personal fields collected in
# FR-5.1 a-d (and are therefore skipped when those are already covered), and
# Aspect 40 is the Databank re-use consent (FR-4.2 / PRD §10). The remaining
# prompts are placeholders to be replaced with the real questionnaire text.
_PERSONAL_ASPECTS: dict[int, str] = {
    1: "full_name", 2: "city", 3: "age", 4: "gender",
}
ASPECT_DEFINITIONS: list[AspectOut] = (
    [
        AspectOut(id=1, prompt="Full Name (as per PF records / Class X memorandum)"),
        AspectOut(id=2, prompt="Residing City"),
        AspectOut(id=3, prompt="Age"),
        AspectOut(id=4, prompt="Gender"),
    ]
    + [AspectOut(id=n, prompt=f"Aspect {n} (questionnaire item {n})") for n in range(5, 40)]
    + [AspectOut(id=40, prompt="Do you consent to being matched against future "
                               "roles via the PickReady Databank?")]
)


async def _outreach_context(session: AsyncSession, token: str):
    payload = decode_outreach_token(token)
    profile = await session.get(Profile, uuid.UUID(payload["profile_id"]))
    if profile is None:
        raise HTTPException(status_code=404, detail="Invalid or expired link")
    candidate = await session.get(Candidate, profile.candidate_id)
    job = await session.get(Job, uuid.UUID(payload["job_id"]))
    return profile, candidate, job


@router.get("/outreach/{token}", response_model=OutreachInfoOut)
async def outreach_info(
    token: str, session: AsyncSession = Depends(get_public_db)
) -> OutreachInfoOut:
    """What the outreach asks for (FR-5.1/6.1): personal fields still missing,
    the 40 aspects minus those already covered, a fresh resume, and up to 3
    previous-employer HR emails."""
    profile, candidate, job = await _outreach_context(session, token)
    tenant = await session.get(Tenant, job.tenant_id) if job else None

    missing_personal = [
        field for field in ("full_name", "city", "age", "gender")
        if getattr(candidate, field, None) is None
    ]
    covered_ids = {
        aspect_id for aspect_id, field in _PERSONAL_ASPECTS.items()
        if getattr(candidate, field, None) is not None
    }
    return OutreachInfoOut(
        job_title=job.title if job else None,
        company_name=tenant.name if tenant else None,
        already_submitted=profile.aspects_completed_at is not None,
        personal_fields=missing_personal,
        aspects=[a for a in ASPECT_DEFINITIONS if a.id not in covered_ids],
        max_employer_emails=MAX_EMPLOYER_EMAILS,
    )


@router.post("/outreach/{token}", response_model=OutreachSubmitOut)
async def outreach_submit(
    token: str,
    resume: UploadFile = File(...),
    aspects: str = Form(...),  # JSON object {"5": "...", ..., "40": true}
    full_name: str | None = Form(default=None),
    city: str | None = Form(default=None),
    age: int | None = Form(default=None),
    gender: str | None = Form(default=None),
    employer_emails: list[str] = Form(default=[]),
    session: AsyncSession = Depends(get_public_db),
) -> OutreachSubmitOut:
    """Candidate completes the outreach (FR-6.1): personal fields, the
    40-aspect questionnaire, a fresh resume, and up to 3 previous-employer
    HR emails. Single-use: a completed profile rejects re-submission."""
    profile, candidate, job = await _outreach_context(session, token)
    if profile.aspects_completed_at is not None:
        raise HTTPException(status_code=409, detail="This outreach was already completed")
    if len(employer_emails) > MAX_EMPLOYER_EMAILS:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_EMPLOYER_EMAILS} previous employers (FR-5.2)",
        )
    try:
        aspects_data = json.loads(aspects)
        if not isinstance(aspects_data, dict):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="aspects must be a JSON object") from exc

    if full_name is not None:
        candidate.full_name = full_name
    if city is not None:
        candidate.city = city
    if age is not None:
        candidate.age = age
    if gender is not None:
        candidate.gender = gender
    # Aspect 40 is the Databank consent (PRD §10 / FR-4.2).
    consent = aspects_data.get("40")
    candidate.consent_databank = bool(consent) and str(consent).lower() not in ("false", "no", "0")

    from app.api.candidates import store_resume  # shared helper (Track A file)

    profile.resume_url = await store_resume(resume)
    profile.aspects_json = aspects_data
    profile.aspects_completed_at = datetime.now(timezone.utc)

    created = 0
    for seq, employer_email in enumerate(employer_emails, start=1):
        if not employer_email.strip():
            continue
        session.add(VerificationRequest(
            tenant_id=job.tenant_id,
            profile_id=profile.id,
            employer_seq=seq,
            employer_email=employer_email.strip(),
            token=secrets.token_urlsafe(32),  # single-use employer form token
            status=VerificationStatus.pending,
        ))
        created += 1
    await session.flush()

    celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
    if created:
        celery_app.send_task(
            "pickready.send_verification_requests", args=[str(profile.id)]
        )
    return OutreachSubmitOut(
        profile_id=profile.id,
        aspects_received=len(aspects_data),
        verification_requests_created=created,
    )


# ── Authenticated candidate endpoints ───────────────────────────────────────

async def _candidate_for_user(
    session: AsyncSession, user: CurrentUser
) -> Candidate:
    row = await session.get(User, user.user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    candidate = (
        await session.execute(
            select(Candidate).where(
                (Candidate.user_id == user.user_id) | (Candidate.email == row.email)
            )
        )
    ).scalars().first()
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail="No candidate record yet — you appear after an employer's first outreach",
        )
    if candidate.user_id is None:
        candidate.user_id = user.user_id  # link portal login to the candidate record
    return candidate


async def _contacted_tenant_ids(
    session: AsyncSession, candidate: Candidate
) -> set[uuid.UUID]:
    """Tenants that have contacted this candidate (ESD §13): any existing
    job link (outreach/sourcing always creates one first)."""
    rows = (
        await session.execute(
            select(JobCandidateLink.tenant_id).where(
                JobCandidateLink.candidate_id == candidate.id
            )
        )
    ).all()
    return {r.tenant_id for r in rows}


@router.get("/jobs", response_model=PortalJobsOut)
async def portal_jobs(
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> PortalJobsOut:
    """New Jobs — only from tenants that have previously contacted this
    candidate (FR-9.1); this is not a public job board."""
    candidate = await _candidate_for_user(session, user)
    tenant_ids = await _contacted_tenant_ids(session, candidate)
    if not tenant_ids:
        return PortalJobsOut(jobs=[])
    jobs = (
        await session.execute(
            select(Job).where(
                Job.tenant_id.in_(tenant_ids), Job.ratified_at.isnot(None)
            ).order_by(Job.created_at.desc())
        )
    ).scalars().all()
    tenants = {
        t.id: t for t in (
            await session.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
        ).scalars().all()
    }
    return PortalJobsOut(jobs=[
        PortalJobOut(
            id=j.id, title=j.title, department=j.department, level=j.level,
            company_name=tenants[j.tenant_id].name if j.tenant_id in tenants else None,
            status=j.status,
        )
        for j in jobs
    ])


@router.post(
    "/jobs/{job_id}/apply", response_model=ApplyOut, status_code=status.HTTP_201_CREATED
)
async def apply_to_job(
    job_id: uuid.UUID,
    resume: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ApplyOut:
    """Apply with a FRESH resume — resumes are never persisted on the portal
    between applications (FR-9.2 / claude.md rule 6), so a new Profile is
    created per application and nothing is pre-filled from a previous one."""
    candidate = await _candidate_for_user(session, user)
    job = await session.get(Job, job_id)
    if job is None or job.ratified_at is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.tenant_id not in await _contacted_tenant_ids(session, candidate):
        raise HTTPException(status_code=403, detail="This employer has not contacted you yet (FR-9.1)")

    dup = (
        await session.execute(
            select(JobCandidateLink).where(
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.candidate_id == candidate.id,
            )
        )
    ).scalars().first()
    if dup is not None:
        raise HTTPException(status_code=409, detail="You have already applied to this job")

    from app.api.candidates import store_resume

    resume_url = await store_resume(resume)
    profile = Profile(
        candidate_id=candidate.id, source_tenant_id=job.tenant_id, resume_url=resume_url
    )
    session.add(profile)
    await session.flush()
    link = JobCandidateLink(
        tenant_id=job.tenant_id, job_id=job.id, candidate_id=candidate.id,
        profile_id=profile.id, source=LinkSource.fresh,
    )
    session.add(link)
    await session.flush()
    celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
    return ApplyOut(link_id=link.id, job_id=job.id)


@router.get("/applications", response_model=ApplicationsOut)
async def my_applications(
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ApplicationsOut:
    """Application Stage Status (FR-9.1)."""
    candidate = await _candidate_for_user(session, user)
    links = (
        await session.execute(
            select(JobCandidateLink)
            .where(JobCandidateLink.candidate_id == candidate.id)
            .order_by(JobCandidateLink.created_at.desc())
        )
    ).scalars().all()
    out: list[ApplicationOut] = []
    for link in links:
        job = await session.get(Job, link.job_id)
        tenant = await session.get(Tenant, link.tenant_id)
        latest = (
            await session.execute(
                select(PipelineStatusEntry)
                .where(PipelineStatusEntry.job_candidate_link_id == link.id)
                .order_by(PipelineStatusEntry.at.desc())
            )
        ).scalars().first()
        out.append(ApplicationOut(
            link_id=link.id,
            job_id=link.job_id,
            job_title=job.title if job else "",
            company_name=tenant.name if tenant else None,
            applied_at=link.created_at,
            stage=latest.status if latest else None,
        ))
    return ApplicationsOut(applications=out)
