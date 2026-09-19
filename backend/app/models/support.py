"""In-product customer support: threads and messages (migration 0093).

WHAT THIS REPLACED, AND WHY IT IS A TABLE RATHER THAN A VENDOR
---------------------------------------------------------------
A third-party customer-success sync, deleted on 2026-09-10 by owner decision
(named in claude.md's 2026-09-10 section; deliberately not named here, so the
removal sweep can stay absolute). That integration projected customers outward
and spent its whole design budget on a closed allowlist stopping candidate data
going with them. Keeping the conversation INSIDE the product removes the
projection entirely: there is no payload to widen, no vendor credential, and no
second place a customer's history lives.

THE CANDIDATE BOUNDARY SURVIVED THE VENDOR
--------------------------------------------
A support thread is about the CUSTOMER: their account, their billing, their
staff, a screen that will not load. It is never about a candidate. `body` is
free text a human types, so nothing here can validate that, and pretending
otherwise would be worse than saying it plainly. What IS enforced is that no
automated path constructs a message from candidate material:
`tests/test_support_candidate_boundary.py` sweeps for it, and
`services/support.py` holds the one catalogue of system-authored copy.

WHY `author_side` IS STORED RATHER THAN DERIVED
-------------------------------------------------
It would be one join to answer "was this written by staff or by the customer"
from the author's role. It would also be WRONG the first time somebody's role
changes: a recruiter promoted to Super Admin would retroactively rewrite who
said what in every thread they ever touched. The side is a fact about the
moment the message was written, so it is written down then.

WHY `last_message_at` IS DENORMALISED
---------------------------------------
Both queues sort by it, and both are the first screen their reader opens. A
correlated subquery over `support_messages` on every list render is a cost paid
on the most frequent read to save a column write on the least frequent one.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Thread status ────────────────────────────────────────────────────────────
#
# NAMED FOR WHO OWES THE NEXT MOVE, which is the only thing a support queue is
# ever sorted by. The brief proposed `open | pending | resolved` and invited a
# better fit with the vocabulary already in this codebase; two things argued
# against `pending`. It is ambiguous about direction (both `open` and `pending`
# would read as "waiting on somebody"), and `pending` already has a settled and
# DIFFERENT meaning here: `email_senders.pending_verification` is waiting on a
# third party's proof, not on a reply.
#
# So the pair is `open` and `awaiting_customer`, which cannot be read backwards.

#: The ball is with ReadyPick. A new thread, or one the customer just replied
#: to. This is the queue ReadyPick staff work from.
THREAD_OPEN = "open"
#: ReadyPick has replied and is waiting on the customer. Deliberately not
#: called "pending": a reader has to be able to tell at a glance which side is
#: holding things up, and this queue exists so nobody chases the wrong one.
THREAD_AWAITING_CUSTOMER = "awaiting_customer"
#: Done. Not terminal: a customer replying reopens it, because a thread that
#: could not be reopened would force somebody with a follow-up question to
#: start a new one and lose the history that made it answerable.
THREAD_RESOLVED = "resolved"

THREAD_STATUSES: tuple[str, ...] = (
    THREAD_OPEN,
    THREAD_AWAITING_CUSTOMER,
    THREAD_RESOLVED,
)

# ── Message side ─────────────────────────────────────────────────────────────

#: Written by a member of the customer's own staff.
SIDE_CUSTOMER = "customer"
#: Written by ReadyPick staff.
SIDE_STAFF = "staff"

MESSAGE_SIDES: tuple[str, ...] = (SIDE_CUSTOMER, SIDE_STAFF)


class SupportThread(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "support_threads"
    __table_args__ = (
        # The customer's own list: their threads, newest activity first.
        Index("ix_support_threads_tenant", "tenant_id", "last_message_at"),
        # ReadyPick's queue: everything waiting on us, across every customer.
        Index("ix_support_threads_queue", "status", "last_message_at"),
    )

    #: THE RLS BOUNDARY. A thread belongs to exactly one customer, and unlike
    #: `candidate_updates` there is no cross-tenant subject here to complicate
    #: it: the people in this conversation are that tenant's own staff. So the
    #: policy is plain tenant equality in both directions, which is the
    #: strictest shape available and the right one when nothing needs more.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Who raised it. RESTRICT would strand a thread whose author left the
    #: company; SET NULL keeps the conversation and loses only the attribution,
    #: which is the right trade for a support record.
    opened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=THREAD_OPEN, server_default=THREAD_OPEN
    )
    #: The ReadyPick staff member who answered first. Claimed by replying
    #: rather than by a separate claim action: a claim flow nobody is obliged
    #: to use is a field that is empty on every real thread.
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SupportMessage(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "support_messages"
    __table_args__ = (
        Index("ix_support_messages_thread", "thread_id", "created_at"),
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Carried on the message as well as reachable through the thread, because
    #: the RLS policy has to answer from THIS row: a policy that joined to the
    #: parent would let a message be inserted against a thread the session
    #: cannot see, and be refused only on the next read.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: `customer` or `staff`, decided and stored AT WRITE TIME. See the module
    #: docstring: deriving it from the author's role at read time would let a
    #: later role change rewrite the history of a conversation.
    author_side: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
