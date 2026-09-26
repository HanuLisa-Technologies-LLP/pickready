"""FIFO credit lots and the three-month expiry (change request 25).

Six things are worth guarding here, and every one of them is invisible without
a real database:

  * FIFO ORDER. "Oldest valid lot first" is not observable from a balance. A
    draw-down that picked the newest lot would produce exactly the same balance
    and exactly the same statement total, and would quietly expire the wrong
    credits three months later.
  * SPANNING. A charge larger than the oldest lot's remainder has to consume it
    and continue. An implementation that took `min(cost, lot)` and stopped
    would undercharge, silently, only for customers who have bought twice.
  * AN EXPIRED LOT IS NOT SPENDABLE. And the ledger has to agree: the balance
    is SUM(subunits_delta), so expiry has to write a debit or the two
    definitions drift apart.
  * A NULL-EXPIRY LOT IS NEVER EXPIRED. This is the owner's ruling and it is
    the most expensive thing in this file to get wrong: those rows are credits
    already sold under a printed "Credits never expire." Every other test here
    protects a balance; this one protects a promise.
  * CONCURRENCY. Two assessments finishing at once must not draw the same
    sub-unit twice. That failure is invisible in a single-connection test by
    construction, so the test below drives two real connections.
  * DEMO TENANTS. Exempt from refusals, never from records. The dangerous
    direction is a LEAKED exemption, so the demo assertions have a paying twin.

Same convention as test_billing.py: arithmetic always runs, the ledger tests
skip cleanly with no database.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.billing import (
    CREDIT_VALIDITY_MONTHS,
    EVENT_COMPLETED,
    EVENT_EXPIRY,
    EVENT_INCOMPLETE,
    LEDGER_EVENT_TYPES,
    SUBUNITS_PER_CREDIT,
)
from app.services import credit_lots, credits
from tests.test_billing import _factory_or_skip, _tenant


# ── Arithmetic, no database ─────────────────────────────────────────────────

def test_validity_is_three_calendar_months() -> None:
    assert CREDIT_VALIDITY_MONTHS == 3
    issued = datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)
    assert credit_lots.expiry_for(issued) == datetime(
        2026, 4, 15, 9, 30, tzinfo=timezone.utc
    )


def test_a_month_end_issue_date_clamps_rather_than_overflowing() -> None:
    """30 November + 3 months is 28 February, not 2 March. The customer counts
    in months; a day-arithmetic answer would put the expiry in the wrong one."""
    issued = datetime(2025, 11, 30, tzinfo=timezone.utc)
    assert credit_lots.expiry_for(issued) == datetime(
        2026, 2, 28, tzinfo=timezone.utc
    )


def test_expiry_is_a_ledger_event_and_not_a_consumption_rate() -> None:
    """It must be writable to the ledger and must NEVER be chargeable through
    `consume`, which would deduct a rate for it on top of the lot zeroing."""
    from app.models.billing import consumption_subunits

    assert EVENT_EXPIRY in LEDGER_EVENT_TYPES
    assert consumption_subunits(EVENT_EXPIRY, None) is None
    assert consumption_subunits(EVENT_EXPIRY, "STEM") is None


# ── Helpers over a real database ────────────────────────────────────────────

async def _bypass(session) -> None:
    # `false` => SESSION level, so it survives the commits these tests make.
    # Passed `true` outside an explicit transaction it is discarded at once and
    # every later query silently returns zero rows.
    await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))


async def _insert_lot(
    session,
    tenant_id: uuid.UUID,
    *,
    subunits: int,
    age_days: int,
    expires_in_days: int | None,
) -> uuid.UUID:
    """A lot at a controlled age and expiry, written directly.

    Direct SQL rather than `open_lot` because these tests need lots that are
    already old or already expired, and `open_lot` correctly refuses to invent
    a past: it stamps `clock_timestamp()`.

    `expires_in_days=None` writes the NULL that means never expires, which is
    exactly what migration 0111's backfill writes for a pre-existing grant.
    """
    lot_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO credit_lots (id, tenant_id, issued_at, expires_at, "
            "original_subunits, remaining_subunits, source) VALUES "
            "(:id, :tid, now() - make_interval(days => :age), "
            "CASE WHEN :has_expiry THEN now() + make_interval(days => :ttl) END, "
            ":units, :units, :source)"
        ),
        {
            "id": str(lot_id),
            "tid": str(tenant_id),
            "age": age_days,
            "has_expiry": expires_in_days is not None,
            "ttl": expires_in_days or 0,
            "units": subunits,
            "source": (
                credit_lots.SOURCE_GRANT
                if expires_in_days is not None
                else credit_lots.SOURCE_BACKFILL
            ),
        },
    )
    return lot_id


async def _seed_balance(session, tenant_id: uuid.UUID, subunits: int) -> None:
    """A ledger grant with no lot, so a test can set the balance and control
    the lots separately. The lots are what these tests are about; the ledger
    entry only keeps the two halves consistent."""
    await credits.write_entry(
        session,
        tenant_id=tenant_id,
        event_type="grant",
        subunits_delta=subunits,
        idempotency_key=f"test-seed:{uuid.uuid4()}",
    )


async def _lot_state(session, lot_id: uuid.UUID) -> tuple[int, bool]:
    row = (
        await session.execute(
            text(
                "SELECT remaining_subunits, expired_at IS NOT NULL "
                "FROM credit_lots WHERE id = :id"
            ),
            {"id": str(lot_id)},
        )
    ).first()
    return int(row[0]), bool(row[1])


# ── FIFO ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fifo_draws_the_oldest_valid_lot_first() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await _seed_balance(session, tenant_id, 2 * SUBUNITS_PER_CREDIT)
            old = await _insert_lot(
                session, tenant_id, subunits=SUBUNITS_PER_CREDIT,
                age_days=40, expires_in_days=50,
            )
            new = await _insert_lot(
                session, tenant_id, subunits=SUBUNITS_PER_CREDIT,
                age_days=1, expires_in_days=89,
            )
            await session.commit()

            assert await credits.consume(
                session,
                tenant_id=tenant_id,
                event_type=EVENT_COMPLETED,
                idempotency_key=f"fifo:{tenant_id}",
            )
            await session.commit()

            assert await _lot_state(session, old) == (0, False)
            assert await _lot_state(session, new) == (SUBUNITS_PER_CREDIT, False)
            # And the draw is RECORDED against that lot, which is the half a
            # balance assertion cannot see.
            drawn_from = (
                await session.execute(
                    text(
                        "SELECT lot_id, subunits FROM credit_lot_draws "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).all()
            assert [(row[0], int(row[1])) for row in drawn_from] == [
                (old, SUBUNITS_PER_CREDIT)
            ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_charge_larger_than_one_lot_spans_two() -> None:
    """A STEM completed report is 90 sub-units. Against a 60 and a 60 it must
    empty the first and take 30 from the second, not stop at 60."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await _seed_balance(session, tenant_id, 2 * SUBUNITS_PER_CREDIT)
            first = await _insert_lot(
                session, tenant_id, subunits=SUBUNITS_PER_CREDIT,
                age_days=30, expires_in_days=60,
            )
            second = await _insert_lot(
                session, tenant_id, subunits=SUBUNITS_PER_CREDIT,
                age_days=2, expires_in_days=88,
            )
            await session.commit()

            assert await credits.consume(
                session,
                tenant_id=tenant_id,
                event_type=EVENT_COMPLETED,
                idempotency_key=f"span:{tenant_id}",
                role_classification="STEM",
            )
            await session.commit()

            assert await _lot_state(session, first) == (0, False)
            assert await _lot_state(session, second) == (
                SUBUNITS_PER_CREDIT // 2, False
            )
            draws = (
                await session.execute(
                    text(
                        "SELECT subunits FROM credit_lot_draws "
                        "WHERE tenant_id = :tid ORDER BY subunits DESC"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).scalars().all()
            assert [int(value) for value in draws] == [60, 30]
            assert sum(int(value) for value in draws) == 90
    finally:
        await engine.dispose()


# ── Expiry ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_expired_lot_is_not_spendable_and_leaves_the_ledger_honest() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await _seed_balance(session, tenant_id, SUBUNITS_PER_CREDIT)
            stale = await _insert_lot(
                session, tenant_id, subunits=SUBUNITS_PER_CREDIT,
                age_days=120, expires_in_days=-1,
            )
            await session.commit()

            # The gate sees a zero balance, not a one-credit one.
            assert not await credits.has_positive_balance(session, tenant_id)
            await session.commit()

            assert await _lot_state(session, stale) == (0, True)
            assert await credits.balance_subunits(session, tenant_id) == 0
            # The drop is EXPLAINED, in the statement, not merely absent.
            expiry_rows = (
                await session.execute(
                    text(
                        "SELECT subunits_delta FROM credit_ledger "
                        "WHERE tenant_id = :tid AND event_type = :ev"
                    ),
                    {"tid": str(tenant_id), "ev": EVENT_EXPIRY},
                )
            ).scalars().all()
            assert [int(value) for value in expiry_rows] == [-SUBUNITS_PER_CREDIT]

            # Idempotent: a second sweep must not charge for it again.
            assert await credit_lots.expire_due(session, tenant_id) == 0
            await session.commit()
            assert await credits.balance_subunits(session, tenant_id) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_pre_existing_lot_is_never_expired_by_the_sweep() -> None:
    """The owner's ruling, as a test.

    A NULL `expires_at` is what migration 0111 wrote for every credit granted
    before change request 25, and those credits were sold under a printed
    "Credits never expire." The lot below is older than any conceivable
    validity window; nothing may touch it.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await _seed_balance(session, tenant_id, 5 * SUBUNITS_PER_CREDIT)
            ancient = await _insert_lot(
                session, tenant_id, subunits=5 * SUBUNITS_PER_CREDIT,
                age_days=3650, expires_in_days=None,
            )
            await session.commit()

            assert await credit_lots.expire_due(session, tenant_id) == 0
            assert await _lot_state(session, ancient) == (
                5 * SUBUNITS_PER_CREDIT, False
            )
            # And it is still SPENDABLE, which is the other half of the promise.
            assert await credits.consume(
                session,
                tenant_id=tenant_id,
                event_type=EVENT_INCOMPLETE,
                idempotency_key=f"ancient:{tenant_id}",
            )
            await session.commit()
            remaining, expired = await _lot_state(session, ancient)
            assert expired is False
            assert remaining == 5 * SUBUNITS_PER_CREDIT - SUBUNITS_PER_CREDIT // 3
            summary = await credits.summarize(session, tenant_id)
            assert summary.expired_subunits == 0
            assert summary.non_expiring_subunits == remaining
            assert summary.next_expiry_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_new_grant_opens_a_lot_that_expires_and_the_balance_matches() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await session.commit()

            assert await credits.grant(
                session,
                tenant_id=tenant_id,
                subunits=3 * SUBUNITS_PER_CREDIT,
                idempotency_key=f"grant-once:{tenant_id}",
            )
            # The SECOND call with the same key grants nothing, and must also
            # mint no second lot: a redelivered webhook cannot double the
            # customer's expiring balance.
            assert not await credits.grant(
                session,
                tenant_id=tenant_id,
                subunits=3 * SUBUNITS_PER_CREDIT,
                idempotency_key=f"grant-once:{tenant_id}",
            )
            await session.commit()

            lots = await credit_lots.live_lots(session, tenant_id)
            assert len(lots) == 1
            assert lots[0].remaining_subunits == 3 * SUBUNITS_PER_CREDIT
            assert lots[0].expires_at is not None
            assert lots[0].expires_at == credit_lots.expiry_for(lots[0].issued_at)
            assert await credits.balance_subunits(session, tenant_id) == (
                3 * SUBUNITS_PER_CREDIT
            )
    finally:
        await engine.dispose()


# ── Concurrency ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_concurrent_consumptions_cannot_overspend_one_lot() -> None:
    """The failure this catches is invisible in a single-connection test.

    Two assessments finish at the same moment against a pool holding exactly
    one credit. Both charges are written, because a completed assessment is
    charged even into the negative; what must NOT happen is both of them
    drawing that one credit, which would leave the lot remainder and the ledger
    balance disagreeing by a full credit with nothing recording the difference.

    Two real connections, started together. `FOR UPDATE` on the lot is the
    whole guarantee: the loser blocks, then re-evaluates
    `remaining_subunits > 0` against the committed row and finds nothing to
    take.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await _seed_balance(session, tenant_id, SUBUNITS_PER_CREDIT)
            lot_id = await _insert_lot(
                session, tenant_id, subunits=SUBUNITS_PER_CREDIT,
                age_days=1, expires_in_days=80,
            )
            await session.commit()

        async def charge(key: str) -> bool:
            async with factory() as own:
                await _bypass(own)
                charged = await credits.consume(
                    own,
                    tenant_id=tenant_id,
                    event_type=EVENT_COMPLETED,
                    idempotency_key=key,
                )
                await own.commit()
                return charged

        first, second = await asyncio.gather(
            charge(f"race-a:{tenant_id}"), charge(f"race-b:{tenant_id}")
        )
        assert first and second, "both charges must land; neither is refused"

        async with factory() as session:
            await _bypass(session)
            remaining, _ = await _lot_state(session, lot_id)
            assert remaining == 0
            total_drawn = int(
                (
                    await session.execute(
                        text(
                            "SELECT COALESCE(SUM(subunits), 0) "
                            "FROM credit_lot_draws WHERE lot_id = :id"
                        ),
                        {"id": str(lot_id)},
                    )
                ).scalar_one()
            )
            # THE ASSERTION. Overspending shows up here and nowhere else: the
            # balance below would read -60 either way.
            assert total_drawn == SUBUNITS_PER_CREDIT
            assert await credits.balance_subunits(session, tenant_id) == (
                -SUBUNITS_PER_CREDIT
            )
    finally:
        await engine.dispose()


# ── Demo tenants ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_demo_tenant_bypasses_the_refusal_and_still_gets_its_rows() -> None:
    """Exempt from REFUSALS, never from RECORDS.

    The dangerous direction is the exemption leaking to a paying customer,
    which raises nothing and just stops collecting money, so this has a paying
    twin in the same test: identical ledgers, opposite gate answers.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            demo_id = await _tenant(session)
            paying_id = await _tenant(session)
            await session.execute(
                text("UPDATE tenants SET is_demo = TRUE WHERE id = :id"),
                {"id": str(demo_id)},
            )
            await session.commit()

            for tenant_id in (demo_id, paying_id):
                assert await credits.consume(
                    session,
                    tenant_id=tenant_id,
                    event_type=EVENT_COMPLETED,
                    idempotency_key=f"demo-charge:{tenant_id}",
                )
            await session.commit()

            # RECORDS: both ledgers carry the charge, at the same rate.
            for tenant_id in (demo_id, paying_id):
                assert await credits.balance_subunits(session, tenant_id) == (
                    -SUBUNITS_PER_CREDIT
                )

            # REFUSALS: only the paying tenant is gated.
            assert await credits.has_positive_balance(session, demo_id)
            assert not await credits.has_positive_balance(session, paying_id)
            await session.commit()
    finally:
        await engine.dispose()


# ── The invariant the whole design rests on ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_balance_equals_the_live_lots_after_every_operation() -> None:
    """`remaining_subunits` is a counter and the ledger is not, so the two
    could drift. They are reconciled rather than trusted: after a grant, a
    charge, a spanning charge and an expiry, the ledger balance must equal the
    sum of the live lots (clamped at zero, because an overdraft has no lot to
    sit in)."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await _bypass(session)
            tenant_id = await _tenant(session)
            await session.commit()

            async def check() -> None:
                await credit_lots.expire_due(session, tenant_id)
                balance = await credits.balance_subunits(session, tenant_id)
                lot_total = sum(
                    lot.remaining_subunits
                    for lot in await credit_lots.live_lots(session, tenant_id)
                )
                assert lot_total == max(0, balance), (
                    f"ledger {balance} vs lots {lot_total}"
                )

            await credits.grant(
                session, tenant_id=tenant_id, subunits=SUBUNITS_PER_CREDIT,
                idempotency_key=f"inv-1:{tenant_id}",
            )
            await session.commit()
            await check()

            await credits.grant(
                session, tenant_id=tenant_id, subunits=SUBUNITS_PER_CREDIT,
                idempotency_key=f"inv-2:{tenant_id}",
            )
            await session.commit()
            await check()

            await credits.consume(
                session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                idempotency_key=f"inv-3:{tenant_id}", role_classification="STEM",
            )
            await session.commit()
            await check()

            # Force the remaining lot past its expiry and re-check.
            await session.execute(
                text(
                    "UPDATE credit_lots SET expires_at = now() - interval '1 day' "
                    "WHERE tenant_id = :tid AND remaining_subunits > 0"
                ),
                {"tid": str(tenant_id)},
            )
            await session.commit()
            await check()
            await session.commit()
            assert await credits.balance_subunits(session, tenant_id) == 0
    finally:
        await engine.dispose()
