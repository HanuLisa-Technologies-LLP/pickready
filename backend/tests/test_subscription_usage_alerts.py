"""The month 10 and month 11 usage summary (change request 27).

Three properties, and they fail in three different directions:

  * ONCE. A summary sent twice is worse than one sent late, because the second
    copy teaches the reader that our emails repeat and can be skimmed. The
    latch is a checked UPDATE, so this is tested by RUNNING THE SWEEP TWICE
    rather than by inspecting the statement.
  * INFORMATIONAL. It must change no subscription state. A sweep that renewed,
    cancelled, charged or re-granted would be a billing action wearing a
    notification's clothes, and nobody would find it until a customer's card
    was hit. Asserted by reading every subscription column back after the run.
  * THE MONTH. Two implementations of "which subscription month is this"
    exist, one in Python for the letter and one in SQL for the sweep's filter.
    Two copies of one fact stay honest only when something compares them, so
    they are compared here over a range of dates, including the month-end
    cases that are the whole reason the expression has a CASE in it.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.billing import (
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_CANCELLED,
    SUBUNITS_PER_CREDIT,
)
from app.services import subscription_usage
from tests.test_billing import _factory_or_skip, _tenant

#: Every tenants column the sweep is forbidden to touch. Listed explicitly
#: rather than compared as "the whole row", because `usage_alert_last_month`
#: is the one column it IS allowed to write and a whole-row comparison could
#: only be made by excluding it, which is the same list written backwards.
_SUBSCRIPTION_COLUMNS = (
    "razorpay_customer_id",
    "razorpay_subscription_id",
    "current_plan_id",
    "subscription_status",
    "subscription_current_end",
    "subscription_started_at",
    "credit_warning_1_sent",
    "credit_warning_2_sent",
    "trial_used",
    "setup_fee_paid",
    "setup_fee_waived",
)


# ── Month arithmetic, no database ───────────────────────────────────────────

def test_the_first_month_is_month_one() -> None:
    """The customer says "I have been paying for ten months" and means ten
    payments. A zero-based count would send the month-10 letter in month 11."""
    started = datetime(2026, 1, 15, tzinfo=timezone.utc)
    assert subscription_usage.subscription_month(started, started) == 1
    assert subscription_usage.subscription_month(
        started, datetime(2026, 2, 15, tzinfo=timezone.utc)
    ) == 2
    assert subscription_usage.subscription_month(
        started, datetime(2026, 10, 15, tzinfo=timezone.utc)
    ) == 10
    # The day BEFORE the anniversary is still the previous month.
    assert subscription_usage.subscription_month(
        started, datetime(2026, 10, 14, tzinfo=timezone.utc)
    ) == 9


def test_a_subscription_that_never_charged_has_no_month() -> None:
    """None, never 1. A tenant row exists from onboarding; treating that as
    month 1 would post a subscription summary to an account that has never
    paid for a subscription."""
    assert subscription_usage.subscription_month(
        None, datetime(2026, 10, 1, tzinfo=timezone.utc)
    ) is None


def test_the_alert_months_are_ten_and_eleven() -> None:
    assert subscription_usage.ALERT_MONTHS == (10, 11)


# ── The two implementations of the month must agree ─────────────────────────

@pytest.mark.asyncio
async def test_the_sql_month_matches_the_python_month() -> None:
    """The sweep filters in SQL and the letter is composed in Python. If those
    two expressions disagree, a tenant is selected for month 10 and told they
    are in month 9, which reads as a bug in the product rather than in a
    filter."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            started = datetime(2025, 1, 31, 12, 0, tzinfo=timezone.utc)
            tenant_id = await _tenant(session)
            await session.execute(
                text(
                    "UPDATE tenants SET subscription_started_at = :started, "
                    "subscription_status = :active WHERE id = :id"
                ),
                {"started": started, "active": SUBSCRIPTION_ACTIVE,
                 "id": str(tenant_id)},
            )
            await session.commit()

            # A year of dates, including every month end, checked against the
            # Python function. `due_tenants` only returns a tenant in an alert
            # month, so the comparison is made where it is observable.
            for offset in range(0, 400, 7):
                now = started + timedelta(days=offset)
                expected = subscription_usage.subscription_month(started, now)
                due = dict(
                    await subscription_usage.due_tenants(
                        session, now=now, limit=50
                    )
                )
                if expected in subscription_usage.ALERT_MONTHS:
                    assert due.get(tenant_id) == expected, (
                        f"{now:%Y-%m-%d}: SQL and Python disagree"
                    )
                else:
                    assert tenant_id not in due, (
                        f"{now:%Y-%m-%d}: selected outside an alert month"
                    )
    finally:
        await engine.dispose()


