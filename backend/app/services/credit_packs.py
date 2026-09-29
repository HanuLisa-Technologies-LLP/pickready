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
* The Starter Assessment Pack (change request 26) is 75 assessments for
  Rs. 24,000, and it does NOT bend the price rule to get there: it is 40
  credits purchased at Rs. 600 (Rs. 24,000 exactly) plus 35 bonus credits at
  Rs. 0. The invoice therefore prints the same two-line shape `volume_100`
  and `volume_200` already print, and `PRICE_PER_CREDIT_INR` does not move.
* Credits granted by a purchase EXPIRE after `CREDIT_VALIDITY_MONTHS`
  (change request 25), and the term is stamped on the purchase row so a
  re-downloaded invoice states the terms it was actually sold under. NULL on
  every purchase made before that change, which is what keeps the "Credits
  never expire." line true on invoices already issued.
* The ₹5,000 setup fee rides on the first purchase only (Rule 6) and is
  waived while fewer than 15 accounts hold the waiver (§5.1). The count of
  `tenants.setup_fee_waived` IS the §5.1 counter — no separate row to drift.
* Settlement is idempotent: the status flip created→paid happens in the same
  UPDATE that checks it, so the browser-verify call and the webhook can race
  (and duplicate webhooks can replay, §9) and exactly one caller grants.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    CREDIT_PACK_LABELS,
    CREDIT_PACKS,
    CREDIT_VALIDITY_MONTHS,
    EVENT_COMPLETED,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    GST_RATE_PERCENT,
    MIN_PURCHASE_CREDITS,
    PRICE_PER_CREDIT_INR,
    PURCHASE_CREATED,
    PURCHASE_PAID,
    ROLE_NON_STEM,
    ROLE_STEM,
    SETUP_FEE_INR,
    SETUP_FEE_WAIVER_LIMIT,
    STARTER_PACK_SLUG,
    SUBUNITS_PER_CREDIT,
    TRIAL_CREDITS,
    BillingTransaction,
    CreditPurchase,
    consumption_subunits,
)
from app.models.tenant import Tenant
from app.services import credits, razorpay


#: Slug stored on a purchase that came through the custom-amount path rather
#: than a named pack. Deliberately not in CREDIT_PACKS: it has no fixed size.
CUSTOM_SLUG = "custom"

#: The once-per-account trial pack (Rule 1).
TRIAL_PACK_SLUG = "trial_20"


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
    #: The customer-facing name. Resolved here rather than in the client so an
    #: invoice, an email and the billing page cannot call one pack three
    #: things.
    label: str
    credits: int
    bonus_credits: int
    #: What the customer actually receives. The Starter Assessment Pack is
    #: quoted to them as 75 assessments; `credits` is 40 because that is what
    #: is PURCHASED at the fixed rate, and the difference is the bonus.
    credits_total: int
    subtotal_inr: int
    setup_fee_inr: int
    setup_fee_waived: bool
    gst_inr: int
    total_inr: int
    available: bool
    trial: bool
    #: Months of validity on the credits this pack grants. Never None today:
    #: every NEW grant expires (change request 25). It is serialised so the
    #: purchase page states the term before the customer pays rather than
    #: after, on the invoice.
    validity_months: int


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
        is_trial = slug == TRIAL_PACK_SLUG
        subtotal, tax, total = _price(credit_count, setup_fee)
        quotes.append(
            PackQuote(
                slug=slug,
                label=CREDIT_PACK_LABELS[slug],
                credits=credit_count,
                bonus_credits=bonus,
                credits_total=credit_count + bonus,
                subtotal_inr=subtotal,
                setup_fee_inr=setup_fee,
                setup_fee_waived=waived,
                gst_inr=tax,
                total_inr=total,
                available=not (is_trial and tenant.trial_used),
                trial=is_trial,
                validity_months=CREDIT_VALIDITY_MONTHS,
            )
        )
    return quotes


