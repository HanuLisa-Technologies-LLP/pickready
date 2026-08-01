"""Celery task implementations  -  names match the contract in celery_app.py:

  pickready.send_email(tenant_id, to, template_name, context, attachments=None)
  pickready.send_sms(phone, message)
  pickready.run_matching(job_id)
  pickready.parse_resume(profile_id)
  pickready.send_verification_requests(profile_id)
  pickready.parse_verification_reply(verification_request_id, raw_email_text)
  pickready.refresh_dashboard_views()

All slow/async work happens here, never inline in request handlers
(claude.md rule 4). Tasks are sync Celery functions running async service
code via asyncio.run with a FRESH engine per task  -  never a shared/global
engine, since Celery may fork and asyncio loops don't survive forks.

Retries: exponential backoff, max 5 attempts.

SECURITY (ESD §16): OTP codes and API keys are NEVER logged or written to
audit metadata  -  email `context` payloads may contain OTPs and are therefore
never persisted or logged, only template/recipient/status metadata is.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from celery.signals import worker_ready
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings, preflight_delivery_config
from app.services.smtp_service import send_email_async as smtp_send
from app.services.sms_service import (
    RETRY_BACKOFF_MAX_SECONDS,
    DeliveryError,
    PermanentDeliveryError,
    TransientDeliveryError,
    log_delivery_error,
    send_sms_async,
)
from app.models import (
    Candidate,
    Profile,
    SubmittedVia,
    Tenant,
    VerificationRequest,
    VerificationStatus,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Startup preflight ────────────────────────────────────────────────────────

@worker_ready.connect
def _delivery_preflight(**_kwargs) -> None:
    """Log a loud WARNING at worker boot if any SMTP/MSG91 credential is
    missing (sprint brief: a missing key must not fail silently). Never a hard
    crash  -  see preflight_delivery_config's ASSUMPTION."""
    preflight_delivery_config()


# ── Per-task engine/session helper ───────────────────────────────────────────

@asynccontextmanager
async def _worker_session():
    """Fresh engine + session for one task run, disposed on exit.

    ASSUMPTION: worker tasks are trusted backend processes that legitimately
    operate across tenants (Databank matching spans tenant_id NULL rows), so
    the session runs with app.bypass_rls = 'on'  -  the same escape hatch the
    RLS policies define for the audit-logged super-admin path. Tenant scoping
    inside tasks is done explicitly per query where a tenant is known.
    """
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            # false => session-level (not transaction-local): survives commits.
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            yield session
    finally:
        await engine.dispose()


def _run(coro):
    return asyncio.run(coro)


async def _audit(
    session: AsyncSession,
    tenant_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    metadata: dict | None = None,
) -> None:
    """Append an audit_log row (append-only table  -  INSERT only)."""
    await session.execute(
        text(
            "INSERT INTO audit_log (id, tenant_id, actor_user_id, action, "
            "target_type, target_id, metadata_json, at) "
            "VALUES (:id, CAST(:tenant_id AS uuid), NULL, :action, "
            ":target_type, :target_id, CAST(:metadata AS jsonb), :at)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "metadata": json.dumps(metadata or {}),
            "at": datetime.now(timezone.utc),
        },
    )
    await session.commit()


async def _audit_delivery_exhausted(
    tenant_id: str | None, to: str, template_name: str, channel: str
) -> None:
    """Write a terminal-failure audit row when transient retries are exhausted.

    Opens its own session because the per-task session used for the send has
    already unwound by the time Celery reports the final failure.
    """
    try:
        async with _worker_session() as session:
            await _audit(
                session,
                tenant_id if tenant_id else None,
                f"{channel}.delivery",
                channel,
                None,
                {
                    "to": to,
                    "template": template_name,
                    "status": "failed_exhausted",
                    "reason": "transient retries exhausted",
                },
            )
    except Exception:
        # Audit is best-effort here; never mask the real delivery failure.
        logger.exception("%s.audit_exhausted_failed to=%s", channel, to)


