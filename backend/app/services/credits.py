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

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

from app.models.billing import (
    CONSUMPTION_SUBUNITS,
    EVENT_COMPLETED,
    EVENT_GRANT,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    STEM_CONSUMPTION_SUBUNITS,
    SUBUNITS_PER_CREDIT,
    CreditLedgerEntry,
    consumption_subunits,
)

__all__ = [
    "SUBUNITS_PER_CREDIT",
    "CONSUMPTION_SUBUNITS",
    "STEM_CONSUMPTION_SUBUNITS",
    "consumption_subunits",
    "EVENT_COMPLETED",
    "EVENT_GRANT",
    "EVENT_INCOMPLETE",
    "EVENT_NO_SHOW",
    "EVENT_OLD_PROFILE_REVIEW",
    "BalanceSummary",
    "balance_subunits",
    "can_start_assessment",
    "consume",
    "credits_from_subunits",
    "grant",
    "has_credit_headroom",
    "has_positive_balance",
    "is_demo_tenant",
    "LOW_BALANCE_FRACTION",
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

    @property
    def exhausted(self) -> bool:
        """The pool reads zero or worse: new work is blocked (spec §11).

        Distinct from `in_deficit`, and the difference is the threshold. A
        completed assessment is charged even into the negative, so `in_deficit`
        is about work already performed; this is about work about to be started,
        and at exactly zero there is nothing left to start it with.
        """
        return not self.unlimited and self.balance_subunits <= 0

    @property
    def warning_level(self) -> int:
        """The Master Directive Part 5 §4 alert tier: 0 none, 1 LOW (balance
        at or below 20 credits), 2 CRITICAL (at or below 10).

        ABSOLUTE thresholds, replacing the old granted-fraction rule: §4.1
        names 20 and 10 as fixed system values that are not configurable and
        must not be adjusted without a product decision.

        Zero for a customer who was never granted anything: a brand-new
        account with an empty ledger is not "running low", it has not started,
        and an urgent top-up warning on first sign-in is noise that teaches
        people to dismiss the one that matters.
        """
        if self.unlimited or self.granted_subunits <= 0:
            return 0
        if self.balance_subunits <= WARNING_2_CREDITS * SUBUNITS_PER_CREDIT:
            return 2
        if self.balance_subunits <= WARNING_1_CREDITS * SUBUNITS_PER_CREDIT:
            return 1
        return 0

    @property
    def low_balance(self) -> bool:
        """True at either warning tier but not yet exhausted. Kept under its
        established name for every existing consumer; the threshold behind it
        is now the directive's absolute 20-credit line."""
        return not self.exhausted and self.warning_level >= 1

    @property
    def balance_fraction(self) -> float:
        """How much of the granted pool is left, 0.0 to 1.0.

        For the progress meter beside the warning. Never a figure shown to a
        CANDIDATE; the no-numbers rule is about rated output reaching a client,
        and a customer reading their own credit balance is the one place in the
        product where a number is the whole point.
        """
        if self.granted_subunits <= 0:
            return 0.0
        return max(0.0, min(1.0, self.balance_subunits / self.granted_subunits))


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
    # Master Directive Part 5 Rule 5: every purchase resets BOTH warning
    # flags, so Warning 1 fires again when the new combined balance drops back
    # to 20 and Warning 2 again at 10.
    await session.execute(
        text(
            "UPDATE tenants SET credit_warning_1_sent = FALSE, "
            "credit_warning_2_sent = FALSE WHERE id = :tid"
        ),
        {"tid": str(tenant_id)},
    )
    return True


async def consume(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    idempotency_key: str,
    job_candidate_link_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    role_classification: str | None = None,
) -> bool:
    """Deduct for one billable event. Returns False when already charged.

    The deduction is NEVER refused (spec §3.3). A completed assessment cannot be
    un-completed, so blocking the charge would simply lose the revenue while the
    customer keeps the work. The balance is allowed to go negative and the
    tenant is flagged; what gets blocked is the NEXT invitation
    (`has_credit_headroom`), which is a thing a human can still choose not to do.

    `role_classification` is the Job record's STEM flag (Master Directive
    Part 5 Rule 9): STEM bills 90 sub-units for a completed report and 30 for
    a partial; None/unknown bills at the non-STEM rate and is the caller's
    data error to log, never a refusal here. The classification is copied into
    the ledger row's metadata for the audit trail Part 3 §5.2 requires.
    """
    cost = consumption_subunits(event_type, role_classification)
    if cost is None:
        raise ValueError(f"{event_type} is not a billable consumption event")
    entry = await _write(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        subunits_delta=-cost,
        idempotency_key=idempotency_key,
        job_candidate_link_id=job_candidate_link_id,
        metadata={
            **(metadata or {}),
            "role_classification": role_classification or "NON_STEM",
        },
    )
    if entry is None:
        return False
    await _sync_deficit(session, tenant_id)
    await _sync_warning_flags(session, tenant_id)
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


async def can_start_assessment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    role_classification: str | None,
) -> tuple[bool, Decimal, Decimal]:
    """May an assessment START against a job of this classification?

    Master Directive Part 5 §2.3: the pool must hold the FULL cost of the
    report the assessment will produce — 1.5 credits for a STEM job, 1.0 for
    non-STEM — before Vaada begins. A balance of 1.2 credits therefore starts
    a non-STEM assessment and refuses a STEM one. The block is at start, never
    at completion: a conversation already running always finishes and is
    charged even into the negative (Rule 8).

    Returns (allowed, required_credits, balance_credits) so the refusal
    message can state the role type, the credits required, and the current
    balance, exactly as §2.3 requires. A demonstration tenant is always
    allowed, same as every other billing refusal.
    """
    from app.models.billing import EVENT_COMPLETED

    required = consumption_subunits(EVENT_COMPLETED, role_classification)
    assert required is not None  # EVENT_COMPLETED is always billable
    if await is_demo_tenant(session, tenant_id):
        return True, credits_from_subunits(required), credits_from_subunits(
            await balance_subunits(session, tenant_id)
        )
    balance = await balance_subunits(session, tenant_id)
    return (
        balance >= required,
        credits_from_subunits(required),
        credits_from_subunits(balance),
    )


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


