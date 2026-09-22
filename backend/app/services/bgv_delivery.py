"""Did the verification request actually arrive, and what follows from that.

THE THREE-DAY CLOCK STARTS AT DELIVERY, NOT AT SEND
-----------------------------------------------------
The brief gives an employer three days to act. An employer cannot act on a
message still sitting in a provider's retry queue, and a link that expires
while a delayed message is in flight is a dead credential in a mailbox that
never showed it. Both the day-3 chase and the form link now key off
`bgv_verifications.delivered_at`.

`clock_start` falls back to the send stamp when no delivery was ever
confirmed, and that fallback is EXPLICIT rather than silent because the
alternative is worse in both directions: under the smtp transport Gmail
reports no delivery events at all, so waiting for one would mean a form link
that never expires (a standing credential in a third party's mailbox, exactly
what the TTL exists to prevent) and a chase that never fires.

THE BINDING, AND WHY AN ADDRESS IS NOT ONE
--------------------------------------------
Every outbound verification request writes an `email_log` row carrying
`bgv_verification_id`. The bounce handler used to correlate back through the
RECIPIENT ADDRESS, scoped to the tenant and ordered by send time, so one HR
mailbox confirming two candidates at the same employer (the normal case at any
large company) resolved to whichever request was sent last. The wrong
candidate was then told to correct an address that worked, and nothing
anywhere could notice. An address is a property of a recipient; it is never an
identity of a message.

WHAT THIS MODULE DOES NOT DO
------------------------------
It writes no verdict. `status` is still set only by a person's decision or by
the employer's own checkbox submission; everything here touches
`delivery_status`, which is a fact about the transport and about nothing else.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.bgv_verification import (
    DELIVERY_BOUNCED,
    DELIVERY_DELIVERED,
    DELIVERY_SENT,
)
from app.models.email_log import (
    EMAIL_TYPE_BGV_VERIFICATION,
    STATUS_QUEUED,
)
from app.services import bgv_form


# ── The clock ────────────────────────────────────────────────────────────────


def clock_start(
    delivered_at: datetime | None, sent_at: datetime | None
) -> datetime | None:
    """When the employer's three days began, or None if nothing was sent.

    Delivery when the provider confirmed one, the send stamp otherwise. The
    second case is not a guess: under the smtp transport no delivery event
    exists to wait for, so the send is the latest instant this product can
    honestly claim the message was in the employer's hands, and keying a TTL
    on a timestamp that will never arrive would leave the link live for ever.
    """
    return delivered_at or sent_at


def link_expires_at(
    delivered_at: datetime | None, sent_at: datetime | None
) -> datetime | None:
    """When the employer's form link stops working, or None if unsent."""
    started = clock_start(delivered_at, sent_at)
    return None if started is None else bgv_form.expires_at(started)


