"""Conversation persistence: the one place a message becomes a row.

EVERY WRITE GOES THROUGH `post_message`, AND THAT IS THE DESIGN
----------------------------------------------------------------
A message can arrive from a recruiter's browser, from a background task
recording a bounce, from the BGV agent's first send, or from an employer's
reply landing on the inbound webhook. All four call this function, so
idempotency, the tenant check, the `last_message_at` bump and the realtime
fan-out happen once each rather than four times with three of them subtly
different. The support build's lesson, applied before the drift.

IDEMPOTENCY IS A CONSTRAINT, NOT A LOOKUP
-------------------------------------------
`post_message` does not check whether a message already exists and then insert
it: two concurrent requests would both pass the check. It inserts and lets the
UNIQUE constraint refuse the loser, then returns the row that won. That is the
same shape the credit ledger's `idempotency_key` uses, and it is the only
version that holds when two Fargate tasks handle a retry at the same moment.

AUTHORISATION IS ASKED OF THE ROW, NEVER OF THE REQUEST
---------------------------------------------------------
A conversation id arriving from a browser is a claim. `authorize_participant`
re-reads the conversation under the caller's own RLS session and compares the
tenant; a caller from another customer sees nothing, so the lookup fails as
"not found" rather than "forbidden", which is the rule cross-tenant reads
already follow everywhere else in this product.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import (
    CHANNEL_CHAT,
    CHANNELS,
    CONVERSATION_OPEN,
    DELIVERY_DELIVERED,
    DELIVERY_STATES,
    KIND_BGV,
    KIND_CANDIDATE,
    MAX_BODY_CHARS,
    PARTIES,
    PARTY_EMPLOYER_HR,
    PARTY_RECRUITER,
)


class ConversationRefused(RuntimeError):
    """The request cannot be served, with a sentence saying why."""


class ConversationNotFound(LookupError):
    """No conversation with that id is visible to this caller.

    Raised for a conversation belonging to another tenant as well as for one
    that does not exist, deliberately: distinguishing them would confirm the
    existence of another customer's data to somebody who cannot read it.
    """


def mint_thread_token() -> str:
    """The correlation secret that routes an inbound reply.

    URL-safe randomness, unguessable on purpose: it travels in a reply-to
    address a third party can see, and a guessable one would let somebody post
    into a customer's BGV thread by emailing it.
    """
    return secrets.token_urlsafe(32)[:64]


def _validate_body(body: str) -> str:
    cleaned = (body or "").strip()
    if not cleaned:
        raise ConversationRefused("A message cannot be empty.")
    if len(cleaned) > MAX_BODY_CHARS:
        raise ConversationRefused(
            f"A message is limited to {MAX_BODY_CHARS} characters."
        )
    return cleaned


async def authorize_participant(
    session: AsyncSession, *, conversation_id: uuid.UUID, tenant_id: uuid.UUID
) -> dict:
    """Load a conversation, or refuse. Never trusts the id it was handed."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, kind, subject, status, candidate_id, "
                    "bgv_verification_id, job_id, thread_token, last_message_at "
                    "FROM conversations WHERE id = :cid"
                ),
                {"cid": str(conversation_id)},
            )
        )
        .mappings()
        .first()
    )
    # Belt and braces over RLS, which has already filtered by tenant: the
    # explicit comparison is what makes the guarantee readable at the call site
    # rather than an invisible property of the session (claude.md rule 1).
    if row is None or str(row["tenant_id"]) != str(tenant_id):
        raise ConversationNotFound("Conversation not found")
    return dict(row)


async def _add_participant(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    party: str,
    user_id: uuid.UUID | None = None,
    external_email: str | None = None,
    external_name: str | None = None,
) -> None:
    """Idempotent by the same rule as a message: insert and let the constraint
    refuse the duplicate, rather than checking first and racing."""
    if party not in PARTIES:
        raise ConversationRefused(f"{party!r} is not a conversation party")
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO conversation_participants "
                    "(id, conversation_id, tenant_id, party, user_id, "
                    " external_email, external_name, created_at) "
                    "VALUES (gen_random_uuid(), :cid, :tid, :party, :uid, "
                    " :email, :name, now())"
                ),
                {
                    "cid": str(conversation_id),
                    "tid": str(tenant_id),
                    "party": party,
                    "uid": str(user_id) if user_id else None,
                    "email": external_email,
                    "name": external_name,
                },
            )
    except IntegrityError:
        # Already in the conversation. Adding somebody twice is not an error
        # anybody needs to hear about.
        pass