# ── Zero-balance gating (spec §11) ───────────────────────────────────────────
# `has_credit_headroom` above answers "may this customer send NEW invitations?"
# and goes false at a NEGATIVE balance, because a completed assessment is
# charged even into the negative: the work is already done and refusing the
# charge would only lose the revenue.
#
# Draft v4 adds a stricter, separate question with a different threshold. Two
# actions are blocked the instant the pool reads ZERO, before the balance can go
# negative at all:
#
#   * creating a job;
#   * advancing any candidate into the assessment stage, across every job,
#     existing or new, regardless of how many un-assessed applicants are already
#     sitting in the pipeline.
#
# The threshold difference is the whole point and is not a subtlety to tidy
# away. "Negative" is the right line for work already performed; "zero" is the
# right line for work about to be started. Together they close the free-ATS gap:
# a recruiter cannot use already-created jobs, or a backlog of applicants, to
# keep assessing candidates once the quota is exhausted.

#: Fraction of the granted pool at or below which the customer is warned. The
#: old client number (spec: "below 30 percent"), retained ONLY because the
#: API contract exposes it as `low_balance_threshold`; the live warning logic
#: is the absolute two-tier rule below.
LOW_BALANCE_FRACTION = 0.30

# ── Two-tier warning alerts (Master Directive Part 5 §4) ─────────────────────
#: Warning 1, LOW BALANCE: fires when the balance falls to or below this many
#: credits. FIXED SYSTEM VALUE — not configurable by the client, not to be
#: adjusted without a product decision (§4.1).
WARNING_1_CREDITS = 20
#: Warning 2, CRITICAL BALANCE: persistent banner + email (+ SMS where
#: configured) at or below this many credits.
WARNING_2_CREDITS = 10
#: Platform default consumption for the alert's remaining-assessments
#: estimate when the account has no 30-day history: the §4.2 mixed
#: STEM/Non-STEM average.
DEFAULT_CREDITS_PER_ASSESSMENT = Decimal("1.2")


