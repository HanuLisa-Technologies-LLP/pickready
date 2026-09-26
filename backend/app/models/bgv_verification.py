"""Recruiter-driven background verification: one record per employer, per tenant.

WHY THIS IS TENANT DATA WHEN THE EMPLOYMENT HISTORY IS NOT
------------------------------------------------------------
`candidate_employments` is the candidate's claim and travels with the person.
A VERIFICATION is a hiring decision one customer made after reading one reply,
and it must not travel: if tenant A marked an employer verified, tenant B has
done no diligence at all, and inheriting the result would let one customer's
judgement silently clear another customer's hire. So these rows carry a
`tenant_id`, sit behind the same RLS policy as every other tenant table, and
are UNIQUE on (tenant, employment).

NO MODEL DECIDES ANY OF THIS
-----------------------------
The agent writes the email. The recruiter sends it, reads the reply and marks
the outcome. `decided_by` is set on any decided row and is ON DELETE RESTRICT,
alone with `review_dispositions.decided_by` among user references in this
schema, and for the identical reason: a verification whose person was erased
asserts that a human decided while being unable to say who, which is
indistinguishable from the pipeline having written it.

`services/bgv_workflow` derives the CANDIDATE-level status from these rows by
arithmetic. It is never stored, for the reason `profile_age` and
`posting_status` are never stored: a denormalised copy of a derived fact is a
copy that can disagree with the fact, and this one decides whether somebody
gets an offer.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# The employer-level states. Four, and each one is a different fact about the
# world rather than a stage in a progress bar. NOT_STARTED and PENDING are
# deliberately distinct: "nobody has asked this employer" and "we asked and are
# waiting" produce the same empty screen and completely different next actions.

VERIFICATION_NOT_STARTED = "not_started"
VERIFICATION_PENDING = "pending"
VERIFICATION_VERIFIED = "verified"
VERIFICATION_NOT_VERIFIED = "not_verified"

VERIFICATION_STATUSES: frozenset[str] = frozenset(
    {
        VERIFICATION_NOT_STARTED,
        VERIFICATION_PENDING,
        VERIFICATION_VERIFIED,
        VERIFICATION_NOT_VERIFIED,
    }
)

#: The two a recruiter may write. `pending` is reached by SENDING and
#: `not_started` is where a row is born, so neither is something a person
#: chooses: a status somebody can set by hand is a status that stops meaning
#: what it says.
RECRUITER_DECIDABLE: frozenset[str] = frozenset(
    {VERIFICATION_VERIFIED, VERIFICATION_NOT_VERIFIED}
)

#: A decided row. Both are terminal for the offer gate, and only one of them
#: opens it.
DECIDED_STATUSES: frozenset[str] = RECRUITER_DECIDABLE


# ── Delivery, which is a DIFFERENT QUESTION from status ──────────────────────
#
# `status` answers "what did the employer say". These answer "did the message
# reach them at all", and conflating the two is what let a bounced request sit
# at `pending` looking identical to one an HR team had simply not opened. The
# day-3 chase then told the candidate their employer had not responded to a
# letter nobody ever received, which sends them to argue with an innocent
# person about an email that does not exist.

DELIVERY_NOT_SENT = "not_sent"
DELIVERY_SENT = "sent"
DELIVERY_DELIVERED = "delivered"
DELIVERY_BOUNCED = "bounced"

DELIVERY_STATES: frozenset[str] = frozenset(
    {DELIVERY_NOT_SENT, DELIVERY_SENT, DELIVERY_DELIVERED, DELIVERY_BOUNCED}
)


# The candidate-level states, DERIVED from the rows above and never stored.

CANDIDATE_BGV_NOT_REQUIRED = "not_required"
CANDIDATE_BGV_NOT_STARTED = "not_started"
CANDIDATE_BGV_PENDING = "pending"
CANDIDATE_BGV_VERIFIED = "verified"
CANDIDATE_BGV_NOT_VERIFIED = "not_verified"

CANDIDATE_BGV_STATUSES: frozenset[str] = frozenset(
    {
        CANDIDATE_BGV_NOT_REQUIRED,
        CANDIDATE_BGV_NOT_STARTED,
        CANDIDATE_BGV_PENDING,
        CANDIDATE_BGV_VERIFIED,
        CANDIDATE_BGV_NOT_VERIFIED,
    }
)


class BGVVerification(Base, UUIDPKMixin, CreatedAtMixin):
    """One tenant's verification of one of a candidate's previous employers."""

    __tablename__ = "bgv_verifications"
    __table_args__ = (
        # One verification per employer per customer. Without this, two
        # recruiters opening the same candidate on the same afternoon produce
        # two records for one employer and the completeness arithmetic counts
        # the same job twice.
        UniqueConstraint(
            "tenant_id", "candidate_employment_id", name="uq_bgv_verification_employer"
        ),
        Index("ix_bgv_verifications_tenant_candidate", "tenant_id", "candidate_id"),
        Index("ix_bgv_verifications_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Denormalised from the employment row so a candidate's whole set reads
    #: with one index hit. The employment row is immutable, so it cannot drift.
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_employment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_employments.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: PROVENANCE, not scope. Which application prompted this verification,
    #: recorded so a reader can see why it exists. The verification itself is
    #: per (tenant, employer) rather than per job: a customer who verified an
    #: employer last month has verified it, and asking the same HR contact
    #: again because the candidate applied to a second role is a way to get
    #: ignored.
    initiated_from_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_candidate_links.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VERIFICATION_NOT_STARTED
    )
    #: The BGV conversation this employer's correspondence lives in. One per
    #: verification, which is what makes seven employers seven independently
    #: trackable threads rather than one inbox nobody can follow.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
    )
    first_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: ON DELETE RESTRICT. See the module docstring: a decision has to be able
    #: to say who made it, or it is not a decision.
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why the recruiter decided what they decided, in their own words. Free
    #: text on purpose: a NOT_VERIFIED that cannot explain itself is a dead end
    #: for whoever reads the candidate next.
    decision_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── The employer checkbox form (migration 0102, vivekium feature 4) ─────
    # The token is the single-use credential in the employer's mailbox;
    # expiry is DERIVED from `form_token_issued_at` by `bgv_form.expires_at`.
    # `form_submitted_at` is the single-use latch, and `form_answers_json`
    # holds exactly what the employer ticked. `reminder_sent_at` stamps the
    # day-3 chase so it goes out once.
    form_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    form_token_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    form_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    form_answers_json: Mapped[dict | None] = mapped_column(JSONB)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # ── Delivery (migration 0114) ───────────────────────────────────────────
    #: Where the outbound request got to, as the provider reported it. Written
    #: by the SES event webhook through the `email_log.bgv_verification_id`
    #: binding, never inferred from a recipient address.
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DELIVERY_NOT_SENT,
        server_default=DELIVERY_NOT_SENT,
    )
    #: WHEN THE THREE-DAY CLOCK IS ALLOWED TO START. The brief's window is
    #: three days for the employer to act, and an employer cannot act on a
    #: message still in the provider's retry queue. Under a transport that
    #: reports no delivery events this stays NULL for ever, which is why
    #: `bgv_delivery.clock_start` falls back to the send stamp EXPLICITLY
    #: rather than leaving a credential that never expires.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The provider's own short reason, for the recruiter's screen. Bounded by
    #: `_failure_reason`, which reads SES's structured fields rather than
    #: dumping an event document that carries headers and a recipient list.
    delivery_detail: Mapped[str | None] = mapped_column(Text)
