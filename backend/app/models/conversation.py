"""Native ReadyPick conversations: recruiter to candidate, recruiter to an employer.

WHY THIS IS NOT `support_threads`
----------------------------------
The support tables carry a HARD structural rule: no candidate identifier,
score, grade or evaluation detail may ever reach `support_messages`, enforced
by import graph and swept by `test_support_candidate_boundary`. A support
thread is about the CUSTOMER's relationship with ReadyPick. These conversations
are about a CANDIDATE by definition, so reusing that table would not be reuse,
it would be deleting the guarantee that makes it safe.

Same shape, different subject. Everything the support build learned is carried
across deliberately:

  * every row holds its OWN `tenant_id`, including messages, because an RLS
    policy that joins to its parent evaluates against rows the session cannot
    see and therefore hides nothing;
  * the author's PARTY is denormalised at write time, so a later role change
    cannot rewrite who said what;
  * status is derived from what happened, not from a flag somebody remembers
    to set.

THE EMAIL BRIDGE IS THE POINT, NOT A FEATURE ON THE SIDE
----------------------------------------------------------
An employer's HR contact has no ReadyPick login and never will. So a message to
them is BOTH a row here and an email through SES, and their reply is BOTH an
email and a row here. One record, two transports; never two records for one
thing. `channel` says how a message travelled and `email_log_id` points at the
delivery record that already exists, so the conversation and the mail log can
never disagree about whether something was sent.

Correlation is by TOKEN, never by subject line. `thread_token` is minted per
conversation and travels in the reply-to address and in the References header,
which is what makes a reply land on the right employer of the right candidate
even when seven verifications are open at once and every one of them says
"Employment verification" in the subject.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# What a conversation is ABOUT. The kind decides who may be in it and what the
# email bridge does, so it is a column rather than something inferred from
# which foreign key happens to be set.
KIND_CANDIDATE = "candidate"
KIND_BGV = "bgv"

CONVERSATION_KINDS: frozenset[str] = frozenset({KIND_CANDIDATE, KIND_BGV})

CONVERSATION_OPEN = "open"
CONVERSATION_CLOSED = "closed"

CONVERSATION_STATUSES: frozenset[str] = frozenset(
    {CONVERSATION_OPEN, CONVERSATION_CLOSED}
)

# WHO is speaking. Denormalised onto every message at write time.
PARTY_RECRUITER = "recruiter"
PARTY_CANDIDATE = "candidate"
PARTY_EMPLOYER_HR = "employer_hr"
#: Not a person. A delivery failure, a bounce, or the system recording that an
#: email could not be sent, written into the thread so the recruiter reads it
#: where they are already looking rather than in a log they are not.
PARTY_SYSTEM = "system"

PARTIES: frozenset[str] = frozenset(
    {PARTY_RECRUITER, PARTY_CANDIDATE, PARTY_EMPLOYER_HR, PARTY_SYSTEM}
)

#: The parties that have no ReadyPick session and are reached by email only.
EXTERNAL_PARTIES: frozenset[str] = frozenset({PARTY_EMPLOYER_HR})

# How a message travelled.
CHANNEL_CHAT = "chat"
CHANNEL_EMAIL = "email"

CHANNELS: frozenset[str] = frozenset({CHANNEL_CHAT, CHANNEL_EMAIL})

# Delivery state for the email leg. A chat-only message is `delivered` the
# moment it is persisted, because there is nothing else for it to wait for.
DELIVERY_PENDING = "pending"
DELIVERY_SENT = "sent"
DELIVERY_DELIVERED = "delivered"
DELIVERY_FAILED = "failed"
DELIVERY_BOUNCED = "bounced"

DELIVERY_STATES: frozenset[str] = frozenset(
    {
        DELIVERY_PENDING,
        DELIVERY_SENT,
        DELIVERY_DELIVERED,
        DELIVERY_FAILED,
        DELIVERY_BOUNCED,
    }
)

#: Ceiling on one message body. Generous, because a pasted HR reply can be
#: long, and bounded, because an unbounded text column reached by an inbound
#: webhook is a denial-of-service surface.
MAX_BODY_CHARS = 20_000


class Conversation(Base, UUIDPKMixin, CreatedAtMixin):
    """One thread. Candidate-facing, or one employer's BGV correspondence."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("thread_token", name="uq_conversations_thread_token"),
        # One BGV conversation per verification. Seven employers are seven
        # threads; a second row for the same employer would split the
        # correspondence in half with no way to tell which half is current.
        UniqueConstraint(
            "bgv_verification_id", name="uq_conversations_bgv_verification"
        ),
        Index("ix_conversations_tenant_recent", "tenant_id", "last_message_at"),
        Index("ix_conversations_candidate", "tenant_id", "candidate_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Shown as the thread title. Written once from real context rather than
    #: typed, so a list of seven BGV threads can be told apart at a glance.
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE")
    )
    bgv_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bgv_verifications.id", ondelete="CASCADE")
    )
    #: Which job this correspondence belongs to, when there is one. Provenance.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CONVERSATION_OPEN
    )
    #: The correlation secret. Unguessable, unique, and the ONLY thing that
    #: routes an inbound reply: subject lines are edited, forwarded and
    #: translated, and matching on one would eventually file an employer's
    #: answer against the wrong candidate.
    thread_token: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Denormalised so a conversation list sorts without touching messages.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ConversationParticipant(Base, UUIDPKMixin, CreatedAtMixin):
    """Who is in a conversation, and how much of it they have read.

    An internal participant has a `user_id`; an external one has an email and
    no login. Exactly one of the two is set, enforced by a database CHECK,
    because a participant that is neither is a row nothing can deliver to and a
    participant that is both is two identities wearing one name.
    """

    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_participant_user"),
        Index("ix_participants_conversation", "conversation_id"),
        Index("ix_participants_user_unread", "user_id", "last_read_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    party: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    external_email: Mapped[str | None] = mapped_column(String(320))
    external_name: Mapped[str | None] = mapped_column(String(200))
    #: NULL means "has never opened it", which is not the same as "read
    #: nothing": the unread count treats NULL as the beginning of time, so a
    #: participant added to an old thread sees its history as unread rather
    #: than silently caught up.
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(Base, UUIDPKMixin, CreatedAtMixin):
    """One message. The same row whether it travelled as chat or as email."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        # IDEMPOTENCY. A retried send, a double-clicked button and a websocket
        # replay all carry the same client token, and the second one loses.
        UniqueConstraint(
            "conversation_id", "client_token", name="uq_message_client_token"
        ),
        # The same property for the inbound direction: SES and SNS both deliver
        # at least once, so the provider's message id is the idempotency key
        # for a reply.
        UniqueConstraint(
            "tenant_id", "inbound_message_id", name="uq_message_inbound_id"
        ),
        Index("ix_messages_conversation", "conversation_id", "created_at", "id"),
        Index("ix_messages_tenant_created", "tenant_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Its own tenant, not the parent's. See the module docstring.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Denormalised at write time and never recomputed: a role change must not
    #: rewrite the history of who said what.
    author_party: Mapped[str] = mapped_column(String(20), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: For an external author, the address the message actually came from,
    #: recorded verbatim. Never resolved to a participant row on the way in: a
    #: reply from a colleague of the HR contact is still evidence, and
    #: discarding it because the address did not match would lose it.
    author_email: Mapped[str | None] = mapped_column(String(320))
    author_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DELIVERY_DELIVERED
    )
    #: Why a delivery failed, in the provider's words, so the thread can say
    #: what went wrong rather than that something did.
    delivery_detail: Mapped[str | None] = mapped_column(Text)
    #: Supplied by the client for an outbound message; NULL for inbound.
    client_token: Mapped[str | None] = mapped_column(String(64))
    #: The provider's id for an inbound message; NULL for outbound.
    inbound_message_id: Mapped[str | None] = mapped_column(String(400))
    #: The RFC 5322 Message-ID this message went out with, so a reply quoting
    #: it in In-Reply-To can be threaded even when the token is stripped.
    email_message_id: Mapped[str | None] = mapped_column(String(400))
    #: The delivery record in `email_log`, so the conversation and the mail log
    #: can never disagree about whether something was sent.
    email_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_log.id", ondelete="SET NULL")
    )


class ConversationAttachment(Base, UUIDPKMixin, CreatedAtMixin):
    """A file on a message, stored where every other ReadyPick file is stored.

    The object key never crosses an API boundary, the same rule the assessment
    video and the PRISM PDF already follow: a client receives an id and asks
    for a short-lived URL, so a bucket name is never something a browser knows.
    """

    __tablename__ = "conversation_attachments"
    __table_args__ = (Index("ix_attachments_message", "message_id"),)

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversation_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
