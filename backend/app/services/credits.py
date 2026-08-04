"""The credit ledger (killer-spec Part 3).

Three rules hold this module together, and every function here exists to keep
one of them true:

1. **No floats, ever.** Consumption is 1, 1/3, 1/15 and 1/20 of a credit.
   LCM(1, 3, 15, 20) = 60, so a credit is 60 integer sub-units and all four
   rates divide it exactly. Nothing in this file, the schema, or the API
   arithmetic is a float; the only division is the one that formats a balance
   for DISPLAY, and it is done with Decimal.

2. **The balance is the ledger.** `SUM(subunits_delta)`, never a mutable
   counter. A customer disputing their usage gets a statement, not a number.

3. **Every write is idempotent.** Celery redelivers, Razorpay redelivers, a
   recruiter double-clicks. Each entry carries a UNIQUE `idempotency_key`, so
   the second attempt is a no-op instead of a second charge.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    CONSUMPTION_SUBUNITS,
    EVENT_COMPLETED,
    EVENT_GRANT,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    SUBUNITS_PER_CREDIT,
    CreditLedgerEntry,
)

__all__ = [
    "SUBUNITS_PER_CREDIT",
    "CONSUMPTION_SUBUNITS",
    "EVENT_COMPLETED",
    "EVENT_GRANT",
    "EVENT_INCOMPLETE",
    "EVENT_NO_SHOW",
    "EVENT_OLD_PROFILE_REVIEW",
    "BalanceSummary",
    "balance_subunits",
    "consume",
    "credits_from_subunits",
    "grant",
    "has_credit_headroom",
    "is_demo_tenant",
    "summarize",
]


def credits_from_subunits(subunits: int) -> Decimal:
    """Sub-units to display credits, rounded to 2 decimals (spec §3.4).

    Decimal, not float: 20 sub-units is exactly 0.33 credits here, and a
    statement that adds up is the whole point of the ledger.
    """
    return (Decimal(subunits) / Decimal(SUBUNITS_PER_CREDIT)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class BalanceSummary:
    """What the Customer Portal billing page renders."""

    balance_subunits: int
    granted_subunits: int
    consumed_subunits: int
    #: event_type -> sub-units consumed, this billing month.
    month_by_event: dict[str, int]
    #: Sub-units carried in from before this month's first grant.
    rollover_subunits: int
    in_deficit: bool
    #: A permanent demonstration company. The billing page still renders every
    #: figure above -- usage is real and the statement adds up -- but the
    #: BALANCE is presented as unlimited rather than as the ledger sum, which
    #: for a demo tenant that has run assessments is a negative number on a page
    #: that is supposed to read as fully paid.
    unlimited: bool = False

    @property
    def balance_credits(self) -> Decimal:
        return credits_from_subunits(self.balance_subunits)


async def balance_subunits(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Current balance. Always the SUM — there is no cached column to trust."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0)).where(
                CreditLedgerEntry.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    return int(total or 0)


async def _write(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    subunits_delta: int,
    idempotency_key: str,
    job_candidate_link_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> CreditLedgerEntry | None:
    """Append one entry, or return None if this exact event was already written.

    The dedupe is the UNIQUE constraint, not a prior SELECT: two workers racing
    the same event would both see "not present" and both insert. A SAVEPOINT
    keeps the caller's transaction usable when the constraint fires — without
    it the whole surrounding unit of work (a completed assessment, a webhook)
    would be poisoned by an error that means "already done".
    """
    entry = CreditLedgerEntry(
        tenant_id=tenant_id,
        event_type=event_type,
        subunits_delta=subunits_delta,
        job_candidate_link_id=job_candidate_link_id,
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        metadata_json=metadata,
    )
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        return None
    return entry


async def _sync_deficit(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Recompute `tenants.credit_deficit` from the ledger. Returns the flag.

    Written with raw SQL against `tenants` because that table is global (no RLS
    policy keyed to app.tenant_id), so it is reachable from the tenant-scoped
    session that just wrote the ledger entry as well as from the webhook's
    bypass scope.
    """
    balance = await balance_subunits(session, tenant_id)
    # A demonstration tenant is never in deficit, whatever the ledger sums to.
    # The flag drives the dunning email and the portal's deficit banner, so
    # letting it go true here would have a demo company chased for payment.
    in_deficit = balance < 0 and not await is_demo_tenant(session, tenant_id)
    await session.execute(
        text("UPDATE tenants SET credit_deficit = :flag WHERE id = :tid"),
        {"flag": in_deficit, "tid": str(tenant_id)},
    )
    return in_deficit


