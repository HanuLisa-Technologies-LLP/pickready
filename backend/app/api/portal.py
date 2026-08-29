"""Candidate portal (FR-6.x, FR-9.x / ESD §13). Authenticated endpoints use
the candidate JWT audience; the outreach link endpoints are public, gated by
the signed outreach token."""
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    decode_outreach_token,
    get_candidate_db,
    get_current_any,
    get_current_candidate,
    get_public_db,
)
from app.core.db import get_session_factory, superadmin_scope
from app.models.candidate import (
    Candidate,
    JobCandidateLink,
    PipelineStatusEntry,
    Profile,
    VerificationRequest,
)
from app.models.assessment import AssessmentConversation, FunctionalSkillsReport
from app.models.company import Company
from app.models.enums import LinkSource, PipelineStatus, VerificationStatus
from app.models.job import Job
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.portal import (
    ApplicationOut,
    ApplicationsOut,
    ApplyOut,
    AspectOut,
    MeOut,
    MeUpdateIn,
    OutreachInfoOut,
    OutreachSubmitOut,
    PortalJobOut,
    PortalJobsOut,
    StatusEventOut,
)
from app.services import application_validation
from app.services import candidate_profile_form as profile_form
from app.services import hiring_pipeline
from app.services import job_posting
from app.services import job_relevance
from app.services import retake
from app.services.audit import audit
from app.workers.celery_app import celery_app
from app.services.resume_storage import apply_resume_asset, copy_resume_metadata, store_resume

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_EMPLOYER_EMAILS = 3  # FR-5.2


# ── Apply-context response models (FR-6.2 resume reuse / FR-9.2) ────────────
# Declared here rather than in schemas/portal.py: they exist purely to let the
# apply UI decide, BEFORE the candidate fills 40 questions, whether "reuse my
# last resume" is offerable and whether they have already applied.

class StoredResumeOut(BaseModel):
    """The candidate's most recent stored resume, reusable on a new
    application (claude.md rule 6 / FR-6.2). No Cloudinary URL is exposed —
    the UI only needs to name the file it would reuse."""
    has_resume: bool = False
    filename: str | None = None
    size_bytes: int | None = None
    uploaded_at: datetime | None = None


class ApplyContextOut(BaseModel):
    """Everything the public apply page needs before showing the form."""
    job_id: uuid.UUID
    already_applied: bool = False
    applied_at: datetime | None = None
    resume: StoredResumeOut = StoredResumeOut()
    #: Whether the candidate's My Profile advanced form is filled in. The apply
    #: dialog uses this to send them to their profile first rather than letting
    #: them submit an application with no validation data behind it.
    profile_complete: bool = False
    profile_missing: list[str] = []
    #: The mandatory validation fields (spec §7), served from the backend so
    #: the form the candidate fills in and the answers the report renders cannot
    #: drift apart. Every one of them is required.
    validation_fields: list[dict] = []
    #: Copy shown above those fields. Served rather than hardcoded in the page
    #: for the same reason the fields are: it states the reuse behaviour, and
    #: `validation_values` below is the behaviour it states.
    validation_intro: str = ""
    #: The candidate's answers from their most recent application, prefilled so
    #: they type this once. They are still SUBMITTED per application and still
    #: snapshotted onto that application's own `validation_json`; what is reused
    #: is the typing, not the record.
    validation_values: dict[str, str] = {}

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
                               "roles via the ReadyPick Databank?")]
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

    asset = await store_resume(resume)
    apply_resume_asset(profile, asset)
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
            detail="No candidate record yet, you appear after an employer's first outreach",
        )
    if candidate.user_id is None:
        candidate.user_id = user.user_id  # link portal login to the candidate record
    return candidate


def _portal_job_out(
    job: Job, tenant: Tenant | None, company: Company | None = None
) -> PortalJobOut:
    """One place where a Job becomes the candidate-facing job payload.

    Includes the JD so the apply dialog can render the description straight
    from the list/detail response instead of a second call to
    `/jobs/public/{id}`, plus the employer's About/Culture prose — a candidate
    deciding whether to apply is choosing the company as much as the role.
    Internal ATS fields (compensation, created_by, approval state, match
    scores) are deliberately not carried over.
    """
    return PortalJobOut(
        id=job.id,
        title=job.title,
        department=job.department,
        level=job.level,
        company_name=tenant.name if tenant else None,
        status=job.status,
        jd_json=job.jd_json or {},
        assessment_grade=job.assessment_grade,
        # Company Profile is the sole company-information surface. Tenant
        # onboarding data remains a fallback for accounts that have not saved
        # their profile yet.
        company_about=(company.about_company if company else None) or (tenant.details if tenant else None),
        company_culture=(company.work_life if company else None) or (tenant.culture if tenant else None),
        company_industry=tenant.industry if tenant else None,
        company_benefits=company.benefits_text if company else None,
    )


