"""Celery task implementations — names match the contract in celery_app.py:

  pickready.send_email(tenant_id, to, template_name, context, attachments=None)
  pickready.send_sms(phone, message)
  pickready.run_matching(job_id)
  pickready.parse_resume(profile_id)
  pickready.send_verification_requests(profile_id)
  pickready.parse_verification_reply(verification_request_id, raw_email_text)
  pickready.refresh_dashboard_views()

All slow/async work happens here, never inline in request handlers
(claude.md rule 4). Tasks are sync Celery functions running async service
code via asyncio.run with a FRESH engine per task — never a shared/global
engine, since Celery may fork and asyncio loops don't survive forks.

Retries: exponential backoff, max 5 attempts.

SECURITY (ESD §16): OTP codes and API keys are NEVER logged or written to
audit metadata — email `context` payloads may contain OTPs and are therefore
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings, preflight_delivery_config
from app.services.mailtrap_service import send_email_async as mailtrap_send
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
    """Log a loud WARNING at worker boot if any Mailtrap/MSG91 credential is
    missing (sprint brief: a missing key must not fail silently). Never a hard
    crash — see preflight_delivery_config's ASSUMPTION."""
    preflight_delivery_config()


# ── Per-task engine/session helper ───────────────────────────────────────────

@asynccontextmanager
async def _worker_session():
    """Fresh engine + session for one task run, disposed on exit.

    ASSUMPTION: worker tasks are trusted backend processes that legitimately
    operate across tenants (Databank matching spans tenant_id NULL rows), so
    the session runs with app.bypass_rls = 'on' — the same escape hatch the
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
    """Append an audit_log row (append-only table — INSERT only)."""
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
) -> None:
    from app.services import email_render

    settings = get_settings()

    # ROOT-CAUSE FIX (2026-07-23): tenant_id is None for platform-level
    # emails — the Owner/super_admin OTP has no tenant — and this path used to
    # crash with ValueError('badly formed hexadecimal UUID string') from
    # uuid.UUID(str(None)) before any email was sent. No tenant → skip the
    # tenant lookup entirely: default templates + the default Mailtrap sender.
    tenant: Tenant | None = None
    if tenant_id:
        try:
            tenant = await session.get(Tenant, uuid.UUID(str(tenant_id)))
        except (ValueError, TypeError):
            tenant = None  # invalid id → default-sender path, never a crash
        if tenant is None:
            logger.warning(
                "email.tenant_missing template=%s tenant_id=%s — using default sender",
                template_name, tenant_id,
            )

    # Sender selection (claude.md rule 5): the tenant's own domain may ONLY be
    # used as From once its sending domain is SPF/DKIM-verified AND Mailtrap has
    # that domain verified — an unverified From is rejected/bounces. In
    # development, or whenever the domain is not verified (or there is no
    # tenant), fall back to settings.mailtrap_sender_email.
    tenant_from = f"recruitment@{tenant.domain}" if tenant is not None else None
    domain_verified = (
        tenant is not None and (tenant.spf_dkim_status or "").lower() == "verified"
    )
    if domain_verified and settings.environment != "development":
        from_addr = tenant_from
        sender_path = "tenant_domain"
    else:
        from_addr = settings.mailtrap_sender_email
        sender_path = "default_sender"

    # Structured, secret-free log so delivery failures are diagnosable —
    # never the message body/context (may carry OTP codes, ESD §16).
    logger.info(
        "email.sender provider=mailtrap path=%s template=%s tenant_id=%s spf_dkim=%s env=%s",
        sender_path,
        template_name,
        str(tenant.id) if tenant is not None else "-",
        tenant.spf_dkim_status if tenant is not None else "-",
        settings.environment,
    )

    subject, body = await email_render.render(
        session, tenant.id if tenant is not None else None, template_name, context
    )
    html_body = email_render.text_to_html(body)
    delivery_status = "sent"
    message_id = ""
    err_meta: dict = {}
    try:
        message_id = await mailtrap_send(
            from_email=from_addr,
            from_name=settings.mailtrap_sender_name,
            to=to,
            subject=subject,
            html=html_body,
            text=body,
            attachments=attachments,
        )
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
        # Log delivery to audit_log (ESD §11). NEVER include `context` — it may
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


@celery_app.task(
    bind=True,
    name="pickready.send_email",
    # Only TRANSIENT failures auto-retry, with EXPONENTIAL backoff. Permanent
    # failures (unverified domain, invalid recipient, bad key) are handled in
    # the body and NOT retried — retrying them just floods the logs.
    autoretry_for=(TransientDeliveryError,),
    retry_backoff=True,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
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
    """attachments: [{"filename": str, "content": <base64 str>}] (Mailtrap shape).

    tenant_id None = platform-level email (e.g. Owner OTP): default template,
    default Mailtrap sender. Interview invites and verification emails also
    route through here, so they inherit the verified-domain/default-sender
    selection.

    Failure handling:
      * PermanentDeliveryError → swallowed after logging + audit (no retry).
      * TransientDeliveryError → exponential backoff, capped at
        settings.delivery_max_retries; audited when retries are exhausted.
    """
    self.max_retries = get_settings().delivery_max_retries

    async def _task():
        async with _worker_session() as session:
            await _send_email_async(
                session, tenant_id, to, template_name, context, attachments
            )

    try:
        _run(_task())
    except PermanentDeliveryError as err:
        # Already logged + audited inside _send_email_async. Do NOT re-raise —
        # a permanent failure must not consume the retry budget.
        logger.error(
            "email.permanent_failure_final template=%s to=%s — not retrying. "
            "ACTION: %s", template_name, to, err.hint,
        )
        return
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
    may be an OTP) are never logged — only status + the provider error body on
    failure. Permanent failures (bad sender id / recipient / missing key) are
    not retried; transient ones use exponential backoff."""
    self.max_retries = get_settings().delivery_max_retries
    try:
        _run(send_sms_async(phone, message))
    except PermanentDeliveryError as err:
        log_delivery_error("sms", err)
        logger.error(
            "sms.permanent_failure_final — not retrying. ACTION: %s", err.hint
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
    """Fallback path: employer replied by email instead of using the form —
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
                # Already submitted via form or overridden — form wins, don't clobber.
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
    transaction block — hence the AUTOCOMMIT connection."""
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
