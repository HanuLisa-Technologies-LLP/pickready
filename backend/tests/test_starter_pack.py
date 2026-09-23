"""The Starter Assessment Pack: 75 assessments for Rs. 24,000 (change 26).

The commercial contract and the product's own price rule pull against each
other, and this file is where the resolution is pinned down.

Rs. 24,000 / 75 is Rs. 320. `PRICE_PER_CREDIT_INR` is Rs. 600 and Rule 3 says
it NEVER moves: volume is rewarded with FREE credits, never with a discounted
rate, so the invoice can always show purchased credits at the headline price
with no discount arithmetic anywhere. Modelling the pack as "75 at Rs. 320"
would have broken that rule for one SKU and left `credit_packs._price` with
two answers to one question.

It is modelled instead as 40 credits PURCHASED at Rs. 600 (Rs. 24,000 exactly)
plus 35 BONUS credits at Rs. 0. The customer receives 75 for Rs. 24,000, which
is the contract; the invoice prints the same two-line shape `volume_100`
already prints; the constant does not move.

The naming hazard is pinned too. `pricing_plans` already has a SUBSCRIPTION
plan whose slug is `starter` (50 applications, Rs. 10,000), so a top-up called
"Starter" would be two different products with one name on one billing page.
"""
from __future__ import annotations

import re
import uuid

import pytest

from app.models.billing import (
    CREDIT_PACK_LABELS,
    CREDIT_PACKS,
    CREDIT_VALIDITY_MONTHS,
    PRICE_PER_CREDIT_INR,
    STARTER_PACK_BONUS_CREDITS,
    STARTER_PACK_CREDITS_PURCHASED,
    STARTER_PACK_PRICE_INR,
    STARTER_PACK_SLUG,
    STARTER_PACK_TOTAL_CREDITS,
    SUBUNITS_PER_CREDIT,
    CreditPurchase,
)
from app.services import credit_packs, credits, razorpay
from tests.test_billing import _factory_or_skip, _tenant


# ── Arithmetic, no database ─────────────────────────────────────────────────

def test_the_pack_delivers_seventy_five_for_twenty_four_thousand() -> None:
    assert STARTER_PACK_TOTAL_CREDITS == 75
    assert STARTER_PACK_PRICE_INR == 24_000
    assert STARTER_PACK_CREDITS_PURCHASED == 40
    assert STARTER_PACK_BONUS_CREDITS == 35
    assert CREDIT_PACKS[STARTER_PACK_SLUG] == (40, 35)
    # The identity the whole modelling decision rests on: the headline price is
    # exactly the purchased credits at the UNCHANGED rate.
    assert STARTER_PACK_CREDITS_PURCHASED * PRICE_PER_CREDIT_INR == 24_000


def test_the_price_per_credit_did_not_move_to_accommodate_the_pack() -> None:
    """Rule 3, restated here because this pack is the first thing that ever
    made moving it look attractive. Rs. 24,000 / 75 = Rs. 320; if that number
    ever becomes the constant, every other pack silently reprices."""
    assert PRICE_PER_CREDIT_INR == 600
    assert STARTER_PACK_PRICE_INR // STARTER_PACK_CREDITS_PURCHASED == 600


def test_the_pack_invoices_twenty_four_thousand_plus_gst() -> None:
    subtotal, gst, total = credit_packs._price(STARTER_PACK_CREDITS_PURCHASED, 0)
    assert subtotal == 24_000
    assert gst == 4_320
    assert total == 28_320


def test_the_label_cannot_be_confused_with_the_starter_subscription_plan() -> None:
    """`pricing_plans.slug = 'starter'` is a MONTHLY PLAN. The top-up's label
    must not read as that plan's name on a page that shows both."""
    label = CREDIT_PACK_LABELS[STARTER_PACK_SLUG]
    assert label != "Starter"
    assert "Starter Assessment Pack" in label
    assert str(STARTER_PACK_TOTAL_CREDITS) in label
    # Every pack has a label, or the purchase page falls back to a raw slug.
    assert set(CREDIT_PACK_LABELS) == set(CREDIT_PACKS)


def test_the_volume_bonus_ladder_is_untouched_by_the_pack() -> None:
    """`bonus_for` serves the CUSTOM path and must not learn about this pack:
    a 40-credit custom purchase is refused by the 50 minimum, and a 75-credit
    custom purchase must still earn zero bonus, not 35."""
    assert credit_packs.bonus_for(40) == 0
    assert credit_packs.bonus_for(75) == 0
    assert credit_packs.bonus_for(100) == 5
    assert credit_packs.bonus_for(200) == 15


# ── Against a real database ─────────────────────────────────────────────────

async def _stub_order(monkeypatch) -> None:
    async def _create_order(**kwargs):
        return {"id": f"order_{uuid.uuid4().hex[:12]}"}

    monkeypatch.setattr(razorpay, "create_order", _create_order)