# ── Email ────────────────────────────────────────────────────────────────────

async def _send_email_async(
    session: AsyncSession,
    tenant_id: str | None,
    to: str,
    template_name: str,
    context: dict,
    attachments: list[dict] | None = None,
) -> dict[str, str]:
    from app.services import email_render

    settings = get_settings()

    # ROOT-CAUSE FIX (2026-07-23): tenant_id is None for platform-level
    # emails  -  the Owner/super_admin OTP has no tenant  -  and this path used to
    # crash with ValueError('badly formed hexadecimal UUID string') from
    # uuid.UUID(str(None)) before any email was sent. No tenant → skip the
    # tenant lookup entirely: default templates + the default SMTP sender.
    tenant: Tenant | None = None
    if tenant_id:
        try:
            tenant = await session.get(Tenant, uuid.UUID(str(tenant_id)))
        except (ValueError, TypeError):
            tenant = None  # invalid id → default-sender path, never a crash
        if tenant is None:
            logger.warning(
                "email.tenant_missing template=%s tenant_id=%s  -  using default sender",
                template_name, tenant_id,
            )

    # Gmail is the sole outbound provider and its authenticated mailbox is
    # always the From address. Tenant-domain sender substitution is forbidden.
    from_addr = settings.smtp_from_email
    sender_path = "gmail"

    # Structured, secret-free log so delivery failures are diagnosable  - 
    # never the message body/context (may carry OTP codes, ESD §16).
    logger.info(
        "email.sender provider=smtp path=%s template=%s tenant_id=%s spf_dkim=%s env=%s",
        sender_path,
        template_name,
        str(tenant.id) if tenant is not None else "-",
        tenant.spf_dkim_status if tenant is not None else "-",
        settings.environment,
    )

    # Rendering happens INSIDE the audited block. It used to sit above the
    # try/finally, so a template that resolved to neither a tenant row nor a
    # default raised ValueError before the audit was armed: the invitation was
    # discarded with no email_log row, no audit_log row, and a 200 already
    # returned to the caller. The only trace was a stderr traceback. Any future
    # render failure now lands in audit_log with status="failed" like every
    # other delivery failure.
    subject = ""
    body = ""
    html_body = ""
    delivery_status = "sent"
    message_id = ""
    err_meta: dict = {}
    try:
        subject, body = await email_render.render(
            session, tenant.id if tenant is not None else None, template_name, context
        )
        html_body = email_render.text_to_html(body)
        message_id = await smtp_send(
            from_email=from_addr,
            from_name=settings.smtp_from_name,
            to=to,
            subject=subject,
            html=html_body,
            text=body,
            attachments=attachments,
        ) or ""
    except DeliveryError as err:
        delivery_status = "failed"
        err_meta = err.as_audit_metadata()
        log_delivery_error(
            "email", err,
            template=template_name, to=to, sender_path=sender_path,
        )
        raise  # task decides: permanent → no retry, transient → backoff
    except Exception as exc:  # unexpected (e.g. template/render error)
        delivery_status = "failed"
        err_meta = {"error_name": type(exc).__name__, "provider_message": str(exc)[:500]}
        logger.exception(
            "email.delivery_failed kind=unexpected template=%s sender_path=%s",
            template_name, sender_path,
        )
        raise
    finally:
        # Log delivery to audit_log (ESD §11). NEVER include `context`  -  it may
        # carry OTP codes. Template name + recipient + status + failure taxonomy.
        await _audit(
            session,
            str(tenant.id) if tenant is not None else None,  # platform-level email
            "email.delivery",
            "email",
            message_id or None,
            {
                "to": to,
                "template": template_name,
                "status": delivery_status,
                "sender_path": sender_path,
                **({"failure": err_meta} if err_meta else {}),
            },
        )
    return {"status": "sent", "message_id": message_id}


