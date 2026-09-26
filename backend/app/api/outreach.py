"""Bulk candidate outreach — select candidates, preview, send (FR-5.2).

Two composition paths, one send path:
  * mode="ai"     → `outreach_content.generate_outreach_email` composes a
                    150–200 word personalized email per candidate.
  * mode="manual" → one subject/body written by the recruiter, with
                    {{candidate_name}} / {{company}} / {{job_title}}
                    substituted per recipient.

Sending is ALWAYS `pickready.send_email` enqueued once per recipient — never
inline SMTP in the request handler (claude.md rule 4). Every endpoint is
capability-gated on SEND_OUTREACH and runs on the RLS tenant session
(claude.md rules 1 and 3).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.core.config import get_settings
from app.models.candidate import Candidate, JobCandidateLink
from app.models.company import EmailTemplate
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.outreach import (
    MANUAL_PLACEHOLDERS,
    OutreachComposeIn,
    OutreachDeliveryStatusIn,
    OutreachDeliveryStatusOut,
    OutreachPreviewOut,
    OutreachSendIn,
    OutreachSendOut,
    ResolvedEmail,
    SkippedRecipient,
)
from app.services import capabilities as caps
from app.services import engagement
from app.services import outreach_content
from app.services.audit import audit
from app.services.yukti import projection
from app.workers import status as task_status
from app.workers.dispatch import dispatch_after_commit


router = APIRouter()

# A pass-through tenant template: the subject/body composed here (AI or manual)
# is the email. It is created on first send so the existing send_email task —
# which always renders a *named* template — can carry arbitrary content without
# the worker needing to know about outreach at all.
DIRECT_TEMPLATE_NAME = "outreach_direct"
_DIRECT_SUBJECT = "{{subject}}"
_DIRECT_BODY = "{{body}}"

SMTP_KEYS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")
_SMTP_WARNING = (
    "Email delivery is not configured on the server (SMTP credentials are "
    "missing), so queued emails will not actually reach candidates until an "
    "administrator sets them."
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _delivery_status() -> tuple[bool, str | None]:
    missing = [k for k in get_settings().missing_delivery_keys() if k in SMTP_KEYS]
    if missing:
        return False, _SMTP_WARNING
    return True, None


def _display_name(candidate: Candidate | None) -> str:
    if candidate is None:
        return "Unknown candidate"
    if candidate.full_name and candidate.full_name.strip():
        return candidate.full_name.strip()
    if candidate.email:
        return candidate.email
    return "Unknown candidate"


def _substitute(template: str, ctx: dict[str, str]) -> str:
    out = template
    for key in MANUAL_PLACEHOLDERS:
        out = out.replace("{{" + key + "}}", ctx.get(key, ""))
        out = out.replace("{{ " + key + " }}", ctx.get(key, ""))
    return out


async def _load_job(session: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    # RLS already scopes the read; the explicit check keeps the 404 honest.
    if job is None or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _company_context(
    session: AsyncSession, user: CurrentUser
) -> tuple[str, str]:
    if user.tenant_id is None:
        return "our company", "Not provided"
    row = (
        await session.execute(
            select(Tenant.name, Tenant.culture).where(Tenant.id == user.tenant_id)
        )
    ).one_or_none()
    if row is None:
        return "our company", "Not provided"
    return row.name or "our company", row.culture or "Not provided"


async def _load_links(
    session: AsyncSession, user: CurrentUser, job: Job, link_ids: list[uuid.UUID]
) -> list[JobCandidateLink]:
    rows = (
        (
            await session.execute(
                select(JobCandidateLink).where(
                    JobCandidateLink.id.in_(link_ids),
                    JobCandidateLink.job_id == job.id,
                    JobCandidateLink.tenant_id == user.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != len(set(link_ids)):
        raise HTTPException(
            status_code=404,
            detail="One or more selected candidates are not linked to this job",
        )
    # Preserve the caller's ordering so the modal pagination is stable.
    by_id = {row.id: row for row in rows}
    return [by_id[lid] for lid in dict.fromkeys(link_ids)]


async def _resolve(
    session: AsyncSession,
    user: CurrentUser,
    payload: OutreachComposeIn,
    job: Job,
) -> tuple[list[ResolvedEmail], list[SkippedRecipient], str]:
    """Resolve the selection into per-candidate subject/body pairs."""
    if payload.mode == "manual" and not (
        (payload.subject or "").strip() and (payload.body or "").strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="A subject and a message body are required when writing the email yourself",
        )

    company, company_culture = await _company_context(session, user)
    links = await _load_links(session, user, job, payload.link_ids)
    # The job's CURRENT skill names, read once: an evidence tag names a skill
    # by id, and the email says what the skill is called today.
    skill_names = (await projection.job_skills_view(session, job.id)).names

    # Batch-load the candidates instead of one SELECT per recipient. A send to
    # 50 selected candidates was 50 extra round trips before the first email was
    # even composed, and the AI path then adds an LLM call per recipient on top.
    candidates_by_id: dict[uuid.UUID, Candidate] = {}
    if links:
        candidates_by_id = {
            row.id: row
            for row in (
                await session.execute(
                    select(Candidate).where(
                        Candidate.id.in_([link.candidate_id for link in links])
                    )
                )
            ).scalars().all()
        }

    recipients: list[ResolvedEmail] = []
    skipped: list[SkippedRecipient] = []

    for link in links:
        candidate = candidates_by_id.get(link.candidate_id)
        name = _display_name(candidate)
        if candidate is None or not (candidate.email or "").strip():
            skipped.append(
                SkippedRecipient(
                    link_id=link.id,
                    candidate_id=link.candidate_id,
                    name=name,
                    reason="No email address on file for this candidate",
                )
            )
            continue

        # The skills the resume EVIDENCED (positive skill tags, by name), and
        # never a grade word, a score or a model-written tag: a candidate must
        # not be able to reconstruct an internal rating from their email.
        evidenced = projection.skill_list_phrase(
            projection.strengths_for_prompt(link.evidence_tags_json, skill_names)
        )
        if payload.mode == "manual":
            strengths = (
                f"your experience with {evidenced}"
                if evidenced
                else "the experience outlined in your profile"
            )
            ctx = {
                "candidate_name": name,
                "company": company,
                "job_title": job.title,
                "candidate_strengths": strengths,
            }
            subject = _substitute(payload.subject or "", ctx)
            body = _substitute(payload.body or "", ctx)
            ai_fallback = False
        else:
            # Only the evidenced skills reach the prompt, under the one
            # evidence key they answer; the other keys stay absent, which
            # `generation_sufficiency.outreach_evidence` reads as "nothing
            # recorded" rather than as a sentence about the record.
            email = await outreach_content.generate_outreach_email(
                {
                    "name": candidate.full_name,
                    "email": candidate.email,
                    "skills_comment": (
                        f"the {evidenced} experience on your resume" if evidenced else ""
                    ),
                },
                {"title": job.title},
                {"name": company, "culture": company_culture},
                kind="next_round",
            )
            subject = email["subject"]
            body = email["text"]
            # The template fallback always uses this exact subject shape.
            ai_fallback = subject == f"Next steps for the {job.title} role at {company}"

        recipients.append(
            ResolvedEmail(
                link_id=link.id,
                candidate_id=link.candidate_id,
                name=name,
                email=candidate.email or "",
                subject=subject,
                body=body,
                ai_fallback=ai_fallback,
            )
        )

    if not recipients:
        raise HTTPException(
            status_code=422,
            detail="None of the selected candidates have an email address on file",
        )
    return recipients, skipped, company


async def _ensure_direct_template(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Get-or-create the tenant's pass-through template (idempotent)."""
    existing = (
        await session.execute(
            select(EmailTemplate.id).where(
                EmailTemplate.tenant_id == tenant_id,
                EmailTemplate.name == DIRECT_TEMPLATE_NAME,
            )
        )
    ).first()
    if existing is not None:
        return
    session.add(
        EmailTemplate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=DIRECT_TEMPLATE_NAME,
            subject=_DIRECT_SUBJECT,
            body=_DIRECT_BODY,
            version=1,
            is_active=True,
        )
    )
    await session.flush()


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/preview", response_model=OutreachPreviewOut)
async def preview_outreach(
    payload: OutreachComposeIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> OutreachPreviewOut:
    """Per-candidate resolved {subject, body} for the selected candidates.

    Nothing is sent and nothing is written — this is the modal's preview.
    """
    job = await _load_job(session, user, payload.job_id)
    recipients, skipped, company = await _resolve(session, user, payload, job)
    smtp_ok, warning = _delivery_status()
    return OutreachPreviewOut(
        job_title=job.title,
        company=company,
        mode=payload.mode,
        recipients=recipients,
        skipped=skipped,
        placeholders=list(MANUAL_PLACEHOLDERS),
        smtp_configured=smtp_ok,
        delivery_warning=warning,
    )


@router.post("/send", response_model=OutreachSendOut)
async def send_outreach(
    payload: OutreachSendIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> OutreachSendOut:
    """Enqueue one `pickready.send_email` task per recipient (never inline)."""
    job = await _load_job(session, user, payload.job_id)
    recipients, skipped, _company = await _resolve(session, user, payload, job)

    edits = {o.link_id: o for o in payload.overrides}
    for rec in recipients:
        override = edits.get(rec.link_id)
        if override is not None:
            if override.subject.strip():
                rec.subject = override.subject
            if override.body.strip():
                rec.body = override.body

    await _ensure_direct_template(session, user.tenant_id)

    queued: list[str] = []
    task_ids: list[str] = []
    for rec in recipients:
        # After the COMMIT (CONTRACT v5), so the engagement stamp and the audit
        # row below and the send are one outcome. The invoke is no longer
        # attempted inside the request, so there is no per-recipient enqueue
        # failure left to report here: a lost invoke is logged at ERROR by the
        # commit hook, and the task id returned below reads as PENDING on the
        # outreach modal's delivery poll, which is where the recruiter looks.
        # A programming error (unknown task, unserialisable argument) raises,
        # as it should, before anything is sent.
        task = dispatch_after_commit(
            session,
            "pickready.send_email",
            args=[
                str(user.tenant_id),
                rec.email,
                DIRECT_TEMPLATE_NAME,
                {"subject": rec.subject, "body": rec.body},
            ],
        )
        queued.append(rec.email)
        task_ids.append(task.id)
        # THE CANDIDATE DID NOT HAVE TO DO ANYTHING FOR THIS TO COUNT. Feature
        # 8 treats a job-matching email the candidate RECEIVES as engagement,
        # because it resets the retention clock for somebody the platform is
        # still actively putting in front of employers. Recorded here rather
        # than in the delivery task on purpose: the delivery task sends EVERY
        # letter, including the one warning them that an unused profile is
        # about to be removed, and stamping engagement there would make that
        # warning cancel the very clock it exists to announce.
        await engagement.record_engagement_by_id(session, rec.candidate_id)

    # No "nothing could be queued" 503 any more: `_resolve` already refuses an
    # empty recipient list with a 422, and a send can no longer fail to
    # enqueue inside the request, so every recipient here is queued.
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="outreach_email_queued",
        target_type="job",
        target_id=job.id,
        # Never the message body — only routing metadata (ESD §16).
        metadata={
            "mode": payload.mode,
            "queued": len(queued),
            "link_ids": [str(r.link_id) for r in recipients],
            "skipped": len(skipped),
        },
    )

    smtp_ok, warning = _delivery_status()
    return OutreachSendOut(
        queued=len(queued),
        recipients=queued,
        task_ids=task_ids,
        skipped=skipped,
        smtp_configured=smtp_ok,
        delivery_warning=warning,
    )


@router.post("/status", response_model=OutreachDeliveryStatusOut)
async def outreach_delivery_status(
    payload: OutreachDeliveryStatusIn,
    _user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
) -> OutreachDeliveryStatusOut:
    sent = 0
    failed = 0
    pending = 0
    for task_id in payload.task_ids:
        result = await task_status.read(task_id)
        if not result.done:
            pending += 1
            continue
        # `send_email` returns {"status": "sent"} on delivery and
        # {"status": "failed"} on a PERMANENT failure it decided not to retry,
        # which is a run that succeeded and an email that did not arrive. Both
        # halves have to be read, or a bad address counts as delivered.
        if result.state == task_status.STATE_SUCCESS and (
            result.payload.get("status") == "sent"
        ):
            sent += 1
        else:
            failed += 1
    return OutreachDeliveryStatusOut(
        total=len(payload.task_ids),
        pending=pending,
        sent=sent,
        failed=failed,
        done=pending == 0,
    )
