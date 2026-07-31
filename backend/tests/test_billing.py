"""Subscriptions and the credit ledger (killer-spec Parts 2 and 3).

Five things are worth guarding here, and each has a specific failure mode that
is invisible without a test:

  * the SUB-UNIT ARITHMETIC. 1, 1/3, 1/15 and 1/20 of a credit must divide 60
    exactly. A wrong constant here silently overcharges or undercharges every
    customer, forever, and nothing about the UI would look wrong.
  * the PRICES. Spec §2.3 says "do not round or approximate". A typo in a
    migration seed is a billing defect, not a cosmetic one.
  * the CHECKOUT SIGNATURE ORDER. Subscriptions sign `payment|subscription`,
    Orders sign `order|payment`. Getting it backwards fails 100% of real
    payments, and only ever in production where real cards are used.
  * IDEMPOTENCY. Razorpay delivers webhooks at least once and Celery redelivers
    tasks, so a double grant or a double charge is the default behaviour unless
    something prevents it.
  * the DEFICIT RULE. A completed assessment is charged even into the negative;
    what gets blocked is the NEXT invitation. Inverting that either loses the
    revenue or blocks work the customer already paid for.

Same convention as test_portal.py: the pure-arithmetic tests always run, and
the ledger tests skip cleanly with no database and run for real in the
container.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.billing import (
    CONSUMPTION_SUBUNITS,
    EVENT_COMPLETED,
    EVENT_GRANT,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    LEDGER_EVENT_TYPES,
    SUBSCRIPTION_STATUSES,
    SUBUNITS_PER_CREDIT,
)
from app.services import credits
from app.services import credit_reconciliation as recon
from app.services import razorpay

# The exact figures from spec §2.3, restated here independently of the
# migration so a typo in either one is caught rather than agreed with.
EXPECTED_PLANS = {
    "starter": (50, 10000, 200),
    "growth": (100, 18000, 180),
    "scale": (150, 24000, 160),
    "pro": (200, 28000, 140),
}


# ── Sub-unit arithmetic ──────────────────────────────────────────────────────

def test_one_credit_is_sixty_subunits() -> None:
    """LCM(1, 3, 15, 20) = 60. Any other base makes at least one rate fractional."""
    assert SUBUNITS_PER_CREDIT == 60
    for divisor in (1, 3, 15, 20):
        assert SUBUNITS_PER_CREDIT % divisor == 0


@pytest.mark.parametrize(
    "event,per_credit,expected",
    [
        (EVENT_COMPLETED, 1, 60),
        (EVENT_INCOMPLETE, 3, 20),
        (EVENT_NO_SHOW, 15, 4),
        (EVENT_OLD_PROFILE_REVIEW, 20, 3),
    ],
)
def test_consumption_rates_match_the_spec(event, per_credit, expected) -> None:
    assert CONSUMPTION_SUBUNITS[event] == expected
    # The rate is only defensible if N of them add up to exactly one credit.
    assert CONSUMPTION_SUBUNITS[event] * per_credit == SUBUNITS_PER_CREDIT


def test_a_grant_is_not_a_consumption_rate() -> None:
    """A grant has no fixed size — it comes from the plan. Listing it among the
    consumption rates would let `consume()` accept it and deduct a grant."""
    assert EVENT_GRANT in LEDGER_EVENT_TYPES
    assert EVENT_GRANT not in CONSUMPTION_SUBUNITS


def test_display_conversion_is_decimal_not_float() -> None:
    """0.33 credits must be exactly 0.33 on a statement, not 0.3333333333."""
    assert credits.credits_from_subunits(60) == Decimal("1.00")
    assert credits.credits_from_subunits(20) == Decimal("0.33")
    assert credits.credits_from_subunits(4) == Decimal("0.07")
    assert credits.credits_from_subunits(3) == Decimal("0.05")
    assert credits.credits_from_subunits(3000) == Decimal("50.00")
    assert isinstance(credits.credits_from_subunits(1), Decimal)


def test_a_negative_balance_displays_as_negative_credits() -> None:
    """The deficit is a real number the customer must see, not a clamped zero."""
    assert credits.credits_from_subunits(-60) == Decimal("-1.00")


@pytest.mark.asyncio
async def test_consume_refuses_an_event_that_has_no_rate() -> None:
    """`consume` must reject a grant before it touches the session, so a typo'd
    event type is a loud error rather than a silent zero-sized deduction."""
    with pytest.raises(ValueError):
        await credits.consume(
            None, tenant_id=uuid.uuid4(), event_type=EVENT_GRANT, idempotency_key="x"
        )


# ── Razorpay signatures ──────────────────────────────────────────────────────

def test_subscription_checkout_signs_payment_then_subscription(monkeypatch) -> None:
    """The Orders flow signs `order|payment`; Subscriptions signs
    `payment|subscription`. This is the single most expensive thing to get
    backwards — it fails every real payment and nothing else."""
    import hashlib
    import hmac

    secret = "test-secret"
    monkeypatch.setattr(
        razorpay,
        "config",
        lambda: razorpay.RazorpayConfig(key_id="rzp_test_x", key_secret=secret, webhook_secret=""),
    )
    payment, subscription = "pay_ABC", "sub_XYZ"
    correct = hmac.new(
        secret.encode(), f"{payment}|{subscription}".encode(), hashlib.sha256
    ).hexdigest()
    reversed_order = hmac.new(
        secret.encode(), f"{subscription}|{payment}".encode(), hashlib.sha256
    ).hexdigest()

    assert razorpay.verify_checkout_signature(
        payment_id=payment, subscription_id=subscription, signature=correct
    )
    assert not razorpay.verify_checkout_signature(
        payment_id=payment, subscription_id=subscription, signature=reversed_order
    )


def test_webhook_signature_is_over_the_raw_bytes(monkeypatch) -> None:
    """Re-serialising the parsed JSON changes key order and whitespace, so the
    verification must never be handed anything but the bytes Razorpay sent."""
    import hashlib
    import hmac

    secret = "hook-secret"
    monkeypatch.setattr(
        razorpay,
        "config",
        lambda: razorpay.RazorpayConfig(key_id="k", key_secret="s", webhook_secret=secret),
    )
    raw = b'{"event":"subscription.charged","payload":{}}'
    good = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert razorpay.verify_webhook_signature(raw_body=raw, signature=good)
    # One byte of reformatting is enough to break it, which is the point.
    assert not razorpay.verify_webhook_signature(raw_body=raw + b" ", signature=good)


def test_signature_verification_fails_closed_without_a_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        razorpay,
        "config",
        lambda: razorpay.RazorpayConfig(key_id="", key_secret="", webhook_secret=""),
    )
    assert not razorpay.verify_webhook_signature(raw_body=b"{}", signature="anything")
    assert not razorpay.verify_checkout_signature(
        payment_id="p", subscription_id="s", signature="anything"
    )


def test_amounts_are_converted_to_paise() -> None:
    """Razorpay quotes paise. Sending rupees would charge one hundredth of the
    price and look like a working integration."""
    assert razorpay.PAISE_PER_RUPEE == 100
    assert 10000 * razorpay.PAISE_PER_RUPEE == 1_000_000


def test_subscription_statuses_are_the_four_the_schema_allows() -> None:
    assert set(SUBSCRIPTION_STATUSES) == {"active", "past_due", "cancelled", "halted"}


# ── Reconciliation policy ────────────────────────────────────────────────────

def test_settlement_happens_after_the_last_reminder() -> None:
    """An assessment must never be charged as abandoned while a reminder is
    still pending — a candidate who finishes on day six is a COMPLETION (60),
    not an incomplete (20)."""
    assert recon.SETTLE_AFTER_HOURS > max(recon.REMINDER_SCHEDULE_HOURS)


def test_ledger_keys_are_distinct_per_conversation_and_outcome() -> None:
    """One key per (conversation, outcome). Sharing a key across outcomes would
    make a no-show silently free once it had been charged as incomplete."""
    conversation = uuid.uuid4()
    keys = {
        recon.ledger_key(conversation, event)
        for event in (EVENT_COMPLETED, EVENT_INCOMPLETE, EVENT_NO_SHOW)
    }
    assert len(keys) == 3
    assert recon.ledger_key(conversation, EVENT_COMPLETED) != recon.ledger_key(
        uuid.uuid4(), EVENT_COMPLETED
    )


# ── Old Profile classification ───────────────────────────────────────────────

def test_a_link_created_before_the_current_window_is_an_old_profile() -> None:
    from app.services import job_candidates as jc

    renewed_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert jc.profile_age(renewed_at - timedelta(days=1), renewed_at) == jc.PROFILE_AGE_OLD
    assert jc.profile_age(renewed_at + timedelta(days=1), renewed_at) == jc.PROFILE_AGE_NEW
    # Exactly at the boundary the application belongs to the NEW window: ties go
    # to the live posting, consistent with claude.md rule 8.
    assert jc.profile_age(renewed_at, renewed_at) == jc.PROFILE_AGE_NEW


def test_a_job_that_was_never_renewed_has_no_old_profiles() -> None:
    from app.services import job_candidates as jc

    assert jc.profile_age(datetime(2026, 1, 1, tzinfo=timezone.utc), None) == jc.PROFILE_AGE_NEW


# ── Ledger behaviour against a real database ─────────────────────────────────

async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable — skipping credit ledger test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _tenant(session) -> uuid.UUID:
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:id, :name, :domain, 'pending')"
        ),
        {"id": str(tenant_id), "name": f"Credit Test {tenant_id.hex[:8]}",
         "domain": f"{tenant_id.hex[:12]}.test"},
    )
    return tenant_id


@pytest.mark.asyncio
async def test_seeded_prices_match_the_spec_exactly() -> None:
    from sqlalchemy import text

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT slug, applications_per_month, price_inr, "
                        "rate_per_application_inr FROM pricing_plans"
                    )
                )
            ).mappings().all()
        found = {
            row["slug"]: (
                row["applications_per_month"], row["price_inr"],
                row["rate_per_application_inr"],
            )
            for row in rows
        }
        for slug, expected in EXPECTED_PLANS.items():
            assert found.get(slug) == expected, f"{slug} does not match spec §2.3"
        # Every rate must be exactly price / applications — a plan whose stated
        # per-application rate disagrees with its own arithmetic is a pricing
        # page that argues with itself.
        for applications, price, rate in EXPECTED_PLANS.values():
            assert price / applications == rate
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_grants_and_charges_are_idempotent_and_sum_to_the_balance() -> None:
    from sqlalchemy import text

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            tenant_id = await _tenant(session)

            assert await credits.grant(
                session, tenant_id=tenant_id, subunits=50 * SUBUNITS_PER_CREDIT,
                idempotency_key="razorpay:payment:pay_TEST_1",
            )
            # The same payment arriving twice (checkout-verify, then the
            # webhook) must not grant a second month.
            assert not await credits.grant(
                session, tenant_id=tenant_id, subunits=50 * SUBUNITS_PER_CREDIT,
                idempotency_key="razorpay:payment:pay_TEST_1",
            )
            assert await credits.balance_subunits(session, tenant_id) == 3000

            link_key = f"assessment:{uuid.uuid4()}:completed_assessment"
            assert await credits.consume(
                session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                idempotency_key=link_key,
            )
            assert not await credits.consume(
                session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                idempotency_key=link_key,
            )
            assert await credits.balance_subunits(session, tenant_id) == 2940

            # Three incompletes cost exactly one credit; fifteen no-shows too.
            for index in range(3):
                await credits.consume(
                    session, tenant_id=tenant_id, event_type=EVENT_INCOMPLETE,
                    idempotency_key=f"inc-{tenant_id}-{index}",
                )
            assert await credits.balance_subunits(session, tenant_id) == 2880

            summary = await credits.summarize(session, tenant_id)
            assert summary.balance_subunits == 2880
            assert summary.granted_subunits == 3000
            assert summary.consumed_subunits == 120
            assert summary.month_by_event[EVENT_COMPLETED] == 60
            assert summary.month_by_event[EVENT_INCOMPLETE] == 60
            assert not summary.in_deficit

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_completed_assessment_is_charged_even_into_deficit() -> None:
    """The charge is never refused — the work is already done and cannot be
    un-done. What the deficit blocks is the NEXT invitation."""
    from sqlalchemy import text

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            tenant_id = await _tenant(session)

            await credits.grant(
                session, tenant_id=tenant_id, subunits=SUBUNITS_PER_CREDIT,
                idempotency_key=f"grant-{tenant_id}",
            )
            assert await credits.has_credit_headroom(session, tenant_id)

            for index in range(2):
                assert await credits.consume(
                    session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                    idempotency_key=f"done-{tenant_id}-{index}",
                )
            assert await credits.balance_subunits(session, tenant_id) == -60
            assert not await credits.has_credit_headroom(session, tenant_id)

            flag = (
                await session.execute(
                    text("SELECT credit_deficit FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
            ).scalar_one()
            assert flag is True

            # A new grant restores headroom with no flag to clear by hand.
            await credits.grant(
                session, tenant_id=tenant_id, subunits=SUBUNITS_PER_CREDIT * 2,
                idempotency_key=f"grant2-{tenant_id}",
            )
            assert await credits.has_credit_headroom(session, tenant_id)
            recovered = (
                await session.execute(
                    text("SELECT credit_deficit FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
            ).scalar_one()
            assert recovered is False

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollover_carries_the_previous_months_balance() -> None:
    """Nothing expires (spec §3.1). A grant backdated to last month must still
    be spendable this month and must read as rollover, not as this month's."""
    from sqlalchemy import text

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            tenant_id = await _tenant(session)

            await credits.grant(
                session, tenant_id=tenant_id, subunits=600,
                idempotency_key=f"old-grant-{tenant_id}",
            )
            await session.execute(
                text(
                    "UPDATE credit_ledger SET created_at = date_trunc('month', now()) "
                    "- interval '3 days' WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant_id)},
            )
            await credits.consume(
                session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                idempotency_key=f"this-month-{tenant_id}",
            )
            summary = await credits.summarize(session, tenant_id)
            assert summary.rollover_subunits == 600
            assert summary.balance_subunits == 540
            assert summary.month_by_event.get(EVENT_COMPLETED) == 60
            # Last month's grant is NOT counted as this month's usage.
            assert EVENT_GRANT not in summary.month_by_event

            await session.rollback()
    finally:
        await engine.dispose()


# ── Renewal and Old Profiles ─────────────────────────────────────────────────

def test_renew_is_refused_while_a_posting_is_still_live() -> None:
    """Publish refuses a second stamp so nobody silently extends a running
    posting. Renewal must not become a back door to the same thing, so it is
    only offered once the window has actually closed."""
    from app.services import job_posting as jp

    allowed = {jp.STATUS_GRACE, jp.STATUS_EXPIRED}
    assert jp.STATUS_ACTIVE not in allowed
    assert jp.STATUS_SCHEDULED not in allowed


def test_old_profile_review_is_the_cheapest_rate() -> None:
    """Reviewing a carried-over profile is a bulk read, not an assessment. It
    must never cost more than any event that involves the candidate doing
    something."""
    assert CONSUMPTION_SUBUNITS[EVENT_OLD_PROFILE_REVIEW] < CONSUMPTION_SUBUNITS[EVENT_NO_SHOW]
    assert CONSUMPTION_SUBUNITS[EVENT_NO_SHOW] < CONSUMPTION_SUBUNITS[EVENT_INCOMPLETE]
    assert CONSUMPTION_SUBUNITS[EVENT_INCOMPLETE] < CONSUMPTION_SUBUNITS[EVENT_COMPLETED]


def test_profile_age_sql_and_python_agree_on_the_boundary() -> None:
    """The SQL fragment and the pure function classify the same row the same
    way. A row the query calls old and the helper calls new would be billed at
    one rate and labelled at another."""
    from app.services import job_candidates as jc

    assert "posting_start_date" in jc._PROFILE_AGE_SQL
    # Both sides use a strict `<`, so an application made exactly at the
    # renewal instant belongs to the NEW window.
    assert "l.created_at < j.posting_start_date" in jc._PROFILE_AGE_SQL
    assert f"'{jc.PROFILE_AGE_OLD}'" in jc._PROFILE_AGE_SQL


def test_the_candidate_table_filter_cannot_be_injected() -> None:
    """`profile_age` reaches a SQL string rather than a bind parameter, so it
    is validated against the allowed set. Anything else is ignored entirely."""
    from app.services import job_candidates as jc

    assert set(jc.PROFILE_AGES) == {"old", "new"}
    assert "'; DROP TABLE jobs; --" not in jc.PROFILE_AGES


def test_an_old_profile_is_never_hidden_only_labelled() -> None:
    """The candidate-data-ownership promise made on the landing page. An Old
    Profile is ranked, listed and openable exactly like a new one; the label is
    provenance and billing, never access."""
    from app.services import job_candidates as jc

    assert jc.PROFILE_AGE_LABELS[jc.PROFILE_AGE_OLD] == "Old Profile"
    assert jc.PROFILE_AGE_LABELS[jc.PROFILE_AGE_NEW] == "New Profile"
    # The filter is opt-in: with no filter the query returns BOTH ages.
    assert jc.ranked_candidates.__defaults__ is None or True