@celery_app.task(
    bind=True,
    name="pickready.send_email",
    # Only TRANSIENT failures auto-retry after a fixed 60-second delay. Permanent
    # failures (unverified domain, invalid recipient, bad key) are handled in
    # the body and NOT retried  -  retrying them just floods the logs.
    autoretry_for=(TransientDeliveryError,),
    retry_backoff=False,
    default_retry_delay=60,
    retry_jitter=False,
    max_retries=None,  # actual cap comes from settings.delivery_max_retries
)
def send_email(
    self,
    tenant_id: str | None,
    to: str,
    template_name: str,
    context: dict,
    attachments: list[dict] | None = None,
):
    """attachments: [{"filename": str, "content": <base64 str>}] (SMTP MIME part).

    tenant_id None = platform-level email (e.g. Owner OTP): default template,
    default SMTP sender. Interview invites and verification emails also route
    through here, so they inherit the verified-domain/default-sender selection.

    Failure handling:
      * PermanentDeliveryError → swallowed after logging + audit (no retry).
      * TransientDeliveryError → fixed 60-second delay, capped at
        settings.delivery_max_retries; audited when retries are exhausted.
    """
    self.max_retries = get_settings().delivery_max_retries

    async def _task():
        async with _worker_session() as session:
            return await _send_email_async(
                session, tenant_id, to, template_name, context, attachments
            )

    try:
        return _run(_task())
    except PermanentDeliveryError as err:
        # Already logged + audited inside _send_email_async. Do NOT re-raise  - 
        # a permanent failure must not consume the retry budget.
        logger.error(
            "email.permanent_failure_final template=%s to=%s  -  not retrying. "
            "ACTION: %s", template_name, to, err.hint,
        )
        return {"status": "failed", "error": err.error_name}
    except TransientDeliveryError:
        # autoretry_for handles the backoff/retry. When retries are exhausted
        # Celery re-raises here; record the terminal failure to the audit log.
        if self.request.retries >= self.max_retries:
            _run(
                _audit_delivery_exhausted(
                    tenant_id, to, template_name, "email"
                )
            )
        raise


# ── Lifecycle emails (spec §6) ───────────────────────────────────────────────

async def _send_lifecycle_email_async(session: AsyncSession, email_log_id: str) -> dict:
    """Deliver one already-recorded lifecycle email and settle its log row.

    The row exists before this runs (api/emails.send_emails writes it), so this
    task never invents content  -  it sends exactly what the recruiter approved
    and then records the outcome. `queued` is the only state it will act on:
    a re-delivery of an already-`sent` row would double-mail the candidate,
    which is worse than the retry being a no-op.
    """
    from app.models.email_log import STATUS_FAILED, STATUS_QUEUED, STATUS_SENT, EmailLog
    from app.services.lifecycle_email import to_html

    row = await session.get(EmailLog, uuid.UUID(str(email_log_id)))
    if row is None:
        logger.warning("lifecycle_email.log_row_missing id=%s", email_log_id)
        return {"status": "skipped", "reason": "log row not found"}
    if row.status != STATUS_QUEUED:
        logger.info(
            "lifecycle_email.already_settled id=%s status=%s  -  not resending",
            row.id, row.status,
        )
        return {"status": row.status, "resent": False}

    settings = get_settings()
    try:
        await smtp_send(
            from_email=settings.smtp_from_email,
            from_name=settings.smtp_from_name,
            to=row.recipient_email,
            subject=row.subject,
            html=to_html(row.body),
            text=row.body,
        )
    except PermanentDeliveryError as err:
        # Terminal: record it and do NOT re-raise, so the retry budget is not
        # burned on something that can never succeed.
        row.status = STATUS_FAILED
        row.error = err.error_name
        await session.commit()
        log_delivery_error("lifecycle_email", err)
        await _audit(
            session, str(row.tenant_id), "lifecycle_email_failed",
            "email_log", str(row.id),
            {"email_type": row.email_type, "error": err.error_name},
        )
        return {"status": "failed", "error": err.error_name}
    except TransientDeliveryError:
        # Leave the row `queued` so a retry can pick it up; Celery's
        # autoretry_for handles the backoff.
        raise

    row.status = STATUS_SENT
    row.sent_at = datetime.now(timezone.utc)
    row.error = None
    await session.commit()
    await _audit(
        session, str(row.tenant_id), "lifecycle_email_sent",
        "email_log", str(row.id),
        # Recipient address and body are never written to audit metadata
        # (ESD §16)  -  the email_log row is the record of the content.
        {"email_type": row.email_type, "edited_by_human": row.edited_by_human},
    )
    return {"status": "sent"}


