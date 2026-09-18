"""Candidate outreach, and the inbound-email webhook.

- Outreach applies to FRESHLY sourced candidates only; Databank candidates
  never re-enter this flow (claude.md rule 7) and are reported as skipped.
- The outreach link is a signed stateless JWT (see deps.make_outreach_token).

THE TENANT-OWNED EMPLOYER-VERIFICATION SYSTEM IS RETIRED (vivekium C8,
owner-ruled 2026-09-18). Its routes (/profile, /requests/{id}/override, the
ten-field /form/{token}) and its tasks are gone the way Company DNA and
Intercom went; the `verification_requests` TABLE survives unread, because
rows already written are history. The surviving system is the
candidate-owned one: candidate_employments, bgv_verifications and the
seven-item checkbox form at /bgv/form/{token}.
"""
import hmac
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_public_db,
    get_tenant_db,
    make_outreach_token,
    require_capability,
)
from app.core.config import get_settings
from app.models.candidate import Candidate, JobCandidateLink
from app.models.enums import LinkSource
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.verification import (
    InboundEmailIn,
    InboundEmailOut,
    OutreachIn,
    OutreachOut,
)
from app.models.conversation import CHANNEL_EMAIL, PARTY_EMPLOYER_HR
from app.services import capabilities as caps
from app.services import conversations, realtime
from app.services.audit import audit
from app.workers.dispatch import dispatch

logger = logging.getLogger(__name__)

router = APIRouter()

# Background-verification replies (add-features spec 2026-09-05): the inquiry
# email carries "Reference: BGV-<token>" in its body and asks the employer to
# keep it in the reply, so the reply (or its quoted original) contains it.
_BGV_TOKEN_IN_BODY = re.compile(r"BGV-([A-Za-z0-9_\-]{16,64})")


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
        dispatch(
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


#: The header the inbound-mail Lambda presents. Named rather than reused from a
#: standard auth header so a stray `Authorization` from a proxy cannot satisfy
#: it by accident.
INBOUND_SECRET_HEADER = "X-ReadyPick-Webhook-Secret"


def _require_relay_secret(request: Request) -> None:
    """Prove the caller is our own inbound-mail Lambda, when we can.

    `hmac.compare_digest` rather than `==`, because a plain comparison returns
    as soon as two bytes differ and leaks the secret's prefix to anyone willing
    to time a few thousand requests.

    A 403 rather than a 401: there is no authentication scheme to negotiate
    here, so telling a caller to try again with credentials would be a lie.
    """
    expected = get_settings().inbound_webhook_secret
    if not expected:
        # See `Settings.inbound_webhook_secret`. Unconfigured is a real state,
        # and it is logged every time rather than once so the line cannot be
        # lost in a rotation of the log.
        logger.warning(
            "verification.inbound_unauthenticated "
            "reason=no_inbound_webhook_secret_configured"
        )
        return
    presented = request.headers.get(INBOUND_SECRET_HEADER, "")
    if not hmac.compare_digest(presented, expected):
        logger.warning("verification.inbound_refused reason=bad_relay_secret")
        raise HTTPException(status_code=403, detail="Not permitted.")


@router.post(
    "/inbound-email",
    response_model=InboundEmailOut,
    dependencies=[Depends(_require_relay_secret)],
)
async def inbound_email_webhook(
    body: InboundEmailIn, session: AsyncSession = Depends(get_public_db)
) -> InboundEmailOut:
    """Resend inbound-parsing webhook: when an employer replies by email
    instead of using the form, enqueue LLM extraction as the fallback path
    (FR-5.3). Always returns 200 so the provider does not retry storms."""
    recipients = body.to if isinstance(body.to, list) else [body.to or ""]
    recipients += body.cc if isinstance(body.cc, list) else [body.cc or ""]

    # CONVERSATIONS FIRST, because its token is read from the ADDRESS and is
    # therefore exact. The two older paths below match on a URL or a reference
    # string in the BODY, which depends on the employer's mail client quoting
    # the original; asking the precise question first means a thread reply is
    # never mistaken for one of them.
    routed = await _match_conversation_reply(session, body, recipients)
    if routed is not None:
        return routed

    return await _match_bgv_reply(session, body.text or "")


async def _match_conversation_reply(
    session: AsyncSession, body: InboundEmailIn, recipients: list[str]
) -> InboundEmailOut | None:
    """Post an employer's reply into the conversation its address names.

    THE ADDRESS IS THE ROUTING. `conversations+<thread_token>@<inbound domain>`
    is set as the Reply-To on every verification request, so the answer lands
    in the right employer's thread whatever the employer does to the subject
    line and however little of the original their client quotes. Matching on a
    subject was considered and refused: "Re: Fwd: Re:" prefixes, translated
    prefixes and a recruiter forwarding a thread all break it, and the failure
    is silent.

    THE TOKEN IS THE AUTHORIZATION, exactly as the employer form's token is.
    This handler runs under the bypass scope (the tenant is unknown until the
    row is found), so it reads ONE row by its unguessable token and writes only
    inside that row's tenant.

    Returns None when nothing matched, so the caller falls through to the two
    older reply paths rather than swallowing their mail.
    """
    token = conversations.token_from_address(*recipients)
    if token is None:
        return None
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, bgv_verification_id "
                    "FROM conversations WHERE thread_token = :tok"
                ),
                {"tok": token},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        # A well-formed token nobody holds. Recorded without the token itself,
        # which is a live routing secret for whichever thread does hold it.
        logger.warning("conversations.inbound_unknown_thread")
        return InboundEmailOut(matched=False)

    tenant_id = uuid.UUID(str(row["tenant_id"]))
    conversation_id = uuid.UUID(str(row["id"]))
    reply_text = (body.text or "").strip()
    if not reply_text:
        # An empty reply is a real thing (an attachment with no words, an
        # HTML-only client). Recorded as arriving rather than dropped, because
        # "the employer has answered" is the fact the recruiter is waiting for.
        reply_text = "The employer replied with no message text."

    try:
        message = await conversations.post_message(
            session,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            author_party=PARTY_EMPLOYER_HR,
            body=reply_text,
            channel=CHANNEL_EMAIL,
            author_email=body.from_,
            inbound_message_id=body.message_id,
        )
    except conversations.ConversationRefused as exc:
        logger.warning("conversations.inbound_refused reason=%s", exc)
        return InboundEmailOut(matched=False)

    if row["bgv_verification_id"] is not None:
        # `responded_at` is stamped, and the STATUS is not. A person decides
        # verified or not verified after reading this; inferring either from
        # the arrival of a reply would be the automated hiring decision the
        # brief refuses.
        await session.execute(
            text(
                "UPDATE bgv_verifications SET responded_at = now(), "
                " updated_at = now() "
                "WHERE id = :vid AND responded_at IS NULL"
            ),
            {"vid": str(row["bgv_verification_id"])},
        )

    realtime.publish_after_commit(
        session,
        realtime.message_event(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message=message,
        ),
    )
    return InboundEmailOut(matched=True)


