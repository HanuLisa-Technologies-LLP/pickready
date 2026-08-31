"""Billing and credit-ledger schemas (killer-spec Parts 2 and 3).

Balances cross this boundary in BOTH units on purpose. `balance_subunits` is
the exact integer the ledger holds and is what any arithmetic must use;
`balance_credits` is the rounded 2-decimal figure the page renders (spec §3.4).
Sending only the rounded value would make the frontend do credit arithmetic on
a float, which is the exact failure mode the sub-unit system exists to prevent.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BillingConfigOut",
    "BillingOverviewOut",
    "CheckoutVerifyIn",
    "CreditLedgerEntryOut",
    "CreditSummaryOut",
    "PlanOut",
    "ProviderBillingRowOut",
    "SubscribeIn",
    "SubscribeOut",
    "SubscriptionOut",
    "TransactionOut",
    "UsageBreakdownOut",
]


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    applications_per_month: int
    price_inr: int
    rate_per_application_inr: int
    is_active: bool
    #: True once a Razorpay Plan exists for it. A Subscribe button on a plan
    #: without one would open Checkout and immediately fail, so the UI disables
    #: it instead of pretending.
    checkout_ready: bool


class BillingConfigOut(BaseModel):
    """What the browser needs to open Razorpay Checkout.

    The Key ID is public by design — Razorpay's own client library takes it in
    the page. It is served from here rather than inlined as a NEXT_PUBLIC_ build
    variable so there is exactly one source of truth and the frontend container
    never needs the .env at all.
    """

    razorpay_key_id: str | None
    configured: bool
    currency: Literal["INR"] = "INR"
    plans: list[PlanOut]


class SubscribeIn(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=50)


class SubscribeOut(BaseModel):
    subscription_id: str
    razorpay_key_id: str
    plan: PlanOut
    #: Razorpay's hosted page, offered as a fallback when the embedded widget
    #: cannot open (blocked script, unsupported browser).
    short_url: str | None = None


class CheckoutVerifyIn(BaseModel):
    """The handler payload Razorpay Checkout hands back in the browser.

    Signed as ``payment_id|subscription_id`` for subscriptions — the reverse of
    the Orders flow.
    """

    razorpay_payment_id: str = Field(min_length=1, max_length=100)
    razorpay_subscription_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(min_length=1, max_length=200)


class SubscriptionOut(BaseModel):
    plan: PlanOut | None
    status: str | None
    razorpay_subscription_id: str | None
    current_end: datetime | None


class UsageBreakdownOut(BaseModel):
    """This month's consumption, per event type, in both units."""

    completed_assessment: int = 0
    incomplete_assessment: int = 0
    no_show: int = 0
    old_profile_review: int = 0
    adjustment: int = 0


class CreditSummaryOut(BaseModel):
    balance_subunits: int
    balance_credits: Decimal
    # Current plan-rate equivalent. Credits remain the ledger's unit; INR is
    # shown beside them so the commercial value is never hidden.
    balance_inr: Decimal | None = None
    subunits_per_credit: int
    granted_subunits: int
    consumed_subunits: int
    rollover_subunits: int
    rollover_credits: Decimal
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
    #: §4.2's estimate: balance ÷ 30-day average credits per assessment
    #: (platform default 1.2), rounded down.
    estimated_assessments_remaining: int = 0
    average_credits_per_assessment: float = 1.2
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
    """Everything /org/billing renders in one call.

    One round trip rather than four: the page has no state in which it wants
    the plan without the balance, and four parallel calls each pay the same
    auth + RLS setup cost.
    """

    subscription: SubscriptionOut
    credits: CreditSummaryOut
    plans: list[PlanOut]
    razorpay_key_id: str | None
    recent_ledger: list[CreditLedgerEntryOut]
    transactions: list[TransactionOut]


class ProviderBillingRowOut(BaseModel):
    """One customer's billing state in the Provider Portal overview."""

    tenant_id: uuid.UUID
    customer_name: str
    plan_name: str | None
    subscription_status: str | None
    balance_subunits: int
    balance_credits: Decimal
    balance_inr: Decimal | None = None
    in_deficit: bool
    current_end: datetime | None