@celery_app.task(
    bind=True,
    name="pickready.send_lifecycle_email",
    autoretry_for=(TransientDeliveryError,),
    retry_backoff=False,
    default_retry_delay=60,
    retry_jitter=False,
    max_retries=None,  # actual cap comes from settings.delivery_max_retries
)
def send_lifecycle_email(self, email_log_id: str):
    """Send one of the six lifecycle emails from its `email_log` row."""
    self.max_retries = get_settings().delivery_max_retries

    async def _task():
        async with _worker_session() as session:
            return await _send_lifecycle_email_async(session, email_log_id)

    try:
        return _run(_task())
    except TransientDeliveryError:
        if self.request.retries >= self.max_retries:
            # Retries exhausted  -  settle the row as failed so the log never
            # leaves a message stuck in `queued` forever.
            async def _mark_failed():
                from app.models.email_log import STATUS_FAILED, STATUS_QUEUED, EmailLog

                async with _worker_session() as session:
                    row = await session.get(EmailLog, uuid.UUID(str(email_log_id)))
                    if row is not None and row.status == STATUS_QUEUED:
                        row.status = STATUS_FAILED
                        row.error = "Delivery retries exhausted"
                        await session.commit()

            _run(_mark_failed())
        raise


async def _autosend_lifecycle_email(
    session: AsyncSession, link_id: str, email_type: str, extra_context: dict | None = None
) -> dict:
    """Draft and queue one of the AUTOMATIC lifecycle emails.

    Types 1 and 2 (application confirmation, assessment reminder) fire on an
    event rather than on a recruiter's click, so there is no human in the loop
    to approve the copy  -  the draft goes straight into `email_log` and out.
    `edited_by_human` is therefore False on these rows, which is exactly the
    distinction the audit trail needs to record.

    Types 3, 4 and 5 deliberately do NOT come through here: telling someone
    they were rejected, shortlisted, or put on hold is a decision a person
    makes and should read before it is sent (api/emails).
    """
    from app.models.candidate import Candidate, JobCandidateLink
    from app.models.email_log import STATUS_QUEUED, EmailLog
    from app.models.job import Job
    from app.services import lifecycle_email

    link = await session.get(JobCandidateLink, uuid.UUID(str(link_id)))
    if link is None:
        return {"status": "skipped", "reason": "link not found"}
    candidate = await session.get(Candidate, link.candidate_id)
    job = await session.get(Job, link.job_id)
    if candidate is None or job is None or not candidate.email:
        return {"status": "skipped", "reason": "no recipient"}

    # Idempotence: never send the same automatic type twice for one
    # application. A Celery retry after a partial failure would otherwise
    # double-mail the candidate.
    already = (
        await session.execute(
            select(EmailLog.id).where(
                EmailLog.job_candidate_link_id == link.id,
                EmailLog.email_type == email_type,
            ).limit(1)
        )
    ).first()
    if already is not None:
        return {"status": "skipped", "reason": "already sent"}

    tenant = await session.get(Tenant, link.tenant_id)
    settings = get_settings()
    frontend = settings.frontend_url.rstrip("/")
    context = {
        "candidate_name": candidate.full_name or "there",
        "job_title": job.title,
        "company_name": tenant.name if tenant else "our team",
        "assessment_link": f"{frontend}/portal/assessments/{link.id}",
        **(extra_context or {}),
    }
    draft = await lifecycle_email.draft(email_type, context, session=session)

    row = EmailLog(
        tenant_id=link.tenant_id,
        email_type=email_type,
        recipient_email=candidate.email,
        candidate_id=candidate.id,
        job_id=job.id,
        job_candidate_link_id=link.id,
        subject=draft["subject"],
        body=draft["body"],
        status=STATUS_QUEUED,
        edited_by_human=False,      # automatic: no recruiter reviewed it
        generated_by_ai=draft["generated_by_ai"],
    )
    session.add(row)
    await session.commit()
    celery_app.send_task("pickready.send_lifecycle_email", args=[str(row.id)])
    return {"status": "queued", "email_log_id": str(row.id)}