async def _match_bgv_reply(
    session: AsyncSession, reply_text: str
) -> InboundEmailOut:
    """Route an employer's background-verification reply to its inquiry row
    (add-features spec 2026-09-05, Candidate Verification).

    The reply is matched on the `BGV-<token>` reference the inquiry email
    asked the employer to keep. The raw reply is stored on the row FIRST and
    only then is extraction dispatched, so a failed parse never destroys the
    evidence it failed on (`pickready.parse_bgv_reply` reads the same text).
    A reply for a row that is not expecting one -- never dispatched, or
    already parsed -- is left alone rather than clobbering a good result.
    """
    from app.models.bgv import (
        STATUS_DISPATCHED,
        STATUS_PARSE_FAILED,
        STATUS_RESPONSE_RECEIVED,
        BGVInquiry,
    )

    match = _BGV_TOKEN_IN_BODY.search(reply_text)
    if match is None:
        return InboundEmailOut(matched=False)
    inquiry = (
        await session.execute(
            select(BGVInquiry).where(BGVInquiry.reply_token == match.group(1))
        )
    ).scalars().first()
    if inquiry is None or inquiry.status not in {
        STATUS_DISPATCHED,
        STATUS_RESPONSE_RECEIVED,
        STATUS_PARSE_FAILED,
    }:
        return InboundEmailOut(matched=False)

    now = datetime.now(timezone.utc)
    inquiry.response_raw = reply_text
    inquiry.response_received_at = inquiry.response_received_at or now
    inquiry.status = STATUS_RESPONSE_RECEIVED
    inquiry.updated_at = now
    await session.flush()
    dispatch("pickready.parse_bgv_reply", args=[str(inquiry.id), reply_text])
    return InboundEmailOut(matched=True)