async def average_credits_per_assessment(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Decimal:
    """Average credits consumed per completed assessment, last 30 days.

    §4.2's estimate input. Only FULL reports count — partials and no-shows are
    residue of assessments that never produced a report, and folding them in
    would understate what the next real assessment costs. No history → the
    1.2-credit platform default.
    """
    row = (
        await session.execute(
            select(
                func.count(CreditLedgerEntry.id),
                func.coalesce(-func.sum(CreditLedgerEntry.subunits_delta), 0),
            ).where(
                CreditLedgerEntry.tenant_id == tenant_id,
                CreditLedgerEntry.event_type == EVENT_COMPLETED,
                CreditLedgerEntry.created_at >= func.now() - text("interval '30 days'"),
            )
        )
    ).one()
    count, subunits = int(row[0] or 0), int(row[1] or 0)
    if count <= 0 or subunits <= 0:
        return DEFAULT_CREDITS_PER_ASSESSMENT
    return (Decimal(subunits) / Decimal(count) / Decimal(SUBUNITS_PER_CREDIT)).quantize(
        Decimal("0.01")
    )


def estimated_assessments_remaining(balance_subunits: int, average: Decimal) -> int:
    """§4.2: balance ÷ average credits per assessment, rounded DOWN, floor 0."""
    if balance_subunits <= 0 or average <= 0:
        return 0
    balance = Decimal(balance_subunits) / Decimal(SUBUNITS_PER_CREDIT)
    return int(balance / average)


async def has_active_stem_jobs(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Whether the §4.2 alert should note the 1.5-credit STEM rate."""
    flag = (
        await session.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM jobs WHERE tenant_id = :tid "
                "AND role_classification = 'STEM' AND archived_at IS NULL)"
            ),
            {"tid": str(tenant_id)},
        )
    ).scalar()
    return bool(flag)


async def _sync_warning_flags(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Stamp the warning-sent flags and queue the alert email on transition.

    Runs after every deduction. Each tier's email goes out exactly once per
    purchase cycle: the flag is checked and set in the same statement, so a
    burst of concurrent completions cannot double-send, and `grant` resets
    both flags (Rule 5) so the system starts fresh after each top-up.

    The email is a Celery task and the enqueue is best-effort: a broker outage
    must never turn a credit deduction into an error, so the failure is logged
    and the flag stays set (the in-app banner from the summary API still
    shows).
    """
    if await is_demo_tenant(session, tenant_id):
        return
    balance = await balance_subunits(session, tenant_id)
    for level, threshold_credits, flag_column in (
        (2, WARNING_2_CREDITS, "credit_warning_2_sent"),
        (1, WARNING_1_CREDITS, "credit_warning_1_sent"),
    ):
        if balance > threshold_credits * SUBUNITS_PER_CREDIT:
            continue
        transitioned = (
            await session.execute(
                text(
                    f"UPDATE tenants SET {flag_column} = TRUE "
                    f"WHERE id = :tid AND {flag_column} = FALSE RETURNING id"
                ),
                {"tid": str(tenant_id)},
            )
        ).first()
        if transitioned is None:
            continue
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task(
                "pickready.send_credit_warning_email",
                args=[str(tenant_id), level],
            )
        except Exception:  # noqa: BLE001 — alerting must never break billing
            log.warning(
                "credits.warning_email_enqueue_failed tenant=%s level=%s",
                tenant_id, level, exc_info=True,
            )
        break  # the deeper tier subsumes the shallower one for this event


async def has_positive_balance(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """May this customer START new work? (spec §11)

    Strictly greater than zero, and that is deliberate: at exactly zero there is
    nothing left to spend, and letting one more assessment start would be a
    credit spent from an empty pool.

    A demonstration tenant is always true, checked FIRST, for the same reason as
    `has_credit_headroom`: a demo company that has run assessments has a
    negative ledger like any other, and asking the balance first would gate the
    one set of accounts that must never be gated.
    """
    if await is_demo_tenant(session, tenant_id):
        return True
    return await balance_subunits(session, tenant_id) > 0


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
