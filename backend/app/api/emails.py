"""Lifecycle email endpoints (spec §6): draft with AI, edit, send, audit.

Flow the UI drives:
  1. Recruiter selects candidates and picks an email type.
  2. POST /emails/draft  -> one AI draft PER candidate, personalised.
  3. Recruiter reads and optionally edits each draft.
  4. POST /emails/send   -> each message is written to `email_log` first, then
     a dispatched task delivers it (claude.md rules 4 and 5).

The log row is created BEFORE the send is attempted, so a message that fails in
transit still leaves a record of what was going to be said and why it did not
arrive. The worker owns the queued -> sent | failed transition.

Every row is written by `services/email_outbox`, the one writer of candidate
email: it records the corporate sender (the one the recruiter chose, else the
tenant's default), binds the email to the recruiter's thread with the
candidate so a reply can land there, and dispatches the send after the
request commits.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_db, require_capability
from app.core.config import get_settings
from app.models.candidate import Candidate, JobCandidateLink
from app.models.email_log import EmailLog
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
from app.services import generation_sufficiency
from app.services import assessment_invite, email_outbox, lifecycle_email
from app.services.audit import audit
from app.services.matching import RANKING_COMMENT_KEYS, ranking_payload

router = APIRouter()


async def _load_targets(
    session: AsyncSession, user: CurrentUser, link_ids: list[uuid.UUID]
) -> tuple[list[tuple[JobCandidateLink, Candidate, Job]], list[dict]]:
    """Resolve link ids to (link, candidate, job), reporting what was skipped.

    A candidate with no email address on file is SKIPPED, not failed: the rest
    of a 20-person batch must still go out, and the recruiter needs to be told
    exactly who was left behind rather than discovering it later.

    THREE QUERIES, NOT THREE PER RECIPIENT
    ---------------------------------------
    This read one link, one candidate and one job per selected recipient, so a
    fifty-person send was a hundred and fifty round trips before the first
    draft was composed, and the drafting path then adds a model call per
    recipient on top. `api/outreach._resolve` was repaired for exactly this and
    the shape here is the same one on purpose, `.in_(...)` then a dict lookup,
    because two spellings of one idea is how the second one stops being
    maintained.

    What is deliberately unchanged is every decision this function makes: the
    order of the returned targets, which recipients are rejected, and the
    wording each rejection carries. A missing link, a link belonging to another
    tenant, a missing candidate or job, and a candidate with no address all
    skip exactly as before, and a link id repeated by the caller still appears
    once per occurrence.
    """
    targets: list[tuple[JobCandidateLink, Candidate, Job]] = []
    skipped: list[dict] = []

    unique_ids = list(dict.fromkeys(link_ids))
    links_by_id: dict[uuid.UUID, JobCandidateLink] = {}
    if unique_ids:
        links_by_id = {
            row.id: row
            for row in (
                await session.execute(
                    select(JobCandidateLink).where(JobCandidateLink.id.in_(unique_ids))
                )
            )
            .scalars()
            .all()
        }

    # Only the links that survive the tenant check contribute ids to the next
    # two reads, so a row this request may not see never widens them.
    in_tenant = [
        link for link in links_by_id.values() if link.tenant_id == user.tenant_id
    ]
    candidates_by_id: dict[uuid.UUID, Candidate] = {}
    jobs_by_id: dict[uuid.UUID, Job] = {}
    if in_tenant:
        candidates_by_id = {
            row.id: row
            for row in (
                await session.execute(
                    select(Candidate).where(
                        Candidate.id.in_({link.candidate_id for link in in_tenant})
                    )
                )
            )
            .scalars()
            .all()
        }
        jobs_by_id = {
            row.id: row
            for row in (
                await session.execute(
                    select(Job).where(Job.id.in_({link.job_id for link in in_tenant}))
                )
            )
            .scalars()
            .all()
        }

    for link_id in link_ids:
        link = links_by_id.get(link_id)
        # Explicit tenant check is defense in depth; RLS is the boundary.
        if link is None or link.tenant_id != user.tenant_id:
            skipped.append({"link_id": str(link_id), "reason": "Application not found"})
            continue
        candidate = candidates_by_id.get(link.candidate_id)
        job = jobs_by_id.get(link.job_id)
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
        # THE CONSTANT, not the literal. `generation_sufficiency` has to
        # RECOGNISE this exact default in order to refuse generation over it,
        # and a default and its recogniser held as two independent literals
        # drift the first time somebody rewords one of them.
        generation_sufficiency.GENERIC_STRENGTHS_PLACEHOLDER
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
            # Signed, expiring, and bound to this candidate's address, so the
            # click has to pass through the candidate portal sign-in before it
            # can reach an assessment (services/assessment_invite).
            "assessment_link": assessment_invite.assessment_link_url(
                frontend, link_id=link.id, email=candidate.email
            ),
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

    Every `email_log` row carries the sender it will go out under: the one the
    recruiter chose, else the tenant's default, else the platform mailbox.
    The deliveries are dispatched AFTER the request commits, so a request that
    fails part way queues nothing at all.
    """
    by_link = {m.link_id: m for m in body.messages}
    targets, skipped = await _load_targets(session, user, list(by_link))

    # Queue-time sender validation (Corporate Email System spec section 6):
    # a chosen corporate sender must exist in THIS tenant and be active. The
    # worker re-validates at send time (spec section 11), so this check is the
    # early, actionable refusal, not the security boundary. Resolved ONCE,
    # before any row is written, so a refusal leaves nothing behind.
    try:
        sender_id = await email_outbox.resolve_sender(
            session, user.tenant_id, body.sender_id
        )
    except email_outbox.SenderNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except email_outbox.SenderNotActive as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    logs: list[EmailLog] = []
    for link, candidate, job in targets:
        message = by_link[link.id]
        log = await email_outbox.queue_candidate_email(
            session,
            tenant_id=user.tenant_id,
            email_type=body.email_type,
            recipient_email=candidate.email,
            candidate_id=candidate.id,
            job_id=job.id,
            link_id=link.id,
            subject=message.subject,
            body=message.body,
            generated_by_ai=message.generated_by_ai,
            edited_by_human=message.edited_by_human,
            sent_by=user.user_id,
            requested_sender_id=sender_id,
            candidate_name=candidate.full_name,
        )
        if log is None:
            # A human send carries no dedupe key, so the outbox cannot skip
            # it; None here is a programming error, never a runtime state.
            raise RuntimeError("the outbox deduplicated an email with no key")
        logs.append(log)

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