#: What a candidate is told when a posting has closed. Deliberately identical
#: to the not-found message: whether a job EXISTS but has expired is not
#: something an unauthorised viewer should be able to probe.
_EXPIRED_DETAIL = "This job posting is no longer available"


async def _published_job_or_404(session: AsyncSession, job_id: uuid.UUID) -> Job:
    """A job is publicly applyable once it is published (FR-3.5). The FSM's
    terminal state is `ratified` (there is no separate 'published' state), so
    `ratified_at is not None` is the published gate — matching the rest of the
    codebase (candidates.upload_resume, etc.)."""
    job = await session.get(Job, job_id)
    if job is None or job.ratified_at is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _visible_job_or_404(
    session: AsyncSession, candidate: Candidate, job_id: uuid.UUID
) -> Job:
    """A published job this candidate is allowed to see (spec §2.2).

    404 rather than 403 when the window has closed: a candidate who registered
    after the posting ended must not be able to tell the job ever existed, and
    two different status codes would tell them.
    """
    job = await _published_job_or_404(session, job_id)
    has_applied = (
        await session.execute(
            select(JobCandidateLink.id).where(
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.candidate_id == candidate.id,
            ).limit(1)
        )
    ).first() is not None
    if not job_posting.can_view_job(
        posting_start=job.posting_start_date,
        posting_end_date=job.posting_end_date,
        grace_period_end_date=job.grace_period_end_date,
        candidate_created_at=candidate.created_at,
        has_applied=has_applied,
    ):
        raise HTTPException(status_code=404, detail=_EXPIRED_DETAIL)
    return job


async def _previous_resume(
    session: AsyncSession, candidate: Candidate
) -> Profile | None:
    """The candidate's most recent stored resume, reused across applications
    (FR-6.2 / FR-9.2 / claude.md rule 6, reversed 2026-07-24).

    # The latest profile with complete Cloudinary metadata is the reusable
    # resume snapshot. A new application copies that immutable metadata.
    """
    return (
        await session.execute(
            select(Profile)
            .where(
                Profile.candidate_id == candidate.id,
                Profile.resume_public_id.isnot(None),
            )
            .order_by(Profile.created_at.desc())
        )
    ).scalars().first()


async def _employers_for(
    session: AsyncSession, jobs: list[Job]
) -> tuple[dict[uuid.UUID, Tenant], dict[uuid.UUID, Company]]:
    """Tenant + company profile for a batch of jobs, in two queries rather than
    two per job."""
    tenant_ids = {job.tenant_id for job in jobs}
    if not tenant_ids:
        return {}, {}
    tenants = {
        t.id: t
        for t in (
            await session.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
        ).scalars().all()
    }
    companies = {
        c.tenant_id: c
        for c in (
            await session.execute(select(Company).where(Company.tenant_id.in_(tenant_ids)))
        ).scalars().all()
    }
    return tenants, companies


