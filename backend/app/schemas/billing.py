"""Billing and credit-ledger schemas: credits, packs, purchases, invoices.

Per-credit only. The monthly subscription schemas (plans, subscribe, the
subscription checkout payload) were deleted with the subscription model on
2026-09-29 (owner spec, section 23).

Balances cross this boundary in BOTH units on purpose. `balance_subunits` is
the exact integer the ledger holds and is what any arithmetic must use;
`balance_credits` is the rounded 2-decimal figure the page renders (spec §3.4).
Sending only the rounded value would make the frontend do credit arithmetic on
a float, which is the exact failure mode the sub-unit system exists to prevent.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CreditLotOut",
    "BillingOverviewOut",
    "CreditLedgerEntryOut",
    "CreditPackQuoteOut",
    "CreditPacksOut",
    "CreditPurchaseCreatedOut",
    "CreditPurchaseIn",
    "CreditPurchaseOut",
    "CreditPurchaseVerifyIn",
    "CreditSummaryOut",
    "ProviderBillingRowOut",
    "TransactionOut",
    "UsageBreakdownOut",
]


class UsageBreakdownOut(BaseModel):
    """This month's consumption, per event type, in both units."""

    completed_assessment: int = 0
    incomplete_assessment: int = 0
    no_show: int = 0
    old_profile_review: int = 0
    adjustment: int = 0


class CreditLotOut(BaseModel):
    """One batch of credits with its own expiry, for the billing page's
    validity table.

    `expires_at` of None means never expires, which is every credit granted
    before change request 25. The client must render that as a statement
    rather than as a blank cell: a missing date beside a balance reads as
    missing data, and this one is a promise.
    """

    lot_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime | None
    remaining_subunits: int
    remaining_credits: Decimal


class CreditSummaryOut(BaseModel):
    balance_subunits: int
    balance_credits: Decimal
    subunits_per_credit: int
    granted_subunits: int
    consumed_subunits: int
    #: Everything that happened before this calendar month's first day, which
    #: is what the billing page labels "Carried over from last month".
    #:
    #: NAME AND MEANING BOTH UNCHANGED by change request 25's three-month
    #: validity window. This field shipped two months earlier, is rendered on a
    #: page customers read today, and has nothing to do with expiry. Pointing
    #: it at "credits within their validity window" would have silently
    #: changed a number already on a screen. The validity story is told by the
    #: separate, distinctly named fields below.
    rollover_subunits: int
    rollover_credits: Decimal
    # ── Credit validity (change request 25) ──────────────────────────────────
    #: Removed by lots reaching their expiry, ever. Its OWN figure rather than
    #: part of `consumed_subunits`: the page labels that "Used to date", and
    #: folding expiry into it would bill the customer in the UI for
    #: assessments nobody ran.
    expired_subunits: int = 0
    expired_credits: Decimal = Decimal("0.00")
    #: The part of the balance that never expires: credits granted before
    #: change request 25, which keep the promise printed on their invoices.
    #: This is what lets the page say "X of your credits never expire" instead
    #: of flipping a sentence that is still true for many customers.
    non_expiring_subunits: int = 0
    non_expiring_credits: Decimal = Decimal("0.00")
    #: The part expiring within `expiring_soon_days`, and the earliest date any
    #: live batch expires.
    expiring_soon_subunits: int = 0
    expiring_soon_credits: Decimal = Decimal("0.00")
    expiring_soon_days: int = 30
    next_expiry_at: datetime | None = None
    #: How long a NEW grant stays spendable, so the client can state the term
    #: without hardcoding the product's number.
    credit_validity_months: int = 3
    #: Every batch with credits still on it, oldest first.
    lots: list["CreditLotOut"] = []
    usage_this_month_subunits: UsageBreakdownOut
    in_deficit: bool
    #: Plain-language reason shown on the billing page when invitations are
    #: paused. None when there is nothing to explain.
    deficit_message: str | None = None
    # ── Zero-balance and low-balance alerts (spec §11) ───────────────────────
    # Three states, and they are deliberately three fields rather than one enum:
    # a client renders a blocking dialog for one and a dismissible banner for
    # another, and an enum would make every consumer re-derive which is which.
    #
    #: The pool reads zero or worse. Job creation and new assessments are
    #: BLOCKED, and the client should say so before the user tries rather than
    #: only after a 402.
    exhausted: bool = False
    #: Below the warning threshold but not yet exhausted. The customer is asked
    #: to acknowledge and top up so service continues without interruption.
    low_balance: bool = False
    #: 0.0 to 1.0 of the granted pool, for the meter beside the warning.
    balance_fraction: float = 0.0
    #: The fraction at which `low_balance` turns on, so the copy can name the
    #: threshold without hardcoding the product's number in the client.
    low_balance_threshold: float = 0.30
    # ── Two-tier absolute warnings (Master Directive Part 5 §4) ─────────────
    #: 0 none, 1 LOW (balance <= 20 credits), 2 CRITICAL (<= 10). Tier 2
    #: renders as a PERSISTENT banner with urgent top-up styling.
    warning_level: int = 0
    warning_1_threshold_credits: int = 20
    warning_2_threshold_credits: int = 10
    #: Estimate: remaining credits at one credit per completed assessment.
    estimated_assessments_remaining: int = 0
    average_credits_per_assessment: float = 1.0
    #: Plain-language copy for whichever alert is showing. Resolved server-side
    #: so the API, the on-screen dialog and the 402 refusal cannot describe the
    #: same situation three different ways.
    alert_message: str | None = None
    #: A permanent demonstration company. Every figure above is still real
    #: usage; only the BALANCE should be presented as unlimited, because a demo
    #: tenant that has run assessments sums to a negative ledger and the page is
    #: meant to read as fully paid. Invitations are never gated for these.
    unlimited: bool = False


class CreditLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    subunits_delta: int
    credits_delta: Decimal
    created_at: datetime
    job_candidate_link_id: uuid.UUID | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    razorpay_payment_id: str | None
    amount_inr: int
    status: str
    transaction_type: str
    created_at: datetime


class BillingOverviewOut(BaseModel):
    """The balance, the usage and the recent history /org/billing renders.

    One round trip rather than several: each call pays the same auth and RLS
    setup cost, and the page has no state in which it wants the balance
    without the usage.
    """

    credits: CreditSummaryOut
    razorpay_key_id: str | None
    recent_ledger: list[CreditLedgerEntryOut]
    transactions: list[TransactionOut]


# ── Credit-pack purchases (Master Directive Part 5) ──────────────────────────

class CreditPackQuoteOut(BaseModel):
    """One purchase option, priced for THIS tenant.

    Historical fee fields remain in the shape for existing invoices. New
    Starter top-up quotes always set them to zero and false.
    """

    slug: str
    #: Resolved server-side so the page, the invoice and an email cannot call
    #: one pack three things.
    label: str
    credits: int
    bonus_credits: int
    #: Completed assessments added to the shared employer pool.
    credits_total: int
    subtotal_inr: int
    setup_fee_inr: int
    setup_fee_waived: bool
    gst_inr: int
    total_inr: int
    #: The published Starter top-up is always available to paid employers.
    available: bool
    trial: bool
    #: Months the granted credits stay spendable, stated BEFORE payment.
    validity_months: int


class CreditPacksOut(BaseModel):
    packs: list[CreditPackQuoteOut]
    gst_rate_percent: int


class CreditPurchaseIn(BaseModel):
    """Exactly one of the two: a named pack, or a custom credit count."""

    pack_slug: str | None = Field(default=None, max_length=30)
    custom_credits: int | None = Field(default=None, ge=1, le=100_000)


class CreditPurchaseCreatedOut(BaseModel):
    """What the browser needs to open Razorpay Checkout for the Order, plus
    the stored breakdown so the confirmation screen shows the same figures
    the invoice will."""

    purchase_id: uuid.UUID
    razorpay_order_id: str
    razorpay_key_id: str
    total_inr: int
    credits: int
    bonus_credits: int
    subtotal_inr: int
    setup_fee_inr: int
    gst_inr: int


class CreditPurchaseVerifyIn(BaseModel):
    """The handler payload Razorpay Checkout returns for an ORDERS payment.

    Signed as ``order_id|payment_id`` (see services/razorpay.verify_order_signature).
    """

    razorpay_order_id: str = Field(min_length=1, max_length=100)
    razorpay_payment_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(min_length=1, max_length=200)


class CreditPurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pack_slug: str
    credits_purchased: int
    bonus_credits: int
    subtotal_inr: int
    setup_fee_inr: int
    gst_inr: int
    total_inr: int
    status: str
    invoice_number: str | None = None
    created_at: datetime
    paid_at: datetime | None = None


class ProviderBillingRowOut(BaseModel):
    """One customer's credit balance in the Provider Portal overview."""

    tenant_id: uuid.UUID
    customer_name: str
    balance_subunits: int
    balance_credits: Decimal
    in_deficit: bool
