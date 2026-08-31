"""Subscriptions, checkout and the credit ledger API (killer-spec Parts 2 and 3).

Route shape:

    GET  /billing/config              public — Key ID + the price list
    GET  /billing/overview            customer — plan, balance, usage, history
    GET  /billing/ledger              customer — paginated statement
    POST /billing/subscribe           customer — create a Razorpay Subscription
    POST /billing/checkout/verify     customer — verify the Checkout handler
    POST /billing/change-plan         customer — upgrade / downgrade
    POST /billing/cancel              customer — cancel at cycle end
    POST /billing/webhook/razorpay    Razorpay — signature-verified, no session

Credits are granted by ONE code path (`_grant_for_payment`) shared by the
webhook and the checkout-verify handler, keyed on the Razorpay payment id. Both
can therefore run for the same payment — which they routinely do, since
Checkout returns before the webhook lands — and the customer is granted exactly
one month either way.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_public_db,
    get_superadmin_db,
    get_tenant_db,
    require_capability,
)
from app.core import cache
from app.core.config import get_settings
from app.core.db import get_session
from app.models.billing import (
    GST_RATE_PERCENT,
    MIN_PURCHASE_CREDITS,
    PRICE_PER_CREDIT_INR,
    PURCHASE_CREATED,
    PURCHASE_FAILED,
    PURCHASE_PAID,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_HALTED,
    SUBSCRIPTION_PAST_DUE,
    SUBUNITS_PER_CREDIT,
    BillingTransaction,
    CreditLedgerEntry,
    CreditPurchase,
    PricingPlan,
    WebhookEvent,
)
from app.models.tenant import Tenant
from app.schemas.billing import (
    BillingConfigOut,
    BillingOverviewOut,
    CheckoutVerifyIn,
    CreditLedgerEntryOut,
    CreditPackQuoteOut,
    CreditPacksOut,
    CreditPurchaseCreatedOut,
    CreditPurchaseIn,
    CreditPurchaseOut,
    CreditPurchaseVerifyIn,
    CreditSummaryOut,
    PlanOut,
    ProviderBillingRowOut,
    SubscribeIn,
    SubscribeOut,
    SubscriptionOut,
    TransactionOut,
    UsageBreakdownOut,
)
from app.services import capabilities as caps
from app.services import credit_packs, credits, razorpay
from app.services.audit import audit

log = logging.getLogger(__name__)

router = APIRouter()

# Both credit-granting paths derive their idempotency key from this, so a
# payment granted by checkout-verify cannot be granted again by the webhook.
def _payment_key(payment_id: str) -> str:
    return f"razorpay:payment:{payment_id}"


def _plan_out(plan: PricingPlan) -> PlanOut:
    """Serialise one plan.

    `checkout_ready` asks "can a Subscribe button work right now?", which is a
    question about the SERVER's credentials, not about whether this plan already
    has a Razorpay id. Razorpay Plans are minted lazily on the first subscribe
    (`_ensure_razorpay_plan`), so keying this off `razorpay_plan_id` disabled
    every button on a fresh install and the only thing that could have populated
    that column was the button it had just disabled.
    """
    return PlanOut(
        id=plan.id,
        slug=plan.slug,
        name=plan.name,
        applications_per_month=plan.applications_per_month,
        price_inr=plan.price_inr,
        rate_per_application_inr=plan.rate_per_application_inr,
        is_active=plan.is_active,
        checkout_ready=plan.is_active and razorpay.config().configured,
    )


async def _active_plans(session: AsyncSession) -> list[PricingPlan]:
    return list(
        (
            await session.execute(
                select(PricingPlan)
                .where(PricingPlan.is_active.is_(True))
                .order_by(PricingPlan.sort_order, PricingPlan.price_inr)
            )
        ).scalars().all()
    )


async def _plan_by_slug(session: AsyncSession, slug: str) -> PricingPlan:
    plan = (
        await session.execute(
            select(PricingPlan).where(
                PricingPlan.slug == slug, PricingPlan.is_active.is_(True)
            )
        )
    ).scalars().first()
    if plan is None:
        raise HTTPException(status_code=404, detail="No such plan")
    return plan


# ── Public: config + price list ──────────────────────────────────────────────

#: The price list changes on a migration, not on a click, and every landing
#: page view reads it. Cached for an hour. `checkout_ready` is deliberately
#: recomputed OUTSIDE the cache below: it depends on the server's credentials
#: rather than on the row, and an hour of a stale "payments are off" is exactly
#: the wrong thing to cache.
_PLANS_CACHE_KEY = cache.key("billing", "plans")


@router.get("/config", response_model=BillingConfigOut)
async def billing_config(session: AsyncSession = Depends(get_session)) -> BillingConfigOut:
    """Key ID and the price list. Public: the landing page renders this before
    anybody has signed in, and the Key ID is a public credential by design."""
    cfg = razorpay.config()

    async def _load() -> list[dict]:
        plans = await _active_plans(session)
        return [_plan_out(plan).model_dump(mode="json") for plan in plans]

    plans = await cache.get_or_set(
        _PLANS_CACHE_KEY, _load, ttl=cache.TTL_PRICING_PLANS
    )
    return BillingConfigOut(
        razorpay_key_id=cfg.key_id or None,
        configured=cfg.configured,
        plans=[
            # Recomputed per response, never served from the cache: adding the
            # keys to a running server must enable Subscribe immediately, not
            # up to an hour later.
            PlanOut.model_validate(
                {**plan, "checkout_ready": bool(plan["is_active"]) and cfg.configured}
            )
            for plan in plans
        ],
    )


# ── Customer: overview ───────────────────────────────────────────────────────

_DEFICIT_MESSAGE = (
    "You are over your credit limit. New assessment invitations are paused "
    "until your next billing date or you upgrade your plan."
)

#: Shown when the pool reads zero. Names BOTH blocked actions, because a
#: recruiter who reads "assessments are paused" and then cannot create a job
#: has been told half the truth and will report it as a second bug.
_EXHAUSTED_MESSAGE = (
    "Your credit pool is exhausted. New jobs cannot be created and no further "
    "candidates can be moved into assessment. Purchase a credit bundle to "
    "continue. A conversation already in progress will finish, and its report "
    "is written as soon as credits are available."
)

def _warning_message(
    level: int, balance: Decimal, estimate: int, stem_active: bool
) -> str | None:
    """The Master Directive Part 5 §4.1 alert copy, tier by tier, with the
    §4.2 estimate folded in. Composed server-side so the banner, the email and
    the 402 refusal cannot describe one situation three different ways."""
    if level <= 0:
        return None
    stem_note = (
        " Note: STEM roles consume 1.5 credits per report." if stem_active else ""
    )
    if level >= 2:
        return (
            f"Critical: Only {balance} credits remaining. Some assessments may "
            f"not complete. At current usage, this covers approximately "
            f"{estimate} more assessments.{stem_note} Top up immediately."
        )
    return (
        f"Credits running low. You have {balance} credits remaining. At current "
        f"usage, this covers approximately {estimate} more assessments."
        f"{stem_note} Top up now to keep your pipeline moving."
    )


async def _summary_out(session: AsyncSession, tenant_id: uuid.UUID) -> CreditSummaryOut:
    summary = await credits.summarize(session, tenant_id)
    average = await credits.average_credits_per_assessment(session, tenant_id)
    estimate = credits.estimated_assessments_remaining(
        summary.balance_subunits, average
    )
    stem_active = await credits.has_active_stem_jobs(session, tenant_id)
    rate = (
        await session.execute(
            select(PricingPlan.rate_per_application_inr)
            .join(Tenant, Tenant.current_plan_id == PricingPlan.id)
            .where(Tenant.id == tenant_id)
        )
    ).scalar_one_or_none()
    return CreditSummaryOut(
        balance_subunits=summary.balance_subunits,
        balance_credits=summary.balance_credits,
        balance_inr=(
            (summary.balance_credits * Decimal(rate)).quantize(Decimal("0.01"))
            if rate is not None
            else None
        ),
        subunits_per_credit=SUBUNITS_PER_CREDIT,
        granted_subunits=summary.granted_subunits,
        consumed_subunits=summary.consumed_subunits,
        rollover_subunits=summary.rollover_subunits,
        rollover_credits=credits.credits_from_subunits(summary.rollover_subunits),
        usage_this_month_subunits=UsageBreakdownOut(**summary.month_by_event),
        in_deficit=summary.in_deficit,
        deficit_message=_DEFICIT_MESSAGE if summary.in_deficit else None,
        exhausted=summary.exhausted,
        low_balance=summary.low_balance,
        balance_fraction=summary.balance_fraction,
        low_balance_threshold=credits.LOW_BALANCE_FRACTION,
        warning_level=summary.warning_level,
        warning_1_threshold_credits=credits.WARNING_1_CREDITS,
        warning_2_threshold_credits=credits.WARNING_2_CREDITS,
        estimated_assessments_remaining=estimate,
        average_credits_per_assessment=float(average),
        alert_message=(
            _EXHAUSTED_MESSAGE
            if summary.exhausted
            else _warning_message(
                summary.warning_level, summary.balance_credits, estimate, stem_active
            )
        ),
        unlimited=summary.unlimited,
    )


async def _tenant_or_404(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalars().first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return tenant


@router.get("/overview", response_model=BillingOverviewOut)
async def billing_overview(
    user: CurrentUser = Depends(require_capability(caps.VIEW_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> BillingOverviewOut:
    tenant = await _tenant_or_404(session, user.tenant_id)
    plans = await _active_plans(session)
    current = next((p for p in plans if p.id == tenant.current_plan_id), None)
    if current is None and tenant.current_plan_id:
        current = await session.get(PricingPlan, tenant.current_plan_id)

    ledger = (
        await session.execute(
            select(CreditLedgerEntry)
            .where(CreditLedgerEntry.tenant_id == user.tenant_id)
            .order_by(CreditLedgerEntry.created_at.desc())
            .limit(25)
        )
    ).scalars().all()
    transactions = (
        await session.execute(
            select(BillingTransaction)
            .where(BillingTransaction.tenant_id == user.tenant_id)
            .order_by(BillingTransaction.created_at.desc())
            .limit(25)
        )
    ).scalars().all()

    return BillingOverviewOut(
        subscription=SubscriptionOut(
            plan=_plan_out(current) if current else None,
            status=tenant.subscription_status,
            razorpay_subscription_id=tenant.razorpay_subscription_id,
            current_end=tenant.subscription_current_end,
        ),
        credits=await _summary_out(session, user.tenant_id),
        plans=[_plan_out(plan) for plan in plans],
        razorpay_key_id=razorpay.config().key_id or None,
        recent_ledger=[
            CreditLedgerEntryOut(
                id=row.id,
                event_type=row.event_type,
                subunits_delta=row.subunits_delta,
                credits_delta=credits.credits_from_subunits(row.subunits_delta),
                created_at=row.created_at,
                job_candidate_link_id=row.job_candidate_link_id,
            )
            for row in ledger
        ],
        transactions=[TransactionOut.model_validate(row) for row in transactions],
    )


@router.get("/ledger", response_model=list[CreditLedgerEntryOut])
async def billing_ledger(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    user: CurrentUser = Depends(require_capability(caps.VIEW_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[CreditLedgerEntryOut]:
    rows = (
        await session.execute(
            select(CreditLedgerEntry)
            .where(CreditLedgerEntry.tenant_id == user.tenant_id)
            .order_by(CreditLedgerEntry.created_at.desc(), CreditLedgerEntry.id)
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()
    return [
        CreditLedgerEntryOut(
            id=row.id,
            event_type=row.event_type,
            subunits_delta=row.subunits_delta,
            credits_delta=credits.credits_from_subunits(row.subunits_delta),
            created_at=row.created_at,
            job_candidate_link_id=row.job_candidate_link_id,
        )
        for row in rows
    ]


# ── Customer: subscribe / change / cancel ────────────────────────────────────

def _razorpay_or_503(exc: Exception) -> HTTPException:
    if isinstance(exc, razorpay.RazorpayNotConfigured):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payments are not configured on this server yet.",
        )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


async def _ensure_razorpay_plan(session: AsyncSession, plan: PricingPlan) -> str:
    """Return the plan's Razorpay id, creating it on first use.

    Creating it lazily rather than in the migration keeps the migration offline
    and idempotent: a schema upgrade that reaches out to a payment gateway would
    fail on any machine without credentials, which is every CI runner.
    """
    if plan.razorpay_plan_id:
        return plan.razorpay_plan_id
    plan_id = await razorpay.create_plan(
        name=f"ReadyPick {plan.name}",
        price_inr=plan.price_inr,
        notes={"pickready_plan_slug": plan.slug},
    )
    # Written with raw SQL: `pricing_plans` is a global table the tenant-scoped
    # role can only SELECT, so this runs as one explicit, narrow statement.
    await session.execute(
        text("UPDATE pricing_plans SET razorpay_plan_id = :rid WHERE id = :pid"),
        {"rid": plan_id, "pid": str(plan.id)},
    )
    plan.razorpay_plan_id = plan_id
    return plan_id


@router.post("/subscribe", response_model=SubscribeOut)
async def subscribe(
    body: SubscribeIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SubscribeOut:
    """Create a Razorpay Subscription for this customer and hand its id back.

    Credits are NOT granted here. A created subscription is an intent to pay;
    the money arrives with `subscription.charged`, and that is what grants.
    """
    tenant = await _tenant_or_404(session, user.tenant_id)
    plan = await _plan_by_slug(session, body.plan_slug)
    if tenant.razorpay_subscription_id and tenant.subscription_status == SUBSCRIPTION_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail="This account already has an active subscription. Use Change Plan instead.",
        )
    try:
        razorpay_plan_id = await _ensure_razorpay_plan(session, plan)
        subscription = await razorpay.create_subscription(
            plan_id=razorpay_plan_id,
            customer_id=tenant.razorpay_customer_id,
            notes={"tenant_id": str(tenant.id), "plan_slug": plan.slug},
        )
    except (razorpay.RazorpayError, razorpay.RazorpayNotConfigured) as exc:
        raise _razorpay_or_503(exc) from exc

    tenant.razorpay_subscription_id = subscription["id"]
    tenant.current_plan_id = plan.id
    # Not active until it is charged. Recording it as active here would grant a
    # month of credits to anyone who opened Checkout and then closed the tab.
    tenant.subscription_status = SUBSCRIPTION_PAST_DUE
    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="subscription_created", target_type="tenant", target_id=tenant.id,
        metadata={"plan": plan.slug, "subscription_id": subscription["id"]},
    )
    return SubscribeOut(
        subscription_id=subscription["id"],
        razorpay_key_id=razorpay.config().key_id,
        plan=_plan_out(plan),
        short_url=subscription.get("short_url"),
    )


async def _grant_for_payment(
    session: AsyncSession,
    *,
    tenant: Tenant,
    plan: PricingPlan | None,
    payment_id: str,
    amount_inr: int,
    subscription_id: str | None,
) -> bool:
    """Record the charge and grant that month's credits. Idempotent.

    Returns True when this call is the one that granted. Both the webhook and
    checkout-verify reach this with the same `payment_id`, and only the first
    writes anything.
    """
    if plan is None:
        log.warning("billing.grant_without_plan tenant=%s payment=%s", tenant.id, payment_id)
        return False
    granted = await credits.grant(
        session,
        tenant_id=tenant.id,
        subunits=plan.monthly_subunits,
        idempotency_key=_payment_key(payment_id),
        plan_id=plan.id,
        metadata={
            "applications_per_month": plan.applications_per_month,
            "razorpay_payment_id": payment_id,
        },
    )
    if not granted:
        return False
    session.add(
        BillingTransaction(
            tenant_id=tenant.id,
            razorpay_payment_id=payment_id,
            razorpay_subscription_id=subscription_id,
            amount_inr=amount_inr,
            status="success",
            transaction_type="subscription_charge",
            plan_id=plan.id,
        )
    )
    tenant.subscription_status = SUBSCRIPTION_ACTIVE
    # A top-up releases whatever finalisation was held for want of credits
    # (spec 11). Enqueued rather than run inline: this is a payment path, and
    # writing a batch of reports on it would make a customer's card confirmation
    # wait on the slowest LLM call in the queue.
    #
    # Fired from this ONE helper, which both the webhook and checkout-verify go
    # through, so a customer is released exactly once however their payment
    # arrives. The task re-checks the balance per tenant, so an extra call
    # costs a query and changes nothing.
    from app.workers.celery_app import celery_app

    celery_app.send_task(
        "pickready.release_held_assessments", args=[str(tenant.id)]
    )
    return True


@router.post("/checkout/verify", response_model=BillingOverviewOut)
async def verify_checkout(
    body: CheckoutVerifyIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> BillingOverviewOut:
    """Verify the Checkout handler payload and activate the subscription.

    This exists so the customer sees their credits immediately instead of
    staring at a zero balance until the webhook arrives. It grants under the
    same idempotency key the webhook uses, so the webhook that follows is a
    no-op rather than a second month.
    """
    if not razorpay.verify_checkout_signature(
        payment_id=body.razorpay_payment_id,
        subscription_id=body.razorpay_subscription_id,
        signature=body.razorpay_signature,
    ):
        raise HTTPException(status_code=400, detail="Payment could not be verified")

    tenant = await _tenant_or_404(session, user.tenant_id)
    if tenant.razorpay_subscription_id != body.razorpay_subscription_id:
        # The signature proves Razorpay issued this payment, not that it belongs
        # to the caller's account.
        raise HTTPException(status_code=403, detail="This payment belongs to another account")

    plan = await session.get(PricingPlan, tenant.current_plan_id) if tenant.current_plan_id else None
    granted = await _grant_for_payment(
        session,
        tenant=tenant,
        plan=plan,
        payment_id=body.razorpay_payment_id,
        amount_inr=plan.price_inr if plan else 0,
        subscription_id=body.razorpay_subscription_id,
    )
    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="subscription_checkout_verified", target_type="tenant", target_id=tenant.id,
        metadata={"granted": granted, "payment_id": body.razorpay_payment_id},
    )
    return await billing_overview(user=user, session=session)


@router.post("/change-plan", response_model=SubscriptionOut)
async def change_plan(
    body: SubscribeIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SubscriptionOut:
    """Upgrade or downgrade. Razorpay computes the proration, not us."""
    tenant = await _tenant_or_404(session, user.tenant_id)
    if not tenant.razorpay_subscription_id:
        raise HTTPException(
            status_code=409, detail="There is no subscription to change yet."
        )
    plan = await _plan_by_slug(session, body.plan_slug)
    if plan.id == tenant.current_plan_id:
        raise HTTPException(status_code=409, detail="This is already your current plan.")
    try:
        razorpay_plan_id = await _ensure_razorpay_plan(session, plan)
        await razorpay.update_subscription(
            tenant.razorpay_subscription_id, plan_id=razorpay_plan_id
        )
    except (razorpay.RazorpayError, razorpay.RazorpayNotConfigured) as exc:
        raise _razorpay_or_503(exc) from exc

    previous = tenant.current_plan_id
    tenant.current_plan_id = plan.id
    session.add(
        BillingTransaction(
            tenant_id=tenant.id,
            razorpay_subscription_id=tenant.razorpay_subscription_id,
            amount_inr=plan.price_inr,
            status="success",
            transaction_type="plan_change",
            plan_id=plan.id,
            notes=f"Changed from plan {previous} to {plan.slug}",
        )
    )
    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="subscription_plan_changed", target_type="tenant", target_id=tenant.id,
        metadata={"to": plan.slug},
    )
    return SubscriptionOut(
        plan=_plan_out(plan),
        status=tenant.subscription_status,
        razorpay_subscription_id=tenant.razorpay_subscription_id,
        current_end=tenant.subscription_current_end,
    )


@router.post("/cancel", response_model=SubscriptionOut)
async def cancel(
    user: CurrentUser = Depends(require_capability(caps.MANAGE_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SubscriptionOut:
    """Cancel at cycle end. Credits already in the pool are NOT clawed back —
    they were paid for, and nothing expires (spec §2.2 / §3.1)."""
    tenant = await _tenant_or_404(session, user.tenant_id)
    if not tenant.razorpay_subscription_id:
        raise HTTPException(status_code=409, detail="There is no subscription to cancel.")
    try:
        await razorpay.cancel_subscription(tenant.razorpay_subscription_id)
    except (razorpay.RazorpayError, razorpay.RazorpayNotConfigured) as exc:
        raise _razorpay_or_503(exc) from exc
    tenant.subscription_status = SUBSCRIPTION_CANCELLED
    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="subscription_cancelled", target_type="tenant", target_id=tenant.id,
        metadata={},
    )
    plan = await session.get(PricingPlan, tenant.current_plan_id) if tenant.current_plan_id else None
    return SubscriptionOut(
        plan=_plan_out(plan) if plan else None,
        status=tenant.subscription_status,
        razorpay_subscription_id=tenant.razorpay_subscription_id,
        current_end=tenant.subscription_current_end,
    )


# ── Credit-pack purchases (Master Directive Part 5) ──────────────────────────

@router.get("/credit-packs", response_model=CreditPacksOut)
async def credit_pack_quotes(
    user: CurrentUser = Depends(require_capability(caps.VIEW_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CreditPacksOut:
    """Every pack priced for THIS tenant, breakdown included (§3.3 step 2).

    Priced server-side rather than letting the client multiply, because the
    setup fee and the trial's availability depend on tenant state the client
    cannot know (the §5.1 waiver count lives across all tenants), and the
    figure the customer accepts must be the figure the Order is created for.
    """
    tenant = await _tenant_or_404(session, user.tenant_id)
    quotes = await credit_packs.quote(session, tenant)
    return CreditPacksOut(
        packs=[CreditPackQuoteOut(**q.__dict__) for q in quotes],
        price_per_credit_inr=PRICE_PER_CREDIT_INR,
        gst_rate_percent=GST_RATE_PERCENT,
        min_custom_credits=MIN_PURCHASE_CREDITS,
        trial_used=tenant.trial_used,
    )


@router.post("/purchase", response_model=CreditPurchaseCreatedOut)
async def create_credit_purchase(
    body: CreditPurchaseIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CreditPurchaseCreatedOut:
    """Validate the purchase and mint its Razorpay Order.

    Credits are NOT granted here — a created Order is an intent to pay, and
    the grant happens on payment confirmation (§3.3 step 5), exactly as the
    subscription flow separates /subscribe from the charge event. The rule
    violations (trial reuse, sub-50 amounts) are 422s with the service's own
    message, so the form can show the reason verbatim.
    """
    tenant = await _tenant_or_404(session, user.tenant_id)
    try:
        purchase = await credit_packs.create_purchase(
            session,
            tenant,
            user.user_id,
            pack_slug=body.pack_slug,
            custom_credits=body.custom_credits,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (razorpay.RazorpayError, razorpay.RazorpayNotConfigured) as exc:
        raise _razorpay_or_503(exc) from exc

    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="credit_purchase_created", target_type="credit_purchase",
        target_id=purchase.id,
        metadata={
            "pack_slug": purchase.pack_slug,
            "credits": purchase.credits_purchased,
            "total_inr": purchase.total_inr,
            "razorpay_order_id": purchase.razorpay_order_id,
        },
    )
    return CreditPurchaseCreatedOut(
        purchase_id=purchase.id,
        razorpay_order_id=purchase.razorpay_order_id,
        razorpay_key_id=razorpay.config().key_id,
        total_inr=purchase.total_inr,
        credits=purchase.credits_purchased,
        bonus_credits=purchase.bonus_credits,
        subtotal_inr=purchase.subtotal_inr,
        setup_fee_inr=purchase.setup_fee_inr,
        gst_inr=purchase.gst_inr,
    )


@router.post("/purchase/verify", response_model=BillingOverviewOut)
async def verify_credit_purchase(
    body: CreditPurchaseVerifyIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> BillingOverviewOut:
    """Verify the Checkout handler payload for an Order and settle.

    Same shape as /checkout/verify and for the same reason: the customer sees
    their credits the moment Checkout closes instead of staring at the old
    balance until the webhook lands. Settlement is idempotent, so whichever of
    this and the webhook runs second is a no-op.
    """
    if not razorpay.verify_order_signature(
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
    ):
        raise HTTPException(status_code=400, detail="Payment could not be verified")

    purchase = (
        await session.execute(
            select(CreditPurchase).where(
                CreditPurchase.razorpay_order_id == body.razorpay_order_id
            )
        )
    ).scalars().first()
    # The tenant-scoped session's RLS already filters to the caller's rows,
    # but the ownership check is stated explicitly: the signature proves
    # Razorpay issued the payment, not that the order is the caller's.
    if purchase is None or purchase.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such purchase")

    settled = await credit_packs.settle_purchase(
        session, purchase, body.razorpay_payment_id
    )
    await audit(
        session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
        action="credit_purchase_verified", target_type="credit_purchase",
        target_id=purchase.id,
        metadata={"settled": settled, "payment_id": body.razorpay_payment_id},
    )
    return await billing_overview(user=user, session=session)


@router.get("/purchases", response_model=list[CreditPurchaseOut])
async def list_credit_purchases(
    limit: int = Query(default=100, ge=1, le=500),
    user: CurrentUser = Depends(require_capability(caps.VIEW_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[CreditPurchaseOut]:
    """The tenant's purchase history, newest first — §7.4's transaction view,
    and where the §7.3 invoice download links come from. Bounded like every
    other list route: the newest hundred purchases cover years of buying at
    any plausible cadence, and the ceiling keeps the page alive past that."""
    rows = (
        await session.execute(
            select(CreditPurchase)
            .where(CreditPurchase.tenant_id == user.tenant_id)
            .order_by(CreditPurchase.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [CreditPurchaseOut.model_validate(row) for row in rows]


@router.get("/purchases/{purchase_id}/invoice")
async def download_credit_invoice(
    purchase_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_BILLING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """The GST invoice PDF, downloadable at any time (§7.3).

    404 for anything that is not the caller's own PAID purchase: an unpaid
    purchase has no invoice (§9: no invoice on a failed payment), and a
    non-existent one and another tenant's must be indistinguishable.
    """
    purchase = (
        await session.execute(
            select(CreditPurchase).where(CreditPurchase.id == purchase_id)
        )
    ).scalars().first()
    if (
        purchase is None
        or purchase.tenant_id != user.tenant_id
        or purchase.status != PURCHASE_PAID
    ):
        raise HTTPException(status_code=404, detail="No invoice for this purchase")
    tenant = await _tenant_or_404(session, user.tenant_id)
    pdf = credit_packs.render_invoice_pdf(purchase, tenant)
    filename = f"{purchase.invoice_number or purchase.id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Razorpay webhook ─────────────────────────────────────────────────────────

_HANDLED_EVENTS = {
    "payment.captured",
    "order.paid",
    "subscription.charged",
    "subscription.cancelled",
    "subscription.halted",
    "subscription.completed",
    "subscription.pending",
    "payment.failed",
}


async def _tenant_for_subscription(
    session: AsyncSession, subscription_id: str | None, notes: dict | None
) -> Tenant | None:
    """Resolve the customer from the subscription id, falling back to notes.

    The notes fallback matters for the landing-page flow, where the subscription
    is created before the tenant row has ever been written back with its id.
    """
    if subscription_id:
        tenant = (
            await session.execute(
                select(Tenant).where(Tenant.razorpay_subscription_id == subscription_id)
            )
        ).scalars().first()
        if tenant is not None:
            return tenant
    raw = (notes or {}).get("tenant_id")
    if not raw:
        return None
    try:
        tenant_id = uuid.UUID(str(raw))
    except ValueError:
        return None
    return (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalars().first()


@router.post("/webhook/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request, session: AsyncSession = Depends(get_public_db)
) -> dict:
    """Signature-verified subscription events.

    Always answers 200 for anything it has authenticated, including events it
    does not act on. A non-2xx makes Razorpay retry, and retrying an event we
    have deliberately ignored just fills the retry queue forever.
    """
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    settings = get_settings()
    if not razorpay.verify_webhook_signature(raw_body=raw, signature=signature):
        if settings.is_production or razorpay.config().webhook_secret:
            # A configured secret that does not match is a forgery, not a
            # misconfiguration.
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
        # Local development with no RAZORPAY_WEBHOOK_SECRET: accept, loudly.
        log.warning(
            "billing.webhook_unverified, RAZORPAY_WEBHOOK_SECRET is not set. "
            "This is accepted in development ONLY."
        )

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed webhook payload") from exc

    event_type = str(body.get("event") or "")
    # Razorpay's delivery id header is the stable per-delivery identity; the
    # payload carries no event id of its own.
    event_id = request.headers.get("X-Razorpay-Event-Id") or f"{event_type}:{body.get('created_at')}"

    # Dedupe FIRST. Razorpay delivers at least once and a replayed
    # subscription.charged would otherwise grant a second month.
    session.add(WebhookEvent(provider="razorpay", event_id=event_id, event_type=event_type,
                             payload_json=body))
    try:
        await session.flush()
    except Exception:  # noqa: BLE001 - unique violation means "already handled"
        await session.rollback()
        return {"status": "duplicate"}

    if event_type not in _HANDLED_EVENTS:
        return {"status": "ignored"}

    payload = body.get("payload") or {}
    subscription_entity = ((payload.get("subscription") or {}).get("entity")) or {}
    payment_entity = ((payload.get("payment") or {}).get("entity")) or {}
    order_entity = ((payload.get("order") or {}).get("entity")) or {}

    # ── Credit-pack purchases first (Master Directive Part 5 §3.3 step 5) ────
    # A payment.captured / order.paid / payment.failed whose order id matches
    # a credit_purchases row belongs to the pack flow, whatever else the event
    # carries. Resolved by order id, not by tenant: the WebhookEvent dedupe
    # above plus settle_purchase's own status-flip idempotency give the §9
    # duplicate-webhook guarantee twice over.
    order_id = payment_entity.get("order_id") or order_entity.get("id")
    if order_id:
        purchase = (
            await session.execute(
                select(CreditPurchase).where(
                    CreditPurchase.razorpay_order_id == order_id
                )
            )
        ).scalars().first()
        if purchase is not None:
            if event_type in {"payment.captured", "order.paid"}:
                await credit_packs.settle_purchase(
                    session, purchase, payment_entity.get("id")
                )
            elif event_type == "payment.failed":
                # §9: no credits, no invoice. Marked failed only while still
                # `created` — a purchase the settle path already won stays
                # paid, and the client retries with a NEW purchase.
                await session.execute(
                    text(
                        "UPDATE credit_purchases SET status = :failed "
                        "WHERE id = :id AND status = :created"
                    ),
                    {
                        "failed": PURCHASE_FAILED,
                        "created": PURCHASE_CREATED,
                        "id": str(purchase.id),
                    },
                )
            await session.execute(
                text(
                    "UPDATE webhook_events SET processed_at = now() "
                    "WHERE provider = 'razorpay' AND event_id = :eid"
                ),
                {"eid": event_id},
            )
            return {"status": "ok"}
    if event_type in {"payment.captured", "order.paid"}:
        # A captured payment with no matching purchase row is a subscription
        # charge's payment leg (subscription.charged handles those) or noise.
        return {"status": "ignored"}

    subscription_id = subscription_entity.get("id") or payment_entity.get("subscription_id")
    tenant = await _tenant_for_subscription(
        session, subscription_id, subscription_entity.get("notes") or payment_entity.get("notes")
    )
    if tenant is None:
        log.warning("billing.webhook_unmatched event=%s subscription=%s", event_type, subscription_id)
        return {"status": "unmatched"}

    plan = (
        await session.get(PricingPlan, tenant.current_plan_id)
        if tenant.current_plan_id
        else None
    )

    if event_type == "subscription.charged":
        payment_id = payment_entity.get("id") or f"sub:{subscription_id}:{body.get('created_at')}"
        amount_paise = int(payment_entity.get("amount") or 0)
        await _grant_for_payment(
            session,
            tenant=tenant,
            plan=plan,
            payment_id=payment_id,
            amount_inr=amount_paise // razorpay.PAISE_PER_RUPEE,
            subscription_id=subscription_id,
        )
        end = subscription_entity.get("current_end")
        if end:
            tenant.subscription_current_end = datetime.fromtimestamp(int(end), tz=timezone.utc)

    elif event_type == "payment.failed":
        # Do NOT keep granting. The customer is emailed and the subscription is
        # marked past_due; the next successful charge flips it back.
        tenant.subscription_status = SUBSCRIPTION_PAST_DUE
        session.add(
            BillingTransaction(
                tenant_id=tenant.id,
                razorpay_payment_id=payment_entity.get("id"),
                razorpay_subscription_id=subscription_id,
                amount_inr=int(payment_entity.get("amount") or 0) // razorpay.PAISE_PER_RUPEE,
                status="failed",
                transaction_type="subscription_charge",
                plan_id=plan.id if plan else None,
                notes=str(payment_entity.get("error_description") or "")[:500],
            )
        )
        from app.workers.celery_app import celery_app

        celery_app.send_task("pickready.send_payment_failed_email", args=[str(tenant.id)])

    elif event_type in {"subscription.cancelled", "subscription.completed"}:
        # Future grants stop. Unused credits stay in the pool — they were paid
        # for, and the rollover rule still applies to what is already there.
        tenant.subscription_status = SUBSCRIPTION_CANCELLED

    elif event_type in {"subscription.halted", "subscription.pending"}:
        tenant.subscription_status = (
            SUBSCRIPTION_HALTED if event_type == "subscription.halted" else SUBSCRIPTION_PAST_DUE
        )

    await session.execute(
        text("UPDATE webhook_events SET processed_at = now() WHERE provider = 'razorpay' "
             "AND event_id = :eid"),
        {"eid": event_id},
    )
    return {"status": "ok"}


# ── Provider Portal: billing overview across customers ───────────────────────

@router.get("/provider/overview", response_model=list[ProviderBillingRowOut])
async def provider_billing_overview(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[ProviderBillingRowOut]:
    """Which customers are on which plan, their status, and their balance.

    One query with a LEFT JOIN and a grouped ledger sum, not a per-customer
    balance lookup: 30 customers must not become 31 round trips.
    """
    balances = (
        select(
            CreditLedgerEntry.tenant_id.label("tenant_id"),
            func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0).label("balance"),
        )
        .group_by(CreditLedgerEntry.tenant_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                Tenant.id,
                Tenant.name,
                PricingPlan.name,
                PricingPlan.rate_per_application_inr,
                Tenant.subscription_status,
                func.coalesce(balances.c.balance, 0),
                Tenant.subscription_current_end,
            )
            .select_from(Tenant)
            .outerjoin(PricingPlan, PricingPlan.id == Tenant.current_plan_id)
            .outerjoin(balances, balances.c.tenant_id == Tenant.id)
            .order_by(Tenant.name)
            .offset(skip)
            .limit(limit)
        )
    ).all()
    return [
        ProviderBillingRowOut(
            tenant_id=tenant_id,
            customer_name=name,
            plan_name=plan_name,
            subscription_status=sub_status,
            balance_subunits=int(balance),
            balance_credits=credits.credits_from_subunits(int(balance)),
            balance_inr=(
                (
                    credits.credits_from_subunits(int(balance))
                    * Decimal(plan_rate)
                ).quantize(Decimal("0.01"))
                if plan_rate is not None
                else None
            ),
            in_deficit=int(balance) < 0,
            current_end=current_end,
        )
        for tenant_id, name, plan_name, plan_rate, sub_status, balance, current_end in rows
    ]