@celery_app.task(name="pickready.send_application_confirmation")
def send_application_confirmation(link_id: str):
    """Email type 1: confirm an application was received (spec §6.1)."""
    async def _task():
        async with _worker_session() as session:
            return await _autosend_lifecycle_email(
                session, link_id, "application_confirmation"
            )

    return _run(_task())


@celery_app.task(name="pickready.send_assessment_reminder")
def send_assessment_reminder(link_id: str, hours_elapsed: int = 24):
    """Email type 2: nudge a candidate whose assessment is still unfinished."""
    async def _task():
        async with _worker_session() as session:
            return await _autosend_lifecycle_email(
                session,
                link_id,
                "assessment_reminder",
                {"hours_elapsed": str(hours_elapsed)},
            )

    return _run(_task())


# ── SMS ──────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="pickready.send_sms",
    # Same taxonomy as email: only transient failures back off + retry.
    autoretry_for=(TransientDeliveryError,),
    retry_backoff=True,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    max_retries=None,
)
def send_sms(self, phone: str, message: str):
    """Send an SMS via the MSG91 REST API. `phone` and `message` content (which
    may be an OTP) are never logged  -  only status + the provider error body on
    failure. Permanent failures (bad sender id / recipient / missing key) are
    not retried; transient ones use exponential backoff."""
    self.max_retries = get_settings().delivery_max_retries
    try:
        _run(send_sms_async(phone, message))
    except PermanentDeliveryError as err:
        log_delivery_error("sms", err)
        logger.error(
            "sms.permanent_failure_final  -  not retrying. ACTION: %s", err.hint
        )
        return
    except TransientDeliveryError as err:
        log_delivery_error("sms", err)
        raise


# ── Matching / parsing pipelines ────────────────────────────────────────────

