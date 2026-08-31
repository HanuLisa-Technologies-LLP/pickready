"""Subscriptions and the credit ledger (killer-spec Parts 2 and 3).

Two deliberate departures from the spec's literal DDL, both forced by rules
this codebase already lives under:

1. The spec writes ``ALTER TABLE companies ADD COLUMN razorpay_customer_id...``.
   In ReadyPick a **customer IS a `tenants` row**, not a `companies` row
   (claude.md, Provider Portal rules): `companies` is the client-authored,
   candidate-facing page and does not exist until the client signs in, while
   `tenants` carries the customer identity from onboarding. A subscription
   hung off `companies` would therefore be unreachable for exactly the
   customers who have just paid and not yet signed in. It lives on `tenants`.

2. The spec's ``credit_ledger.related_application_id REFERENCES applications``
   maps to `job_candidate_links` here, which is this schema's application row.

Everything else is the spec verbatim, including the 60-sub-unit arithmetic.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Credit arithmetic (spec §3.1) ────────────────────────────────────────────
# Consumption is fractional: 1, 1/3, 1/15 and 1/20 of a credit. Floats would
# compound rounding error over thousands of transactions and eventually make a
# customer's balance indefensible in a dispute, so every quantity in this module
# is an INTEGER number of sub-units. LCM(1, 3, 15, 20) = 60, so one credit is
# 60 sub-units and all four rates divide it exactly.
SUBUNITS_PER_CREDIT = 60

EVENT_GRANT = "grant"
EVENT_COMPLETED = "completed_assessment"
EVENT_INCOMPLETE = "incomplete_assessment"
EVENT_NO_SHOW = "no_show"
EVENT_OLD_PROFILE_REVIEW = "old_profile_review"
EVENT_ADJUSTMENT = "adjustment"

LEDGER_EVENT_TYPES: tuple[str, ...] = (
    EVENT_GRANT,
    EVENT_COMPLETED,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    EVENT_ADJUSTMENT,
)

#: Sub-units consumed per billable event for a NON-STEM job. Positive numbers;
#: the ledger stores the negated value as `subunits_delta`.
CONSUMPTION_SUBUNITS: dict[str, int] = {
    EVENT_COMPLETED: SUBUNITS_PER_CREDIT,            # 60/1  — 1 credit
    EVENT_INCOMPLETE: SUBUNITS_PER_CREDIT // 3,      # 60/3  — 3 per credit
    EVENT_NO_SHOW: SUBUNITS_PER_CREDIT // 15,        # 60/15 — 15 per credit
    EVENT_OLD_PROFILE_REVIEW: SUBUNITS_PER_CREDIT // 20,  # 60/20 — 20 per credit
}

ROLE_STEM = "STEM"
ROLE_NON_STEM = "NON_STEM"

#: Master Directive Part 5 §2.1 — STEM rates. A STEM report is 1.5 credits
#: (90 sub-units) and a STEM partial is 0.50 credits (30 sub-units): the same
#: one-third-of-the-full-rate rule the non-STEM partial already follows, on
#: the STEM base. No-shows and old-profile reviews are FLAT — the §2.1 table
#: prices "Unfilled / No candidate response" identically for either type,
#: because no AI assessment depth was ever spent on them.
STEM_CONSUMPTION_SUBUNITS: dict[str, int] = {
    EVENT_COMPLETED: SUBUNITS_PER_CREDIT * 3 // 2,   # 90 — 1.5 credits
    EVENT_INCOMPLETE: SUBUNITS_PER_CREDIT // 2,      # 30 — 0.50 credits
    EVENT_NO_SHOW: SUBUNITS_PER_CREDIT // 15,        # flat
    EVENT_OLD_PROFILE_REVIEW: SUBUNITS_PER_CREDIT // 20,  # flat
}


def consumption_subunits(event_type: str, role_classification: str | None) -> int | None:
    """Sub-unit cost of one billable event at the job's classified rate.

    Part 5 Rule 9: the classification comes from the Job record and NOWHERE
    else, and a NULL/unknown value bills at the non-STEM rate (the
    commercially safe direction) rather than failing the deduction.
    Returns None for a non-billable event type, mirroring the dict lookup the
    callers previously did.
    """
    table = (
        STEM_CONSUMPTION_SUBUNITS
        if role_classification == ROLE_STEM
        else CONSUMPTION_SUBUNITS
    )
    return table.get(event_type)

SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_PAST_DUE = "past_due"
SUBSCRIPTION_CANCELLED = "cancelled"
SUBSCRIPTION_HALTED = "halted"
SUBSCRIPTION_STATUSES: tuple[str, ...] = (
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_PAST_DUE,
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_HALTED,
)


class PricingPlan(Base, UUIDPKMixin, CreatedAtMixin):
    """One self-serve tier. Razorpay's own plan id is DATA, never a constant in
    code, so a repriced plan is a row edit rather than a redeploy."""

    __tablename__ = "pricing_plans"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_pricing_plans_slug"),
        CheckConstraint("price_inr >= 0", name="ck_pricing_plans_price_non_negative"),
        CheckConstraint(
            "applications_per_month > 0", name="ck_pricing_plans_applications_positive"
        ),
    )

    slug: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    applications_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    price_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_per_application_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    razorpay_plan_id: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    @property
    def monthly_subunits(self) -> int:
        return self.applications_per_month * SUBUNITS_PER_CREDIT


class BillingTransaction(Base, UUIDPKMixin, CreatedAtMixin):
    """One Razorpay money event. `razorpay_payment_id` is UNIQUE so a webhook
    Razorpay delivers twice (which it will — at-least-once delivery) cannot
    double-grant credits."""

    __tablename__ = "billing_transactions"
    __table_args__ = (
        UniqueConstraint(
            "razorpay_payment_id", name="uq_billing_transactions_payment"
        ),
        Index("ix_billing_transactions_tenant_at", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(100))
    razorpay_subscription_id: Mapped[str | None] = mapped_column(String(100))
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pricing_plans.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class CreditLedgerEntry(Base, UUIDPKMixin, CreatedAtMixin):
    """Append-only credit history. The balance is SUM(subunits_delta), never a
    mutable column: a disputed invoice needs the transactions, not a number.

    `idempotency_key` is UNIQUE and is what makes every writer safe to retry —
    one completed assessment charges once no matter how many times the Celery
    task is redelivered.
    """

    __tablename__ = "credit_ledger"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_credit_ledger_idempotency"),
        Index("ix_credit_ledger_tenant_at", "tenant_id", "created_at"),
        Index("ix_credit_ledger_tenant_event", "tenant_id", "event_type"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subunits_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    # The spec calls this `related_application_id`; an application in this
    # schema is a `job_candidate_links` row.
    job_candidate_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="SET NULL")
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pricing_plans.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)


class OldProfileReview(Base, UUIDPKMixin, CreatedAtMixin):
    """First open of an Old Profile by a recruiter, per (tenant, link, user).

    The charge is per REVIEW, not per click: the UNIQUE constraint means the
    same recruiter reopening the same profile twenty times is billed once, which
    is what "20 profiles use 1 credit" has to mean for the number to be fair.
    """

    __tablename__ = "old_profile_reviews"
    __table_args__ = (
        UniqueConstraint(
            "job_candidate_link_id",
            "reviewer_user_id",
            name="uq_old_profile_review_link_reviewer",
        ),
        Index("ix_old_profile_reviews_tenant", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class WebhookEvent(Base, UUIDPKMixin, CreatedAtMixin):
    """Razorpay delivers at least once. This is the dedupe table: the handler
    inserts the provider's event id first and does nothing if it already
    exists, so a replayed `subscription.charged` cannot grant a second month."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_id"),
    )

    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="razorpay")
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict | None] = mapped_column(JSONB)


