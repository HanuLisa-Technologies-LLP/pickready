"""FIFO credit lots, and the three-month expiry that applies to NEW GRANTS ONLY.

WHAT THIS MODULE IS FOR
-----------------------
`credit_ledger` is a flat list of signed sub-unit deltas. It answers "what is
the balance" perfectly and cannot answer "which credits expire when", because
it has no batch, no expiry and no record of which grant a debit drew down. That
last absence is the one that matters: FIFO is not reconstructible after the
fact from deltas alone, so it has to be recorded as it happens.

`credit_lots` is the batch; `credit_lot_draws` is the linkage. A grant creates
one lot, a consumption draws from the oldest valid lot first, and a consumption
larger than the oldest lot's remainder spans lots and writes one draw row each.

THE OWNER'S RULING, AND WHY IT IS A NULL RATHER THAN A DATE COMPARISON
----------------------------------------------------------------------
Three-month expiry applies to NEW grants only. Every credit granted before this
shipped keeps the "never expire" promise, because that promise is printed on
GST tax invoices already issued to paying customers and stated in the billing
UI. A lot with `expires_at IS NULL` never expires, migration 0111 backfills
every historical grant as exactly that, and `expire_due` below filters on
`expires_at IS NOT NULL`. So the ruling lives in the ROW. A cut-off date
compared in code would be one refactor away from retroactively expiring a sold
credit, which is a commercial and legal problem rather than a bug.

WHY EXPIRY IS A LEDGER ROW AND NOT A SECOND DEFINITION OF "BALANCE"
--------------------------------------------------------------------
claude.md: "The balance is SUM(subunits_delta), never a stored counter." A lot
carries `remaining_subunits`, which IS a counter, so there are now two things
that could be called the balance and they must never be allowed to disagree.
They are reconciled rather than reconciled-over: expiring a lot writes an
`expiry` ledger row for whatever was left on it, in the SAME transaction that
zeroes the lot. The ledger stays the single answer, the lot stays the
allocation record, and `tests/test_credit_lots.py` asserts after every
operation that the balance equals the sum of the live lots.

The consequence is that expiry has to be MATERIALISED before a balance is read
or spent, which is what `expire_due` is and why every gate calls it first. It
is the repair-on-read pattern the framework GETs already use: idempotent, a
no-op indexed scan in the normal case, and loud when it does something.

CONCURRENCY: `FOR UPDATE` ON THE LOTS, NOT AN ADVISORY LOCK
-------------------------------------------------------------
`services/locks.py` exists and its `pg_try_advisory_xact_lock` is the right
tool for the case it was written for: a scoring run whose second caller should
RETURN, because the first is already doing exactly that work. A credit
deduction is the opposite. It can never be skipped (a completed assessment is
charged even into the negative) and it can never be refused, so a `try` lock
whose failure means "give up" is the wrong shape entirely, and a waiting
advisory lock would be a second serialisation mechanism layered on top of the
row locks Postgres is going to take anyway.

`SELECT ... WHERE remaining_subunits > 0 ... ORDER BY issued_at, id FOR UPDATE`
is the whole guarantee. A second consumer blocks on the row the first holds;
when the first commits, READ COMMITTED re-evaluates the qualification against
the new row version, so a lot the first consumer emptied is no longer a
candidate and the second moves to the next lot. Two concurrent consumptions
therefore cannot draw the same sub-unit, and a tenant with one credit left and
two assessments finishing at once draws it once and goes negative by the other,
which is exactly the documented behaviour.

NO FLOATS. Every quantity here is an integer number of sub-units.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import CREDIT_VALIDITY_MONTHS

log = logging.getLogger(__name__)

#: `source` values on a lot. The grant's own provenance, denormalised so a lot
#: can be explained without joining back through the ledger's metadata blob.
SOURCE_GRANT = "grant"
#: Every lot migration 0111 created from a pre-existing grant. These are the
#: rows that carry `expires_at IS NULL`, and the value is what makes that
#: visible in a query rather than inferred from a null.
SOURCE_BACKFILL = "backfill_never_expires"


@dataclass(frozen=True)
class LotView:
    """One live lot, for the billing page's expiry table."""

    lot_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime | None
    remaining_subunits: int


