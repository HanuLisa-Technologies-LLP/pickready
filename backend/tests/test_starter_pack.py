"""Starter is the only top-up under the monthly pricing contract."""
from __future__ import annotations

import uuid
from io import BytesIO

import pytest

from app.models.billing import (
    CREDIT_PACKS, CREDIT_PACK_LABELS, CREDIT_VALIDITY_MONTHS,
    STARTER_PACK_SLUG, STARTER_PACK_PRICE_INR, CreditPurchase,
)
from app.models.tenant import Tenant
from app.services import credit_packs


def test_only_starter_top_up_is_sold() -> None:
    assert CREDIT_PACKS == {STARTER_PACK_SLUG: (75, 0)}
    assert STARTER_PACK_PRICE_INR == 24_000
    assert "75 assessments" in CREDIT_PACK_LABELS[STARTER_PACK_SLUG]
    assert credit_packs._price(75, 0) == (24_000, 4_320, 28_320)
    with pytest.raises(ValueError, match="Only the Starter"):
        credit_packs._price(100, 0)


def test_new_top_up_invoice_shows_bundle_without_a_unit_rate() -> None:
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    purchase = CreditPurchase(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        pack_slug=STARTER_PACK_SLUG, credits_purchased=75, bonus_credits=0,
        subtotal_inr=24_000, setup_fee_inr=0, setup_fee_waived=False,
        gst_inr=4_320, total_inr=28_320, status="paid",
        invoice_number="RP-2026-000123", credit_validity_months=CREDIT_VALIDITY_MONTHS,
    )
    tenant = Tenant(id=purchase.tenant_id, name="Pack Test", domain="x.test")
    text = " ".join(pypdf.PdfReader(BytesIO(
        credit_packs.render_invoice_pdf(purchase, tenant)
    )).pages[0].extract_text().split())
    assert "Starter top-up: 75 completed assessments" in text
    assert "Rs. 24,000" in text and "Rs. 4,320" in text
    assert " x Rs." not in text
    assert "valid for 3 months" in text


def test_historical_invoice_preserves_its_original_term() -> None:
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    purchase = CreditPurchase(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), pack_slug="standard_50",
        credits_purchased=50, bonus_credits=0, subtotal_inr=30_000,
        setup_fee_inr=0, setup_fee_waived=False, gst_inr=5_400,
        total_inr=35_400, status="paid", invoice_number="RP-2026-000001",
        credit_validity_months=None,
    )
    tenant = Tenant(id=purchase.tenant_id, name="Older Customer", domain="y.test")
    text = " ".join(pypdf.PdfReader(BytesIO(
        credit_packs.render_invoice_pdf(purchase, tenant)
    )).pages[0].extract_text().split())
    assert "Credits never expire." in text