def link_expired(
    delivered_at: datetime | None,
    sent_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """True once the link is past its window.

    An UNSENT verification has no live link, so it reads as expired: a token
    with no send behind it is a token nobody was given.
    """
    expires = link_expires_at(delivered_at, sent_at)
    if expires is None:
        return True
    return (now or datetime.now(timezone.utc)) >= expires


#: The WHERE clause the reminder sweep must use, as one string so the sweep
#: and this module cannot drift into two definitions of "three days late".
#:
#: `COALESCE(v.delivered_at, v.first_sent_at)` is `clock_start` in SQL, and the
#: two are pinned against each other by `tests/test_bgv_delivery.py`. The
#: `delivery_status <> 'bounced'` term is the half that matters most: chasing a
#: candidate about an employer who never received anything sends them to argue
#: with an innocent HR team, and the bounce path has already written to them.
REMINDER_DUE_WHERE: str = (
    "v.responded_at IS NULL "
    "AND v.reminder_sent_at IS NULL "
    "AND v.first_sent_at IS NOT NULL "
    "AND v.delivery_status <> 'bounced' "
    "AND COALESCE(v.delivered_at, v.first_sent_at) <= "
    "    now() - make_interval(days => :days)"
)


def reminder_due_sql() -> str:
    """The sweep's full SELECT, so the task holds no copy of the predicate.

    The columns are exactly the ones the chase letter needs and no more:
    `id` to stamp, `tenant_id` to send under, the candidate's name and address,
    and the HR address the chase MASKS.

    `hr_email` is the EFFECTIVE address, the newest correction or the original
    declaration, which is `effective_hr_email` expressed in SQL. Reading the
    declaration instead would show a candidate who has already corrected a
    bounced address a mask of the address they replaced, and they would
    reasonably conclude their correction never took.
    """
    return (
        "SELECT v.id, v.tenant_id, c.full_name, c.email AS candidate_email, "
        " COALESCE(("
        "   SELECT k.hr_email FROM bgv_contact_corrections k "
        "    WHERE k.candidate_employment_id = e.id "
        "    ORDER BY k.created_at DESC, k.id DESC LIMIT 1"
        " ), e.hr_email) AS hr_email "
        "FROM bgv_verifications v "
        "JOIN candidate_employments e ON e.id = v.candidate_employment_id "
        "JOIN candidates c ON c.id = v.candidate_id "
        f"WHERE {REMINDER_DUE_WHERE}"
    )


# ── The effective HR address ─────────────────────────────────────────────────


async def effective_hr_email(
    session: AsyncSession, employment_id: uuid.UUID
) -> str | None:
    """The address a request to this employer must actually go to.

    The newest correction the candidate appended, or the address they declared
    when nothing has been corrected. `candidate_employments` is immutable and
    stays that way: a correction is a new row beside the claim, never a rewrite
    of it, so the declaration remains readable and a redirected verification
    remains visible.
    """
    corrected = (
        await session.execute(
            text(
                "SELECT hr_email FROM bgv_contact_corrections "
                "WHERE candidate_employment_id = :eid "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"eid": str(employment_id)},
        )
    ).scalar()
    if corrected:
        return str(corrected)
    declared = (
        await session.execute(
            text("SELECT hr_email FROM candidate_employments WHERE id = :eid"),
            {"eid": str(employment_id)},
        )
    ).scalar()
    return str(declared) if declared else None


# ── The outbound record ──────────────────────────────────────────────────────


def form_link_paragraph(token: str) -> str:
    """The sentence carrying the employer's single-use form link.

    ONE implementation, used by the recruiter's send, by the shortlist
    autostart and by a resend after a correction. It is appended server-side
    under whatever body is being sent, so no draft can drop it and no prompt
    can rewrite the contract it states.
    """
    settings = get_settings()
    frontend = settings.frontend_url.rstrip("/")
    return (
        "To complete this verification in under two minutes, use the secure "
        "form below. The link is unique to this request, works once, and "
        f"expires in {settings.verification_link_ttl_days} days:\n"
        f"{frontend}/verify-employment/{token}"
    )


async def record_outbound(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    verification_id: uuid.UUID,
    candidate_id: uuid.UUID,
    recipient: str,
    subject: str,
    body: str,
    generated_by_ai: bool,
    edited_by_human: bool,
) -> uuid.UUID:
    """Write the delivery record for one verification request, bound to it.

    Returns the `email_log` row id. The row is written BEFORE the dispatch, the
    rule the lifecycle path already follows: a record created after a
    successful send cannot describe a send that failed, and the failures are
    the rows anybody ever reads this table for.

    The row is born `queued`. Under the current `pickready.send_email` task it
    stays there, because that task records an audit line and no provider
    message id; see this module's note in the release report for the one
    trailing argument that closes it.
    """
    log_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO email_log (id, tenant_id, email_type, recipient_email, "
            " candidate_id, subject, body, status, edited_by_human, "
            " generated_by_ai, bgv_verification_id, created_at) "
            "VALUES (:id, :tid, :type, :to, :cid, :subject, :body, :status, "
            " :edited, :ai, :vid, now())"
        ),
        {
            "id": str(log_id),
            "tid": str(tenant_id),
            "type": EMAIL_TYPE_BGV_VERIFICATION,
            "to": recipient,
            "cid": str(candidate_id),
            "subject": subject[:500],
            "body": body,
            "status": STATUS_QUEUED,
            "edited": edited_by_human,
            "ai": generated_by_ai,
            "vid": str(verification_id),
        },
    )
    return log_id


