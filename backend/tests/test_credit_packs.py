"""Credit-pack purchases (Master Directive Part 5 — Pricing Model).

The Part 5 §10 acceptance checklist rows this file owns, verbatim:

  * First purchase of 20 credits succeeds with setup fee applied (if not
    waived)
  * Second purchase attempt of 20 credits is blocked — minimum 50 enforced
  * Purchase of 50 credits: correct amount, correct GST, credits added
  * Purchase of 100 credits: 105 added, invoice shows 100 at ₹60,000 + GST
  * Purchase of 200 credits: 215 added, invoice shows 200 at ₹1,20,000 + GST
  * Duplicate webhook/settle: credits not doubled, idempotency confirmed
  * First 15 accounts: fee waived, setup_fee_paid AND setup_fee_waived TRUE
  * Setup fee never charged again after setup_fee_paid = TRUE
  * The §5.2 worked example: 50 credits + setup fee → ₹41,300 grand total

Same convention as test_billing.py / test_stem_credit_rates.py: the
pure-arithmetic tests always run; the ledger tests skip cleanly without the
containerised database. Razorpay is never hit — `create_order` is stubbed.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

from app.models.billing import (
    CREDIT_PACKS,
    GST_RATE_PERCENT,
    MIN_PURCHASE_CREDITS,
    PRICE_PER_CREDIT_INR,
    SETUP_FEE_INR,
    SETUP_FEE_WAIVER_LIMIT,
    SUBUNITS_PER_CREDIT,
    TRIAL_CREDITS,
    CreditPurchase,
)
from app.services import credit_packs, credits, razorpay
from tests.test_billing import _factory_or_skip, _tenant


# ── Constants and pack table (always runs) ──────────────────────────────────

def test_pricing_constants_match_the_directive() -> None:
    """Part 5 §1's table, restated independently of the constants file."""
    assert PRICE_PER_CREDIT_INR == 600
    assert GST_RATE_PERCENT == 18
    assert SETUP_FEE_INR == 5000
    assert SETUP_FEE_WAIVER_LIMIT == 15
    assert TRIAL_CREDITS == 20
    assert MIN_PURCHASE_CREDITS == 50
    # §3.2's pack table verbatim: (credits purchased, bonus credits).
    assert CREDIT_PACKS["trial_20"] == (20, 0)
    assert CREDIT_PACKS["standard_50"] == (50, 0)
    assert CREDIT_PACKS["volume_100"] == (100, 5)
    assert CREDIT_PACKS["volume_200"] == (200, 15)


def test_bonus_levels_are_free_credits_not_discounts():
    """Rule 3: bonus by level reached, and never below 100."""
    assert credit_packs.bonus_for(50) == 0
    assert credit_packs.bonus_for(99) == 0
    assert credit_packs.bonus_for(100) == 5
    assert credit_packs.bonus_for(150) == 5
    assert credit_packs.bonus_for(200) == 15
    assert credit_packs.bonus_for(500) == 15


def test_gst_and_totals_for_the_standard_packs() -> None:
    """§5.2's per-line figures with no setup fee in play."""
    assert credit_packs._price(50, 0) == (30_000, 5_400, 35_400)
    assert credit_packs._price(20, 0) == (12_000, 2_160, 14_160)
    # Volume packs: the SUBTOTAL is purchased credits at full price — the
    # bonus adds nothing to the invoice (Rule 3).
    assert credit_packs._price(100, 0) == (60_000, 10_800, 70_800)
    assert credit_packs._price(200, 0) == (120_000, 21_600, 141_600)


def test_the_section_5_2_worked_example_totals_41300() -> None:
    """50 credits + setup fee: ₹30,000 + ₹5,000 = ₹35,000, GST ₹6,300,
    grand total ₹41,300 — the §5.2 invoice table, to the rupee."""
    subtotal, gst, total = credit_packs._price(50, SETUP_FEE_INR)
    assert subtotal == 30_000
    assert gst == 6_300
    assert total == 41_300
    # GST decomposes exactly as the table shows: 5,400 on credits + 900 on fee.
    assert credit_packs.gst_inr(30_000) == 5_400
    assert credit_packs.gst_inr(5_000) == 900