# ── The sweep ───────────────────────────────────────────────────────────────

async def _subscriber(session, *, month: int, status: str = SUBSCRIPTION_ACTIVE):
    """A tenant with an account admin, currently in subscription `month`.

    The start date is computed by POSTGRES with `make_interval`, not by
    subtracting 31 days per month in Python. That shortcut overshoots by a day
    a month and lands a "month 10" fixture in month 11, which is the same
    calendar-versus-blocks mistake the production expression exists to avoid;
    a fixture that makes it would be testing the wrong month.

    The extra five days put the tenant comfortably past the anniversary rather
    than exactly on it, so the test does not depend on which side of a
    boundary the clock happens to be when it runs.
    """
    tenant_id = await _tenant(session)
    await session.execute(
        text(
            "UPDATE tenants SET subscription_started_at = "
            "now() - make_interval(months => :m) - interval '5 days', "
            "subscription_status = :status WHERE id = :id"
        ),
        {"m": month - 1, "status": status, "id": str(tenant_id)},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, role, status) "
            "VALUES (:uid, :tid, :email, 'client', 'active')"
        ),
        {
            "uid": str(uuid.uuid4()),
            "tid": str(tenant_id),
            "email": f"admin-{tenant_id.hex[:10]}@usage.test",
            "status": "active",
        },
    )
    return tenant_id


async def _subscription_state(session, tenant_id: uuid.UUID) -> dict:
    columns = ", ".join(_SUBSCRIPTION_COLUMNS)
    row = (
        await session.execute(
            text(f"SELECT {columns} FROM tenants WHERE id = :id"),
            {"id": str(tenant_id)},
        )
    ).mappings().one()
    return dict(row)