async def mark_sent(
    session: AsyncSession, *, verification_id: uuid.UUID, at: datetime
) -> None:
    """Stamp a request as on its way, and reset everything the send invalidates.

    `delivered_at`, `bounced_at` and `reminder_sent_at` are all CLEARED. A
    resend after a correction is a new request to a new address, so carrying
    the old delivery outcome forward would start the new three days at the
    moment the previous message was refused, and the chase would fire
    immediately about a letter that had just gone out.

    `first_sent_at` uses COALESCE, so the FIRST send keeps its stamp: it is the
    provenance of when this employer was first asked, and a resend is not a
    reason to rewrite history.
    """
    await session.execute(
        text(
            "UPDATE bgv_verifications SET delivery_status = :state, "
            " first_sent_at = COALESCE(first_sent_at, :at), "
            " delivered_at = NULL, bounced_at = NULL, delivery_detail = NULL, "
            " reminder_sent_at = NULL, updated_at = :at "
            "WHERE id = :vid"
        ),
        {"state": DELIVERY_SENT, "at": at, "vid": str(verification_id)},
    )


async def mark_delivered(
    session: AsyncSession, *, verification_id: uuid.UUID, at: datetime
) -> None:
    """The provider confirmed the employer's mail server took the message.

    Written once (`COALESCE`), because SNS delivers at least once and a
    redelivered event must not restart the employer's three days.

    A row already recorded as BOUNCED is left alone. SES can report a delivery
    to one recipient and a bounce to another on the same message, and events
    arrive out of order; demoting a bounce to a delivery would tell a candidate
    their correction was unnecessary and silently stop the chase.
    """
    await session.execute(
        text(
            "UPDATE bgv_verifications SET delivered_at = COALESCE(delivered_at, :at), "
            " delivery_status = :state, updated_at = :at "
            "WHERE id = :vid AND delivery_status <> :bounced"
        ),
        {
            "at": at,
            "state": DELIVERY_DELIVERED,
            "bounced": DELIVERY_BOUNCED,
            "vid": str(verification_id),
        },
    )


async def mark_bounced(
    session: AsyncSession,
    *,
    verification_id: uuid.UUID,
    at: datetime,
    reason: str | None,
) -> None:
    """The message was refused. Terminal until somebody sends a new one.

    BOUNCED outranks every other delivery state, the same ordering
    `_OUTCOME_RANK` applies to the `email_log` row: it is the one a person has
    to act on, and a later delivery event about a second recipient must not
    erase it.
    """
    await session.execute(
        text(
            "UPDATE bgv_verifications SET delivery_status = :state, "
            " bounced_at = COALESCE(bounced_at, :at), "
            " delivery_detail = COALESCE(:reason, delivery_detail), "
            " updated_at = :at "
            "WHERE id = :vid"
        ),
        {
            "state": DELIVERY_BOUNCED,
            "at": at,
            "reason": (reason or None),
            "vid": str(verification_id),
        },
    )


async def issue_form_token(
    session: AsyncSession, *, verification_id: uuid.UUID, at: datetime
) -> str | None:
    """Mint a FRESH single-use form token for a request about to go out.

    None once the form has been submitted: a completed verification never
    reissues a credential, and the caller sends without a link rather than
    handing an employer a second door into work they have already done.
    """
    submitted = (
        await session.execute(
            text(
                "SELECT form_submitted_at FROM bgv_verifications WHERE id = :vid"
            ),
            {"vid": str(verification_id)},
        )
    ).scalar()
    if submitted is not None:
        return None
    token = bgv_form.mint_token()
    await session.execute(
        text(
            "UPDATE bgv_verifications SET form_token = :tok, "
            " form_token_issued_at = :at WHERE id = :vid"
        ),
        {"tok": token, "at": at, "vid": str(verification_id)},
    )
    return token