@router.get("/jobs", response_model=PortalJobsOut)
async def portal_jobs(
    search: str | None = None,
    all_jobs: bool = False,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> PortalJobsOut:
    """The candidate's New Jobs board (FR-3.5/9.1).

    By default this returns the published jobs RELEVANT to this candidate,
    ranked against their main resume, its parsed skills, and their profile form
    (`services/job_relevance.py`) — not the whole cross-tenant catalogue.

    `?search=` is the escape hatch: it searches every published job and skips
    relevance filtering entirely, so a candidate can always find a role they
    know the name of. `?all_jobs=true` shows the unfiltered board.

    This ranking is candidate-side presentation ONLY. It never decides who is
    scored — every non-archived link on a job still enters the scoring pool.
    """
    candidate = await _candidate_for_user(session, user)
    jobs = list(
        (
            await session.execute(
                select(Job).where(Job.ratified_at.isnot(None))
                .order_by(Job.created_at.desc())
            )
        ).scalars().all()
    )

    # ── The 30-day posting window (spec §2.2) ────────────────────────────────
    # Applied BEFORE relevance ranking and before search, deliberately: this is
    # an access rule, not a presentation preference. `?search=` bypasses
    # relevance, and if the window check sat downstream of it a candidate could
    # search their way to a job they must never see (spec Rule 3).
    applied_job_ids = {
        row.job_id
        for row in await session.execute(
            select(JobCandidateLink.job_id).where(
                JobCandidateLink.candidate_id == candidate.id
            )
        )
    }
    jobs = [
        job
        for job in jobs
        if job_posting.can_view_job(
            posting_start=job.posting_start_date,
            posting_end_date=job.posting_end_date,
            grace_period_end_date=job.grace_period_end_date,
            candidate_created_at=candidate.created_at,
            has_applied=job.id in applied_job_ids,
        )
    ]

    searching = bool(search and search.strip())
    if searching:
        jobs = [job for job in jobs if job_relevance.matches_search(job, search or "")]
    elif not all_jobs:
        main = await _main_resume_profile(session, candidate)
        signal = job_relevance.candidate_signal(main, candidate.profile_form_json)
        ranked = await job_relevance.rank_jobs(session, jobs, signal)
        jobs = job_relevance.visible(ranked, signal)

    tenants, companies = await _employers_for(session, jobs)
    return PortalJobsOut(jobs=[
        _portal_job_out(job, tenants.get(job.tenant_id), companies.get(job.tenant_id))
        for job in jobs
    ])


@router.get("/jobs/{job_id}", response_model=PortalJobOut)
async def portal_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> PortalJobOut:
    """View a single published job by id — the public job link target
    (`readypick.ai/apply/{job_uuid}`). Any authenticated candidate may view it
    regardless of prior contact (FR-3.5, open application), PROVIDED the
    posting window still admits them (spec §2.2): direct-URL access is exactly
    the path Rule 3 has to close, not just the job board."""
    candidate = await _candidate_for_user(session, user)
    job = await _visible_job_or_404(session, candidate, job_id)
    tenant = await session.get(Tenant, job.tenant_id)
    company = (
        await session.execute(select(Company).where(Company.tenant_id == job.tenant_id))
    ).scalars().first()
    return _portal_job_out(job, tenant, company)


# ── Self-service profile CRUD (Settings & Profile) ──────────────────────────
#
# ASSUMPTION: the Settings & Profile page is reachable from every portal, so
# these two endpoints accept ANY signed-in audience (candidate, org staff,
# owner) rather than the candidate audience alone — the row a caller can read
# or write is pinned to their own JWT `user_id`, so widening the audience
# widens nothing else. `users` is a global table keyed by that id, which is why
# the session below is not tenant-scoped; every query filters by user_id
# exactly, the same argument `get_candidate_db` already makes.


async def _self_db(
    _user: CurrentUser = Depends(get_current_any),
) -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                yield session


async def _me_out(session: AsyncSession, user: CurrentUser) -> MeOut:
    row = await session.get(User, user.user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return MeOut(
        id=row.id,
        full_name=row.full_name,
        email=row.email,
        phone=row.phone,
        role=row.role.value if hasattr(row.role, "value") else str(row.role),
    )


@router.get("/me", response_model=MeOut)
async def get_me(
    user: CurrentUser = Depends(get_current_any),
    session: AsyncSession = Depends(_self_db),
) -> MeOut:
    """The signed-in user's own editable details."""
    return await _me_out(session, user)


@router.patch("/me", response_model=MeOut)
async def update_me(
    body: MeUpdateIn,
    user: CurrentUser = Depends(get_current_any),
    session: AsyncSession = Depends(_self_db),
) -> MeOut:
    """Update the signed-in user's own name and/or phone.

    Both the `users` row and the caller's matching `candidates` row are kept in
    step, so a corrected name shows up on the HR Review Screen (which reads the
    candidate record) and not just in the portal header. `email` is read-only
    (claude.md rule 2) and is rejected by the request schema.
    """
    row = await session.get(User, user.user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown user")

    fields = body.model_fields_set
    changed: dict[str, object] = {}
    if "full_name" in fields and body.full_name is not None:
        row.full_name = body.full_name
        changed["full_name"] = body.full_name
    if "phone" in fields:
        row.phone = body.phone
        changed["phone"] = body.phone

    if changed:
        # Keep the shared candidate record in step. Matched the same way
        # `_candidate_for_user` matches, so a candidate whose record predates
        # their login (created by an employer's outreach) is still updated.
        match = Candidate.user_id == user.user_id
        if row.email:  # a phone-only account has no email to match on
            match = match | (Candidate.email == row.email)
        candidates = (
            await session.execute(select(Candidate).where(match))
        ).scalars().all()
        for candidate in candidates:
            if candidate.user_id is None:
                candidate.user_id = user.user_id
            if "full_name" in changed:
                candidate.full_name = row.full_name
            if "phone" in changed:
                candidate.phone = row.phone
        await session.flush()
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="profile_updated",
            target_type="user",
            target_id=row.id,
            metadata={"fields": sorted(changed)},
        )
    return await _me_out(session, user)


# ── The unified candidate profile (My Profile) ──────────────────────────────
#
# The 40 validation aspects are answered ONCE here rather than inside every
# job's assessment conversation (client decision, 2026-07-27), alongside the
# candidate's MAIN resume. Both are reused on every application.


class ProfileFormOut(BaseModel):
    """The advanced form: its definition, the candidate's saved answers, and
    the main resume that goes with it."""
    definition: dict
    answers: dict = {}
    complete: bool = False
    missing: list[str] = []
    updated_at: datetime | None = None
    main_resume: StoredResumeOut = StoredResumeOut()


class ProfileFormIn(BaseModel):
    """Answers keyed by the form's field keys. Unknown keys are dropped rather
    than stored — `candidate_profile_form.clean_answers` is the gate."""
    answers: dict


def _profile_form_out(candidate: Candidate, main: Profile | None) -> ProfileFormOut:
    answers = candidate.profile_form_json or {}
    return ProfileFormOut(
        definition=profile_form.form_definition(),
        answers=answers,
        complete=profile_form.is_complete(answers),
        missing=profile_form.missing_required(answers),
        updated_at=candidate.profile_form_updated_at,
        main_resume=_resume_summary(main),
    )


async def _main_resume_profile(
    session: AsyncSession, candidate: Candidate
) -> Profile | None:
    """The candidate's MAIN resume — the one shown on My Profile and offered on
    every application. Falls back to the newest profile carrying a resume so a
    candidate who applied before this feature existed still has one."""
    if candidate.main_profile_id is not None:
        main = await session.get(Profile, candidate.main_profile_id)
        if main is not None and main.resume_public_id is not None:
            return main
    return await _previous_resume(session, candidate)


@router.get("/me/profile-form", response_model=ProfileFormOut)
async def get_profile_form(
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ProfileFormOut:
    """My Profile: the advanced form definition plus this candidate's answers."""
    candidate = await _candidate_for_user(session, user)
    return _profile_form_out(candidate, await _main_resume_profile(session, candidate))


@router.put("/me/profile-form", response_model=ProfileFormOut)
async def save_profile_form(
    body: ProfileFormIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ProfileFormOut:
    """Save the advanced form. Partial saves are allowed — the candidate can
    come back to it — so missing required answers are REPORTED, not rejected.
    Personal fields the ATS shows are mirrored onto the candidate record."""
    candidate = await _candidate_for_user(session, user)
    answers = profile_form.clean_answers(body.answers)
    candidate.profile_form_json = answers
    candidate.profile_form_updated_at = datetime.now(timezone.utc)
    # Keep the denormalised candidate columns in step so the HR Review Screen
    # shows a city rather than a blank, exactly as the outreach flow did.
    if city := answers.get("current_city"):
        candidate.city = str(city)[:120]
    if full_name := answers.get("declaration_full_name"):
        candidate.full_name = str(full_name)[:255]
    await session.flush()
    await audit(
        session,
        tenant_id=None,
        actor_user_id=user.user_id,
        action="candidate_profile_form_saved",
        target_type="candidate",
        target_id=candidate.id,
        metadata={"answered": len(answers), "complete": profile_form.is_complete(answers)},
    )
    return _profile_form_out(candidate, await _main_resume_profile(session, candidate))


@router.put("/me/resume", response_model=StoredResumeOut)
async def replace_main_resume(
    resume: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> StoredResumeOut:
    """Upload or re-upload the candidate's MAIN resume (FR-6.2).

    A fresh Profile row carries the file so past applications keep pointing at
    the resume they were actually submitted with — an application is an
    immutable snapshot. Only `candidates.main_profile_id` moves.
    """
    candidate = await _candidate_for_user(session, user)
    asset = await store_resume(resume)
    main = Profile(
        candidate_id=candidate.id,
        aspects_json=candidate.profile_form_json or {},
        aspects_completed_at=candidate.profile_form_updated_at,
    )
    apply_resume_asset(main, asset)
    session.add(main)
    await session.flush()
    candidate.main_profile_id = main.id
    await session.flush()
    celery_app.send_task("pickready.parse_resume", args=[str(main.id)])
    return _resume_summary(main)


def _resume_summary(profile: Profile | None) -> StoredResumeOut:
    if profile is None:
        return StoredResumeOut()
    return StoredResumeOut(
        has_resume=True,
        filename=profile.resume_original_filename,
        size_bytes=profile.resume_size_bytes,
        uploaded_at=profile.resume_uploaded_at,
    )


@router.get("/me/resume", response_model=StoredResumeOut)
async def my_stored_resume(
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> StoredResumeOut:
    """Is there a main resume on file to reuse, and which one? (FR-6.2.)"""
    candidate = await _candidate_for_user(session, user)
    return _resume_summary(await _main_resume_profile(session, candidate))


@router.get("/jobs/{job_id}/apply-context", response_model=ApplyContextOut)
async def apply_context(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ApplyContextOut:
    """State the apply UI needs up front: has this candidate already applied to
    this job, is there a main resume they can reuse, and is their profile form
    complete? Lets the page say "you already applied" instead of surfacing a raw
    409, and prompt for a missing profile before the upload rather than after."""
    candidate = await _candidate_for_user(session, user)
    job = await _published_job_or_404(session, job_id)
    existing = (
        await session.execute(
            select(JobCandidateLink).where(
                JobCandidateLink.job_id == job.id,
                JobCandidateLink.candidate_id == candidate.id,
            )
        )
    ).scalars().first()
    answers = candidate.profile_form_json or {}
    # The mandatory fields are answered once and reused. Sourced from the most
    # recent application rather than a profile column so each application keeps
    # its own snapshot of what was true when it was submitted (the rule that put
    # them on the link in the first place); only the TYPING is saved.
    previous = (
        await session.execute(
            select(JobCandidateLink.validation_json)
            .where(JobCandidateLink.candidate_id == candidate.id)
            .order_by(JobCandidateLink.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    return ApplyContextOut(
        job_id=job.id,
        already_applied=existing is not None,
        applied_at=existing.created_at if existing is not None else None,
        resume=_resume_summary(await _main_resume_profile(session, candidate)),
        profile_complete=profile_form.is_complete(answers),
        profile_missing=profile_form.missing_required(answers),
        validation_fields=[dict(field) for field in application_validation.VALIDATION_FIELDS],
        validation_intro=application_validation.SECTION_INTRO,
        validation_values=application_validation.reusable_defaults(previous),
    )


@router.post(
    "/jobs/{job_id}/apply", response_model=ApplyOut, status_code=status.HTTP_201_CREATED
)
async def apply_to_job(
    job_id: uuid.UUID,
    aspects: str = Form(default="{}"),  # legacy/optional extra answers
    resume: UploadFile | None = File(default=None),
    reuse_previous: bool = Form(default=False),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
    full_name: str | None = Form(default=None),
    residing_city: str | None = Form(default=None),
    age: int | None = Form(default=None),
    gender: str | None = Form(default=None),
    # Where the applicant came from (spec §1.1). The public /apply page posts
    # "sourced" when the candidate arrived via an external job link; the
    # in-portal board posts nothing and defaults to "direct".
    #
    # Declared LAST on purpose: the existing tests call this handler
    # positionally through `reuse_previous, user, session`, so inserting a
    # parameter ahead of those would silently shift `session` and hand the
    # handler a `Depends` object instead of a database session.
    application_source: str = Form(default="direct"),
    # The six mandatory validation fields (spec §7), posted as one JSON object
    # alongside the resume. Declared last for the same positional-call reason as
    # `application_source` above.
    validation: str = Form(default="{}"),
) -> ApplyOut:
    """Open application to any published job (FR-3.5/6.1/9.2).

    Two data sets travel with an application, and they are not the same thing:

    * The candidate's My Profile form is SNAPSHOTTED onto this application's
      Profile (`aspects_json`), so the report and the ATS read exactly what they
      always did.
    * The six MANDATORY validation fields — current CTC, expected CTC, notice
      period, joining date, document readiness, and why the role interests them
      — are answered per application and land on the link's `validation_json`.
      They are captured, never scored (spec §7), and capturing them here rather
      than after the conversation is what lets a recruiter filter out a
      candidate plainly outside the budget or notice window before a single
      credit is spent on assessing them.

    The candidate either applies with their MAIN resume (`reuse_previous=true`)
    or uploads a fresh one for this application. Each application still mints
    its OWN Profile. No prior-contact gate — any authenticated candidate may
    apply."""
    candidate = await _candidate_for_user(session, user)
    job = await _visible_job_or_404(session, candidate, job_id)

    # A NEW application requires the 30-day active window. The grace period is
    # for editing an application that already exists (spec §5.1) and never for
    # creating one — 409 rather than 404 because this candidate can legitimately
    # see the job, so hiding the reason would just confuse them.
    if not job_posting.can_apply(
        posting_start=job.posting_start_date,
        posting_end_date=job.posting_end_date,
        grace_period_end_date=job.grace_period_end_date,
        candidate_created_at=candidate.created_at,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Applications for this role closed on "
                f"{job.posting_end_date:%d %b %Y}."
            ),
        )

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

    try:
        extra_aspects = json.loads(aspects or "{}")
        if not isinstance(extra_aspects, dict):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="aspects must be a JSON object") from exc

    # ── The mandatory fields (spec §7) ──────────────────────────────────────
    # Refused BEFORE the resume is stored: rejecting the application after a
    # file has been uploaded to remote storage leaves an orphaned asset behind
    # for an error the candidate can fix in ten seconds.
    try:
        validation_data = json.loads(validation or "{}")
        if not isinstance(validation_data, dict):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="validation must be a JSON object"
        ) from exc
    missing = application_validation.missing_fields(validation_data)
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Please complete every required field: " + ", ".join(missing),
        )
    validation_data = application_validation.normalise(validation_data)

    # The profile form is the source of truth; anything posted alongside it is
    # merged UNDER it so a stale client can never overwrite saved answers.
    aspects_data: dict = {**extra_aspects, **(candidate.profile_form_json or {})}

    # Resolve the resume: a fresh upload wins; otherwise use the main resume.
    resume_reused = False
    if resume is not None and resume.filename:
        asset = await store_resume(resume)
    elif reuse_previous:
        previous_profile = await _main_resume_profile(session, candidate)
        if previous_profile is None:
            raise HTTPException(
                status_code=422,
                detail="No main resume to reuse, upload one with this application",
            )
        resume_reused = True
    else:
        raise HTTPException(
            status_code=422,
            detail="Attach a resume file or set reuse_previous=true (FR-6.2)",
        )

    # The apply form collects the personal fields alongside the questionnaire
    # (FR-5.1 a–d). They belong on the Candidate, not only in aspects_json, so
    # the ATS shows a name rather than a blank row.
    if isinstance(full_name, str) and full_name.strip():
        candidate.full_name = full_name.strip()
    if isinstance(residing_city, str) and residing_city.strip():
        candidate.city = residing_city.strip()
    if isinstance(age, int):
        candidate.age = age
    if isinstance(gender, str) and gender.strip():
        candidate.gender = gender.strip()

    # Databank re-use consent (PRD §10 / FR-4.2). The profile form's declaration
    # carries it now; legacy aspect 40 remains the fallback for old payloads.
    consent = aspects_data.get("declaration_accepted", aspects_data.get("40"))
    candidate.consent_databank = bool(consent) and str(consent).lower() not in ("false", "no", "0")

    profile = Profile(
        candidate_id=candidate.id, source_tenant_id=job.tenant_id,
        aspects_json=aspects_data, aspects_completed_at=datetime.now(timezone.utc),
    )
    if resume_reused:
        copy_resume_metadata(previous_profile, profile)
    else:
        apply_resume_asset(profile, asset)
    session.add(profile)
    await session.flush()
    # A first-time applicant who uploaded here now has a main resume — otherwise
    # their My Profile page would show none straight after applying.
    if not resume_reused and candidate.main_profile_id is None:
        candidate.main_profile_id = profile.id
    link = JobCandidateLink(
        tenant_id=job.tenant_id, job_id=job.id, candidate_id=candidate.id,
        profile_id=profile.id, source=LinkSource.fresh,
        # Anything other than the one known alternative reads as "direct" — a
        # crafted value must not end up in the column the CHECK constraint
        # guards, and mis-attributing a source is not worth a 422 to the
        # candidate mid-application.
        application_source=(
            "sourced" if application_source == "sourced" else "direct"
        ),
        status=hiring_pipeline.APPLIED,
        status_updated_at=datetime.now(timezone.utc),
        current_stage=hiring_pipeline.STAGE_LABELS[hiring_pipeline.APPLIED],
        validation_json=validation_data,
    )
    session.add(link)
    await session.flush()

    # ── Retake classification ────────────────────────────────────────────────
    # Every application now runs its own assessment: under PPI the questions
    # and the framework are generated from THIS job, so a prior report grades
    # criteria this job never used (services/retake). The classification still
    # runs so the candidate is told WHY they are answering questions again,
    # before they open the assessment rather than after.
    decision = await retake.decide(session, candidate.id, job.id)
    if job.assessment_status == "ready_for_candidates":
        session.add(
            AssessmentConversation(
                tenant_id=job.tenant_id,
                job_id=job.id,
                job_candidate_link_id=link.id,
                grade=job.assessment_grade or "non_managerial",
            )
        )
        await session.flush()
        # Start generating THIS candidate's questions now, not when they first
        # press Start.
        #
        # `_ensure_conversation_ready` used to be the only thing that enqueued
        # this, and it enqueues LAZILY: the candidate opens the assessment, the
        # questions do not exist yet, so it fires the task and answers 409 "We
        # are preparing your assessment. Please try again in a moment." The
        # candidate then waits on an LLM chain that legitimately takes a while,
        # refreshing a page that keeps saying the same thing. That is the delay
        # between applying and being able to begin.
        #
        # Enqueued here, generation runs while the candidate is still reading
        # the confirmation screen, so choosing "start now" is genuinely
        # available immediately. The lazy path stays exactly as it is: it is the
        # backstop for an application that predates this change, for a job whose
        # setup was approved after the candidate applied, and for a task that
        # failed. Both paths are idempotent -- generate_candidate_questions
        # writes rows keyed on the link, and `_ensure_conversation_ready` only
        # fires when the count is still zero.
        #
        # Never allowed to fail the application, for the same reason as the
        # confirmation email below: a broker hiccup must not cost the candidate
        # their submission, and the lazy path will still cover it.
        try:
            celery_app.send_task(
                "pickready.generate_candidate_questions", args=[str(link.id)]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "portal.question_generation_enqueue_failed link_id=%s error=%s",
                link.id, type(exc).__name__,
            )

    celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
    # Email 1 of 6: confirm receipt (spec §6.1). Enqueued, never inline
    # (claude.md rule 4), and never allowed to fail the application — a broker
    # hiccup must not cost the candidate their submission.
    try:
        celery_app.send_task(
            "pickready.send_application_confirmation", args=[str(link.id)]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "portal.confirmation_enqueue_failed link_id=%s error=%s",
            link.id, type(exc).__name__,
        )
    return ApplyOut(
        link_id=link.id, job_id=job.id, profile_id=profile.id,
        resume_reused=resume_reused, aspects_received=len(aspects_data),
        assessment_required=decision.requires_new_assessment,
        assessment_notice=decision.message(),
    )


@router.get("/applications", response_model=ApplicationsOut)
async def my_applications(
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ApplicationsOut:
    """Application Stage Status (FR-9.1).

    Two queries total, whatever the number of applications. It used to be six
    PER application — job, tenant, latest status, conversation, report existence
    and timeline — so a candidate with twenty applications paid a hundred and
    twenty round trips for one page. The joined query below answers all of that
    at once, and the timelines come back in a single batched call.
    """
    candidate = await _candidate_for_user(session, user)
    rows = (
        await session.execute(
            select(
                JobCandidateLink,
                Job,
                Tenant.name,
                AssessmentConversation,
                # EXISTS rather than a join: a link is UNIQUE on its report
                # today, but joining a table that could ever return two rows
                # would silently duplicate an application in this list.
                select(FunctionalSkillsReport.id)
                .where(FunctionalSkillsReport.job_candidate_link_id == JobCandidateLink.id)
                .limit(1)
                .exists()
                .label("report_ready"),
                select(PipelineStatusEntry.status)
                .where(PipelineStatusEntry.job_candidate_link_id == JobCandidateLink.id)
                .order_by(PipelineStatusEntry.at.desc())
                .limit(1)
                .scalar_subquery()
                .label("latest_status"),
            )
            .select_from(JobCandidateLink)
            .outerjoin(Job, Job.id == JobCandidateLink.job_id)
            .outerjoin(Tenant, Tenant.id == JobCandidateLink.tenant_id)
            .outerjoin(
                AssessmentConversation,
                AssessmentConversation.job_candidate_link_id == JobCandidateLink.id,
            )
            .where(JobCandidateLink.candidate_id == candidate.id)
            .order_by(JobCandidateLink.created_at.desc())
        )
    ).all()
    all_timelines = await hiring_pipeline.timelines(
        session, [row[0].id for row in rows]
    )

    out: list[ApplicationOut] = []
    for link, job, company_name, conversation, report_ready, latest_status in rows:
        window = job_posting.describe(job) if job else None
        can_edit = job is not None and job_posting.can_edit_application(
            applied_at=link.created_at,
            posting_start=job.posting_start_date,
            posting_end_date=job.posting_end_date,
            grace_period_end_date=job.grace_period_end_date,
        )
        status = hiring_pipeline.normalize(link.status)
        # The legacy `stage` field only understands the OLD five-value enum, so
        # a new pipeline stage maps to None there rather than being coerced
        # into a value it does not mean.
        legacy_stage = latest_status
        try:
            legacy_stage = PipelineStatus(legacy_stage) if legacy_stage else None
        except ValueError:
            legacy_stage = None

        out.append(ApplicationOut(
            link_id=link.id,
            job_id=link.job_id,
            job_title=job.title if job else "",
            company_name=company_name,
            applied_at=link.created_at,
            stage=legacy_stage,
            assessment_status=job.assessment_status if job else None,
            conversation_status=conversation.status if conversation else None,
            report_ready=report_ready,
            status=status,
            stage_label=hiring_pipeline.STAGE_LABELS.get(status, status),
            status_updated_at=link.status_updated_at,
            timeline=[
                StatusEventOut(**event)
                for event in all_timelines.get(str(link.id), [])
            ],
            posting_status=window.posting_status if window else None,
            posting_end_date=window.posting_end_date if window else None,
            grace_period_end_date=window.grace_period_end_date if window else None,
            can_edit=can_edit,
            edit_closes_at=window.grace_period_end_date if window else None,
            days_until_edit_closes=(
                window.days_until_grace_ends if window and can_edit else 0
            ),
            # The invitation, not the application, is what unlocks the
            # assessment (spec §3.1).
            assessment_invited=(
                conversation is not None and conversation.invitation_sent_at is not None
            ),
            assessment_completed=(
                conversation is not None and conversation.completed_at is not None
            ),
        ))
    return ApplicationsOut(applications=out)


@router.patch("/applications/{link_id}", response_model=ApplyOut)
async def edit_application(
    link_id: uuid.UUID,
    resume: UploadFile | None = File(default=None),
    refresh_profile_form: bool = Form(default=False),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ApplyOut:
    """Edit an application during the 5-day grace period (spec §5.1).

    Two things are editable, matching the spec: the RESUME, and the validation
    form answers. Both work the same way — the application's Profile snapshot
    is updated in place, so the recruiter sees the candidate's current material
    rather than a second competing application.

    Deliberately NOT editable: which job this is (that would be a new
    application) and the candidate's identity. Those are the spec's own
    exclusions and they are also the two that would invalidate the matching
    already computed against this row.

    Editing is allowed while the job is ACTIVE as well as during the grace
    period — the grace period extends the right, it does not create it. Refused
    with 409 once the window has closed.
    """
    candidate = await _candidate_for_user(session, user)
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.candidate_id != candidate.id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Application not found")

    if not job_posting.can_edit_application(
        applied_at=link.created_at,
        posting_start=job.posting_start_date,
        posting_end_date=job.posting_end_date,
        grace_period_end_date=job.grace_period_end_date,
    ):
        closes = job.grace_period_end_date
        raise HTTPException(
            status_code=409,
            detail=(
                "The edit window for this application closed on "
                f"{closes:%d %b %Y}." if closes else
                "This application can no longer be edited."
            ),
        )

    if resume is None and not refresh_profile_form:
        raise HTTPException(
            status_code=422,
            detail="Attach a new resume or set refresh_profile_form=true",
        )

    profile = await session.get(Profile, link.profile_id) if link.profile_id else None
    if profile is None:
        raise HTTPException(
            status_code=409, detail="This application has no profile to edit"
        )

    resume_replaced = False
    if resume is not None and resume.filename:
        # Overwrite the snapshot in place rather than minting a new Profile:
        # this IS the same application, and a second profile would leave the
        # recruiter looking at whichever one their query happened to join.
        apply_resume_asset(profile, await store_resume(resume))
        profile.resume_text = None          # re-extracted by the parse task
        profile.embedding = None            # re-embedded from the new text
        resume_replaced = True

    if refresh_profile_form:
        # Re-snapshot the candidate's CURRENT My Profile answers onto this
        # application. The form is the source of truth; this is what makes an
        # edit there reach an application already submitted.
        profile.aspects_json = candidate.profile_form_json or {}
        profile.aspects_completed_at = datetime.now(timezone.utc)

    await session.flush()
    await audit(
        session,
        tenant_id=link.tenant_id,
        actor_user_id=None,
        action="application_edited_in_grace_period",
        target_type="job_candidate_link",
        target_id=link.id,
        metadata={
            "resume_replaced": resume_replaced,
            "profile_form_refreshed": refresh_profile_form,
        },
    )
    if resume_replaced:
        # Re-parse and re-embed, then re-score: the recruiter's ranking must
        # reflect the resume actually on file.
        celery_app.send_task("pickready.parse_resume", args=[str(profile.id)])
        celery_app.send_task("pickready.run_matching", args=[str(link.job_id)])

    return ApplyOut(
        link_id=link.id,
        job_id=link.job_id,
        profile_id=profile.id,
        resume_reused=not resume_replaced,
        aspects_received=len(profile.aspects_json or {}),
        assessment_required=False,
        assessment_notice=None,
    )
