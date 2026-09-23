"""Corporate email sender management (Corporate Email System spec, 2026-09-05).

The whole sender flow lives behind these routes:

    add sender -> business email check -> Super Admin approves or rejects
    -> ACTIVE (and SES eligibility is checked, never asserted)

THE MAILBOX OTP WAS WITHDRAWN, and this is the reasoning rather than an
omission. It proved the POC could read the mailbox. SES already refuses to
send as any identity the account has not verified, so the OTP re-proved a
property AWS enforces on every single send -- and it cost the product an OTP
surface in a portal that bans OTP everywhere else. Two questions remain, and
they are different questions: the COMPANY authorizes the address (the Super
Admin's decision, recorded here) and AWS carries mail from it
(`email_senders.eligibility`, which asks and never asserts).

Two capabilities split the flow exactly where spec section 10 does:
`manage_email_senders` covers registration (client Super Admin + Recruitment
Manager), `authorize_email_senders` covers approve, reject, disable, enable
and revoke (client Super Admin only). Every state change goes through
`email_senders.assert_transition` -- the FSM is the one chokepoint -- and
every event is audited (spec section 10).

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
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_public_db, get_tenant_db, require_capability
from app.core.config import get_settings
from app.models.email_log import (
    EMAIL_TYPE_BGV_VERIFICATION,
    STATUS_BOUNCED,
    STATUS_COMPLAINT,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENT,
    EmailLog,
)
from app.models.email_sender import (
    SENDER_ACTIVE,
    SENDER_DISABLED,
    SENDER_PENDING_VERIFICATION,
    SENDER_REJECTED,
    SENDER_REVOKED,
    ClientEmailSender,
)
from app.models.tenant import Tenant
from app.schemas.email_senders import (
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
    SenderDomainBlocked,
    SenderEmailInvalid,
    assert_transition,
    validate_business_email,
)
from app.services.email_senders.eligibility import check_sender_eligibility
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


async def _sender_out(sender: ClientEmailSender) -> SenderOut:
    """One sender, with its current sending eligibility attached.

    The eligibility lookup is DELIBERATELY not cached on the row: an identity
    can be verified or fail verification in AWS at any moment without anything
    in this product being told, so a stored copy would be a fact with an
    expiry date and no refresh. Asking at read time keeps the portal honest.
    """
    eligibility = await check_sender_eligibility(sender.email)
    return SenderOut(
        id=sender.id,
        name=sender.name,
        email=sender.email,
        status=sender.status,
        email_verified=sender.email_verified,
        authorized_at=sender.authorized_at,
        created_at=sender.created_at,
        # NO AWS VOCABULARY CROSSES THIS BOUNDARY. The Super Admin is told
        # whether the address can send and, if not, what happens next -- never
        # SES, an identity, a domain-verification status or a DKIM record.
        can_send=eligibility.eligible,
        sending_detail=eligibility.detail,
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
        senders=[await _sender_out(r) for r in rows],
        can_manage=True,  # the capability gate above already proved it
        can_authorize=can_authorize,
    )


@router.post(
    "",
    response_model=SenderOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("sender_create", limit=10, window=3600))],
)
async def create_sender(
    body: SenderCreateIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """Register one corporate mailbox, pending the Super Admin's decision.

    The business-email gate runs first (spec section 2): a malformed address
    or a free-provider domain is refused with the reason named. A REVOKED or
    REJECTED row for the same address is REPLACED, not resurrected -- both are
    terminal in the FSM, so proposing the mailbox again starts a fresh row and
    leaves the original decision on the audit record.

    NO CODE IS SENT. Mailbox ownership used to be proved by an OTP here; SES
    identity verification already establishes that the account may send as
    this address, and the Super Admin's approval establishes that the company
    authorizes it. Those are the two questions, and neither is an OTP.
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
        if existing.status not in {SENDER_REVOKED, SENDER_REJECTED}:
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

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="email_sender_created",
        target_type="client_email_sender",
        target_id=sender.id,
        metadata={"email": sender.email, "replaced_terminal": existing is not None},
    )
    return await _sender_out(sender)


# ── Super Admin decision ─────────────────────────────────────────────────────
#
# THE MAILBOX OTP THAT USED TO LIVE HERE IS GONE. It proved the POC could read
# the mailbox; SES already refuses to send as any identity this account has not
# verified, so the OTP re-proved a property AWS enforces anyway -- at the cost
# of an OTP surface in a portal that bans OTP everywhere else. What remains is
# the one decision only the company can make: does this address speak for us.
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
    return await _sender_out(sender)