async def ensure_candidate_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    candidate_id: uuid.UUID,
    candidate_name: str,
    actor_user_id: uuid.UUID | None,
    job_id: uuid.UUID | None = None,
) -> dict:
    """The recruiter-to-candidate thread, created once and reused.

    ONE per candidate per tenant, deliberately: a second thread would split a
    conversation with one person across two screens, and the recruiter reading
    the newer one would not know the older existed.
    """
    existing = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, kind, subject, status, candidate_id, "
                    "bgv_verification_id, job_id, thread_token, last_message_at "
                    "FROM conversations "
                    "WHERE tenant_id = :tid AND candidate_id = :cid AND kind = :kind "
                    "ORDER BY created_at LIMIT 1"
                ),
                {
                    "tid": str(tenant_id),
                    "cid": str(candidate_id),
                    "kind": KIND_CANDIDATE,
                },
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        conversation = dict(existing)
    else:
        conversation_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, kind, subject, "
                " candidate_id, job_id, status, thread_token, created_by, created_at) "
                "VALUES (:id, :tid, :kind, :subject, :cid, :jid, :status, "
                " :token, :actor, now())"
            ),
            {
                "id": str(conversation_id),
                "tid": str(tenant_id),
                "kind": KIND_CANDIDATE,
                "subject": f"{candidate_name}, recruitment",
                "cid": str(candidate_id),
                "jid": str(job_id) if job_id else None,
                "status": CONVERSATION_OPEN,
                "token": mint_thread_token(),
                "actor": str(actor_user_id) if actor_user_id else None,
            },
        )
        conversation = await authorize_participant(
            session, conversation_id=conversation_id, tenant_id=tenant_id
        )

    if actor_user_id is not None:
        await _add_participant(
            session,
            conversation_id=uuid.UUID(str(conversation["id"])),
            tenant_id=tenant_id,
            party=PARTY_RECRUITER,
            user_id=actor_user_id,
        )
    return conversation


async def create_bgv_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    verification_id: uuid.UUID,
    candidate_id: uuid.UUID,
    candidate_name: str,
    employer_name: str,
    hr_name: str,
    hr_email: str,
    actor_user_id: uuid.UUID | None,
    job_id: uuid.UUID | None = None,
) -> dict:
    """One thread per employer. Seven employers are seven independent threads.

    The subject is composed from real context rather than typed, so a list of
    seven reads as seven different things at a glance instead of seven rows
    that all say "Employment verification".
    """
    conversation_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, kind, subject, candidate_id, "
            " bgv_verification_id, job_id, status, thread_token, created_by, "
            " created_at) "
            "VALUES (:id, :tid, :kind, :subject, :cid, :vid, :jid, :status, "
            " :token, :actor, now())"
        ),
        {
            "id": str(conversation_id),
            "tid": str(tenant_id),
            "kind": KIND_BGV,
            "subject": f"{candidate_name}, {employer_name}, background verification",
            "cid": str(candidate_id),
            "vid": str(verification_id),
            "jid": str(job_id) if job_id else None,
            "status": CONVERSATION_OPEN,
            "token": mint_thread_token(),
            "actor": str(actor_user_id) if actor_user_id else None,
        },
    )
    await _add_participant(
        session,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        party=PARTY_EMPLOYER_HR,
        external_email=hr_email,
        external_name=hr_name,
    )
    if actor_user_id is not None:
        await _add_participant(
            session,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            party=PARTY_RECRUITER,
            user_id=actor_user_id,
        )
    return await authorize_participant(
        session, conversation_id=conversation_id, tenant_id=tenant_id
    )


