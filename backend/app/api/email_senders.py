"""Corporate email sender management (Corporate Email System spec, 2026-09-05).

The spec's whole section 3 flow lives behind these routes:

    add sender -> business email check -> 6-digit OTP to the mailbox ->
    POC enters OTP -> ownership verified -> Super Admin authorizes -> ACTIVE

Two capabilities split the flow exactly where spec section 10 does:
`manage_email_senders` covers registration and verification (client Super
Admin + Recruitment Manager), `authorize_email_senders` covers activation,
disable, enable and revoke (client Super Admin only). Every state change goes
through `email_senders.assert_transition` -- the FSM is the one chokepoint --
and every event is audited (spec section 10), never with the code in it.

The OTP email leaves through the existing dispatched email path
(`pickready.send_email` with the fixed `sender_verification` template), never
inline in the handler (claude.md rule 4). The plaintext code exists only in
the dispatch payload and the mailbox; Redis holds the HMAC hash
(services/email_senders/verification).

This module also carries the SES event webhook (spec section 8): SNS posts
delivery/bounce/complaint events here, the message signature and TopicArn are
validated against `ses_sns_topic_arn`, and `email_log` is updated by
`provider_message_id`. It is mounted inside this router because the events
exist only because corporate senders exist; under the SMTP transport the
setting is empty and the endpoint refuses everything.
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_public_db, get_tenant_db, require_capability
from app.core.config import get_settings
from app.models.email_log import (
    STATUS_BOUNCED,
    STATUS_COMPLAINT,
    STATUS_DELIVERED,
    STATUS_SENT,
    EmailLog,
)
from app.models.email_sender import (
    SENDER_ACTIVE,
    SENDER_DISABLED,
    SENDER_EMAIL_VERIFIED,
    SENDER_PENDING_VERIFICATION,
    SENDER_REVOKED,
    SENDER_VERIFICATION_EXPIRED,
    ClientEmailSender,
)
from app.models.tenant import Tenant
from app.schemas.email_senders import (
    OtpIssueOut,
    OtpVerifyIn,
    SenderCreateIn,
    SenderListOut,
    SenderOut,
    TemplateOut,
    TemplatePreviewIn,
    TemplatePreviewOut,
)
from app.services import capabilities as caps
from app.services import email_templates, rbac
from app.services.audit import audit
from app.services.email_senders import (
    IllegalSenderTransition,
    ResendCooldownActive,
    SenderDomainBlocked,
    SenderEmailInvalid,
    VerificationUnavailable,
    assert_transition,
    issue_otp,
    validate_business_email,
    verify_otp,
)
from app.services.email_senders import verification as otp_state
from app.services.rate_limit import rate_limit
from app.workers.dispatch import dispatch
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _load_sender(
    session: AsyncSession, user: CurrentUser, sender_id: uuid.UUID
) -> ClientEmailSender:
    """Tenant-scoped load; a sender outside the tenant is a 404, never a 403
    (the cross-tenant rule: existence is not confirmed)."""
    sender = await session.get(ClientEmailSender, sender_id)
    if sender is None or sender.tenant_id != user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found"
        )
    return sender


def _transition_or_409(sender: ClientEmailSender, target: str) -> None:
    try:
        assert_transition(sender.status, target)
    except IllegalSenderTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    sender.status = target


async def _dispatch_otp_email(
    session: AsyncSession, user: CurrentUser, sender: ClientEmailSender, code: str
) -> None:
    """Send the verification code to the MAILBOX BEING REGISTERED, through the
    dispatched email path (never inline). The code travels only in the task
    payload and the mailbox; it is never persisted, logged or audited."""
    settings = get_settings()
    tenant = await session.get(Tenant, user.tenant_id)
    dispatch(
        "pickready.send_email",
        args=[
            str(user.tenant_id),
            sender.email,
            "sender_verification",
            {
                "sender_name": sender.name,
                "company_name": tenant.name if tenant else "Your company",
                "otp_code": code,
                "ttl_minutes": str(max(1, settings.sender_otp_ttl_seconds // 60)),
            },
        ],
    )


def _otp_issue_out(sender: ClientEmailSender) -> OtpIssueOut:
    settings = get_settings()
    return OtpIssueOut(
        sender_id=sender.id,
        status=sender.status,
        resend_cooldown_seconds=settings.sender_otp_resend_cooldown_seconds,
        expires_in_seconds=settings.sender_otp_ttl_seconds,
    )


# ── list + create ────────────────────────────────────────────────────────────

@router.get("", response_model=SenderListOut)
async def list_senders(
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderListOut:
    """Every sender this tenant has registered, plus what the CALLER may do,
    so the UI renders only reachable controls."""
    rows = (
        (
            await session.execute(
                select(ClientEmailSender)
                .where(ClientEmailSender.tenant_id == user.tenant_id)
                .order_by(ClientEmailSender.created_at, ClientEmailSender.id)
            )
        )
        .scalars()
        .all()
    )
    can_authorize = await rbac.has_capability(
        session, user.tenant_id, user.role, caps.AUTHORIZE_EMAIL_SENDERS, user.user_id
    )
    return SenderListOut(
        senders=[SenderOut.model_validate(r) for r in rows],
        can_manage=True,  # the capability gate above already proved it
        can_authorize=can_authorize,
    )


@router.post(
    "",
    response_model=OtpIssueOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("sender_create", limit=10, window=3600))],
)
async def create_sender(
    body: SenderCreateIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> OtpIssueOut:
    """Register one corporate mailbox and send its first verification code.

    The business-email gate runs first (spec section 2): a malformed address
    or a free-provider domain is refused with the reason named. A REVOKED row
    for the same address is REPLACED, not resurrected -- revoked is terminal
    in the FSM, so registering the mailbox again starts the whole
    verification flow from scratch on a fresh row.
    """
    try:
        email = validate_business_email(body.email)
    except SenderDomainBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SenderEmailInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    existing = (
        await session.execute(
            select(ClientEmailSender).where(
                ClientEmailSender.tenant_id == user.tenant_id,
                ClientEmailSender.email == email,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status != SENDER_REVOKED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That address is already registered for your company.",
            )
        # Terminal means the ROW never comes back; the ADDRESS may. email_log
        # references it ON DELETE SET NULL, so delivery history survives.
        await session.delete(existing)
        await session.flush()

    sender = ClientEmailSender(
        tenant_id=user.tenant_id,
        name=body.name.strip(),
        email=email,
        status=SENDER_PENDING_VERIFICATION,
    )
    session.add(sender)
    await session.flush()

    try:
        code = await issue_otp(sender.id)
    except VerificationUnavailable as exc:
        # The session dependency rolls the row back with this, so a sender
        # never exists without a code having been minted for it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification is temporarily unavailable. Try again shortly.",
        ) from exc

    await _dispatch_otp_email(session, user, sender, code)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="email_sender_created",
        target_type="client_email_sender",
        target_id=sender.id,
        metadata={"email": sender.email, "replaced_revoked": existing is not None},
    )
    return _otp_issue_out(sender)


# ── verification ─────────────────────────────────────────────────────────────

@router.post(
    "/{sender_id}/resend-otp",
    response_model=OtpIssueOut,
    dependencies=[Depends(rate_limit("sender_otp_resend", limit=10, window=300))],
)
async def resend_otp(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> OtpIssueOut:
    """Mint and send a fresh code. A resend REPLACES the outstanding code and
    re-arms an expired verification (the FSM's one edge out of
    verification_expired that is not revoke)."""
    sender = await _load_sender(session, user, sender_id)
    if sender.status == SENDER_VERIFICATION_EXPIRED:
        _transition_or_409(sender, SENDER_PENDING_VERIFICATION)
    elif sender.status != SENDER_PENDING_VERIFICATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This sender is not awaiting verification.",
        )

    try:
        code = await issue_otp(sender.id)
    except ResendCooldownActive as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except VerificationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification is temporarily unavailable. Try again shortly.",
        ) from exc

    await _dispatch_otp_email(session, user, sender, code)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="email_sender_otp_resent",
        target_type="client_email_sender",
        target_id=sender.id,
        metadata={"email": sender.email},
    )
    return _otp_issue_out(sender)


class VerifyOut(BaseModel):
    """The OTP dialog's whole answer in one shape: wrong-code is an OUTCOME
    the dialog renders with the attempts left, not an exception."""

    verified: bool
    reason: str
    attempts_remaining: int
    status: str


@router.post(
    "/{sender_id}/verify-otp",
    response_model=VerifyOut,
    dependencies=[Depends(rate_limit("sender_otp_verify", limit=15, window=300))],
)
async def verify_sender_otp(
    sender_id: uuid.UUID,
    body: OtpVerifyIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> VerifyOut:
    """Judge one entered code. Success moves the sender to email_verified and
    invalidates the code; an expired code moves it to verification_expired so
    the list tells the truth about why nothing is progressing."""
    sender = await _load_sender(session, user, sender_id)
    if sender.status != SENDER_PENDING_VERIFICATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This sender is not awaiting verification.",
        )

    try:
        outcome = await verify_otp(sender.id, body.code)
    except VerificationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification is temporarily unavailable. Try again shortly.",
        ) from exc

    if outcome.verified:
        _transition_or_409(sender, SENDER_EMAIL_VERIFIED)
        sender.email_verified = True
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="email_sender_verified",
            target_type="client_email_sender",
            target_id=sender.id,
            metadata={"email": sender.email},
        )
    elif outcome.reason == otp_state.REASON_EXPIRED:
        _transition_or_409(sender, SENDER_VERIFICATION_EXPIRED)
        await audit(
            session,
            tenant_id=user.tenant_id,
            actor_user_id=user.user_id,
            action="email_sender_verification_expired",
            target_type="client_email_sender",
            target_id=sender.id,
            metadata={"email": sender.email},
        )

    return VerifyOut(
        verified=outcome.verified,
        reason=outcome.reason,
        attempts_remaining=outcome.attempts_remaining,
        status=sender.status,
    )


# ── authorization + lifecycle (spec sections 3 and 11) ───────────────────────

async def _lifecycle_move(
    session: AsyncSession,
    user: CurrentUser,
    sender_id: uuid.UUID,
    target: str,
    action: str,
) -> SenderOut:
    sender = await _load_sender(session, user, sender_id)
    _transition_or_409(sender, target)
    if target == SENDER_ACTIVE and sender.authorized_at is None:
        # First activation IS the authorization (spec section 3); a re-enable
        # after disable keeps the original authorization record.
        sender.authorized_by = user.user_id
        sender.authorized_at = datetime.now(timezone.utc)
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="client_email_sender",
        target_id=sender.id,
        metadata={"email": sender.email, "status": sender.status},
    )
    await session.flush()
    return SenderOut.model_validate(sender)


@router.post("/{sender_id}/authorize", response_model=SenderOut)
async def authorize_sender(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.AUTHORIZE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """The client Super Admin's explicit activation (spec section 3). Only a
    verified sender can be authorized; the FSM refuses everything else."""
    return await _lifecycle_move(
        session, user, sender_id, SENDER_ACTIVE, "email_sender_authorized"
    )


@router.post("/{sender_id}/disable", response_model=SenderOut)
async def disable_sender(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.AUTHORIZE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """Reversible pause. The send-time chokepoint refuses anything not active,
    so queued emails under this sender fail honestly from this moment."""
    return await _lifecycle_move(
        session, user, sender_id, SENDER_DISABLED, "email_sender_disabled"
    )


@router.post("/{sender_id}/enable", response_model=SenderOut)
async def enable_sender(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.AUTHORIZE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """Reverse a disable. The original authorization record is kept."""
    return await _lifecycle_move(
        session, user, sender_id, SENDER_ACTIVE, "email_sender_enabled"
    )


@router.post("/{sender_id}/revoke", response_model=SenderOut)
async def revoke_sender(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.AUTHORIZE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """Terminal removal (spec section 11). Queued emails carrying this sender
    are revalidated at send time and fail rather than sending under an
    identity the client has withdrawn."""
    return await _lifecycle_move(
        session, user, sender_id, SENDER_REVOKED, "email_sender_revoked"
    )


# ── templates (spec section 7) ───────────────────────────────────────────────

@router.get("/templates", response_model=list[TemplateOut])
async def list_email_templates(
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[TemplateOut]:
    """The fixed reusable catalogue, verbatim, with each template's own
    variable vocabulary so the composer can offer exactly the right fields."""
    return [
        TemplateOut(
            key=t.key,
            label=t.label,
            subject=t.subject,
            body=t.body,
            variables=sorted(t.variables),
        )
        for t in email_templates.list_templates()
    ]


@router.post("/templates/{template_key}/preview", response_model=TemplatePreviewOut)
async def preview_email_template(
    template_key: str,
    body: TemplatePreviewIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TemplatePreviewOut:
    """Render one template with the supplied variables. Refuses rather than
    guesses: an unknown or missing variable is a 422 naming it, never a
    half-substituted email."""
    try:
        subject, rendered = email_templates.render(template_key, body.variables)
    except email_templates.UnknownTemplate as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        ) from exc
    except (email_templates.UnknownVariable, email_templates.MissingVariable) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return TemplatePreviewOut(key=template_key, subject=subject, body=rendered)


# ── SES delivery events over SNS (spec section 8) ────────────────────────────
#
# SNS delivers a JSON document over HTTPS with its own signature scheme. The
# endpoint is unauthenticated by necessity (SNS holds no ReadyPick session),
# so THREE independent checks gate every message before a row is touched:
# the TopicArn must equal `ses_sns_topic_arn` exactly (empty setting = refuse
# everything), the signing certificate must come from an https URL on
# sns.<region>.amazonaws.com, and the RSA signature over the canonical string
# must verify against that certificate. Anything failing any check is a 403
# with nothing revealed about why.

_SNS_NOTIFICATION_FIELDS = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
_SNS_CONFIRMATION_FIELDS = (
    "Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"
)


def _sns_canonical_string(payload: dict) -> bytes:
    fields = (
        _SNS_CONFIRMATION_FIELDS
        if payload.get("Type") in {"SubscriptionConfirmation", "UnsubscribeConfirmation"}
        else _SNS_NOTIFICATION_FIELDS
    )
    parts: list[str] = []
    for field in fields:
        value = payload.get(field)
        if value is None:
            continue
        parts.append(field)
        parts.append(str(value))
    return ("\n".join(parts) + "\n").encode("utf-8")


def _cert_url_is_trusted(cert_url: str) -> bool:
    """Only an https URL on an sns.<region>.amazonaws.com host may supply the
    signing certificate; anything else is an attacker choosing their own key."""
    parsed = urlparse(cert_url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and host.startswith("sns.")
        and host.endswith(".amazonaws.com")
    )


async def _verify_sns_signature(payload: dict) -> bool:
    """Fetch the signing certificate (bounded) and verify the RSA signature.
    SignatureVersion 1 is SHA1withRSA, 2 is SHA256withRSA."""
    import httpx
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509 import load_pem_x509_certificate

    cert_url = str(payload.get("SigningCertURL", ""))
    if not _cert_url_is_trusted(cert_url):
        return False
    try:
        signature = base64.b64decode(str(payload.get("Signature", "")))
    except (ValueError, TypeError):
        return False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(cert_url)
            response.raise_for_status()
            cert = load_pem_x509_certificate(response.content)
    except Exception:
        logger.warning("ses_events.cert_fetch_failed")
        return False

    algorithm = (
        hashes.SHA256()
        if str(payload.get("SignatureVersion", "1")) == "2"
        else hashes.SHA1()
    )
    try:
        cert.public_key().verify(
            signature, _sns_canonical_string(payload), padding.PKCS1v15(), algorithm
        )
    except Exception:
        return False
    return True


def _extract_ses_event(message: dict) -> tuple[str | None, str | None]:
    """(event kind, provider message id) from an SES event document. SES event
    publishing writes `eventType`; the older notification shape writes
    `notificationType`. Both carry mail.messageId."""
    kind = str(message.get("eventType") or message.get("notificationType") or "").lower()
    message_id = (message.get("mail") or {}).get("messageId")
    return (kind or None), (str(message_id) if message_id else None)


@router.post(
    "/events/ses",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("ses_events", limit=300, window=60))],
)
async def ses_event_webhook(
    request: Request,
    session: AsyncSession = Depends(get_public_db),
) -> dict:
    """Record one SES delivery/bounce/complaint event against its email_log
    row. Idempotent: SNS delivers at least once, and re-stamping the same
    timestamp field is harmless."""
    settings = get_settings()
    expected_topic = settings.ses_sns_topic_arn.strip()
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload"
        ) from exc

    if (
        not expected_topic
        or str(payload.get("TopicArn", "")) != expected_topic
        or not await _verify_sns_signature(payload)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Refused")

    message_type = str(payload.get("Type", ""))
    if message_type == "SubscriptionConfirmation":
        # Confirm by visiting the SubscribeURL, which is signed into the
        # message we just verified. Refusing to confirm would leave the topic
        # pending forever; confirming an unverified message is what the
        # signature check above prevents.
        import httpx

        subscribe_url = str(payload.get("SubscribeURL", ""))
        if not _cert_url_is_trusted(subscribe_url):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Refused")
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            await client.get(subscribe_url)
        return {"status": "confirmed"}

    if message_type != "Notification":
        return {"status": "ignored"}

    try:
        message = json.loads(str(payload.get("Message", "")))
    except ValueError:
        return {"status": "ignored"}
    kind, provider_message_id = _extract_ses_event(message)
    if kind is None or provider_message_id is None:
        return {"status": "ignored"}

    row = (
        await session.execute(
            select(EmailLog).where(EmailLog.provider_message_id == provider_message_id)
        )
    ).scalar_one_or_none()
    if row is None:
        # An event for a message this product did not record. Acknowledge so
        # SNS stops redelivering; there is nothing to update.
        return {"status": "unmatched"}

    now = datetime.now(timezone.utc)
    if kind == "delivery":
        row.delivered_at = row.delivered_at or now
        # Never let a late delivery event overwrite a bounce or complaint.
        if row.status == STATUS_SENT:
            row.status = STATUS_DELIVERED
    elif kind == "bounce":
        row.bounced_at = row.bounced_at or now
        row.status = STATUS_BOUNCED
    elif kind == "complaint":
        row.complained_at = row.complained_at or now
        row.status = STATUS_COMPLAINT
    else:
        return {"status": "ignored"}
    await session.flush()
    return {"status": "recorded"}