# ── The published catalogue (the public /pricing page) ──────────────────────

#: The public name of each billable event, the same words the customer's
#: credit statement uses (`components/billing/credit-statement.tsx`).
CONSUMPTION_LABELS: dict[str, str] = {
    EVENT_COMPLETED: "Assessment completed",
    EVENT_INCOMPLETE: "Assessment started, not finished",
    EVENT_NO_SHOW: "Invitation never opened",
    EVENT_OLD_PROFILE_REVIEW: "Earlier applicant reviewed",
}


@dataclass(frozen=True)
class PublishedPack:
    """One pack at the STANDARD price, for a visitor with no account.

    Deliberately not a `PackQuote`: a quote is priced for one tenant (its
    setup fee, its waiver, whether its trial is used), and a signed-out page
    knows none of that. The setup fee is therefore NOT folded in here; the
    catalogue states it once, as the rule it is.
    """

    slug: str
    label: str
    credits: int
    bonus_credits: int
    credits_total: int
    subtotal_inr: int
    gst_inr: int
    total_inr: int
    #: The trial pack: sold once per account, on the first purchase only.
    new_accounts_only: bool
    validity_months: int


@dataclass(frozen=True)
class ConsumptionRate:
    """What one billable event draws from the pool, by role type.

    In integer SUB-UNITS (60 to a credit), read from the same tables
    `credits.consume` bills from, so a third of a credit is stated exactly
    rather than as a rounded decimal the page would then have to explain.
    """

    event_type: str
    label: str
    non_stem_subunits: int
    stem_subunits: int


@dataclass(frozen=True)
class BonusLevel:
    """A purchase of at least `min_credits` adds `bonus_credits` free."""

    min_credits: int
    bonus_credits: int


@dataclass(frozen=True)
class PublishedCatalogue:
    price_per_credit_inr: int
    gst_rate_percent: int
    subunits_per_credit: int
    credit_validity_months: int
    min_custom_credits: int
    setup_fee_inr: int
    setup_fee_gst_inr: int
    setup_fee_waiver_limit: int
    bonus_levels: tuple[BonusLevel, ...]
    packs: tuple[PublishedPack, ...]
    consumption: tuple[ConsumptionRate, ...]


