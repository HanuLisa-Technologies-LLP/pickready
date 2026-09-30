"""Monthly price contract and completed-assessment accounting."""
from __future__ import annotations

import hashlib
import hmac

from app.models.billing import (
    EVENT_COMPLETED, EVENT_INCOMPLETE, EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW, ROLE_NON_STEM, ROLE_STEM,
    STARTER_PACK_SLUG, SUBUNITS_PER_CREDIT, CREDIT_PACKS,
    consumption_subunits,
)
from app.services.monthly_plans import PLANS
from app.services import razorpay
import pytest
from sqlalchemy import select, text
from app.models.billing import BillingTransaction
from app.models.tenant import Tenant
from app.services import credits, monthly_plans
from tests.test_billing import _factory_or_skip, _tenant


def test_four_monthly_prices_and_allowances() -> None:
    assert [(p.slug, p.assessments, p.price_inr) for p in PLANS] == [
        ("starter", 75, 24_000),
        ("growth", 200, 55_000),
        ("scale", 500, 120_000),
        ("pro", 1_200, 240_000),
    ]
    assert [p.rollover_months for p in PLANS] == [3] * 4
    assert [p.total_inr for p in PLANS] == [28_320, 64_900, 141_600, 283_200]


def test_a_credit_always_buys_one_completed_assessment() -> None:
    for role in (ROLE_NON_STEM, ROLE_STEM):
        assert consumption_subunits(EVENT_COMPLETED, role) == SUBUNITS_PER_CREDIT
        for event in (EVENT_INCOMPLETE, EVENT_NO_SHOW, EVENT_OLD_PROFILE_REVIEW):
            assert consumption_subunits(event, role) is None


def test_starter_is_the_only_top_up() -> None:
    assert set(CREDIT_PACKS) == {STARTER_PACK_SLUG}
    purchased, bonus = CREDIT_PACKS[STARTER_PACK_SLUG]
    assert purchased + bonus == 75


def test_subscription_signature_uses_payment_then_subscription(monkeypatch) -> None:
    monkeypatch.setattr(razorpay, "config", lambda: razorpay.RazorpayConfig("key", "secret", "webhook"))
    message = "pay_123|sub_456"
    signature = hmac.new(b"secret", message.encode(), hashlib.sha256).hexdigest()
    assert razorpay.verify_subscription_signature(
        subscription_id="sub_456", payment_id="pay_123", signature=signature,
    )
    assert not razorpay.verify_subscription_signature(
        subscription_id="sub_456", payment_id="pay_123", signature="bad",
    )


@pytest.mark.asyncio
async def test_captured_monthly_charge_grants_once_and_records_invoice() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            tenant_id = await _tenant(session)
            tenant = await session.get(Tenant, tenant_id)
            tenant.razorpay_subscription_id = f"sub_{tenant_id.hex[:12]}"
            tenant.current_plan_slug = "starter"
            assert await monthly_plans.settle_charge(
                session, tenant=tenant, subscription_id=tenant.razorpay_subscription_id,
                payment_id=f"pay_{tenant_id.hex[:12]}", amount_inr=28_320,
            )
            assert not await monthly_plans.settle_charge(
                session, tenant=tenant, subscription_id=tenant.razorpay_subscription_id,
                payment_id=f"pay_{tenant_id.hex[:12]}", amount_inr=28_320,
            )
            await session.flush()
            assert await credits.balance_subunits(session, tenant_id) == 75 * SUBUNITS_PER_CREDIT
            charges = (await session.execute(select(BillingTransaction).where(
                BillingTransaction.tenant_id == tenant_id,
                BillingTransaction.transaction_type == "subscription_charge",
            ))).scalars().all()
            assert len(charges) == 1
            assert charges[0].invoice_number.startswith("RP-")
            assert charges[0].subtotal_inr == 24_000
            assert charges[0].gst_inr == 4_320
            await session.rollback()
    finally:
        await engine.dispose()
