"""The ONE writer of candidate-facing `email_log` rows.

Four places used to build an `EmailLog` by hand (the recruiter's send, the
pipeline transition email, the databank invitation and the automatic
confirmation and reminder) and none of them set `sender_id`, so a corporate
sender a client had registered, verified and authorized was never used for a
single email. Every one of them also dispatched the send BEFORE the request
committed. This module is where all of that is decided once:

WHO IT IS FROM
--------------
`resolve_sender` answers "which corporate sender does this email carry". A
sender the caller named must be one of this tenant's ACTIVE senders (the early,
actionable refusal; the worker re-checks at send time, which is the security
boundary). No sender named means the tenant's DEFAULT active sender, and no
default means None, which is the platform mailbox. So an automatic email with
no human behind it still goes out under the company's own address once the
company has picked one.

ONCE, WHEN IT MUST BE ONCE
--------------------------
`dedupe_key` is UNIQUE in the database (migration 0122) and the insert is
`ON CONFLICT DO NOTHING`, so a redelivered task, a double dispatch and a sweep
that raced a live request all collapse to one row. The key names the STAGE of
an automatic email (`assessment_reminder:<link>:72`), which is what lets the
second reminder be sent at all. Human-sent emails carry no key: sending the
same person two messages on purpose is allowed.

WHERE A REPLY GOES
------------------
An email a PERSON sent is bound to the tenant's thread with that candidate
(`thread`, default True when `sent_by` is set). The recruiter becomes a
participant, the email appears in the thread as a message on the email
channel, and the worker sets the thread's own reply address as Reply-To, so an
emailed answer lands in the conversation. Automatic emails are NOT threaded:
a reply to a confirmation nobody wrote would land in a thread nobody watches.
With no inbound domain configured (pilot, 2026-09) the binding is still
written and the Reply-To simply is not set; `conversations.reply_address`
logs that once.

WHEN IT IS SENT
---------------
`dispatch_after_commit`, always, from requests and from workers alike. A
rolled-back request queues nothing, and the send can never start before its
row is visible. A lost invoke leaves the row `queued`, and
`pickready.reconcile_queued_emails` re-dispatches it; the worker's atomic
claim is what makes that re-dispatch safe.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_log import LOGGED_EMAIL_TYPES, STATUS_QUEUED, EmailLog
from app.models.email_sender import SENDER_ACTIVE, ClientEmailSender
from app.workers.dispatch import dispatch_after_commit

logger = logging.getLogger(__name__)

#: The task that delivers one queued row.
SEND_TASK = "pickready.send_lifecycle_email"


class SenderNotFound(LookupError):
    """The named sender does not exist in this tenant. A 404 at the route,
    never a 403: existence in another tenant is not confirmed."""


class SenderNotActive(RuntimeError):
    """The named sender exists and cannot send. A 409 at the route."""


def confirmation_key(link_id: uuid.UUID | str) -> str:
    return f"application_confirmation:{link_id}"


def reminder_key(link_id: uuid.UUID | str, stage_hours: int) -> str:
    """One key per reminder STAGE, so the 24 hour and the 72 hour reminder are
    two emails and a redelivered 24 hour one is not a third."""
    return f"assessment_reminder:{link_id}:{int(stage_hours)}"


def message_notification_key(message_id: uuid.UUID | str) -> str:
    return f"message_notification:{message_id}"


async def resolve_sender(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    requested_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """The sender an email to be queued now should carry.

    Named: it must be this tenant's and ACTIVE, or this RAISES. Not named: the
    tenant's default ACTIVE sender, or None for the platform mailbox. The
    default being revoked in the meantime is not an error, because a revoke
    clears the flag in the same statement.
    """
    if requested_id is not None:
        sender = await session.get(ClientEmailSender, requested_id)
        if sender is None or sender.tenant_id != tenant_id:
            raise SenderNotFound("Sender not found")
        if sender.status != SENDER_ACTIVE:
            raise SenderNotActive(
                "That sender is not active. Only an active, authorized sender "
                "can be used for automated emails."
            )
        return sender.id
    return (
        await session.execute(
            select(ClientEmailSender.id).where(
                ClientEmailSender.tenant_id == tenant_id,
                ClientEmailSender.is_default.is_(True),
                ClientEmailSender.status == SENDER_ACTIVE,
            )
        )
    ).scalar_one_or_none()


async def dedupe_key_exists(session: AsyncSession, key: str) -> bool:
    """A cheap look BEFORE drafting, so a redelivered automatic email does not
    pay for a model call it will then throw away. The insert's ON CONFLICT is
    still what makes it correct under concurrency; this only saves money."""
    found = (
        await session.execute(
            select(EmailLog.id).where(EmailLog.dedupe_key == key).limit(1)
        )
    ).first()
    return found is not None


async def queue_candidate_email(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email_type: str,
    recipient_email: str,
    candidate_id: uuid.UUID | None,
    job_id: uuid.UUID | None,
    link_id: uuid.UUID | None,
    subject: str,
    body: str,
    generated_by_ai: bool,
    edited_by_human: bool,
    sent_by: uuid.UUID | None,
    requested_sender_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    thread: bool | None = None,
    conversation_id: uuid.UUID | None = None,
    candidate_name: str | None = None,
) -> EmailLog | None:
    """Record one outbound candidate email and queue its delivery after commit.

    Returns the row, or None when `dedupe_key` already exists (the email was
    already queued once, which is the whole point of the key).

    `conversation_id` binds the row to a thread WITHOUT posting anything into
    it: the message notification, whose content is already a message there.
    `thread=True` (the default when a person sent it) finds or opens the
    tenant's thread with the candidate and posts this email into it as a
    message, so the thread shows what the candidate is replying to.
    """
    if email_type not in LOGGED_EMAIL_TYPES:
        # A programming error: the CHECK would refuse it at flush anyway, with
        # a message naming no caller.
        raise ValueError(f"{email_type!r} is not a logged email type")
    if thread is None:
        thread = sent_by is not None and conversation_id is None
    if thread and candidate_id is None:
        raise ValueError("a threaded email needs the candidate it is written to")

    sender_id = await resolve_sender(session, tenant_id, requested_sender_id)

    values = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "email_type": email_type,
        "recipient_email": recipient_email,
        "candidate_id": candidate_id,
        "job_id": job_id,
        "job_candidate_link_id": link_id,
        "subject": subject,
        "body": body,
        "status": STATUS_QUEUED,
        "edited_by_human": edited_by_human,
        "generated_by_ai": generated_by_ai,
        "sent_by": sent_by,
        "sender_id": sender_id,
        "dedupe_key": dedupe_key,
        "conversation_id": conversation_id,
    }
    statement = pg_insert(EmailLog).values(**values).returning(EmailLog.id)
    if dedupe_key is not None:
        statement = statement.on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            index_where=text("dedupe_key IS NOT NULL"),
        )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    if inserted is None:
        logger.info(
            "email_outbox.deduplicated type=%s key=%s", email_type, dedupe_key
        )
        return None
    row = await session.get(EmailLog, inserted)
    if row is None:
        raise RuntimeError(f"email_log {inserted} vanished inside its own transaction")

    if thread:
        await _thread(
            session,
            row=row,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            job_id=job_id,
            sent_by=sent_by,
        )

    # A lost invoke leaves the row `queued`; reconcile_queued_emails repairs it.
    dispatch_after_commit(session, SEND_TASK, args=[str(row.id)])
    return row


async def _thread(
    session: AsyncSession,
    *,
    row: EmailLog,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
    candidate_name: str | None,
    job_id: uuid.UUID | None,
    sent_by: uuid.UUID | None,
) -> None:
    """Bind the email to the candidate's thread and show it there."""
    from app.models.conversation import CHANNEL_EMAIL, DELIVERY_PENDING, PARTY_RECRUITER
    from app.services import conversations, realtime

    conversation = await conversations.ensure_candidate_conversation(
        session,
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name or "Candidate",
        actor_user_id=sent_by,
        job_id=job_id,
    )
    conversation_id = uuid.UUID(str(conversation["id"]))
    row.conversation_id = conversation_id
    message = await conversations.post_message(
        session,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        author_party=PARTY_RECRUITER,
        author_user_id=sent_by,
        body=f"{row.subject}\n\n{row.body}",
        channel=CHANNEL_EMAIL,
        # The email is not delivered yet. The worker moves this with the
        # email_log row, so the thread and the mail log never disagree.
        delivery_status=DELIVERY_PENDING,
        # Derived from the row, so the post is idempotent with the email.
        client_token=f"email-{row.id.hex[:48]}",
        email_log_id=row.id,
    )
    await session.flush()
    realtime.publish_after_commit(
        session,
        realtime.message_event(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message=message,
        ),
    )