def published_catalogue() -> PublishedCatalogue:
    """The platform's standard price list, from the same constants and the
    same arithmetic every tenant quote and every invoice uses.

    ONE SOURCE OF TRUTH is the reason this exists (owner spec, section 4.2):
    the public page used to carry its own copy of the packs, which could
    drift from what checkout actually charges. Every figure here comes from
    `models/billing.py` through `_price` and `bonus_for`, the functions the
    purchase path itself calls. Nothing is read from a table because nothing
    about the published price list is tenant state.
    """
    packs = []
    for slug, (credit_count, bonus) in CREDIT_PACKS.items():
        subtotal, tax, total = _price(credit_count, 0)
        packs.append(
            PublishedPack(
                slug=slug,
                label=CREDIT_PACK_LABELS[slug],
                credits=credit_count,
                bonus_credits=bonus,
                credits_total=credit_count + bonus,
                subtotal_inr=subtotal,
                gst_inr=tax,
                total_inr=total,
                new_accounts_only=slug == TRIAL_PACK_SLUG,
                validity_months=CREDIT_VALIDITY_MONTHS,
            )
        )
    # The volume thresholds exactly as `bonus_for` applies them to a custom
    # amount: every named pack size that earns a bonus by that function.
    levels = sorted(
        {
            credit_count
            for credit_count, bonus in CREDIT_PACKS.values()
            if bonus and bonus_for(credit_count) == bonus
        }
    )
    consumption = []
    for event, label in CONSUMPTION_LABELS.items():
        non_stem = consumption_subunits(event, ROLE_NON_STEM)
        stem = consumption_subunits(event, ROLE_STEM)
        assert non_stem is not None and stem is not None  # billable by construction
        consumption.append(
            ConsumptionRate(
                event_type=event,
                label=label,
                non_stem_subunits=non_stem,
                stem_subunits=stem,
            )
        )
    return PublishedCatalogue(
        price_per_credit_inr=PRICE_PER_CREDIT_INR,
        gst_rate_percent=GST_RATE_PERCENT,
        subunits_per_credit=SUBUNITS_PER_CREDIT,
        credit_validity_months=CREDIT_VALIDITY_MONTHS,
        min_custom_credits=MIN_PURCHASE_CREDITS,
        setup_fee_inr=SETUP_FEE_INR,
        setup_fee_gst_inr=gst_inr(SETUP_FEE_INR),
        setup_fee_waiver_limit=SETUP_FEE_WAIVER_LIMIT,
        bonus_levels=tuple(
            BonusLevel(min_credits=level, bonus_credits=bonus_for(level))
            for level in levels
        ),
        packs=tuple(packs),
        consumption=tuple(consumption),
    )


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
        # Rule 2's 50-credit floor is checked on the CUSTOM path only, and the
        # Starter Assessment Pack is the first named pack whose PURCHASED count
        # (40) sits below it. That is not a hole: the floor is about how much a
        # customer may buy at a time, the pack delivers 75 credits, and 75
        # clears 50. Asserted here so the next reader does not "fix" it by
        # adding a check that would take the pack off the page.
        if pack_slug == STARTER_PACK_SLUG:
            assert credit_count + bonus >= MIN_PURCHASE_CREDITS
        # Rule 1: check trial_used on EVERY attempt, not just the first.
        if pack_slug == TRIAL_PACK_SLUG and tenant.trial_used:
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
        credit_validity_months=CREDIT_VALIDITY_MONTHS,
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

    # Both after the COMMIT (CLAUDE.md rule 4). The invoice email renders the
    # PDF from this purchase row and the release re-checks this grant's
    # balance, so a task started before the commit could read neither; and a
    # settlement whose transaction rolls back must announce nothing. They used
    # to be plain dispatches inside a broad `except`, which both fired before
    # the rows existed and hid a programming error as a log line.
    #
    # A lost invoke after the commit is logged by the dispatcher and never
    # fails the settlement. The held-report release is repaired by the hourly
    # `pickready.release_held_assessments` sweep (`workers/schedule.py`); the
    # invoice stays downloadable from the billing page whatever happens to
    # the email.
    from app.workers.dispatch import dispatch_after_commit

    dispatch_after_commit(
        session, "pickready.send_credit_invoice_email", args=[str(purchase.id)]
    )
    # A top-up releases whatever finalisation was held for want of credits.
    dispatch_after_commit(
        session,
        "pickready.release_held_assessments",
        args=[str(purchase.tenant_id)],
    )
    return True


# ── GST invoice PDF (§5.2 / §7.3) ────────────────────────────────────────────

#: Vivekium brand navy for the invoice header (Part 1's palette).
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
    page.drawString(40, height - 55, "Vivekium")
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
        page.drawString(40, y, f"Vivekium GSTIN: {settings.readypick_gstin}")
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
        f"Vivekium Intelligence Report Credits "
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
    # Read from the ROW, never from the constant. An invoice issued before
    # change request 25 carries NULL here and keeps saying what it said when
    # it was issued; printing today's three-month term over a purchase sold as
    # never-expiring would be a tax document contradicting itself.
    validity = purchase.credit_validity_months
    validity_sentence = (
        "Credits never expire."
        if validity is None
        else f"Credits are valid for {validity} months from the date of this invoice."
    )
    page.drawString(
        40,
        y,
        f"{validity_sentence} GST collected is remitted to the government. "
        "This is a system-generated invoice.",
    )
    page.showPage()
    page.save()
    return buffer.getvalue()