def test_order_signature_signs_order_then_payment(monkeypatch) -> None:
    """Orders sign `order|payment` — the REVERSE of the subscription flow's
    `payment|subscription`. Same stakes as test_billing.py's twin: backwards
    fails 100% of real payments and nothing else."""
    import hashlib
    import hmac

    secret = "test-secret"
    monkeypatch.setattr(
        razorpay,
        "config",
        lambda: razorpay.RazorpayConfig(
            key_id="rzp_test_x", key_secret=secret, webhook_secret=""
        ),
    )
    order, payment = "order_ABC", "pay_XYZ"
    correct = hmac.new(
        secret.encode(), f"{order}|{payment}".encode(), hashlib.sha256
    ).hexdigest()
    reversed_order = hmac.new(
        secret.encode(), f"{payment}|{order}".encode(), hashlib.sha256
    ).hexdigest()
    assert razorpay.verify_order_signature(
        order_id=order, payment_id=payment, signature=correct
    )
    assert not razorpay.verify_order_signature(
        order_id=order, payment_id=payment, signature=reversed_order
    )


def test_invoice_pdf_renders_from_the_stored_row() -> None:
    """The §7.3 PDF is generated from the purchase row alone — no DB, no
    gateway — so a download years later reproduces the original figures."""
    from datetime import datetime, timezone

    from app.models.tenant import Tenant

    purchase = CreditPurchase(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), pack_slug="volume_100",
        credits_purchased=100, bonus_credits=5, subtotal_inr=60_000,
        setup_fee_inr=5_000, setup_fee_waived=False, gst_inr=11_700,
        total_inr=76_700, status="paid", invoice_number="RP-2026-000042",
        paid_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    tenant = Tenant(
        id=purchase.tenant_id, name="Acme Hiring Pvt Ltd",
        domain="acme.example", gstin="29ABCDE1234F1Z5",
    )
    pdf = credit_packs.render_invoice_pdf(purchase, tenant)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500


# ── Purchase flow against a real database ───────────────────────────────────

def _stub_orders(monkeypatch) -> None:
    """Never hit the network: every order is a deterministic-enough fake."""
    async def fake_create_order(*, amount_paise, receipt, notes=None):
        return {
            "id": f"order_TEST{uuid.uuid4().hex[:14]}",
            "amount": amount_paise,
            "receipt": receipt,
        }

    monkeypatch.setattr(razorpay, "create_order", fake_create_order)


async def _bypass_rls(session) -> None:
    from sqlalchemy import text

    await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))


async def _load_tenant(session, tenant_id):
    from sqlalchemy import select

    from app.models.tenant import Tenant

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalars().first()
    # The settle path updates tenants with raw SQL; drop any stale ORM copy.
    if tenant is not None:
        await session.refresh(tenant)
    return tenant


async def _fill_waiver_quota(session) -> None:
    """Insert enough waived tenants that §5.1's 15-account window is closed."""
    from sqlalchemy import text

    for _ in range(SETUP_FEE_WAIVER_LIMIT):
        marker = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, domain, spf_dkim_status, setup_fee_waived) "
                "VALUES (:id, :name, :domain, 'pending', TRUE)"
            ),
            {"id": str(marker), "name": f"Waived {marker.hex[:8]}",
             "domain": f"{marker.hex[:12]}.waived.test"},
        )