# ── Claiming a row, and settling the thread message that mirrors it ──────────


async def claim(session: AsyncSession, email_log_id: uuid.UUID) -> bool:
    """Move one row from `queued` to `processing`, or report that it was not.

    ONE conditional UPDATE, so of two invocations racing on one row exactly one
    sees a row come back: the second waits on the first's row lock, re-reads
    `status`, and finds `processing`. That is what makes a redelivered task, a
    re-dispatch by `reconcile_queued_emails` and a double dispatch safe. The
    caller COMMITS before sending, so the claim is visible to every other
    worker while the transport call is in flight.
    """
    claimed = (
        await session.execute(
            text(
                "UPDATE email_log SET status = 'processing', claimed_at = now() "
                "WHERE id = :id AND status = 'queued' RETURNING id"
            ),
            {"id": str(email_log_id)},
        )
    ).scalar_one_or_none()
    return claimed is not None


async def settle_thread_message(
    session: AsyncSession, email_log_id: uuid.UUID, *, state: str, detail: str | None
) -> None:
    """Carry the email's outcome onto the thread message that shows it, so the
    conversation and the mail log never disagree about whether it was sent."""
    await session.execute(
        text(
            "UPDATE conversation_messages SET delivery_status = :state, "
            " delivery_detail = :detail "
            "WHERE email_log_id = :id AND delivery_status = 'pending'"
        ),
        {"id": str(email_log_id), "state": state, "detail": detail},
    )