def expiry_for(issued_at: datetime) -> datetime:
    """When a lot issued now stops being spendable.

    Calendar months rather than 90 days, because the customer-facing promise is
    "three months" and a February purchase must expire in May rather than at
    whatever 90 days happens to land on. Postgres interval arithmetic already
    clamps a 31st to the last day of a shorter month, so this is done in SQL at
    the point of insert; the Python form exists for the tests and the copy.
    """
    month_index = issued_at.month - 1 + CREDIT_VALIDITY_MONTHS
    year = issued_at.year + month_index // 12
    month = month_index % 12 + 1
    day = min(issued_at.day, _days_in_month(year, month))
    return issued_at.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]


async def open_lot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    ledger_entry_id: uuid.UUID,
    subunits: int,
    expires: bool = True,
) -> uuid.UUID:
    """Create the lot a grant just paid for. Returns its id.

    `expires=True` is the only value any live caller passes, and that is the
    owner's ruling in force: every NEW grant carries a three-month expiry. The
    parameter exists because migration 0111's backfill needs the other answer,
    and a backfill that had to reach around this function would be a second
    implementation of "what a lot is".

    The expiry is computed by POSTGRES, from the row's own `issued_at`, so a
    worker whose clock has drifted cannot mint a lot that expires at a
    different moment from one minted by the API a second earlier.
    """
    if subunits <= 0:
        raise ValueError("A lot must carry a positive number of sub-units")
    lot_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO credit_lots (id, tenant_id, ledger_entry_id, issued_at, "
            "expires_at, original_subunits, remaining_subunits, source) "
            # clock_timestamp() rather than now(). now() is the TRANSACTION's
            # start time, so two grants settled in one transaction would share
            # an `issued_at` and the FIFO order would fall through to a random
            # uuid: the same inputs would spend different batches on different
            # runs. clock_timestamp() advances within the transaction, which
            # makes the order total and reproducible.
            "VALUES (:id, :tid, :entry, clock_timestamp(), "
            "CASE WHEN :expires THEN clock_timestamp() "
            "+ make_interval(months => :months) END, :units, :units, :source)"
        ),
        {
            "id": str(lot_id),
            "tid": str(tenant_id),
            "entry": str(ledger_entry_id),
            "expires": expires,
            "months": CREDIT_VALIDITY_MONTHS,
            "units": subunits,
            "source": SOURCE_GRANT,
        },
    )
    return lot_id


