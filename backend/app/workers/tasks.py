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

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
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

_RESEND_URL = "https://api.resend.com/emails"
_MSG91_URL = "https://control.msg91.com/api/v2/sendsms"
_HTTP_TIMEOUT = 30.0


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


# ── Email ────────────────────────────────────────────────────────────────────

async def _resend_send(
    from_addr: str,
    reply_to: str,
    to: str,
    subject: str,
    body: str,
    attachments: list[dict] | None,
) -> str:
    """POST to the Resend API. Returns the Resend message id."""
    payload: dict = {
        "from": from_addr,
        "reply_to": reply_to,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if attachments:
        payload["attachments"] = [
            {"filename": a["filename"], "content": a["content"]} for a in attachments
        ]
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {get_settings().resend_api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        return str(resp.json().get("id", ""))


async def _send_email_async(
    session: AsyncSession,
    tenant_id: str,
    to: str,
    template_name: str,
    context: dict,
    attachments: list[dict] | None = None,
) -> None:
    from app.services import email_render

    tenant = await session.get(Tenant, uuid.UUID(str(tenant_id)))
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} not found")

    # Client-domain email ONLY via the tenant's verified Resend sending domain
    # (claude.md rule 5) — From/Reply-To are derived from tenants.domain.
    from_addr = f"recruitment@{tenant.domain}"
    reply_to = f"recruitment@{tenant.domain}"

    subject, body = await email_render.render(session, tenant.id, template_name, context)
    delivery_status = "sent"
    message_id = ""
    try:
        message_id = await _resend_send(from_addr, reply_to, to, subject, body, attachments)
    except Exception:
        delivery_status = "failed"
        raise  # let Celery retry with backoff
    finally:
        # Log delivery to audit_log (ESD §11). NEVER include `context` — it may
        # carry OTP codes. Template name + recipient + status only.
        await _audit(
            session,
            str(tenant.id),
            "email.delivery",
            "email",
            message_id or None,
            {"to": to, "template": template_name, "status": delivery_status},
        )


@celery_app.task(
    name="pickready.send_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_email(
    tenant_id: str,
    to: str,
    template_name: str,
    context: dict,
    attachments: list[dict] | None = None,
):
    """attachments: [{"filename": str, "content": <base64 str>}] (Resend shape)."""
    async def _task():
        async with _worker_session() as session:
            await _send_email_async(
                session, tenant_id, to, template_name, context, attachments
            )
    _run(_task())


# ── SMS ──────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="pickready.send_sms",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def send_sms(phone: str, message: str):
    """Send an SMS via the MSG91 REST API. `message` content (which may be an
    OTP) is never logged."""
    async def _task():
        settings = get_settings()
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _MSG91_URL,
                headers={"authkey": settings.msg91_api_key},
                json={
                    "sender": settings.msg91_sender_id,
                    "route": "4",  # transactional
                    "country": "91",
                    "sms": [{"message": message, "to": [phone]}],
                },
            )
            resp.raise_for_status()
        logger.info("sms.delivery status=sent")  # no phone, no message body
    _run(_task())


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