# ── The sweep that repairs a lost dispatch ───────────────────────────────────

#: A queued row older than this was not picked up by the dispatch that queued
#: it. Ten minutes is past every send's first attempt, so a row this old with
#: nothing working on it is a lost invoke, not a slow one. Re-dispatching a row
#: that IS mid-retry is safe: the claim lets exactly one invocation send it.
REDISPATCH_AFTER_MINUTES = 10
#: A queued row OLDER than this is not sent at all. A confirmation or a
#: reminder delivered a day late reads as a mistake, and one delivered a month
#: late (a row stranded before this sweep existed) is one. It is counted and
#: reported at ERROR for a person to decide.
REDISPATCH_WINDOW_HOURS = 24
#: A claimed row still `processing` this long after its CLAIM may or may not
#: have left the transport. It is REPORTED, never resent: a duplicate email is
#: worse than a late alarm, and the transport cannot be asked afterwards.
STUCK_AFTER_MINUTES = 30
#: Bounded, so one sweep over a backlog cannot become a burst the mail
#: provider rate-limits.
SWEEP_BATCH = 200


async def reconcile_queued(session: AsyncSession) -> dict:
    """What `pickready.reconcile_queued_emails` should do this run.

    Returns the ids to re-dispatch and the counts to report. Reads only: the
    task owns the dispatches, the way every sweep's worker owns its run.
    """
    redispatch = [
        str(value)
        for value in (
            await session.execute(
                text(
                    "SELECT id FROM email_log "
                    "WHERE status = 'queued' "
                    "AND created_at < now() - make_interval(mins => :after) "
                    "AND created_at >= now() - make_interval(hours => :window) "
                    "ORDER BY created_at, id LIMIT :batch"
                ),
                {
                    "after": REDISPATCH_AFTER_MINUTES,
                    "window": REDISPATCH_WINDOW_HOURS,
                    "batch": SWEEP_BATCH,
                },
            )
        ).scalars()
    ]
    abandoned = (
        await session.execute(
            text(
                "SELECT count(*) FROM email_log WHERE status = 'queued' "
                "AND created_at < now() - make_interval(hours => :window)"
            ),
            {"window": REDISPATCH_WINDOW_HOURS},
        )
    ).scalar_one()
    stuck = (
        await session.execute(
            text(
                "SELECT count(*) FROM email_log WHERE status = 'processing' "
                "AND claimed_at < now() - make_interval(mins => :stuck)"
            ),
            {"stuck": STUCK_AFTER_MINUTES},
        )
    ).scalar_one()
    return {
        "redispatch": redispatch,
        "abandoned_queued": int(abandoned),
        "stuck_processing": int(stuck),
    }