async def expire_due(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Zero every lot past its expiry and write the matching ledger debits.

    Returns the sub-units expired by THIS call, which is zero in the normal
    case and is the number the caller logs when it is not.

    Idempotent three times over, deliberately, because this runs from a GET, a
    deduction and a scheduled sweep and two of those can overlap:

      * the CTE's `FOR UPDATE` serialises concurrent callers, and the loser
        re-evaluates `remaining_subunits > 0` against the committed row and
        finds nothing to do;
      * the zeroing is conditional on the remainder, so it cannot run twice;
      * the ledger row's `idempotency_key` is derived from the LOT ID, so even
        a debit written by some future second path would be refused by the
        UNIQUE constraint rather than double-charging.

    A NULL `expires_at` is excluded by the predicate, which is what keeps the
    owner's "new grants only" ruling true: a pre-existing lot is not merely
    unlikely to be picked up here, it is not selectable at all.
    """
    rows = (
        await session.execute(
            text(
                """
                WITH due AS (
                    SELECT id, remaining_subunits
                    FROM credit_lots
                    WHERE tenant_id = :tid
                      AND expires_at IS NOT NULL
                      AND expires_at <= now()
                      AND remaining_subunits > 0
                    ORDER BY issued_at, id
                    FOR UPDATE
                ), zeroed AS (
                    UPDATE credit_lots lot
                    SET remaining_subunits = 0, expired_at = now()
                    FROM due
                    WHERE lot.id = due.id
                    RETURNING lot.id AS lot_id, due.remaining_subunits AS lost
                )
                SELECT lot_id, lost FROM zeroed
                """
            ),
            {"tid": str(tenant_id)},
        )
    ).all()
    if not rows:
        return 0

    # Imported here rather than at module scope: `credits` imports this module
    # for `open_lot`, so a top-level import either way is a cycle.
    from app.models.billing import EVENT_EXPIRY
    from app.services import credits

    total = 0
    for lot_id, lost in rows:
        lost = int(lost)
        written = await credits.write_entry(
            session,
            tenant_id=tenant_id,
            event_type=EVENT_EXPIRY,
            subunits_delta=-lost,
            idempotency_key=f"credit-lot-expiry:{lot_id}",
            metadata={"lot_id": str(lot_id), "expired_subunits": lost},
        )
        if written is None:
            # The debit already exists, so this lot was expired by another
            # path that then lost its lot update to this transaction. Nothing
            # to charge twice; say so rather than silently counting it.
            log.warning(
                "credit_lots.expiry_debit_already_written tenant=%s lot=%s",
                tenant_id,
                lot_id,
            )
            continue
        total += lost
    if total:
        log.info(
            "credit_lots.expired tenant=%s subunits=%s lots=%s",
            tenant_id,
            total,
            len(rows),
        )
    return total


async def draw_fifo(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    ledger_entry_id: uuid.UUID,
    subunits: int,
) -> int:
    """Draw `subunits` from the oldest valid lots first. Returns what was drawn.

    Less than asked means the pool did not cover the charge, which is a normal
    and permitted state: the ledger debit has already been written in full by
    the caller and the balance is allowed to go negative, because a completed
    assessment cannot be un-completed. The shortfall is returned rather than
    raised so the caller can record it; raising here would turn "the customer
    is over their limit" into "the assessment failed".

    A never-expiring lot and an expiring one are ordered by `issued_at` alone,
    with no preference between them. Spending the expiring one first would
    arguably serve the customer better, but it would also mean the FIFO order
    depends on a property of the lot rather than on its age, so two customers
    with identical purchase histories in a different order would be charged
    against different batches. The order is age, and it is total.
    """
    if subunits <= 0:
        raise ValueError("A draw must be positive")
    remaining = subunits
    drawn = 0
    while remaining > 0:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, remaining_subunits
                    FROM credit_lots
                    WHERE tenant_id = :tid
                      AND remaining_subunits > 0
                      AND (expires_at IS NULL OR expires_at > now())
                    ORDER BY issued_at, id
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"tid": str(tenant_id)},
            )
        ).first()
        if row is None:
            break
        lot_id, available = row[0], int(row[1])
        take = min(available, remaining)
        await session.execute(
            text(
                "UPDATE credit_lots SET remaining_subunits = remaining_subunits - :take "
                "WHERE id = :id"
            ),
            {"take": take, "id": str(lot_id)},
        )
        await session.execute(
            text(
                "INSERT INTO credit_lot_draws "
                "(id, tenant_id, lot_id, ledger_entry_id, subunits) "
                "VALUES (gen_random_uuid(), :tid, :lot, :entry, :take)"
            ),
            {
                "tid": str(tenant_id),
                "lot": str(lot_id),
                "entry": str(ledger_entry_id),
                "take": take,
            },
        )
        remaining -= take
        drawn += take
    return drawn


async def live_lots(session: AsyncSession, tenant_id: uuid.UUID) -> list[LotView]:
    """Every lot with sub-units still on it, oldest first.

    This is what the billing page's expiry table renders. It reports lots that
    are past their expiry but not yet materialised as debits too, because the
    alternative is a page that hides a lot the very next deduction is going to
    take away without explanation. `expire_due` is called before this on every
    read path, so that window is one statement wide.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id, issued_at, expires_at, remaining_subunits "
                "FROM credit_lots WHERE tenant_id = :tid AND remaining_subunits > 0 "
                "ORDER BY issued_at, id"
            ),
            {"tid": str(tenant_id)},
        )
    ).all()
    return [
        LotView(
            lot_id=row[0],
            issued_at=row[1],
            expires_at=row[2],
            remaining_subunits=int(row[3]),
        )
        for row in rows
    ]


async def tenants_with_due_lots(session: AsyncSession, limit: int) -> list[uuid.UUID]:
    """Tenants holding at least one lot that is past its expiry.

    Asks the TABLE, never a timestamp on the tenant: a sweep that filtered on
    "last expired at" would skip exactly the tenant whose previous run died
    between the stamp and the work.
    """
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT tenant_id FROM credit_lots "
                "WHERE expires_at IS NOT NULL AND expires_at <= now() "
                "AND remaining_subunits > 0 LIMIT :limit"
            ),
            {"limit": limit},
        )
    ).all()
    return [row[0] for row in rows]