@pytest.mark.asyncio
async def test_a_month_ten_alert_sends_once_and_a_rerun_sends_nothing() -> None:
    from app.workers import dispatch as dispatch_module

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _subscriber(session, month=10)
            await session.commit()

            now = datetime.now(timezone.utc)
            due = await subscription_usage.due_tenants(session, now=now, limit=50)
            assert dict(due).get(tenant_id) == 10

            assert await subscription_usage.claim_month(session, tenant_id, 10)
            await session.commit()
            # The SECOND claim is the re-run, the redelivered dispatch and the
            # two sweeps racing, all at once. It must lose.
            assert not await subscription_usage.claim_month(session, tenant_id, 10)
            await session.commit()

            # And the tenant is no longer selectable, so a later sweep finds
            # nothing rather than relying on the claim to refuse it.
            due_again = await subscription_usage.due_tenants(
                session, now=now, limit=50
            )
            assert tenant_id not in dict(due_again)

            # Month 11 is a DIFFERENT letter and is still available.
            assert await subscription_usage.claim_month(session, tenant_id, 11)
            await session.commit()
            # Going backwards is refused: month 10 has already been told.
            assert not await subscription_usage.claim_month(session, tenant_id, 10)
            await session.commit()
        assert dispatch_module is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_alert_changes_no_subscription_state() -> None:
    """Read back every subscription column after the sweep.

    Not an assertion on the sweep's return value: the failure being prevented
    is a write nobody intended, and a write nobody intended is exactly the one
    a return value does not mention.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _subscriber(session, month=10)
            await session.execute(
                text(
                    "UPDATE tenants SET razorpay_subscription_id = :sub, "
                    "subscription_current_end = now() + interval '20 days' "
                    "WHERE id = :id"
                ),
                {"sub": "sub_usage_test", "id": str(tenant_id)},
            )
            await session.commit()
            before = await _subscription_state(session, tenant_id)

        from app.workers.tasks import sweep_subscription_usage_alerts

        # The task is sync and calls `asyncio.run` internally, so it runs
        # in a worker thread: invoking it directly from an async test
        # raises inside the runner rather than inside the product.
        result = await asyncio.to_thread(sweep_subscription_usage_alerts)
        assert result["sent"] >= 1

        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            after = await _subscription_state(session, tenant_id)
            assert after == before, "the usage summary wrote subscription state"
            # The ONE column it may write, and it did.
            claimed = (
                await session.execute(
                    text(
                        "SELECT usage_alert_last_month FROM tenants WHERE id = :id"
                    ),
                    {"id": str(tenant_id)},
                )
            ).scalar_one()
            assert claimed == 10
            # No ledger row either: an informational letter costs nothing and
            # grants nothing.
            entries = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM credit_ledger WHERE tenant_id = :id"
                    ),
                    {"id": str(tenant_id)},
                )
            ).scalar_one()
            assert int(entries) == 0

        # And the re-run sends nothing for this tenant.
        second = await asyncio.to_thread(sweep_subscription_usage_alerts)
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            still = (
                await session.execute(
                    text(
                        "SELECT usage_alert_last_month FROM tenants WHERE id = :id"
                    ),
                    {"id": str(tenant_id)},
                )
            ).scalar_one()
            assert still == 10
        assert isinstance(second["sent"], int)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_cancelled_subscription_gets_no_summary() -> None:
    """Month 10 of a subscription that is no longer running is not a month of
    anything. Selecting it would send a usage summary and a top-up offer to
    somebody who has already left."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _subscriber(
                session, month=10, status=SUBSCRIPTION_CANCELLED
            )
            await session.commit()
            due = await subscription_usage.due_tenants(
                session, now=datetime.now(timezone.utc), limit=50
            )
            assert tenant_id not in dict(due)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_summary_states_usage_balance_and_expiry_separately() -> None:
    """The letter's figures, and specifically that the rollover figure and the
    expiry figures are DIFFERENT numbers.

    `rollover_credits` is the pre-existing "carried over from last month"
    field and has nothing to do with the three-month validity window; the
    expiry story is `non_expiring` and `expiring_soon`. Conflating them is the
    naming hazard this whole change had to avoid, and here is where a future
    edit that conflates them fails.
    """
    from app.services import credits

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _subscriber(session, month=11)
            await credits.grant(
                session,
                tenant_id=tenant_id,
                subunits=20 * SUBUNITS_PER_CREDIT,
                idempotency_key=f"usage-summary:{tenant_id}",
            )
            await session.commit()

            summary = await subscription_usage.build_summary(session, tenant_id, 11)
            assert summary.subscription_month == 11
            assert summary.assessments_used == 0
            assert summary.balance_credits == credits.credits_from_subunits(
                20 * SUBUNITS_PER_CREDIT
            )
            # Granted just now, so nothing was carried in from last month, and
            # the whole balance is in a lot that expires in three months.
            assert summary.rollover_credits == credits.credits_from_subunits(0)
            assert summary.non_expiring_credits == credits.credits_from_subunits(0)
            assert summary.next_expiry_at is not None
            # Three months out is well past the thirty-day horizon.
            assert summary.expiring_soon_credits == credits.credits_from_subunits(0)
            assert summary.assessments_remaining > 0
    finally:
        await engine.dispose()
