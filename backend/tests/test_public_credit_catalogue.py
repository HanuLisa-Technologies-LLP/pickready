"""The published credit price list: one source of truth, served without a session.

Owner spec 2026-09-29, section 4.2: the public /pricing page must not carry a
second copy of the catalogue that can drift from `credit_packs.py`. So the page
renders `GET /billing/public/credit-packs`, and this file pins three things:

* the route is PUBLIC (no cookie, no session) and discloses nothing an
  account owns: no tenant is read, so no setup-fee waiver count and no trial
  state can reach it;
* every figure it serves is the figure the purchase path charges: each pack's
  subtotal, GST and total equal `credit_packs._price` for the same credits,
  and the constants come from `models/billing.py`, never restated;
* the consumption rates are the integer sub-units `credits.consume` bills,
  so a third of a credit is exact rather than a rounded decimal.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.billing import (
    CREDIT_PACK_LABELS,
    CREDIT_PACKS,
    CREDIT_VALIDITY_MONTHS,
    EVENT_COMPLETED,
    EVENT_INCOMPLETE,
    GST_RATE_PERCENT,
    MIN_PURCHASE_CREDITS,
    PRICE_PER_CREDIT_INR,
    ROLE_NON_STEM,
    ROLE_STEM,
    SETUP_FEE_INR,
    SETUP_FEE_WAIVER_LIMIT,
    SUBUNITS_PER_CREDIT,
    consumption_subunits,
)
from app.services import credit_packs

URL = "/api/v1/billing/public/credit-packs"


@pytest.fixture
def client() -> TestClient:
    # No `with`: the lifespan is not needed for a route that reads no table,
    # and not starting it proves the answer needs no database at all.
    return TestClient(app)


def test_it_answers_without_a_session(client: TestClient) -> None:
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "public, max-age=300"


def test_every_pack_is_priced_by_the_purchase_arithmetic(client: TestClient) -> None:
    body = client.get(URL).json()
    assert body["price_per_credit_inr"] == PRICE_PER_CREDIT_INR
    assert body["gst_rate_percent"] == GST_RATE_PERCENT
    assert body["subunits_per_credit"] == SUBUNITS_PER_CREDIT
    assert body["credit_validity_months"] == CREDIT_VALIDITY_MONTHS
    assert body["min_custom_credits"] == MIN_PURCHASE_CREDITS

    served = {pack["slug"]: pack for pack in body["packs"]}
    assert list(served) == list(CREDIT_PACKS), "every pack, in catalogue order"
    for slug, (credits, bonus) in CREDIT_PACKS.items():
        pack = served[slug]
        subtotal, gst, total = credit_packs._price(credits, 0)
        assert pack["label"] == CREDIT_PACK_LABELS[slug]
        assert (pack["credits"], pack["bonus_credits"]) == (credits, bonus)
        assert pack["credits_total"] == credits + bonus
        assert (pack["subtotal_inr"], pack["gst_inr"], pack["total_inr"]) == (
            subtotal,
            gst,
            total,
        )
        # Rule 3: bonus credits are a gift, never a discount.
        assert pack["subtotal_inr"] == credits * PRICE_PER_CREDIT_INR
        assert pack["validity_months"] == CREDIT_VALIDITY_MONTHS
        assert pack["new_accounts_only"] is (slug == credit_packs.TRIAL_PACK_SLUG)


def test_the_setup_fee_is_stated_as_its_rule_never_as_an_account_state(
    client: TestClient,
) -> None:
    body = client.get(URL).json()
    assert body["setup_fee_inr"] == SETUP_FEE_INR
    assert body["setup_fee_gst_inr"] == credit_packs.gst_inr(SETUP_FEE_INR)
    assert body["setup_fee_waiver_limit"] == SETUP_FEE_WAIVER_LIMIT
    # Nothing account-shaped: no live waiver count, no trial state, no tenant.
    flat = str(body).lower()
    for account_field in ("trial_used", "setup_fee_waived", "tenant", "available"):
        assert account_field not in flat, account_field
    # The published pack is the STANDARD price: the fee is never folded in.
    for pack in body["packs"]:
        assert "setup_fee_inr" not in pack


def test_the_bonus_levels_are_the_ones_a_custom_purchase_earns(
    client: TestClient,
) -> None:
    levels = client.get(URL).json()["bonus_levels"]
    assert levels, "the volume bonus is part of the published price list"
    for level in levels:
        assert credit_packs.bonus_for(level["min_credits"]) == level["bonus_credits"]
        assert credit_packs.bonus_for(level["min_credits"] - 1) < level["bonus_credits"]


def test_the_consumption_rates_are_the_billed_subunits(client: TestClient) -> None:
    rates = {rate["event_type"]: rate for rate in client.get(URL).json()["consumption"]}
    for event in (EVENT_COMPLETED, EVENT_INCOMPLETE):
        assert rates[event]["non_stem_subunits"] == consumption_subunits(
            event, ROLE_NON_STEM
        )
        assert rates[event]["stem_subunits"] == consumption_subunits(event, ROLE_STEM)
    # A completed STEM report is one and a half credits, exactly.
    assert rates[EVENT_COMPLETED]["stem_subunits"] * 2 == SUBUNITS_PER_CREDIT * 3


def test_the_catalogue_is_the_same_object_the_quotes_are_built_from() -> None:
    """The service function the route serialises, asserted directly, so the
    route cannot grow a second copy of the arithmetic."""
    catalogue = credit_packs.published_catalogue()
    assert [pack.slug for pack in catalogue.packs] == list(CREDIT_PACKS)
    assert catalogue.price_per_credit_inr == PRICE_PER_CREDIT_INR
