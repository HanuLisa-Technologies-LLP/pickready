"""Published monthly assessment allowances and their recurring charge amounts.

One completed assessment spends one credit. Every paid monthly charge grants
the plan's allowance as a three-month credit lot. Plans share all features.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    CREDIT_VALIDITY_MONTHS, GST_RATE_PERCENT, SUBUNITS_PER_CREDIT,
    BillingTransaction,
)
from app.models.tenant import Tenant
from app.services import credits, razorpay


@dataclass(frozen=True)
class MonthlyPlan:
    slug: str
    name: str
    assessments: int
    price_inr: int

    @property
    def gst_inr(self) -> int:
        return (self.price_inr * GST_RATE_PERCENT + 50) // 100

    @property
    def total_inr(self) -> int:
        return self.price_inr + self.gst_inr

    @property
    def rollover_months(self) -> int:
        return CREDIT_VALIDITY_MONTHS


PLANS: tuple[MonthlyPlan, ...] = (
    MonthlyPlan("starter", "Starter", 75, 24_000),
    MonthlyPlan("growth", "Growth", 200, 55_000),
    MonthlyPlan("scale", "Scale", 500, 120_000),
    MonthlyPlan("pro", "Pro", 1_200, 240_000),
)
BY_SLUG = {plan.slug: plan for plan in PLANS}
STARTER_TOPUP_SLUG = "starter_pack_75"


async def gateway_plan_id(session: AsyncSession, plan: MonthlyPlan) -> str:
    """Provision a gateway plan once, under a transaction-scoped database lock."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"readypick:monthly-plan:{plan.slug}"},
    )
    existing = (await session.execute(
        text("SELECT razorpay_plan_id, total_inr FROM monthly_gateway_plans WHERE slug = :slug"),
        {"slug": plan.slug},
    )).first()
    if existing:
        if existing.total_inr != plan.total_inr:
            raise RuntimeError("Gateway plan amount differs from the published plan")
        return existing.razorpay_plan_id
    gateway_id = await razorpay.create_monthly_plan(
        name=f"ReadyPick {plan.name}", total_inr=plan.total_inr, slug=plan.slug,
    )
    await session.execute(
        text("INSERT INTO monthly_gateway_plans (slug, razorpay_plan_id, total_inr) "
             "VALUES (:slug, :gateway_id, :total)"),
        {"slug": plan.slug, "gateway_id": gateway_id, "total": plan.total_inr},
    )
    return gateway_id


async def begin_subscription(
    session: AsyncSession, tenant: Tenant, slug: str,
) -> dict:
    plan = BY_SLUG.get(slug)
    if plan is None:
        raise ValueError("Unknown monthly plan")
    if tenant.subscription_status == "created" and tenant.current_plan_slug == slug:
        return {
            "subscription_id": tenant.razorpay_subscription_id,
            "razorpay_key_id": razorpay.config().key_id,
            "plan": plan.__dict__, "gst_inr": plan.gst_inr,
            "total_inr": plan.total_inr,
        }
    if tenant.razorpay_subscription_id and tenant.subscription_status in {"created", "active", "pending", "cancelling"}:
        raise ValueError("A subscription is already in progress for this company")
    gateway_id = await gateway_plan_id(session, plan)
    subscription = await razorpay.create_subscription(
        plan_id=gateway_id, tenant_id=str(tenant.id), slug=slug,
    )
    tenant.razorpay_subscription_id = subscription["id"]
    tenant.current_plan_slug = slug
    tenant.subscription_status = "created"
    tenant.pending_plan_slug = None
    return {
        "subscription_id": subscription["id"],
        "razorpay_key_id": razorpay.config().key_id,
        "plan": plan.__dict__,
        "gst_inr": plan.gst_inr,
        "total_inr": plan.total_inr,
    }


