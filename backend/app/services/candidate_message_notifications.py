"""Telling a candidate that a recruiter wrote to them.

A candidate's Messages page only helps somebody who opens it, and nothing
brought them there: a recruiter's message sat unread until the candidate
happened to sign in. When a recruiter posts in a candidate thread,
`pickready.notify_candidate_of_message` runs (dispatched after the message
commits) and this module writes TWO things, the same pair every other
candidate-facing event produces:

  * an Updates feed entry, `candidate_updates.MESSAGE_RECEIVED`, linking to the
    thread. The feed exists because email fails silently; and
  * one `message_notification` email through `services/email_outbox`, carrying
    the message itself, so a candidate can read it without signing in.

ONE NOTIFICATION PER BURST, AND READING RE-ARMS IT
-------------------------------------------------
Five messages written in two minutes are one notification, not five. The
debounce is `conversations.candidate_notified_at`, and it is CLAIMED with one
conditional UPDATE, so two tasks for a burst racing each other cannot both
send. A notification is due when the thread was never notified, when the last
one is older than `candidate_message_notify_debounce_minutes`, or when the
candidate has read the thread since it (they are caught up, so the next
message is news again).

NOTHING IS SENT ABOUT A MESSAGE ALREADY READ
--------------------------------------------
If the candidate read the thread after the message was written (they had the
page open), the task does nothing: telling somebody about a message they have
just read is noise.

THE COPY IS FIXED, NEVER GENERATED
----------------------------------
No model is called. The email quotes the recruiter's own words verbatim, which
is the content; everything around them is a constant here, bound by the same
rules as the Updates catalogue: no grade word, no score, no number of our
making, no em dash. The "reply to this email" sentence appears ONLY when the
deployment can receive replies (`conversations.reply_address` is not None);
promising a reply path the deployment does not have would lose the
candidate's answer.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.conversation import KIND_CANDIDATE, PARTY_CANDIDATE
from app.models.email_log import EMAIL_TYPE_MESSAGE_NOTIFICATION
from app.services import candidate_updates, conversations, email_outbox

logger = logging.getLogger(__name__)

#: How much of the recruiter's message the email carries before pointing at
#: the portal for the rest. The whole thread lives there; the email is a door.
EMAIL_EXCERPT_CHARS = 2000

SUBJECT = "New message from {company}"

BODY_OPENING = "Hello {name},\n\n{company} sent you a message:\n\n"
BODY_PORTAL = "\n\nRead the conversation and reply in your candidate portal:\n{url}"
BODY_REPLY_BY_EMAIL = "\n\nYou can also reply to this email and your answer will reach them."


@dataclass(frozen=True)
class NotificationOutcome:
    """What the task did, for its log line and its tests. `reason` is set
    whenever nothing was sent."""

    notified: bool
    emailed: bool
    reason: str | None = None

    def as_dict(self) -> dict:
        return {"notified": self.notified, "emailed": self.emailed, "reason": self.reason}


def _excerpt(body: str) -> str:
    cleaned = (body or "").strip()
    if len(cleaned) <= EMAIL_EXCERPT_CHARS:
        return cleaned
    # Cut at a word boundary and SAY that it was cut, so a truncated message
    # never reads as the whole of what the recruiter wrote.
    cut = cleaned[:EMAIL_EXCERPT_CHARS].rsplit(" ", 1)[0]
    return f"{cut} ...\n\n(The full message is in your candidate portal.)"


def compose_email(
    *,
    candidate_name: str | None,
    company_name: str,
    message_body: str,
    portal_url: str,
    reply_by_email: bool,
) -> tuple[str, str]:
    """(subject, body). Pure, so the copy can be swept by a test."""
    subject = SUBJECT.format(company=company_name)
    body = (
        BODY_OPENING.format(name=candidate_name or "there", company=company_name)
        + _excerpt(message_body)
        + BODY_PORTAL.format(url=portal_url)
    )
    if reply_by_email:
        body += BODY_REPLY_BY_EMAIL
    return subject, body


async def _claim_notification(
    session: AsyncSession, *, conversation_id: uuid.UUID, debounce_minutes: int
) -> bool:
    """Take the right to notify for this burst, or learn somebody has it."""
    claimed = (
        await session.execute(
            text(
                "UPDATE conversations SET candidate_notified_at = now() "
                "WHERE id = :cid AND ("
                " candidate_notified_at IS NULL "
                " OR candidate_notified_at <= now() - make_interval(mins => :mins) "
                " OR (candidate_last_read_at IS NOT NULL "
                "     AND candidate_last_read_at >= candidate_notified_at)"
                ") RETURNING id"
            ),
            {"cid": str(conversation_id), "mins": int(debounce_minutes)},
        )
    ).scalar_one_or_none()
    return claimed is not None


async def notify(
    session: AsyncSession, *, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> NotificationOutcome:
    """Tell the candidate about one message, when it is news. The caller
    commits; the email's delivery is dispatched by that commit."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.id, c.tenant_id, c.kind, c.candidate_id, c.thread_token, "
                    " c.candidate_last_read_at, "
                    " m.author_party, m.body, m.created_at AS message_at, "
                    " cand.full_name, cand.email, t.name AS company_name "
                    "FROM conversation_messages m "
                    "JOIN conversations c ON c.id = m.conversation_id "
                    "JOIN candidates cand ON cand.id = c.candidate_id "
                    "JOIN tenants t ON t.id = c.tenant_id "
                    "WHERE m.id = :mid AND c.id = :cid"
                ),
                {"mid": str(message_id), "cid": str(conversation_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return NotificationOutcome(False, False, "message not found")
    if row["kind"] != KIND_CANDIDATE:
        return NotificationOutcome(False, False, "not a candidate thread")
    if row["author_party"] == PARTY_CANDIDATE:
        return NotificationOutcome(False, False, "written by the candidate")
    read_at = row["candidate_last_read_at"]
    if read_at is not None and read_at >= row["message_at"]:
        return NotificationOutcome(False, False, "already read")

    settings = get_settings()
    if not await _claim_notification(
        session,
        conversation_id=conversation_id,
        debounce_minutes=settings.candidate_message_notify_debounce_minutes,
    ):
        return NotificationOutcome(False, False, "notified recently")

    tenant_id = uuid.UUID(str(row["tenant_id"]))
    candidate_id = uuid.UUID(str(row["candidate_id"]))
    company = row["company_name"] or "The hiring team"

    emailed = False
    if row["email"]:
        portal_url = (
            f"{settings.frontend_url.rstrip('/')}/portal/messages"
            f"?conversation={conversation_id}"
        )
        subject, body = compose_email(
            candidate_name=row["full_name"],
            company_name=company,
            message_body=row["body"],
            portal_url=portal_url,
            reply_by_email=conversations.reply_address(row["thread_token"]) is not None,
        )
        queued = await email_outbox.queue_candidate_email(
            session,
            tenant_id=tenant_id,
            email_type=EMAIL_TYPE_MESSAGE_NOTIFICATION,
            recipient_email=row["email"],
            candidate_id=candidate_id,
            job_id=None,
            link_id=None,
            subject=subject,
            body=body,
            generated_by_ai=False,
            edited_by_human=False,
            sent_by=None,
            dedupe_key=email_outbox.message_notification_key(message_id),
            # Bound to the thread so the Reply-To is the thread's own address;
            # nothing is posted, because the message is already there.
            conversation_id=conversation_id,
        )
        emailed = queued is not None
    else:
        logger.info(
            "candidate_message_notification.no_address conversation=%s",
            conversation_id,
        )

    await candidate_updates.record(
        session,
        kind=candidate_updates.MESSAGE_RECEIVED,
        candidate_id=candidate_id,
        company_name=company,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        emailed=emailed,
    )
    return NotificationOutcome(True, emailed)
