"""Credit-pack purchases: quoting, validation, settlement, and the GST invoice
(Master Directive Part 5 — Pricing Model).

Money rules this module keeps true:

* The per-credit price NEVER moves (Rule 3). Volume levels add FREE credits;
  the invoice always shows purchased credits at ₹600 and the bonus as a ₹0
  line, so `subtotal_inr` is always credits × 600 with no discount arithmetic
  anywhere.
* GST is 18% of everything charged — credits AND setup fee (Rule 7) — held in
  its own column so the invoice's breakdown is the stored breakdown.
* The trial (20 credits) is once per account, checked on EVERY attempt
  (Rule 1); everything else is a hard 50-credit minimum (Rule 2).
* The ₹5,000 setup fee rides on the first purchase only (Rule 6) and is
  waived while fewer than 15 accounts hold the waiver (§5.1). The count of
  `tenants.setup_fee_waived` IS the §5.1 counter — no separate row to drift.
* Settlement is idempotent: the status flip created→paid happens in the same
  UPDATE that checks it, so the browser-verify call and the webhook can race
  (and duplicate webhooks can replay, §9) and exactly one caller grants.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    CREDIT_PACKS,
    GST_RATE_PERCENT,
    MIN_PURCHASE_CREDITS,
    PRICE_PER_CREDIT_INR,
    PURCHASE_CREATED,
    PURCHASE_PAID,
    SETUP_FEE_INR,
    SETUP_FEE_WAIVER_LIMIT,
    SUBUNITS_PER_CREDIT,
    TRIAL_CREDITS,
    BillingTransaction,
    CreditPurchase,
)
from app.models.tenant import Tenant
from app.services import credits, razorpay

log = logging.getLogger(__name__)

#: Slug stored on a purchase that came through the custom-amount path rather
#: than a named pack. Deliberately not in CREDIT_PACKS: it has no fixed size.
CUSTOM_SLUG = "custom"


def bonus_for(credit_count: int) -> int:
    """Free credits for a purchase of this size (Rule 3 / §3.2).

    Threshold-based rather than exact-match so a custom purchase of, say,
    150 credits still earns the 100-level bonus: Rule 3 says "at 100 credits
    purchased: add 5", which reads as a level reached, not a SKU.
    """
    if credit_count >= 200:
        return CREDIT_PACKS["volume_200"][1]
    if credit_count >= 100:
        return CREDIT_PACKS["volume_100"][1]
    return 0


def gst_inr(taxable_inr: int) -> int:
    """18% of the taxable amount, rounded half-up to whole rupees.

    Every self-serve figure is a multiple of ₹600 (plus the ₹5,000 fee), so
    in practice this is exact; the rounding exists so a future custom rate
    cannot produce a fractional-paise invoice.
    """
    return (taxable_inr * GST_RATE_PERCENT + 50) // 100


@dataclass(frozen=True)
class PackQuote:
    """One line of the §7.2 purchase page: a pack priced for THIS tenant."""

    slug: str
    credits: int
    bonus_credits: int
    subtotal_inr: int
    setup_fee_inr: int
    setup_fee_waived: bool
    gst_inr: int
    total_inr: int
    available: bool
    trial: bool


def _price(credit_count: int, setup_fee: int) -> tuple[int, int, int]:
    """(subtotal, gst, total) for a purchase of `credit_count` credits."""
    subtotal = credit_count * PRICE_PER_CREDIT_INR
    tax = gst_inr(subtotal + setup_fee)
    return subtotal, tax, subtotal + tax + setup_fee


async def setup_fee_for(session: AsyncSession, tenant: Tenant) -> tuple[int, bool]:
    """(fee to charge on this purchase, whether it is being waived).

    Once `setup_fee_paid` is set nothing is ever charged again (Rule 6).
    Otherwise the §5.1 waiver applies while fewer than 15 accounts carry
    `setup_fee_waived` — the flag count is the counter itself. Read with raw
    SQL because `tenants` is a global table and this must answer identically
    from a tenant-scoped session and from the webhook's public session.
    """
    if tenant.setup_fee_paid:
        return 0, False
    waived_count = (
        await session.execute(
            text("SELECT count(*) FROM tenants WHERE setup_fee_waived")
        )
    ).scalar_one()
    if int(waived_count) < SETUP_FEE_WAIVER_LIMIT:
        return 0, True
    return SETUP_FEE_INR, False


async def quote(session: AsyncSession, tenant: Tenant) -> list[PackQuote]:
    """Price every pack for this tenant, with its live setup-fee treatment.

    The trial pack is listed but `available=False` once used (§7.2: "hidden
    after first use" is the UI's job; the API states the fact).
    """
    setup_fee, waived = await setup_fee_for(session, tenant)
    quotes: list[PackQuote] = []
    for slug, (credit_count, bonus) in CREDIT_PACKS.items():
        is_trial = slug == "trial_20"
        subtotal, tax, total = _price(credit_count, setup_fee)
        quotes.append(
            PackQuote(
                slug=slug,
                credits=credit_count,
                bonus_credits=bonus,
                subtotal_inr=subtotal,
                setup_fee_inr=setup_fee,
                setup_fee_waived=waived,
                gst_inr=tax,
                total_inr=total,
                available=not (is_trial and tenant.trial_used),
                trial=is_trial,
            )
        )
    return quotes


async def create_purchase(
    session: AsyncSession,
    tenant: Tenant,
    user_id: uuid.UUID | None,
    *,
    pack_slug: str | None = None,
    custom_credits: int | None = None,
) -> CreditPurchase:
    """Validate the purchase, create its Razorpay Order, store the row.

    Raises ValueError with a client-worthy message on any rule violation —
    the API maps it to a 422. The row is written with status `created` and
    grants NOTHING: credits arrive only through `settle_purchase`, on payment
    confirmation (§3.3 step 5, and the §9 payment-failed row).
    """
    if (pack_slug is None) == (custom_credits is None):
        raise ValueError("Choose either a credit pack or a custom amount.")

    if pack_slug is not None:
        if pack_slug not in CREDIT_PACKS:
            raise ValueError("Unknown credit pack.")
        credit_count, bonus = CREDIT_PACKS[pack_slug]
        # Rule 1: check trial_used on EVERY attempt, not just the first.
        if pack_slug == "trial_20" and tenant.trial_used:
            raise ValueError(
                f"The {TRIAL_CREDITS}-credit trial is available once per "
                f"account and has already been used. The minimum purchase is "
                f"{MIN_PURCHASE_CREDITS} credits."
            )
        slug = pack_slug
    else:
        # Rule 2: the custom path has the same hard floor as everything else.
        if custom_credits < MIN_PURCHASE_CREDITS:
            raise ValueError(
                f"The minimum purchase is {MIN_PURCHASE_CREDITS} credits."
            )
        credit_count, bonus = custom_credits, bonus_for(custom_credits)
        slug = CUSTOM_SLUG

    setup_fee, waived = await setup_fee_for(session, tenant)
    subtotal, tax, total = _price(credit_count, setup_fee)

    purchase = CreditPurchase(
        # Assigned eagerly (the mixin's default only fires at flush) because
        # the id doubles as the Razorpay receipt below, before any flush.
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        created_by=user_id,
        pack_slug=slug,
        credits_purchased=credit_count,
        bonus_credits=bonus,
        subtotal_inr=subtotal,
        setup_fee_inr=setup_fee,
        setup_fee_waived=waived,
        gst_inr=tax,
        total_inr=total,
        status=PURCHASE_CREATED,
    )
    # The receipt is our purchase id, so the Razorpay dashboard and our table
    # cross-reference by inspection. The order is created BEFORE the row is
    # flushed so a gateway failure leaves nothing behind to reconcile.
    order = await razorpay.create_order(
        amount_paise=total * razorpay.PAISE_PER_RUPEE,
        receipt=str(purchase.id),
        notes={"tenant_id": str(tenant.id), "pack_slug": slug},
    )
    purchase.razorpay_order_id = order["id"]
    session.add(purchase)
    await session.flush()
    return purchase


async def settle_purchase(
    session: AsyncSession, purchase: CreditPurchase, payment_id: str | None
) -> bool:
    """Grant the credits and finalise the invoice. Idempotent; returns True
    only for the ONE call that settles.

    The browser's verify call and the webhook both land here — routinely for
    the same payment, and the webhook possibly more than once (§9 duplicate
    row). The winner is decided by the database: the status flip happens in
    the same UPDATE that checks it, so every other caller sees zero rows and
    returns False having written nothing.
    """
    won = (
        await session.execute(
            text(
                "UPDATE credit_purchases SET status = :paid "
                "WHERE id = :id AND status = :created RETURNING id"
            ),
            {
                "paid": PURCHASE_PAID,
                "created": PURCHASE_CREATED,
                "id": str(purchase.id),
            },
        )
    ).first()
    if won is None:
        return False

    # Belt and braces: the grant's idempotency key is derived from the order
    # id, so even a settle that somehow won twice could not double-grant.
    await credits.grant(
        session,
        tenant_id=purchase.tenant_id,
        subunits=(purchase.credits_purchased + purchase.bonus_credits)
        * SUBUNITS_PER_CREDIT,
        idempotency_key=f"credit-pack:{purchase.razorpay_order_id}",
        metadata={
            "pack_slug": purchase.pack_slug,
            "credits_purchased": purchase.credits_purchased,
            "bonus_credits": purchase.bonus_credits,
            "razorpay_order_id": purchase.razorpay_order_id,
        },
    )

    # Rule 1 + Rule 6 on the tenant, in one statement. trial_used goes TRUE on
    # any first settled purchase (trial or not: either way the trial window is
    # over). setup_fee_paid goes TRUE unconditionally — charged on this
    # invoice, waived on this invoice, or already TRUE from an earlier one.
    # Raw SQL because `tenants` is global and this runs from tenant-scoped and
    # webhook (public) sessions alike.
    await session.execute(
        text(
            "UPDATE tenants SET trial_used = TRUE, setup_fee_paid = TRUE, "
            "setup_fee_waived = setup_fee_waived OR :waived WHERE id = :tid"
        ),
        {"waived": purchase.setup_fee_waived, "tid": str(purchase.tenant_id)},
    )

    # Sequential GST invoice number (§7.3). nextval() is race-free under
    # concurrent settlements; the year makes the series human-auditable
    # against a filing period.
    now = datetime.now(timezone.utc)
    seq = (
        await session.execute(text("SELECT nextval('credit_invoice_seq')"))
    ).scalar_one()
    purchase.status = PURCHASE_PAID
    purchase.razorpay_payment_id = payment_id
    purchase.invoice_number = f"RP-{now.year}-{int(seq):06d}"
    purchase.paid_at = now

    session.add(
        BillingTransaction(
            tenant_id=purchase.tenant_id,
            razorpay_payment_id=payment_id,
            amount_inr=purchase.total_inr,
            status="success",
            transaction_type="credit_pack",
            notes=f"Credit pack {purchase.pack_slug}: "
            f"{purchase.credits_purchased} + {purchase.bonus_credits} bonus",
        )
    )
    await session.flush()

    # Both enqueues are best-effort: a broker outage must never turn a settled
    # payment into an error the customer sees. The invoice email is retried by
    # support if lost; the held-report release re-checks balances anyway.
    try:
        from app.workers.celery_app import celery_app

        celery_app.send_task(
            "pickready.send_credit_invoice_email", args=[str(purchase.id)]
        )
        # A top-up releases whatever finalisation was held for want of
        # credits — same task, same reasoning as the subscription grant path
        # (api/billing._grant_for_payment).
        celery_app.send_task(
            "pickready.release_held_assessments", args=[str(purchase.tenant_id)]
        )
    except Exception:  # noqa: BLE001 - notification must never break settlement
        log.warning(
            "credit_packs.enqueue_failed purchase=%s", purchase.id, exc_info=True
        )
    return True


# ── GST invoice PDF (§5.2 / §7.3) ────────────────────────────────────────────

#: ReadyPick brand navy for the invoice header (Part 1's palette).
_NAVY = (0.06, 0.13, 0.28)


def _inr(amount: int) -> str:
    """Whole rupees with thousands separators. "Rs." rather than the rupee
    sign because the PDF's built-in Helvetica has no glyph for it, and a
    missing-glyph box on a tax document is worse than the abbreviation."""
    return f"Rs. {amount:,}"


def render_invoice_pdf(purchase: CreditPurchase, tenant: Tenant) -> bytes:
    """One-page GST-compliant invoice for a settled purchase (§5.2, §7.3).

    Everything printed comes from the STORED purchase row — nothing is
    recomputed here, so the PDF a customer downloads in two years matches the
    money that actually moved, whatever the constants say by then.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    from app.core.config import get_settings

    settings = get_settings()
    buffer = BytesIO()
    page = pdf_canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Header band: brand navy, white wordmark.
    page.setFillColorRGB(*_NAVY)
    page.rect(0, height - 90, width, 90, stroke=0, fill=1)
    page.setFillColorRGB(1, 1, 1)
    page.setFont("Helvetica-Bold", 22)
    page.drawString(40, height - 55, "ReadyPick")
    page.setFont("Helvetica", 11)
    page.drawRightString(width - 40, height - 55, "TAX INVOICE")

    y = height - 130
    page.setFillColorRGB(0, 0, 0)
    page.setFont("Helvetica-Bold", 11)
    page.drawString(40, y, f"Invoice No: {purchase.invoice_number or ''}")
    invoice_date = purchase.paid_at or purchase.created_at
    page.drawRightString(
        width - 40, y, f"Date: {invoice_date:%d %b %Y}" if invoice_date else "Date:"
    )
    y -= 18
    page.setFont("Helvetica", 10)
    if settings.readypick_gstin:
        page.drawString(40, y, f"ReadyPick GSTIN: {settings.readypick_gstin}")
        y -= 14
    page.drawString(40, y, f"Billed to: {tenant.name}")
    y -= 14
    if tenant.gstin:
        page.drawString(40, y, f"Client GSTIN: {tenant.gstin}")
        y -= 14

    # Line items. Column layout: description, qty x rate, amount.
    y -= 20
    page.setFont("Helvetica-Bold", 10)
    page.drawString(40, y, "Description")
    page.drawRightString(width - 40, y, "Amount")
    y -= 6
    page.setLineWidth(0.5)
    page.line(40, y, width - 40, y)
    y -= 18

    page.setFont("Helvetica", 10)

    def line(label: str, amount_text: str) -> None:
        nonlocal y
        page.drawString(40, y, label)
        page.drawRightString(width - 40, y, amount_text)
        y -= 16

    line(
        f"ReadyPick Intelligence Report Credits "
        f"({purchase.credits_purchased} x {_inr(PRICE_PER_CREDIT_INR)})",
        _inr(purchase.subtotal_inr),
    )
    if purchase.bonus_credits:
        # Rule 3: the bonus is a gift, never a discount — it appears at Rs. 0
        # and the purchased credits above stay at full price.
        line(
            f"Bonus Credits ({purchase.bonus_credits} credits, free)",
            _inr(0),
        )
    if purchase.setup_fee_inr:
        line("Account Setup Fee (one-time)", _inr(purchase.setup_fee_inr))
    elif purchase.setup_fee_waived:
        line("Account Setup Fee (waived, early client)", _inr(0))

    y -= 4
    page.line(300, y, width - 40, y)
    y -= 18
    line("Subtotal", _inr(purchase.subtotal_inr + purchase.setup_fee_inr))
    line(f"GST @ {GST_RATE_PERCENT}%", _inr(purchase.gst_inr))
    page.setFont("Helvetica-Bold", 11)
    line("Grand Total", _inr(purchase.total_inr))

    y -= 24
    page.setFont("Helvetica", 8)
    page.setFillColorRGB(0.35, 0.35, 0.35)
    page.drawString(
        40,
        y,
        "Credits never expire. GST collected is remitted to the government. "
        "This is a system-generated invoice.",
    )
    page.showPage()
    page.save()
    return buffer.getvalue()
