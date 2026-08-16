"""Candidate outreach + employer verification (FR-5.x / ESD §10).

- Outreach applies to FRESHLY sourced candidates only — Databank candidates
  never re-enter this flow (claude.md rule 7); they are reported as skipped.
- The outreach link is a signed stateless JWT (see deps.make_outreach_token).
- The employer form token is `verification_requests.token`
  (secrets.token_urlsafe(32)), single-use: any status other than `pending`
  rejects re-submission.
"""
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_public_db,
    get_tenant_db,
    make_outreach_token,
    require_capability,
)
from app.core.config import get_settings
from app.models.candidate import Candidate, JobCandidateLink, Profile, VerificationRequest
from app.models.enums import LinkSource, SubmittedVia, VerificationStatus
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.verification import (
    EmployerFormField,
    EmployerFormIn,
    EmployerFormOut,
    FormSubmitOut,
    InboundEmailIn,
    InboundEmailOut,
    OutreachIn,
    OutreachOut,
    OverrideIn,
    ProfileVerificationOut,
    VerificationRequestOut,
)
from app.services import capabilities as caps
from app.services.audit import audit
from app.workers.celery_app import celery_app

router = APIRouter()

EMPLOYER_FORM_FIELDS: list[EmployerFormField] = [
    EmployerFormField(name="designation", label="Designation"),
    EmployerFormField(name="doj", label="Date of Joining", type="date"),
    EmployerFormField(name="doe", label="Date of Exit", type="date"),
    EmployerFormField(name="last_drawn_ctc", label="Last Drawn CTC"),
    EmployerFormField(name="last_drawn_gross", label="Last Drawn Gross"),
    EmployerFormField(name="noc_status", label="NOC Status"),
    EmployerFormField(name="exit_formalities_complete",
                      label="Exit Formalities Completed", type="boolean"),
    EmployerFormField(name="bgv_status", label="BGV Status"),
    EmployerFormField(name="proofs_details",
                      label="Educational / Address / ID Proof Details", required=False),
    EmployerFormField(name="prior_experience_details",
                      label="Prior Experience / Compensation Details", required=False),
]

# Recipient scheme for inbound reply-parsing fallback: verify+<token>@<domain>
_TOKEN_IN_ADDRESS = re.compile(r"verify\+([A-Za-z0-9_\-]+)@")
_TOKEN_IN_BODY = re.compile(r"/verification/form/([A-Za-z0-9_\-]{20,})")


@router.post("/outreach", response_model=OutreachOut)
async def send_outreach(
    body: OutreachIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> OutreachOut:
    """HR emails selected FRESH candidates the 40-aspect + data request
    (FR-5.1/5.2). Databank candidates are skipped, never silently."""
    job = await session.get(Job, body.job_id)
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")

    # Loaded once, outside the loop: the template addresses the candidate on
    # behalf of a named company, and rendering it with an empty company name
    # produces "a role at ." in the candidate's inbox.
    tenant = await session.get(Tenant, user.tenant_id)
    company_name = tenant.name if tenant is not None else "ReadyPick"

    sent: list[uuid.UUID] = []
    skipped_databank: list[uuid.UUID] = []
    not_linked: list[uuid.UUID] = []

    for candidate_id in body.candidate_ids:
        link = (
            await session.execute(
                select(JobCandidateLink).where(
                    JobCandidateLink.job_id == job.id,
                    JobCandidateLink.candidate_id == candidate_id,
                )
            )
        ).scalars().first()
        if link is None or link.profile_id is None:
            not_linked.append(candidate_id)
            continue
        if link.source == LinkSource.databank:
            # Databank profiles are reused as-is (FR-4.4 / claude.md rule 7).
            skipped_databank.append(candidate_id)
            continue

        candidate = await session.get(Candidate, candidate_id)
        token = make_outreach_token(link.profile_id, job.id)
        # The candidate page is served at /portal/outreach/[token] (see
        # frontend/app/(candidate)/portal/outreach/[token]/page.tsx). The bare
        # /outreach/{token} this used to build has no route, so every emailed
        # link 404'd even once the mail itself was delivered.
        outreach_url = f"{get_settings().frontend_url}/portal/outreach/{token}"
        celery_app.send_task(
            "pickready.send_email",
            args=[
                str(user.tenant_id), candidate.email, "candidate_outreach",
                {"outreach_url": outreach_url,
                 # Alias: a tenant-authored row may still use the older
                 # {{outreach_link}} placeholder, and an unknown placeholder
                 # renders as an empty string rather than failing loudly.
                 "outreach_link": outreach_url,
                 "job_title": job.title,
                 "candidate_name": candidate.full_name,
                 "company_name": company_name},
            ],
        )
        sent.append(candidate_id)

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="outreach_sent", target_type="job", target_id=job.id,
                metadata={"sent": [str(c) for c in sent],
                          "skipped_databank": [str(c) for c in skipped_databank]})
    return OutreachOut(sent=sent, skipped_databank=skipped_databank, not_linked=not_linked)