# ── Credit-pack purchases (Master Directive Part 5) ──────────────────────────

#: Headline price. NEVER discounted: volume levels add FREE credits instead
#: (Rule 3), so the invoice always shows purchased credits at this rate.
PRICE_PER_CREDIT_INR = 600
#: 18% GST, collected on every transaction and remitted (Rule 7). Stored per
#: purchase in rupees, computed from the subtotal at purchase time.
GST_RATE_PERCENT = 18
#: One-time onboarding fee, waived for the first 15 client accounts (§5.1).
SETUP_FEE_INR = 5000
SETUP_FEE_WAIVER_LIMIT = 15
#: The one purchase allowed below the standard minimum (Rule 1).
TRIAL_CREDITS = 20
#: Every purchase after the first (Rule 2). Custom/Enterprise is negotiated
#: and does not go through the self-serve packs.
MIN_PURCHASE_CREDITS = 50

#: slug -> (credits purchased, bonus credits). §3.2's table verbatim.
CREDIT_PACKS: dict[str, tuple[int, int]] = {
    "trial_20": (TRIAL_CREDITS, 0),
    "standard_50": (50, 0),
    "volume_100": (100, 5),
    "volume_200": (200, 15),
}

PURCHASE_CREATED = "created"
PURCHASE_PAID = "paid"
PURCHASE_FAILED = "failed"


class CreditPurchase(Base, UUIDPKMixin, CreatedAtMixin):
    """One credit-pack purchase, from Razorpay Order to GST invoice.

    The row is created BEFORE payment (status `created`) so the webhook and
    the browser's verify call race safely: whichever arrives first flips the
    status to `paid` inside the same statement that checks it, and the credit
    grant's idempotency key is derived from the order id, so the loser of the
    race grants nothing twice. `razorpay_order_id` is UNIQUE for the same
    reason `billing_transactions.razorpay_payment_id` is.

    Every rupee figure is stored EXCLUSIVE of GST except `total_inr`, and the
    GST amount is its own column: the §5.2 invoice shows each line separately
    and a stored breakdown cannot drift from a recomputed one.
    """

    __tablename__ = "credit_purchases"
    __table_args__ = (
        UniqueConstraint("razorpay_order_id", name="uq_credit_purchases_order"),
        Index("ix_credit_purchases_tenant_at", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    pack_slug: Mapped[str] = mapped_column(String(30), nullable=False)
    credits_purchased: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: credits_purchased x PRICE_PER_CREDIT_INR, excl. GST.
    subtotal_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 0 when already paid or waived; SETUP_FEE_INR when charged here (§5.1).
    setup_fee_inr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    setup_fee_waived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    gst_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    total_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PURCHASE_CREATED
    )
    razorpay_order_id: Mapped[str | None] = mapped_column(String(100))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(100))
    #: Sequential GST invoice number, assigned when the purchase settles.
    invoice_number: Mapped[str | None] = mapped_column(String(40))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
