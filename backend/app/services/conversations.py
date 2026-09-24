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

import logging
import re
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
    PARTY_CANDIDATE,
    PARTY_EMPLOYER_HR,
    PARTY_RECRUITER,
)

logger = logging.getLogger(__name__)


class ConversationRefused(RuntimeError):
    """The request cannot be served, with a sentence saying why."""


class ClientTokenReused(ConversationRefused):
    """A client token already names a DIFFERENT message in this thread.

    A retry must carry the same token AND the same message, because that is
    what makes it a retry. The same token with other words is a client bug
    (a composer that kept its token across an edit), and answering with the
    message that exists would silently drop what the person just wrote.
    """


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


#: The local part every inbound conversation reply arrives on. One address per
#: THREAD via the plus tag, never one shared mailbox everything is sorted out
#: of afterwards: the address IS the routing, so a reply that quotes nothing,
#: rewrites the subject and strips the body still lands in the right thread.
REPLY_LOCAL_PART = "conversations"

_REPLY_TOKEN_IN_ADDRESS = re.compile(
    rf"{REPLY_LOCAL_PART}\+([A-Za-z0-9_\-]{{16,64}})@", re.IGNORECASE
)

#: Logged once per process rather than per send. An operator needs to know the
#: deployment cannot receive replies; they do not need to be told on every
#: email.
_warned_no_inbound_domain = False


def reply_address(thread_token: str) -> str | None:
    """The Reply-To that routes an employer's answer back into this thread.

    None when no inbound domain is configured, and the caller RECORDS that:
    a deployment with no receiving domain still sends verification requests,
    and the reply lands in the sender's own mailbox. Pretending otherwise would
    leave a recruiter waiting for a reply that arrived somewhere else.
    """
    global _warned_no_inbound_domain
    domain = (get_settings().inbound_email_domain or "").strip().lstrip("@")
    if not domain:
        if not _warned_no_inbound_domain:
            _warned_no_inbound_domain = True
            logger.warning(
                "conversations.no_inbound_domain no Reply-To will be set, so an "
                "employer reply arrives in the sending mailbox rather than in "
                "the thread"
            )
        return None
    return f"{REPLY_LOCAL_PART}+{thread_token}@{domain}"


