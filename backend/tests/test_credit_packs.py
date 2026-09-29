"""Starter-only top-up checkout, tax and idempotent settlement."""
from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest
from sqlalchemy import text

from app.models.billing import CREDIT_PACKS, GST_RATE_PERCENT, STARTER_PACK_SLUG, SUBUNITS_PER_CREDIT
from app.models.tenant import Tenant
from app.services import credit_packs, credits, razorpay
from tests.test_billing import _factory_or_skip, _tenant


def test_current_top_up_contract() -> None:
    assert CREDIT_PACKS == {STARTER_PACK_SLUG: (75, 0)}
    assert GST_RATE_PERCENT == 18
    assert credit_packs._price(75, 0) == (24_000, 4_320, 28_320)
    with pytest.raises(ValueError, match="Only the Starter"):
        credit_packs._price(50, 0)


def test_order_signature_signs_order_then_payment(monkeypatch) -> None:
    monkeypatch.setattr(razorpay, "config", lambda: razorpay.RazorpayConfig("key", "secret", "webhook"))
    signature = hmac.new(b"secret", b"order_1|pay_1", hashlib.sha256).hexdigest()
    assert razorpay.verify_order_signature(order_id="order_1", payment_id="pay_1", signature=signature)
    assert not razorpay.verify_order_signature(order_id="order_1", payment_id="pay_1", signature="bad")


@pytest.mark.asyncio
async def test_duplicate_settlement_grants_only_one_starter_top_up(monkeypatch) -> None:
    async def create_order(**_kwargs):
        return {"id": f"order_{uuid.uuid4().hex[:12]}"}
    monkeypatch.setattr(razorpay, "create_order", create_order)
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            tenant_id = await _tenant(session)
            tenant = await session.get(Tenant, tenant_id)
            purchase = await credit_packs.create_purchase(session, tenant, None, pack_slug=STARTER_PACK_SLUG)
            assert purchase.setup_fee_inr == 0
            assert purchase.credits_purchased == 75
            assert await credit_packs.settle_purchase(session, purchase, "pay_D1")
            assert not await credit_packs.settle_purchase(session, purchase, "pay_D1")
            assert await credits.balance_subunits(session, tenant_id) == 75 * SUBUNITS_PER_CREDIT
            await session.rollback()
    finally:
        await engine.dispose()