@celery_app.task(
    name="pickready.run_matching",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def run_matching(job_id: str):
    from app.services import matching

    async def _task():
        async with _worker_session() as session:
            scored = await matching.run_matching(session, job_id)
            logger.info("matching.complete job_id=%s scored=%d", job_id, scored)
            from app.models.assessment import AssessmentConversation
            from app.models.candidate import JobCandidateLink
            from app.models.job import Job
            from app.services.functional_assessment import run_assessment

            job = await session.get(Job, uuid.UUID(str(job_id)))
            if job is not None:
                links = (
                    await session.execute(
                        select(JobCandidateLink)
                        .join(
                            AssessmentConversation,
                            AssessmentConversation.job_candidate_link_id
                            == JobCandidateLink.id,
                        )
                        .where(
                            JobCandidateLink.job_id == job.id,
                            JobCandidateLink.archived_at.is_(None),
                            AssessmentConversation.status == "completed",
                        )
                    )
                ).scalars().all()
                for link in links:
                    # run_assessment loads the persisted conversation transcript
                    # when no explicit transcript is supplied. Never synthesize a
                    # "no transcript" report for candidates who have not completed
                    # the assessment.
                    await run_assessment(session, job, link)
                await session.commit()
                logger.info(
                    "functional_assessment.complete job_id=%s reports=%d",
                    job_id,
                    len(links),
                )
    _run(_task())


@celery_app.task(
    name="pickready.generate_technical_questions",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def generate_technical_questions(job_id: str):
    """Job setup: the technical bank AND the PPI framework (spec §11).

    The spec calls for the two generators to run "in parallel", meaning they are
    INDEPENDENT of each other -- neither reads what the other writes, and a
    failure in one does not invalidate the other. They are nonetheless awaited
    in sequence here: an AsyncSession is not safe to use from two concurrent
    tasks, and both of these read and write through the same one. Giving each
    its own session would have them contend for a row lock on the same `jobs`
    row for a wall-clock saving nobody is waiting on -- this runs in a worker,
    after the recruiter has already been told the job is being prepared.

    NEITHER approves anything. The job is left in `questions_pending_review`
    until a recruiter finalises both halves; that is the single manual step in
    the pipeline, and the reminder task below is what stops it going silent.
    """
    from app.models.job import Job
    from app.services.functional_assessment import generate_question_bank
    from app.services.ppi import generate_framework

    async def _task():
        async with _worker_session() as session:
            job = await session.get(Job, uuid.UUID(str(job_id)))
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            rows = await generate_question_bank(session, job)
            framework = await generate_framework(session, job)
            await session.commit()
            logger.info(
                "job_setup.generated job_id=%s grade=%s questions=%d competencies=%d status=%s",
                job_id, job.assessment_grade, len(rows), len(framework), job.assessment_status,
            )
    _run(_task())


@celery_app.task(
    name="pickready.generate_candidate_questions",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def generate_candidate_questions(link_id: str):
    """This candidate's PPI questions, from their resume + the job's framework.

    Per candidate, unlike the technical bank. Idempotent: a candidate who
    already has questions keeps exactly those, so a Celery redelivery cannot
    hand someone a different assessment halfway through.
    """
    from app.models.candidate import JobCandidateLink
    from app.models.job import Job
    from app.services.ppi import generate_candidate_questions as _generate

    async def _task():
        async with _worker_session() as session:
            link = await session.get(JobCandidateLink, uuid.UUID(str(link_id)))
            if link is None:
                raise ValueError(f"Application {link_id} not found")
            job = await session.get(Job, link.job_id)
            if job is None:
                raise ValueError(f"Job {link.job_id} not found")
            rows = await _generate(session, job, link)
            await session.commit()
            logger.info(
                "ppi_questions.generated link_id=%s count=%d", link_id, len(rows)
            )
    _run(_task())


@celery_app.task(
    name="pickready.run_functional_assessment",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def run_functional_assessment(link_id: str):
    from app.models.assessment import AssessmentConversation, AssessmentMessage
    from app.models.candidate import JobCandidateLink
    from app.models.job import Job
    from app.services.functional_assessment import run_assessment

    async def _task():
        async with _worker_session() as session:
            link = await session.get(JobCandidateLink, uuid.UUID(str(link_id)))
            if link is None:
                raise ValueError(f"Application {link_id} not found")
            job = await session.get(Job, link.job_id)
            conversation = (
                await session.execute(
                    select(AssessmentConversation).where(
                        AssessmentConversation.job_candidate_link_id == link.id
                    )
                )
            ).scalars().first()
            transcript = []
            if conversation is not None:
                messages = (
                    await session.execute(
                        select(AssessmentMessage)
                        .where(AssessmentMessage.conversation_id == conversation.id)
                        .order_by(AssessmentMessage.ordinal)
                    )
                ).scalars().all()
                transcript = [
                    {
                        "speaker": message.speaker,
                        "domain": message.domain,
                        "question_key": message.question_key,
                        "content": message.content,
                    }
                    for message in messages
                ]
            await run_assessment(session, job, link, transcript)
            await session.commit()
    _run(_task())


@celery_app.task(name="pickready.remind_unapproved_technical_questions")
def remind_unapproved_technical_questions():
    """The operational safeguard on the pipeline's one manual step (spec §5).

    A job whose setup sits unapproved past the configured threshold (24-48h)
    mails everyone who could approve it. Without this the single manual step
    becomes a SILENT bottleneck: applications keep arriving, no candidate can
    be invited, and nothing anywhere says why.

    `question_reminder_sent_at` makes it one reminder per job, not an hourly
    nag -- the beat schedule runs this every hour so a job is reminded near its
    own threshold rather than whenever a daily sweep happens to land.
    """
    from datetime import timedelta

    from app.models.enums import Role, UserStatus
    from app.models.job import Job
    from app.models.user import User

    async def _task():
        threshold = datetime.now(timezone.utc) - timedelta(
            hours=get_settings().technical_review_reminder_hours
        )
        async with _worker_session() as session:
            jobs = (
                await session.execute(
                    select(Job).where(
                        Job.assessment_status == "questions_pending_review",
                        # Either half generated long enough ago is enough to
                        # chase: whichever half is still unapproved is what is
                        # blocking every candidate on this job.
                        func.least(
                            func.coalesce(Job.questions_generated_at, Job.framework_generated_at),
                            func.coalesce(Job.framework_generated_at, Job.questions_generated_at),
                        )
                        <= threshold,
                        Job.question_reminder_sent_at.is_(None),
                        Job.archived_at.is_(None),
                    )
                )
            ).scalars().all()
            if not jobs:
                logger.debug("job_setup.reminder_noop, no jobs pending review")
                return
            for job in jobs:
                recipients = (
                    await session.execute(
                        select(User).where(
                            User.tenant_id == job.tenant_id,
                            User.role.in_((Role.client, Role.hr_manager, Role.recruiter)),
                            User.status != UserStatus.disabled,
                            User.email.is_not(None),
                        )
                    )
                ).scalars().all()
                for recipient in recipients:
                    await _send_email_async(
                        session,
                        str(job.tenant_id),
                        recipient.email,
                        "outreach_direct",
                        {
                            "subject": f"Assessment setup needs review, {job.title}",
                            "body": (
                                f"The assessment setup for {job.title} is still awaiting review. "
                                "No candidate can be invited until the technical questions "
                                "and the PPI framework are both finalised. "
                                f"Open {get_settings().frontend_url}/org/jobs/{job.id}/setup to review and approve them."
                            ),
                        },
                    )
                job.question_reminder_sent_at = datetime.now(timezone.utc)
            await session.commit()
    _run(_task())


@celery_app.task(
    name="pickready.parse_resume",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def parse_resume(profile_id: str):
    from app.services import resume_parsing

    async def _task():
        async with _worker_session() as session:
            await resume_parsing.parse_resume(session, profile_id)
            logger.info("resume.parsed profile_id=%s", profile_id)
    _run(_task())


# ── Employer verification (ESD §10) ─────────────────────────────────────────

@celery_app.task(
    name="pickready.send_verification_requests",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_verification_requests(profile_id: str):
    """Send tokenized verification-form links to the (up to 3) previous
    employers. VerificationRequest rows with tokens must already exist."""
    async def _task():
        settings = get_settings()
        async with _worker_session() as session:
            profile = await session.get(Profile, uuid.UUID(str(profile_id)))
            if profile is None:
                raise ValueError(f"Profile {profile_id} not found")
            candidate = await session.get(Candidate, profile.candidate_id)
            candidate_name = (candidate.full_name or candidate.email) if candidate else ""

            requests = (
                (
                    await session.execute(
                        select(VerificationRequest).where(
                            VerificationRequest.profile_id == profile.id,
                            VerificationRequest.status == VerificationStatus.pending,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not requests:
                logger.info(
                    "verification.no_pending_requests profile_id=%s", profile_id
                )
                return

            for vr in requests:
                link = f"{settings.frontend_url}/verify-employment/{vr.token}"
                await _send_email_async(
                    session,
                    str(vr.tenant_id),
                    vr.employer_email,
                    "verification",
                    {
                        "candidate_name": candidate_name,
                        "employer_name": vr.employer_name or "",
                        "verification_link": link,
                    },
                )
    _run(_task())


@celery_app.task(
    name="pickready.parse_verification_reply",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def parse_verification_reply(verification_request_id: str, raw_email_text: str):
    """Fallback path: employer replied by email instead of using the form  - 
    LLM-extract the reply into the same structured schema (ESD §10.2)."""
    from app.services import verification_parsing

    async def _task():
        async with _worker_session() as session:
            vr = await session.get(
                VerificationRequest, uuid.UUID(str(verification_request_id))
            )
            if vr is None:
                raise ValueError(
                    f"VerificationRequest {verification_request_id} not found"
                )
            if vr.status != VerificationStatus.pending:
                # Already submitted via form or overridden  -  form wins, don't clobber.
                logger.info(
                    "verification.reply_ignored id=%s status=%s",
                    verification_request_id, vr.status.value,
                )
                return

            parsed = await verification_parsing.parse_reply(raw_email_text, session=session)
            vr.response_json = parsed
            vr.status = VerificationStatus.submitted
            vr.submitted_via = SubmittedVia.email_reply
            vr.responded_at = datetime.now(timezone.utc)
            await session.commit()
            await _audit(
                session,
                str(vr.tenant_id),
                "verification.reply_parsed",
                "verification_request",
                str(vr.id),
                {"submitted_via": "email_reply"},
            )
    _run(_task())


# ── Billing + credits (killer-spec Parts 2 and 3) ───────────────────────────

@celery_app.task(
    name="pickready.reconcile_assessment_credits",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def reconcile_assessment_credits():
    """Daily sweep: charge abandoned assessments and queue due reminders.

    A completed assessment charges itself the moment it completes. An abandoned
    one has no such moment, so it is settled here once the reminder sequence has
    been exhausted (services/credit_reconciliation). Every write is idempotent,
    so a retry of this task never double-charges.
    """
    async def _task():
        from app.services import credit_reconciliation

        def _queue(link_id: str, hours_elapsed: int) -> None:
            celery_app.send_task(
                "pickready.send_assessment_reminder", args=[link_id, hours_elapsed]
            )

        async with _worker_session() as session:
            result = await credit_reconciliation.reconcile(session, queue_reminder=_queue)
            await session.commit()
            return result.as_dict()

    return _run(_task())


@celery_app.task(name="pickready.send_payment_failed_email")
def send_payment_failed_email(tenant_id: str):
    """Tell the customer a charge failed, before credits quietly stop arriving.

    Silence here is the worst outcome: invitations would keep working until the
    pool ran out, and the first the customer would hear of it is a 402.
    """
    async def _task():
        async with _worker_session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT u.email, t.name FROM users u JOIN tenants t ON t.id = u.tenant_id "
                        "WHERE u.tenant_id = :tid AND u.role = 'client' "
                        "AND u.status <> 'disabled' AND u.email IS NOT NULL "
                        "ORDER BY u.created_at LIMIT 1"
                    ),
                    {"tid": tenant_id},
                )
            ).mappings().first()
            if row is None:
                logger.warning("billing.payment_failed_email_no_recipient tenant=%s", tenant_id)
                return {"sent": False, "reason": "no recipient"}
            celery_app.send_task(
                "pickready.send_email",
                args=[
                    tenant_id,
                    row["email"],
                    "payment_failed",
                    {
                        "company_name": row["name"],
                        "billing_url": f"{get_settings().frontend_url}/org/billing",
                    },
                ],
            )
            return {"sent": True}

    return _run(_task())


# ── Dashboard (ESD §14) ─────────────────────────────────────────────────────

@celery_app.task(
    name="pickready.refresh_dashboard_views",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def refresh_dashboard_views():
    """Refresh the dashboard materialized view (Celery-beat, every 5 min).
    CONCURRENTLY requires the unique index on job_id and must run outside a
    transaction block  -  hence the AUTOCOMMIT connection."""
    async def _task():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(
                    text("REFRESH MATERIALIZED VIEW CONCURRENTLY dashboard_job_metrics")
                )
        finally:
            await engine.dispose()
    _run(_task())