async def post_message(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    author_party: str,
    body: str,
    channel: str = CHANNEL_CHAT,
    author_user_id: uuid.UUID | None = None,
    author_email: str | None = None,
    author_name: str | None = None,
    client_token: str | None = None,
    inbound_message_id: str | None = None,
    delivery_status: str = DELIVERY_DELIVERED,
    email_message_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Persist one message. Idempotent, and the ONLY way a message is written.

    Returns the stored row, whether this call created it or a duplicate lost
    the race: a retried send must answer with the message that exists rather
    than an error the client cannot act on.
    """
    if author_party not in PARTIES:
        raise ConversationRefused(f"{author_party!r} is not a conversation party")
    if channel not in CHANNELS:
        raise ConversationRefused(f"{channel!r} is not a channel")
    if delivery_status not in DELIVERY_STATES:
        raise ConversationRefused(f"{delivery_status!r} is not a delivery state")
    cleaned = _validate_body(body)
    now = now or datetime.now(timezone.utc)
    message_id = uuid.uuid4()

    params = {
        "id": str(message_id),
        "cid": str(conversation_id),
        "tid": str(tenant_id),
        "party": author_party,
        "uid": str(author_user_id) if author_user_id else None,
        "email": author_email,
        "name": author_name,
        "body": cleaned,
        "channel": channel,
        "delivery": delivery_status,
        "client_token": client_token,
        "inbound_id": inbound_message_id,
        "email_msg_id": email_message_id,
        "at": now,
    }
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO conversation_messages (id, conversation_id, "
                    " tenant_id, author_party, author_user_id, author_email, "
                    " author_name, body, channel, delivery_status, client_token, "
                    " inbound_message_id, email_message_id, created_at) "
                    "VALUES (:id, :cid, :tid, :party, :uid, :email, :name, :body, "
                    " :channel, :delivery, :client_token, :inbound_id, "
                    " :email_msg_id, :at)"
                ),
                params,
            )
    except IntegrityError:
        # THE DUPLICATE LOST, which is the whole point. Return what is there:
        # a double-clicked send, a websocket replay and an SNS redelivery all
        # arrive here, and none of them should produce a second message or an
        # error the user has to understand.
        existing = await _find_duplicate(
            session,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            client_token=client_token,
            inbound_message_id=inbound_message_id,
        )
        if existing is not None:
            return existing
        raise

    await session.execute(
        text(
            "UPDATE conversations SET last_message_at = :at "
            "WHERE id = :cid AND (last_message_at IS NULL OR last_message_at < :at)"
        ),
        {"cid": str(conversation_id), "at": now},
    )
    return await get_message(session, message_id=message_id, tenant_id=tenant_id)


async def _find_duplicate(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    client_token: str | None,
    inbound_message_id: str | None,
) -> dict | None:
    if client_token:
        found = (
            await session.execute(
                text(
                    "SELECT id FROM conversation_messages "
                    "WHERE conversation_id = :cid AND client_token = :tok"
                ),
                {"cid": str(conversation_id), "tok": client_token},
            )
        ).scalar()
        if found is not None:
            return await get_message(
                session, message_id=uuid.UUID(str(found)), tenant_id=tenant_id
            )
    if inbound_message_id:
        found = (
            await session.execute(
                text(
                    "SELECT id FROM conversation_messages "
                    "WHERE tenant_id = :tid AND inbound_message_id = :mid"
                ),
                {"tid": str(tenant_id), "mid": inbound_message_id},
            )
        ).scalar()
        if found is not None:
            return await get_message(
                session, message_id=uuid.UUID(str(found)), tenant_id=tenant_id
            )
    return None


async def get_message(
    session: AsyncSession, *, message_id: uuid.UUID, tenant_id: uuid.UUID
) -> dict:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, conversation_id, tenant_id, author_party, "
                    " author_user_id, author_email, author_name, body, channel, "
                    " delivery_status, delivery_detail, client_token, "
                    " email_message_id, created_at "
                    "FROM conversation_messages WHERE id = :mid AND tenant_id = :tid"
                ),
                {"mid": str(message_id), "tid": str(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ConversationNotFound("Message not found")
    return dict(row)


async def mark_read(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Move this reader's watermark forward. NEVER backward.

    A second browser tab that loaded the thread earlier would otherwise mark
    messages unread again when it caught up, and the unread badge would count
    down and then back up while nobody did anything.
    """
    now = now or datetime.now(timezone.utc)
    await session.execute(
        text(
            "UPDATE conversation_participants SET last_read_at = :at "
            "WHERE conversation_id = :cid AND tenant_id = :tid AND user_id = :uid "
            "AND (last_read_at IS NULL OR last_read_at < :at)"
        ),
        {
            "cid": str(conversation_id),
            "tid": str(tenant_id),
            "uid": str(user_id),
            "at": now,
        },
    )


async def unread_counts(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, int]:
    """Unread per conversation, for this reader.

    A participant's own messages never count: a thread does not become unread
    because you answered it. NULL `last_read_at` means the whole history is
    unread, which is what "has never opened it" should look like.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT p.conversation_id, count(m.id) AS unread "
                    "FROM conversation_participants p "
                    "JOIN conversation_messages m "
                    "  ON m.conversation_id = p.conversation_id "
                    " AND (p.last_read_at IS NULL OR m.created_at > p.last_read_at) "
                    " AND (m.author_user_id IS NULL OR m.author_user_id <> p.user_id) "
                    "WHERE p.tenant_id = :tid AND p.user_id = :uid "
                    "GROUP BY p.conversation_id"
                ),
                {"tid": str(tenant_id), "uid": str(user_id)},
            )
        )
        .mappings()
        .all()
    )
    return {str(row["conversation_id"]): int(row["unread"]) for row in rows}


async def list_messages(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    limit: int = 50,
    before: datetime | None = None,
) -> list[dict]:
    """One page of history, oldest first within the page.

    Paged on `created_at` rather than an offset: an offset shifts when a
    message arrives mid-scroll, which duplicates or drops a row, and this is a
    surface where new rows arrive constantly by design.
    """
    params: dict = {"cid": str(conversation_id), "tid": str(tenant_id), "limit": limit}
    clause = ""
    if before is not None:
        clause = "AND m.created_at < :before "
        params["before"] = before
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.id, m.conversation_id, m.author_party, "
                    " m.author_user_id, m.author_email, m.author_name, m.body, "
                    " m.channel, m.delivery_status, m.delivery_detail, m.created_at "
                    "FROM conversation_messages m "
                    f"WHERE m.conversation_id = :cid AND m.tenant_id = :tid {clause}"
                    "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in reversed(rows)]