@router.post("/{sender_id}/approve", response_model=SenderOut)
async def approve_sender(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.AUTHORIZE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """The client Super Admin authorizes this address to speak for the company.

    THIS IS A BUSINESS DECISION AND IT IS NOT GATED ON AWS. Eligibility is
    reported alongside the sender so the person deciding can see it, but a
    transient SES lookup failure must not veto a company's own authorization,
    and an approval granted while the domain is still being set up becomes
    useful the moment that finishes. Nothing unsafe follows from that: the
    send path revalidates the sender AND SES refuses any identity it has not
    verified, so an approved-but-not-yet-sendable address simply cannot send.
    """
    return await _lifecycle_move(
        session, user, sender_id, SENDER_ACTIVE, "email_sender_approved"
    )


@router.post("/{sender_id}/reject", response_model=SenderOut)
async def reject_sender(
    sender_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.AUTHORIZE_EMAIL_SENDERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SenderOut:
    """The client Super Admin refuses this address. Terminal.

    Distinct from revoke, which withdraws an authorization that once existed.
    A rejected address is proposed again by registering it afresh, so the
    refusal stays on the audit record instead of being edited away.
    """
    return await _lifecycle_move(
        session, user, sender_id, SENDER_REJECTED, "email_sender_rejected"
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
# endpoint is unauthenticated by necessity (SNS holds no Vivekium session),
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


#: TERMINAL RANK, and the reason out-of-order events cannot corrupt a row.
#: SNS makes no ordering promise, so a DELIVERY published before a BOUNCE can
#: arrive after it. Ranking the outcomes and refusing to move DOWN means the
#: worst thing that happened to a message is what the row remembers, whatever
#: order the events land in. Equal rank never overwrites either, which is what
#: makes a redelivered duplicate a no-op rather than a rewrite.
_OUTCOME_RANK: dict[str, int] = {
    STATUS_QUEUED: 0,
    STATUS_SENT: 1,
    STATUS_DELIVERED: 2,
    # A complaint means it ARRIVED and the recipient reported it, so it
    # outranks delivery. A bounce and a hard failure outrank everything: those
    # are the states a human has to act on.
    STATUS_COMPLAINT: 3,
    STATUS_BOUNCED: 4,
    STATUS_FAILED: 4,
}


def _failure_reason(kind: str, message: dict) -> str | None:
    """A short, human-readable cause for the outcomes that have one.

    Drawn from SES's own structured fields rather than a dump of the event,
    and bounded: this column is read by staff in the portal, and an event
    document carries the full recipient list and the message headers.
    """
    if kind == "bounce":
        block = message.get("bounce") or {}
        detail = " / ".join(
            part for part in (
                str(block.get("bounceType") or "").strip(),
                str(block.get("bounceSubType") or "").strip(),
            ) if part
        )
        return (f"Bounce: {detail}" if detail else "Bounce")[:500]
    if kind == "complaint":
        detail = str((message.get("complaint") or {}).get(
            "complaintFeedbackType") or "").strip()
        return (f"Complaint: {detail}" if detail else "Complaint")[:500]
    if kind == "reject":
        detail = str((message.get("reject") or {}).get("reason") or "").strip()
        return (f"Rejected by SES: {detail}" if detail else "Rejected by SES")[:500]
    if kind == "renderingfailure":
        detail = str((message.get("failure") or {}).get("errorMessage") or "").strip()
        return (
            f"Template rendering failed: {detail}" if detail
            else "Template rendering failed"
        )[:500]
    if kind == "deliverydelay":
        detail = str((message.get("deliveryDelay") or {}).get("delayType") or "").strip()
        return (f"Delivery delayed: {detail}" if detail else "Delivery delayed")[:500]
    return None


def _bounce_is_permanent(message: dict) -> bool:
    """Whether SES said the address is dead, rather than momentarily unusable.

    `Permanent` is a receiver saying the mailbox does not exist; `Transient` is
    a full mailbox or a throttled server, which SES is still retrying and which
    a candidate cannot fix by correcting anything. Telling somebody to change a
    working address because their former HR manager's inbox was full for an
    hour is worse than saying nothing: they contact the employer, the employer
    says the address is fine, and the product has spent the candidate's
    credibility on a delay it caused.

    `Undetermined` is read as NOT permanent, the safe direction: it is the
    state where SES itself could not classify the refusal.
    """
    return str((message.get("bounce") or {}).get("bounceType") or "") == "Permanent"


async def _alert_candidate_of_bgv_bounce(
    session: AsyncSession, row, verification_id: uuid.UUID
) -> None:
    """Email 4 of the brief: the BGV request could not be delivered.

    The candidate is asked to sign in and correct the HR address; the address
    itself travels PARTIALLY MASKED (`bgv_form.masked_email`), never in full.
    Silent when the verification has already been answered, which is the case
    where a second recipient on the same message bounced and the employer had
    already replied from another.
    """
    from app.services import bgv_form
    # LATE, and it stays late. `tests/test_bgv_form.py` patches
    # `app.workers.dispatch.dispatch`, which a module-level binding would
    # already have resolved past: removing this line as a duplicate import
    # makes that test silently stop intercepting the send.
    from app.workers.dispatch import dispatch as dispatch_email

    match = (
        (
            await session.execute(
                sa_text(
                    "SELECT c.full_name, c.email AS candidate_email, "
                    " e.hr_email, e.id AS employment_id "
                    "FROM bgv_verifications v "
                    "JOIN candidate_employments e "
                    "  ON e.id = v.candidate_employment_id "
                    "JOIN candidates c ON c.id = v.candidate_id "
                    "WHERE v.id = :vid AND v.responded_at IS NULL"
                ),
                {"vid": str(verification_id)},
            )
        )
        .mappings()
        .first()
    )
    if match is None or not match["candidate_email"]:
        return
    from app.services import bgv_delivery

    # The address that actually bounced, which is the candidate's correction
    # when they have already made one. Masking the declaration instead would
    # show them the address they replaced and read as if the correction was
    # never applied.
    failed_address = await bgv_delivery.effective_hr_email(
        session, uuid.UUID(str(match["employment_id"]))
    )
    dispatch_email(
        "pickready.send_email",
        args=[
            str(row.tenant_id),
            match["candidate_email"],
            "bgv_bounced",
            {
                "candidate_name": match["full_name"] or "there",
                "masked_hr_email": bgv_form.masked_email(
                    failed_address or match["hr_email"]
                ),
            },
        ],
    )


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

    # THE TIMESTAMP AND THE STATUS ARE DECIDED SEPARATELY, on purpose. A
    # timestamp records that the thing happened at all and is written once
    # (`or now`, so a duplicate never moves it). The status is the row's
    # single summary and is rank-guarded below, so a late DELIVERY cannot
    # erase a BOUNCE that has already been recorded.
    outcome: str | None = None
    if kind == "send":
        # SES ACCEPTED the message. Not delivery, and the two must never blur:
        # `sent` is what the send path already wrote, so this only ever
        # confirms it.
        outcome = STATUS_SENT
    elif kind == "delivery":
        row.delivered_at = row.delivered_at or now
        outcome = STATUS_DELIVERED
        # THE EMPLOYER'S THREE DAYS START HERE, not at send. A request still in
        # a provider's retry queue is one the HR team cannot act on, so the
        # chase and the form link both key off this stamp
        # (`services/bgv_delivery.clock_start`).
        if row.bgv_verification_id is not None:
            from app.services import bgv_delivery

            await bgv_delivery.mark_delivered(
                session, verification_id=row.bgv_verification_id, at=now
            )
    elif kind == "bounce":
        # Vivekium feature 4, bounce detection: a BGV request that bounced is
        # an address the candidate must fix NOW, not something to wait out for
        # three days.
        #
        # THE VERIFICATION IS RESOLVED BY THE BINDING, never by the recipient
        # address. This used to match `candidate_employments.hr_email` within
        # the tenant, newest send first, limit one, so two open verifications
        # sharing one HR mailbox (one HR manager confirming two candidates at
        # the same company, which is the normal case at a large employer)
        # resolved to whichever went out last: the wrong candidate was told to
        # correct an address that worked, and nothing recorded the mistake. A
        # row with no binding is left UNATTRIBUTED and logged, because
        # attributing it to the most similar message is exactly the defect.
        #
        # PERMANENT ONLY. A transient bounce is a full mailbox or a throttled
        # server that SES is still retrying, and a candidate cannot fix it by
        # correcting an address that is already right.
        #
        # Acted on ONCE, on the first bounce record (`bounced_at is None`):
        # SNS delivers at least once, so a redelivery is the default.
        if row.email_type == EMAIL_TYPE_BGV_VERIFICATION and row.bounced_at is None:
            if not _bounce_is_permanent(message):
                logger.info(
                    "bgv.transient_bounce email_log=%s  -  not alerting, SES is "
                    "still retrying",
                    row.id,
                )
            elif row.bgv_verification_id is None:
                logger.warning(
                    "bgv.bounce_unattributed email_log=%s  -  this row carries no "
                    "verification binding, so the candidate cannot be told which "
                    "employer to correct",
                    row.id,
                )
            else:
                from app.services import bgv_delivery

                await bgv_delivery.mark_bounced(
                    session,
                    verification_id=row.bgv_verification_id,
                    at=now,
                    reason=_failure_reason(kind, message),
                )
                await _alert_candidate_of_bgv_bounce(
                    session, row, row.bgv_verification_id
                )
        row.bounced_at = row.bounced_at or now
        outcome = STATUS_BOUNCED
    elif kind == "complaint":
        row.complained_at = row.complained_at or now
        outcome = STATUS_COMPLAINT
    elif kind in {"reject", "renderingfailure"}:
        # SES never handed it to a receiver. Terminal, and a real failure.
        row.failed_at = row.failed_at or now
        outcome = STATUS_FAILED
    elif kind == "deliverydelay":
        # NOT TERMINAL. SES is still retrying, and calling this a failure
        # would tell a recruiter a candidate was never contacted while the
        # message is still in flight. Record the reason, move no status.
        row.error = _failure_reason(kind, message) or row.error
        await session.flush()
        return {"status": "recorded"}
    else:
        return {"status": "ignored"}

    reason = _failure_reason(kind, message)
    if reason:
        row.error = reason
    # STRICTLY GREATER, so a duplicate event is a no-op and an out-of-order
    # one cannot demote the row.
    if _OUTCOME_RANK.get(outcome, 0) > _OUTCOME_RANK.get(row.status, 0):
        row.status = outcome
    await session.flush()
    return {"status": "recorded"}