def token_from_address(*addresses: str | None) -> str | None:
    """The thread token carried by whichever recipient address holds one.

    Read from the ADDRESS rather than the body. A body match depends on the
    employer's mail client quoting the original, which many do not and some
    mangle; an address is reproduced verbatim by every mail system there is.
    """
    for address in addresses:
        match = _REPLY_TOKEN_IN_ADDRESS.search(address or "")
        if match:
            return match.group(1)
    return None


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
    # ON CONFLICT rather than a caught IntegrityError, and the difference is
    # not style. A swallowed exception hides every OTHER integrity failure this
    # statement could raise (a dangling conversation, a tenant that no longer
    # exists) behind the one that is expected, which is exactly the silent
    # fallback this codebase forbids. Stating the no-op in SQL means the
    # database absorbs the duplicate and nothing else.
    await session.execute(
        text(
            "INSERT INTO conversation_participants "
            "(id, conversation_id, tenant_id, party, user_id, "
            " external_email, external_name, created_at) "
            "VALUES (gen_random_uuid(), :cid, :tid, :party, :uid, "
            " :email, :name, now()) "
            "ON CONFLICT ON CONSTRAINT uq_participant_user DO NOTHING"
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
    email_log_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict:
    """Persist one message. Idempotent, and the ONLY way a message is written.

    Returns the stored row, whether this call created it or a duplicate lost
    the race: a retried send must answer with the message that exists rather
    than an error the client cannot act on.

    A REUSED CLIENT TOKEN IS A RETRY ONLY WHEN IT CARRIES THE SAME MESSAGE.
    The same author and the same words return the stored row; anything else
    raises `ClientTokenReused`, because a composer that kept one token across
    an edit would otherwise lose the edited text without a word.
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
        "email_log_id": str(email_log_id) if email_log_id else None,
        "at": now,
    }
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO conversation_messages (id, conversation_id, "
                    " tenant_id, author_party, author_user_id, author_email, "
                    " author_name, body, channel, delivery_status, client_token, "
                    " inbound_message_id, email_message_id, email_log_id, "
                    " created_at) "
                    "VALUES (:id, :cid, :tid, :party, :uid, :email, :name, :body, "
                    " :channel, :delivery, :client_token, :inbound_id, "
                    " :email_msg_id, :email_log_id, :at)"
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
        if existing is None:
            raise
        if client_token and existing.get("client_token") == client_token:
            same_author = existing["author_party"] == author_party and str(
                existing.get("author_user_id") or ""
            ) == str(author_user_id or "")
            if not same_author or existing["body"] != cleaned:
                raise ClientTokenReused(
                    "This message was already sent with different text. "
                    "Refresh the conversation and send it again."
                ) from None
        return existing

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
    before_id: uuid.UUID | None = None,
) -> list[dict]:
    """One page of history, oldest first within the page.

    Paged on a KEYSET rather than an offset: an offset shifts when a message
    arrives mid-scroll, which duplicates or drops a row, and this is a surface
    where new rows arrive constantly by design.

    The key is (`created_at`, `id`), the order the page is read in, when the
    caller passes the oldest message's id as well as its time. `created_at`
    alone is a partial order: two messages written in the same microsecond
    would both sit on the boundary and "load earlier" would skip one of them
    for ever. `before` alone is still accepted and keeps its old meaning.
    """
    params: dict = {"cid": str(conversation_id), "tid": str(tenant_id), "limit": limit}
    clause = ""
    if before is not None and before_id is not None:
        clause = "AND (m.created_at, m.id) < (:before, CAST(:before_id AS uuid)) "
        params["before"] = before
        params["before_id"] = str(before_id)
    elif before is not None:
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


# ── The candidate's side of a thread ─────────────────────────────────────────
#
# A candidate is never a `conversation_participants` row: they have no tenant,
# and the participant table is the tenant's directory of who is in a thread.
# A candidate thread has exactly ONE candidate, so their watermark lives on the
# thread row (`candidate_last_read_at`, migration 0121). Every function here is
# called with a candidate id the ROUTE resolved from the session, and filters
# by it; none of them trusts a conversation id on its own.


async def mark_candidate_read(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    candidate_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Move the candidate's watermark forward. NEVER backward, for the reason
    `mark_read` gives: a stale tab catching up must not re-mark messages
    unread."""
    now = now or datetime.now(timezone.utc)
    await session.execute(
        text(
            "UPDATE conversations SET candidate_last_read_at = :at "
            "WHERE id = :cid AND candidate_id = :cand AND kind = :kind "
            "AND (candidate_last_read_at IS NULL OR candidate_last_read_at < :at)"
        ),
        {
            "cid": str(conversation_id),
            "cand": str(candidate_id),
            "kind": KIND_CANDIDATE,
            "at": now,
        },
    )


async def candidate_unread_counts(
    session: AsyncSession, *, candidate_id: uuid.UUID
) -> dict[str, int]:
    """Unread per candidate thread: messages from anybody but the candidate,
    written after the candidate last read the thread. A thread with nothing
    unread is absent from the answer."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id AS conversation_id, count(m.id) AS unread "
                    "FROM conversations c "
                    "JOIN conversation_messages m ON m.conversation_id = c.id "
                    " AND m.author_party <> :self "
                    " AND m.created_at > COALESCE(c.candidate_last_read_at, "
                    "     CAST('-infinity' AS timestamptz)) "
                    "WHERE c.candidate_id = :cand AND c.kind = :kind "
                    "GROUP BY c.id"
                ),
                {
                    "cand": str(candidate_id),
                    "kind": KIND_CANDIDATE,
                    "self": PARTY_CANDIDATE,
                },
            )
        )
        .mappings()
        .all()
    )
    return {str(row["conversation_id"]): int(row["unread"]) for row in rows}


# ── Quoted history in an emailed reply ───────────────────────────────────────

#: Where a mail client starts quoting the message being answered. Each is a
#: whole LINE, never a substring: "wrote:" in the middle of a sentence is the
#: candidate's own words.
_QUOTE_HEADERS = (
    # Gmail, Apple Mail and most others: "On Tue, 22 Sep 2026 at 10:00, Priya
    # <priya@corp.example> wrote:", sometimes wrapped over two lines, which the
    # joined check below handles.
    re.compile(r"^\s*On\b.{0,400}\bwrote:\s*$", re.IGNORECASE),
    # Outlook.
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
)


def strip_quoted_reply(body: str) -> str:
    """The new text of an emailed reply, without the history it quotes.

    Cut at the first quote header, or before a trailing block of `>` lines.
    When the cut would leave nothing (a reply that is ONLY a quote, or a
    client that writes its answer below the quote), the ORIGINAL text is
    returned: a message that loses its words is worse than one that repeats
    some of the history.
    """
    original = (body or "").strip()
    lines = original.splitlines()
    cut = len(lines)
    for index, line in enumerate(lines):
        if any(p.match(line) for p in _QUOTE_HEADERS):
            cut = index
            break
        # The wrapped form: "On <date>, <name> <" then "<address>> wrote:".
        # Joined ONLY when the next line is the tail of a header rather than a
        # header of its own, so "On second thought, yes." above a real header
        # is kept as the candidate's words.
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if (
            following.rstrip().endswith("wrote:")
            and not any(p.match(following) for p in _QUOTE_HEADERS)
            and _QUOTE_HEADERS[0].match(f"{line} {following}")
        ):
            cut = index
            break
    # A trailing run of quoted lines, blank lines between them included.
    tail = cut
    while tail > 0 and (
        lines[tail - 1].lstrip().startswith(">") or not lines[tail - 1].strip()
    ):
        tail -= 1
    if tail < cut and any(line.lstrip().startswith(">") for line in lines[tail:cut]):
        cut = tail
    stripped = "\n".join(lines[:cut]).strip()
    return stripped or original