@pytest.mark.asyncio
async def test_the_pack_grants_exactly_seventy_five_credits(monkeypatch) -> None:
    from sqlalchemy import text

    from app.models.tenant import Tenant

    await _stub_order(monkeypatch)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            # The setup fee is not what this test is about; take it out of the
            # arithmetic so a failure here can only mean the pack is wrong.
            await session.execute(
                text("UPDATE tenants SET setup_fee_paid = TRUE WHERE id = :id"),
                {"id": str(tenant_id)},
            )
            await session.commit()
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None

            purchase = await credit_packs.create_purchase(
                session, tenant, None, pack_slug=STARTER_PACK_SLUG
            )
            assert purchase.credits_purchased == 40
            assert purchase.bonus_credits == 35
            assert purchase.subtotal_inr == 24_000
            assert purchase.setup_fee_inr == 0
            assert purchase.gst_inr == 4_320
            assert purchase.total_inr == 28_320
            # Sold under the three-month term, and the term is STORED so a
            # re-download years from now prints what was agreed today.
            assert purchase.credit_validity_months == CREDIT_VALIDITY_MONTHS

            assert await credit_packs.settle_purchase(
                session, purchase, "pay_starter_test"
            )
            await session.commit()

            assert await credits.balance_subunits(session, tenant_id) == (
                75 * SUBUNITS_PER_CREDIT
            )
            # And all 75 sit in ONE expiring lot, not 40 expiring plus 35 free
            # forever: the bonus is part of the same purchase.
            from app.services import credit_lots

            lots = await credit_lots.live_lots(session, tenant_id)
            assert len(lots) == 1
            assert lots[0].remaining_subunits == 75 * SUBUNITS_PER_CREDIT
            assert lots[0].expires_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_invoice_prints_forty_at_full_price_and_the_bonus_at_zero() -> None:
    """The document a customer and a tax officer both read.

    Asserted against the extracted text rather than against the numbers that
    produced it: the point is what is PRINTED, and a renderer that computed
    correctly and drew the wrong line would pass any assertion on the row.
    """
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    from pypdf import PdfReader
    from io import BytesIO

    from app.models.tenant import Tenant

    purchase = CreditPurchase(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        pack_slug=STARTER_PACK_SLUG,
        credits_purchased=40,
        bonus_credits=35,
        subtotal_inr=24_000,
        setup_fee_inr=0,
        setup_fee_waived=False,
        gst_inr=4_320,
        total_inr=28_320,
        status="paid",
        invoice_number="RP-2026-000123",
        credit_validity_months=CREDIT_VALIDITY_MONTHS,
    )
    tenant = Tenant(id=purchase.tenant_id, name="Pack Test Pvt Ltd", domain="x.test")
    pdf = credit_packs.render_invoice_pdf(purchase, tenant)
    text = " ".join(PdfReader(BytesIO(pdf)).pages[0].extract_text().split())

    assert "40 x Rs. 600" in text
    assert "Rs. 24,000" in text
    # The bonus is a GIFT, on its own line, at zero. Never a discount applied
    # to the line above it.
    assert re.search(r"Bonus Credits \(35 credits, free\)\s*Rs\. 0", text)
    assert "Rs. 4,320" in text
    assert "Rs. 28,320" in text
    # There is no Rs. 320 anywhere: no per-credit rate other than 600 is ever
    # printed, which is the whole point of the bonus modelling.
    assert "Rs. 320" not in text
    # And the validity is stated, from the stored row.
    assert "valid for 3 months" in text


def test_an_invoice_issued_before_the_change_still_says_credits_never_expire() -> None:
    """The promise that constrained the whole design, as a rendered document.

    `credit_validity_months IS NULL` is every purchase made before change
    request 25. Re-downloading one of those invoices must print what it printed
    when it was issued, not today's three-month term.
    """
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    from pypdf import PdfReader
    from io import BytesIO

    from app.models.tenant import Tenant

    purchase = CreditPurchase(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        pack_slug="standard_50",
        credits_purchased=50,
        bonus_credits=0,
        subtotal_inr=30_000,
        setup_fee_inr=0,
        setup_fee_waived=False,
        gst_inr=5_400,
        total_inr=35_400,
        status="paid",
        invoice_number="RP-2026-000001",
        credit_validity_months=None,
    )
    tenant = Tenant(id=purchase.tenant_id, name="Older Customer", domain="y.test")
    pdf = credit_packs.render_invoice_pdf(purchase, tenant)
    text = " ".join(PdfReader(BytesIO(pdf)).pages[0].extract_text().split())

    assert "Credits never expire." in text
    assert "valid for" not in text


@pytest.mark.asyncio
async def test_the_pack_is_quoted_to_every_tenant_with_its_delivered_total() -> None:
    from sqlalchemy import text

    from app.models.tenant import Tenant

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            await session.commit()
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None

            quotes = {quote.slug: quote for quote in await credit_packs.quote(
                session, tenant
            )}
            pack = quotes[STARTER_PACK_SLUG]
            assert pack.available
            assert pack.credits == 40
            assert pack.bonus_credits == 35
            assert pack.credits_total == 75
            assert pack.subtotal_inr == 24_000
            assert pack.validity_months == CREDIT_VALIDITY_MONTHS
            assert pack.label == CREDIT_PACK_LABELS[STARTER_PACK_SLUG]
            # The existing SKUs and the custom path are untouched. Removing one
            # is a commercial decision nobody made.
            assert {"trial_20", "standard_50", "volume_100", "volume_200"} <= set(
                quotes
            )
    finally:
        await engine.dispose()