async def settle_charge(
    session: AsyncSession, *, tenant: Tenant, subscription_id: str,
    payment_id: str, amount_inr: int, current_end: int | None = None,
    gateway_plan_id: str | None = None,
) -> bool:
    """Grant one month on a captured gateway charge, once per payment id."""
    if tenant.razorpay_subscription_id != subscription_id:
        raise ValueError("Subscription does not belong to this company")
    charged_slug = tenant.current_plan_slug
    if gateway_plan_id:
        charged_slug = (await session.execute(
            text("SELECT slug FROM monthly_gateway_plans WHERE razorpay_plan_id = :id"),
            {"id": gateway_plan_id},
        )).scalar_one_or_none()
    plan = BY_SLUG.get(charged_slug or "")
    if plan is None:
        raise ValueError("Subscription has no published plan")
    if amount_inr != plan.total_inr:
        raise ValueError("Captured payment amount does not match the plan")
    granted = await credits.grant(
        session, tenant_id=tenant.id,
        subunits=plan.assessments * SUBUNITS_PER_CREDIT,
        idempotency_key=f"subscription:{payment_id}",
        metadata={"plan_slug": plan.slug, "subscription_id": subscription_id,
                  "payment_id": payment_id},
    )
    if not granted:
        return False
    now = datetime.now(timezone.utc)
    invoice_sequence = (await session.execute(
        text("SELECT nextval('credit_invoice_seq')")
    )).scalar_one()
    session.add(BillingTransaction(
        tenant_id=tenant.id, razorpay_payment_id=payment_id,
        razorpay_subscription_id=subscription_id, plan_slug=plan.slug,
        assessments_granted=plan.assessments, subtotal_inr=plan.price_inr,
        gst_inr=plan.gst_inr, amount_inr=amount_inr,
        invoice_number=f"RP-{now.year}-{int(invoice_sequence):06d}",
        status="success", transaction_type="subscription_charge",
    ))
    tenant.current_plan_slug = plan.slug
    if tenant.pending_plan_slug == plan.slug:
        tenant.pending_plan_slug = None
    tenant.subscription_status = "active"
    if tenant.subscription_started_at is None:
        tenant.subscription_started_at = datetime.now(timezone.utc)
    if current_end:
        tenant.subscription_current_end = datetime.fromtimestamp(current_end, tz=timezone.utc)
    from app.workers.dispatch import dispatch_after_commit
    dispatch_after_commit(session, "pickready.release_held_assessments", args=[str(tenant.id)])
    return True


def render_charge_invoice_pdf(charge: BillingTransaction, tenant: Tenant) -> bytes:
    """Render the stored subscription charge and GST amounts as an invoice."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.core.config import get_settings

    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    page.setFont("Helvetica-Bold", 20)
    page.drawString(40, height - 55, "Vivekium")
    page.setFont("Helvetica", 10)
    page.drawRightString(width - 40, height - 55, "TAX INVOICE")
    y = height - 105
    page.drawString(40, y, f"Invoice No: {charge.invoice_number}")
    page.drawRightString(width - 40, y, f"Date: {charge.created_at:%d %b %Y}")
    y -= 20
    if get_settings().readypick_gstin:
        page.drawString(40, y, f"Vivekium GSTIN: {get_settings().readypick_gstin}")
        y -= 18
    page.drawString(40, y, f"Billed to: {tenant.name}")
    y -= 18
    if tenant.gstin:
        page.drawString(40, y, f"Client GSTIN: {tenant.gstin}")
        y -= 18
    y -= 20
    for label, amount in (
        (f"{charge.plan_slug.title()} monthly plan: {charge.assessments_granted} completed assessments", charge.subtotal_inr),
        (f"GST @ {GST_RATE_PERCENT}%", charge.gst_inr),
        ("Total paid", charge.amount_inr),
    ):
        page.drawString(40, y, label)
        page.drawRightString(width - 40, y, f"Rs. {amount:,}")
        y -= 22
    y -= 15
    page.drawString(40, y, "Unused credits expire three months after grant.")
    page.showPage()
    page.save()
    return buffer.getvalue()