@pytest.mark.asyncio
async def test_trial_purchase_charges_setup_fee_and_flips_the_flags(monkeypatch) -> None:
    """§10 row one: the 20-credit trial succeeds WITH the ₹5,000 fee when the
    waiver window is closed, grants exactly 20 credits, and stamps
    trial_used + setup_fee_paid."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            await _fill_waiver_quota(session)
            tenant_id = await _tenant(session)
            tenant = await _load_tenant(session, tenant_id)

            fee, waived = await credit_packs.setup_fee_for(session, tenant)
            assert (fee, waived) == (SETUP_FEE_INR, False)

            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="trial_20"
            )
            assert purchase.credits_purchased == 20
            assert purchase.bonus_credits == 0
            assert purchase.subtotal_inr == 12_000
            assert purchase.setup_fee_inr == 5_000
            assert purchase.gst_inr == credit_packs.gst_inr(17_000)  # 3,060
            assert purchase.total_inr == 20_060
            assert purchase.status == "created"
            # Nothing granted before payment (§3.3 step 5 / §9 failed row).
            assert await credits.balance_subunits(session, tenant_id) == 0

            assert await credit_packs.settle_purchase(session, purchase, "pay_T1")
            assert (
                await credits.balance_subunits(session, tenant_id)
                == 20 * SUBUNITS_PER_CREDIT
            )
            tenant = await _load_tenant(session, tenant_id)
            assert tenant.trial_used is True
            assert tenant.setup_fee_paid is True
            assert tenant.setup_fee_waived is False
            assert re.fullmatch(r"RP-\d{4}-\d{6}", purchase.invoice_number)
            assert purchase.paid_at is not None
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_second_trial_attempt_and_sub_50_customs_are_blocked(monkeypatch) -> None:
    """Rule 1 checked on EVERY attempt, Rule 2's hard floor on custom."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            tenant_id = await _tenant(session)
            tenant = await _load_tenant(session, tenant_id)

            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="trial_20"
            )
            assert await credit_packs.settle_purchase(session, purchase, "pay_T2")

            tenant = await _load_tenant(session, tenant_id)
            with pytest.raises(ValueError, match="once per"):
                await credit_packs.create_purchase(
                    session, tenant, None, pack_slug="trial_20"
                )
            # A custom 20 is not a back door into a second trial.
            with pytest.raises(ValueError, match="minimum purchase is 50"):
                await credit_packs.create_purchase(
                    session, tenant, None, custom_credits=20
                )
            with pytest.raises(ValueError, match="minimum purchase is 50"):
                await credit_packs.create_purchase(
                    session, tenant, None, custom_credits=49
                )
            # 50 exactly is fine.
            ok = await credit_packs.create_purchase(
                session, tenant, None, custom_credits=50
            )
            assert ok.credits_purchased == 50
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fifty_credit_purchase_amounts_and_grant(monkeypatch) -> None:
    """§10 row three: ₹30,000 + ₹5,400 GST once no setup fee is owed, and the
    50 credits land in the ledger."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            tenant_id = await _tenant(session)
            tenant = await _load_tenant(session, tenant_id)
            # First purchase already behind them (fee settled or waived).
            first = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="trial_20"
            )
            assert await credit_packs.settle_purchase(session, first, "pay_F1")
            tenant = await _load_tenant(session, tenant_id)

            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="standard_50"
            )
            assert purchase.subtotal_inr == 30_000
            assert purchase.setup_fee_inr == 0
            assert purchase.gst_inr == 5_400
            assert purchase.total_inr == 35_400
            assert await credit_packs.settle_purchase(session, purchase, "pay_F2")
            # §9 first row: 50 on top of the existing balance, no minimum on
            # the resulting balance — 20 + 50 = 70 credits.
            assert (
                await credits.balance_subunits(session, tenant_id)
                == 70 * SUBUNITS_PER_CREDIT
            )
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_volume_packs_grant_bonus_but_invoice_full_price(monkeypatch) -> None:
    """§10 rows four and five: 100 → 105 credits with a ₹60,000 subtotal,
    200 → 215 with ₹1,20,000. The bonus is in the LEDGER, never the price."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            for slug, credit_count, bonus, subtotal in (
                ("volume_100", 100, 5, 60_000),
                ("volume_200", 200, 15, 120_000),
            ):
                tenant_id = await _tenant(session)
                tenant = await _load_tenant(session, tenant_id)
                tenant.trial_used = True  # not the account's first purchase
                purchase = await credit_packs.create_purchase(
                    session, tenant, None, pack_slug=slug
                )
                assert purchase.credits_purchased == credit_count
                assert purchase.bonus_credits == bonus
                assert purchase.subtotal_inr == subtotal
                assert await credit_packs.settle_purchase(
                    session, purchase, f"pay_{slug}"
                )
                assert (
                    await credits.balance_subunits(session, tenant_id)
                    == (credit_count + bonus) * SUBUNITS_PER_CREDIT
                )
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_settlement_grants_nothing_twice(monkeypatch) -> None:
    """§9's duplicate-webhook row: the second settle of the same order loses
    the status-flip race, returns False, and the balance is unchanged."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            tenant_id = await _tenant(session)
            tenant = await _load_tenant(session, tenant_id)
            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="trial_20"
            )
            assert await credit_packs.settle_purchase(session, purchase, "pay_D1")
            balance = await credits.balance_subunits(session, tenant_id)
            assert balance == 20 * SUBUNITS_PER_CREDIT

            # The webhook redelivers: same purchase, same order id.
            assert not await credit_packs.settle_purchase(session, purchase, "pay_D1")
            assert await credits.balance_subunits(session, tenant_id) == balance
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_waiver_window_waives_and_the_fee_is_never_charged_twice(monkeypatch) -> None:
    """§10 setup-fee rows: inside the first 15 accounts the fee is waived and
    BOTH flags go TRUE; once setup_fee_paid is TRUE no later purchase carries
    a fee (Rule 6)."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            tenant_id = await _tenant(session)
            tenant = await _load_tenant(session, tenant_id)

            fee, waived = await credit_packs.setup_fee_for(session, tenant)
            assert (fee, waived) == (0, True)

            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="trial_20"
            )
            assert purchase.setup_fee_inr == 0
            assert purchase.setup_fee_waived is True
            assert purchase.total_inr == 14_160  # 12,000 + 2,160 GST, no fee
            assert await credit_packs.settle_purchase(session, purchase, "pay_W1")
            tenant = await _load_tenant(session, tenant_id)
            assert tenant.setup_fee_paid is True
            assert tenant.setup_fee_waived is True

            # Rule 6: even with the waiver window now closed, this account is
            # never charged — setup_fee_paid wins before the count is asked.
            await _fill_waiver_quota(session)
            fee, waived = await credit_packs.setup_fee_for(session, tenant)
            assert (fee, waived) == (0, False)
            second = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="standard_50"
            )
            assert second.setup_fee_inr == 0
            assert second.total_inr == 35_400
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_quote_lists_every_pack_and_hides_a_spent_trial(monkeypatch) -> None:
    """§7.2: the trial card is unavailable after first use; every quote folds
    in the live setup-fee treatment."""
    _stub_orders(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass_rls(session)
            tenant_id = await _tenant(session)
            tenant = await _load_tenant(session, tenant_id)

            quotes = {q.slug: q for q in await credit_packs.quote(session, tenant)}
            assert set(quotes) == set(CREDIT_PACKS)
            assert quotes["trial_20"].available is True
            assert quotes["trial_20"].trial is True
            assert quotes["standard_50"].trial is False
            # Waiver window open: no fee on any quote.
            assert all(q.setup_fee_inr == 0 for q in quotes.values())
            assert all(q.setup_fee_waived for q in quotes.values())

            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug="trial_20"
            )
            assert await credit_packs.settle_purchase(session, purchase, "pay_Q1")
            tenant = await _load_tenant(session, tenant_id)
            quotes = {q.slug: q for q in await credit_packs.quote(session, tenant)}
            assert quotes["trial_20"].available is False
            assert all(q.setup_fee_inr == 0 for q in quotes.values())
            await session.rollback()
    finally:
        await engine.dispose()