async def grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subunits: int,
    idempotency_key: str,
    plan_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Add a month's allotment. Returns False when already granted.

    Unused credits roll over and nothing expires (spec §3.1), which is why a
    grant is a plain positive entry and there is no expiry sweep anywhere in
    this module.
    """
    if subunits <= 0:
        raise ValueError("A grant must be positive")
    entry = await _write(
        session,
        tenant_id=tenant_id,
        event_type=EVENT_GRANT,
        subunits_delta=subunits,
        idempotency_key=idempotency_key,
        plan_id=plan_id,
        metadata=metadata,
    )
    if entry is None:
        return False
    await _sync_deficit(session, tenant_id)
    return True


async def consume(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    idempotency_key: str,
    job_candidate_link_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Deduct for one billable event. Returns False when already charged.

    The deduction is NEVER refused (spec §3.3). A completed assessment cannot be
    un-completed, so blocking the charge would simply lose the revenue while the
    customer keeps the work. The balance is allowed to go negative and the
    tenant is flagged; what gets blocked is the NEXT invitation
    (`has_credit_headroom`), which is a thing a human can still choose not to do.
    """
    cost = CONSUMPTION_SUBUNITS.get(event_type)
    if cost is None:
        raise ValueError(f"{event_type} is not a billable consumption event")
    entry = await _write(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        subunits_delta=-cost,
        idempotency_key=idempotency_key,
        job_candidate_link_id=job_candidate_link_id,
        metadata=metadata,
    )
    if entry is None:
        return False
    await _sync_deficit(session, tenant_id)
    return True


async def is_demo_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """A permanent demonstration company, exempt from every billing REFUSAL.

    Read straight from `tenants`, which is a global table with no RLS policy, so
    this answers correctly from a tenant-scoped session and from a webhook's
    bypass scope alike -- the same reason `_sync_deficit` writes there with raw
    SQL.

    Exemption covers refusals and alarms ONLY. Usage is still written to the
    ledger, because the requirement is that the billing UI and the billing logic
    keep working for these tenants, and a billing page with no usage on it
    demonstrates nothing.
    """
    flag = (
        await session.execute(
            text("SELECT is_demo FROM tenants WHERE id = :tid"), {"tid": str(tenant_id)}
        )
    ).scalar()
    return bool(flag)


async def has_credit_headroom(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """May this customer send NEW assessment invitations?

    False once the balance is negative. It stays false until a grant (the next
    billing cycle, or an upgrade) brings it back to zero or above — the ledger
    itself is the recovery condition, so nothing has to remember to clear a flag.

    A demonstration tenant is always true. Checked FIRST, before the balance is
    summed: a demo company that has run assessments has a negative ledger like
    any other, and asking the balance first would gate the one set of accounts
    that must never be gated.
    """
    if await is_demo_tenant(session, tenant_id):
        return True
    return await balance_subunits(session, tenant_id) >= 0


async def summarize(session: AsyncSession, tenant_id: uuid.UUID) -> BalanceSummary:
    """Balance, this month's usage by event type, and the rollover figure."""
    rows = (
        await session.execute(
            select(
                CreditLedgerEntry.event_type,
                func.sum(CreditLedgerEntry.subunits_delta),
            )
            .where(CreditLedgerEntry.tenant_id == tenant_id)
            .group_by(CreditLedgerEntry.event_type)
        )
    ).all()
    granted = sum(int(total) for event, total in rows if event == EVENT_GRANT)
    consumed = -sum(int(total) for event, total in rows if event != EVENT_GRANT)

    # "This month" is the calendar month, matching the monthly billing cycle.
    month_rows = (
        await session.execute(
            select(
                CreditLedgerEntry.event_type,
                func.sum(CreditLedgerEntry.subunits_delta),
            )
            .where(
                CreditLedgerEntry.tenant_id == tenant_id,
                CreditLedgerEntry.event_type != EVENT_GRANT,
                CreditLedgerEntry.created_at
                >= func.date_trunc("month", func.now()),
            )
            .group_by(CreditLedgerEntry.event_type)
        )
    ).all()
    month_by_event = {event: -int(total) for event, total in month_rows}

    # Rollover: everything that happened before this month's first day. That is
    # exactly the balance the customer carried in, which is the number the
    # "nothing expires" promise is about.
    rollover = int(
        (
            await session.execute(
                select(
                    func.coalesce(func.sum(CreditLedgerEntry.subunits_delta), 0)
                ).where(
                    CreditLedgerEntry.tenant_id == tenant_id,
                    CreditLedgerEntry.created_at
                    < func.date_trunc("month", func.now()),
                )
            )
        ).scalar_one()
        or 0
    )

    balance = granted - consumed
    demo = await is_demo_tenant(session, tenant_id)
    in_deficit = balance < 0 and not demo
    return BalanceSummary(
        balance_subunits=balance,
        granted_subunits=granted,
        consumed_subunits=consumed,
        month_by_event=month_by_event,
        rollover_subunits=rollover,
        in_deficit=in_deficit,
        unlimited=demo,
    )
