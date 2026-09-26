"""The inbound-email webhook: every emailed reply the product can receive.

A reply arrives here from the inbound-mail Lambda and is routed, most precise
question first: a conversation thread named by the reply ADDRESS (a candidate
answering a recruiter, or an employer answering a BGV request), then the older
BGV inquiry reference in the body.

THE TENANT-OWNED EMPLOYER-VERIFICATION SYSTEM IS RETIRED (vivekium C8,
owner-ruled 2026-09-18). Its routes and tasks went on 2026-09-18, and its
table went with migration 0121, which drops it only when EMPTY and raises
otherwise. The surviving system is the candidate-owned one:
candidate_employments, bgv_verifications and the seven-item checkbox form at
/bgv/form/{token}.

THE OUTREACH ROUTE IS RETIRED TOO (Phase 6). The recruiter's outreach POST
on this router mailed a sourced candidate a link to a long questionnaire that no screen
sent and nothing downstream read; the unified six-field application replaced
it. The route and its schemas are gone, and the Phase 6 removal sweep keeps
them gone.
"""
import hmac
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_public_db
from app.core.config import get_settings
from app.models.conversation import (
    CHANNEL_EMAIL,
    KIND_CANDIDATE,
    PARTY_CANDIDATE,
    PARTY_EMPLOYER_HR,
)
from app.schemas.verification import InboundEmailIn, InboundEmailOut
from app.services import conversations, realtime
from app.workers.dispatch import dispatch_after_commit

logger = logging.getLogger(__name__)

router = APIRouter()

# Background-verification replies (add-features spec 2026-09-05): the inquiry
# email carries "Reference: BGV-<token>" in its body and asks the employer to
# keep it in the reply, so the reply (or its quoted original) contains it.
_BGV_TOKEN_IN_BODY = re.compile(r"BGV-([A-Za-z0-9_\-]{16,64})")


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
    """Route one emailed reply into the record it answers.

    Always 200, so the relay does not retry a reply nothing here can place.
    A reply that matches nothing is logged by the matcher that looked, and
    answers `matched=false`.
    """
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
    """Post an emailed reply into the conversation its address names.

    THE ADDRESS IS THE ROUTING. `conversations+<thread_token>@<inbound domain>`
    is the Reply-To on every BGV verification request, and on every email a
    recruiter sends a candidate and every new-message notification (Phase 6,
    `services/email_outbox`), so the answer lands in the right thread whatever
    the sender does to the subject line and however little of the original
    their client quotes. Matching on a subject was considered and refused:
    "Re: Fwd: Re:" prefixes, translated prefixes and a forwarded thread all
    break it, and the failure is silent.

    WHO IS WRITING IS THE THREAD'S KIND, never a guess from the address. A
    reply into a CANDIDATE thread is the candidate's (`PARTY_CANDIDATE`), with
    the quoted history stripped (`conversations.strip_quoted_reply`) because
    the candidate's mail client quotes the whole message they are answering.
    `author_email` records the address it actually came from, so a reply sent
    from another mailbox is visible to the recruiter rather than silently
    attributed. A BGV thread's reply is the employer's, unchanged, and its
    body is kept whole: it is evidence for a verification decision.

    INERT WHERE NO MAIL IS RECEIVED. With `INBOUND_EMAIL_DOMAIN` empty (pilot,
    2026-09: its region is not an SES receiving region) no Reply-To carries a
    token and nothing reaches this path; the code ships tested and waits on
    the infrastructure.

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
                    "SELECT id, tenant_id, kind, bgv_verification_id "
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
    from_candidate = row["kind"] == KIND_CANDIDATE
    reply_text = (body.text or "").strip()
    if from_candidate:
        reply_text = conversations.strip_quoted_reply(reply_text)
    if not reply_text:
        # An empty reply is a real thing (an attachment with no words, an
        # HTML-only client). Recorded as arriving rather than dropped, because
        # "they have answered" is the fact the recruiter is waiting for.
        reply_text = (
            "The candidate replied with no message text."
            if from_candidate
            else "The employer replied with no message text."
        )

    try:
        message = await conversations.post_message(
            session,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            author_party=PARTY_CANDIDATE if from_candidate else PARTY_EMPLOYER_HR,
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
    # AFTER the commit that stores the raw reply: the parse reads that row,
    # and a rolled-back webhook must not parse a reply that was never kept.
    # A lost invoke leaves the inquiry at `response_received` with the raw
    # text stored, which is where a person can still read it.
    dispatch_after_commit(
        session, "pickready.parse_bgv_reply", args=[str(inquiry.id), reply_text]
    )
    return InboundEmailOut(matched=True)