@router.get("/profile/{profile_id}", response_model=ProfileVerificationOut)
async def profile_verification_status(
    profile_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ProfileVerificationOut:
    profile = await session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    requests = (
        await session.execute(
            select(VerificationRequest)
            .where(VerificationRequest.profile_id == profile_id)
            .order_by(VerificationRequest.employer_seq)
        )
    ).scalars().all()
    return ProfileVerificationOut(
        profile_id=profile_id,
        requests=[VerificationRequestOut.model_validate(r) for r in requests],
        all_resolved=bool(requests) and all(
            r.status != VerificationStatus.pending for r in requests
        ),
    )


@router.post("/requests/{request_id}/override", response_model=VerificationRequestOut)
async def override_verification(
    request_id: uuid.UUID,
    body: OverrideIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> VerificationRequestOut:
    """Explicit HR override with a logged reason (ESD §10) — the only way a
    fresh candidate moves forward without an employer response."""
    vr = await session.get(VerificationRequest, request_id)
    if vr is None or vr.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Verification request not found")
    if vr.status != VerificationStatus.pending:
        raise HTTPException(status_code=409, detail="Request is already resolved")
    vr.status = VerificationStatus.overridden
    vr.override_reason = body.reason
    vr.responded_at = datetime.now(timezone.utc)
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="verification_overridden", target_type="verification_request",
                target_id=vr.id, metadata={"reason": body.reason})
    return VerificationRequestOut.model_validate(vr)


# ── PUBLIC tokenized employer form (no auth; the token is the auth) ─────────

async def _pending_request_by_token(
    session: AsyncSession, token: str
) -> VerificationRequest:
    vr = (
        await session.execute(
            select(VerificationRequest).where(VerificationRequest.token == token)
        )
    ).scalars().first()
    if vr is None:
        raise HTTPException(status_code=404, detail="Invalid link")
    if vr.status != VerificationStatus.pending:
        # Single-use: any resolved state rejects the link.
        raise HTTPException(status_code=410, detail="This link has already been used")
    return vr


@router.get("/form/{token}", response_model=EmployerFormOut)
async def get_employer_form(
    token: str, session: AsyncSession = Depends(get_public_db)
) -> EmployerFormOut:
    vr = await _pending_request_by_token(session, token)
    profile = await session.get(Profile, vr.profile_id)
    candidate = await session.get(Candidate, profile.candidate_id) if profile else None
    return EmployerFormOut(
        candidate_name=candidate.full_name if candidate else None,
        employer_name=vr.employer_name,
        fields=EMPLOYER_FORM_FIELDS,
    )


@router.post("/form/{token}", response_model=FormSubmitOut)
async def submit_employer_form(
    token: str,
    body: EmployerFormIn,
    session: AsyncSession = Depends(get_public_db),
) -> FormSubmitOut:
    vr = await _pending_request_by_token(session, token)
    vr.response_json = body.model_dump(mode="json")
    vr.status = VerificationStatus.submitted
    vr.submitted_via = SubmittedVia.form
    vr.responded_at = datetime.now(timezone.utc)
    await session.flush()
    await audit(session, tenant_id=vr.tenant_id, actor_user_id=None,
                action="verification_form_submitted", target_type="verification_request",
                target_id=vr.id, metadata={"via": "form"})
    return FormSubmitOut(status=vr.status)


@router.post("/inbound-email", response_model=InboundEmailOut)
async def inbound_email_webhook(
    body: InboundEmailIn, session: AsyncSession = Depends(get_public_db)
) -> InboundEmailOut:
    """Resend inbound-parsing webhook: when an employer replies by email
    instead of using the form, enqueue LLM extraction as the fallback path
    (FR-5.3). Always returns 200 so the provider does not retry storms."""
    recipients = body.to if isinstance(body.to, list) else [body.to or ""]
    token: str | None = None
    for addr in recipients:
        match = _TOKEN_IN_ADDRESS.search(addr or "")
        if match:
            token = match.group(1)
            break
    if token is None:
        # Fallback: the reply often quotes the original form URL.
        match = _TOKEN_IN_BODY.search(body.text or "")
        token = match.group(1) if match else None
    if token is None:
        return InboundEmailOut(matched=False)

    vr = (
        await session.execute(
            select(VerificationRequest).where(VerificationRequest.token == token)
        )
    ).scalars().first()
    if vr is None or vr.status != VerificationStatus.pending:
        return InboundEmailOut(matched=False)

    celery_app.send_task(
        "pickready.parse_verification_reply", args=[str(vr.id), body.text or ""]
    )
    return InboundEmailOut(matched=True)
