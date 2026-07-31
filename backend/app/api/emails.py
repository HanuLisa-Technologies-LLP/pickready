"""Lifecycle email endpoints (spec §6): draft with AI, edit, send, audit.

Flow the UI drives:
  1. Recruiter selects candidates and picks an email type.
  2. POST /emails/draft  -> one AI draft PER candidate, personalised.
  3. Recruiter reads and optionally edits each draft.
  4. POST /emails/send   -> each message is written to `email_log` first, then
     a Celery task delivers it (claude.md rules 4 and 5).

The log row is created BEFORE the send is attempted, so a message that fails in
transit still leaves a record of what was going to be said and why it did not
arrive. The worker owns the queued -> sent | failed transition.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_db, require_capability
from app.core.config import get_settings
from app.models.candidate import Candidate, JobCandidateLink
from app.models.email_log import STATUS_QUEUED, EmailLog
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.emails import (
    EmailDraftIn,
    EmailDraftOut,
    EmailDraftsOut,
    EmailLogOut,
    EmailSendIn,
    EmailSendOut,
)
from app.services import capabilities as caps
from app.services import lifecycle_email
from app.services.audit import audit
from app.services.matching import RANKING_COMMENT_KEYS, ranking_payload
from app.workers.celery_app import celery_app

router = APIRouter()


async def _load_targets(
    session: AsyncSession, user: CurrentUser, link_ids: list[uuid.UUID]
) -> tuple[list[tuple[JobCandidateLink, Candidate, Job]], list[dict]]:
    """Resolve link ids to (link, candidate, job), reporting what was skipped.

    A candidate with no email address on file is SKIPPED, not failed: the rest
    of a 20-person batch must still go out, and the recruiter needs to be told
    exactly who was left behind rather than discovering it later.
    """
    targets: list[tuple[JobCandidateLink, Candidate, Job]] = []
    skipped: list[dict] = []
    for link_id in link_ids:
        link = await session.get(JobCandidateLink, link_id)
        # Explicit tenant check is defense in depth; RLS is the boundary.
        if link is None or link.tenant_id != user.tenant_id:
            skipped.append({"link_id": str(link_id), "reason": "Application not found"})
            continue
        candidate = await session.get(Candidate, link.candidate_id)
        job = await session.get(Job, link.job_id)
        if candidate is None or job is None:
            skipped.append({"link_id": str(link_id), "reason": "Application not found"})
            continue
        if not candidate.email:
            skipped.append({
                "link_id": str(link_id),
                "candidate_id": str(candidate.id),
                "name": candidate.full_name or "Unnamed candidate",
                "reason": "No email address on file for this candidate",
            })
            continue
        targets.append((link, candidate, job))
    return targets, skipped


def _strengths_prose(breakdown: dict | None) -> str:
    """The candidate's evidenced strengths, as PROSE for the prompt.

    Built from the stored ranking COMMENTS only. No score, band, or label goes
    into an email prompt — a candidate must not be able to reconstruct their
    internal rating from the wording they receive (spec §10, claude.md).
    """
    payload = ranking_payload(breakdown)
    lines = [
        payload[key]
        for key in RANKING_COMMENT_KEYS.values()
        if key != "overall_comment" and payload.get(key)
    ]
    return "\n".join(f"- {line}" for line in lines) or (
        "strong, relevant experience for this role"
    )


@router.post("/draft", response_model=EmailDraftsOut)
async def draft_emails(
    body: EmailDraftIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> EmailDraftsOut:
    """Draft one personalised email per selected candidate.

    Drafting NEVER sends. It also never fails on a provider outage — a
    deterministic template comes back with `generated_by_ai=false` so the
    recruiter can see at a glance which drafts deserve a closer read.
    """
    targets, skipped = await _load_targets(session, user, body.link_ids)
    tenant = await session.get(Tenant, user.tenant_id)
    company_name = tenant.name if tenant else "our team"
    frontend = get_settings().frontend_url.rstrip("/")

    drafts: list[EmailDraftOut] = []
    for link, candidate, job in targets:
        context = {
            "candidate_name": candidate.full_name or "there",
            "job_title": job.title,
            "company_name": company_name,
            "strengths": _strengths_prose(link.match_breakdown_json),
            "assessment_link": f"{frontend}/portal/assessments/{link.id}",
            "job_link": f"{frontend}/org/jobs/{job.id}",
            **body.context,
        }
        result = await lifecycle_email.draft(
            body.email_type, context, session=session
        )
        drafts.append(
            EmailDraftOut(
                link_id=link.id,
                candidate_id=candidate.id,
                recipient_email=candidate.email,
                candidate_name=candidate.full_name,
                email_type=result["email_type"],
                subject=result["subject"],
                body=result["body"],
                generated_by_ai=result["generated_by_ai"],
            )
        )

    return EmailDraftsOut(email_type=body.email_type, drafts=drafts, skipped=skipped)


@router.post("/send", response_model=EmailSendOut, status_code=status.HTTP_202_ACCEPTED)
async def send_emails(
    body: EmailSendIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> EmailSendOut:
    """Record and queue the messages exactly as the recruiter left them.

    The `email_log` row is written FIRST and the Celery task is enqueued after
    the flush, so the worker can never pick up an id that is not yet visible.
    """
    by_link = {m.link_id: m for m in body.messages}
    targets, skipped = await _load_targets(session, user, list(by_link))

    logs: list[EmailLog] = []
    for link, candidate, job in targets:
        message = by_link[link.id]
        log = EmailLog(
            tenant_id=user.tenant_id,
            email_type=body.email_type,
            recipient_email=candidate.email,
            candidate_id=candidate.id,
            job_id=job.id,
            job_candidate_link_id=link.id,
            subject=message.subject,
            body=message.body,
            status=STATUS_QUEUED,
            edited_by_human=message.edited_by_human,
            generated_by_ai=message.generated_by_ai,
            sent_by=user.user_id,
        )
        session.add(log)
        logs.append(log)
    await session.flush()

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="lifecycle_emails_queued",
        target_type="email_log",
        target_id=None,
        # Recipients and bodies are deliberately NOT in audit metadata
        # (ESD §16) — only counts and the type.
        metadata={
            "email_type": body.email_type,
            "queued": len(logs),
            "skipped": len(skipped),
            "edited": sum(1 for m in body.messages if m.edited_by_human),
        },
    )
    # Enqueue only after the flush, so every id below exists in the database.
    for log in logs:
        celery_app.send_task("pickready.send_lifecycle_email", args=[str(log.id)])

    return EmailSendOut(
        queued=len(logs),
        logs=[EmailLogOut.model_validate(row) for row in logs],
        skipped=skipped,
    )


@router.get("", response_model=list[EmailLogOut])
async def list_email_log(
    job_id: uuid.UUID | None = Query(default=None),
    candidate_id: uuid.UUID | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[EmailLogOut]:
    """What was sent, to whom, when, and whether it arrived."""
    stmt = (
        select(EmailLog)
        .where(EmailLog.tenant_id == user.tenant_id)
        .order_by(EmailLog.created_at.desc(), EmailLog.id)
        .offset(skip)
        .limit(limit)
    )
    if job_id is not None:
        stmt = stmt.where(EmailLog.job_id == job_id)
    if candidate_id is not None:
        stmt = stmt.where(EmailLog.candidate_id == candidate_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [EmailLogOut.model_validate(r) for r in rows]
