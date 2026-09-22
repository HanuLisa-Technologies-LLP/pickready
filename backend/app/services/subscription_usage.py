"""The month 10 and month 11 usage summary (change request 27).

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
-----------------------------------------------
It is an INFORMATIONAL letter. Twice, late in a subscription year, a customer
is told what they have used, what is left, what is about to expire, what their
own history projects, and what they can do about it. One of the things they can
do about it is NOTHING, and the copy says so in as many words.

It is not a renewal, not a charge, not a nudge with a deadline, and it writes
no subscription state. `sweep` touches exactly one column, `usage_alert_last_month`,
and that column exists only to stop the letter being sent twice. The existing
`credits._sync_warning_flags` path is the model: it sets a boolean and enqueues
an email, and that is the entire blast radius.

The distinction matters because the two letters read as the same kind of
object and are not. The balance warnings are directive ("Top up immediately")
because the customer is about to be unable to run an assessment. This one fires
on a CALENDAR, at a moment when nothing is wrong, so copy borrowed from the
warning path would be manufacturing an urgency the facts do not support, and a
customer who learns that our urgent messages are not urgent stops reading the
ones that are.

WHY A SUBSCRIPTION MONTH HAD TO BE INVENTED
---------------------------------------------
The product had `subscription_status`, `subscription_current_end` and
`razorpay_subscription_id` and no start date, so "which month of their
subscription is this" was unanswerable. `tenants.subscription_started_at` is
stamped on the FIRST successful charge, never on a /subscribe click: a created
subscription is an intent to pay, and counting months from it would put a
customer whose card was declined into month 10 with no money ever collected.

Months are counted on the CALENDAR, not in 30-day blocks. A customer whose
subscription began on the 14th is in month 10 from the 14th, which is the date
they would count from themselves; 30-day blocks would drift a subscription
year by five days and put the "month 11" letter in what the customer calls
month 12.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import SUBSCRIPTION_ACTIVE
from app.services import credits

#: The subscription months that get a summary. Late enough that a year's usage
#: is a real signal and early enough that a customer who wants to change
#: anything still has a month or two to do it in.
ALERT_MONTHS: tuple[int, ...] = (10, 11)


def subscription_month(started_at: datetime | None, now: datetime) -> int | None:
    """Which month of the subscription `now` falls in. 1-based; None when the
    subscription has never charged.

    Month 1 is the first month, so the customer's own "I have been paying for
    ten months" matches what this returns. Calendar months, clamped at the day
    of the month the subscription started: a subscription that began on the
    31st is in its next month on the 30th of a short month, because the
    alternative is a letter that arrives a day late every other month.
    """
    if started_at is None:
        return None
    elapsed = (now.year - started_at.year) * 12 + (now.month - started_at.month)
    if now.day < started_at.day:
        elapsed -= 1
    if elapsed < 0:
        return None
    return elapsed + 1


@dataclass(frozen=True)
class UsageSummary:
    """Everything the letter states. Composed here so the email, a future
    in-product version of the same notice, and the test all read one shape."""

    tenant_id: uuid.UUID
    subscription_month: int
    #: Completed assessments billed to this customer, ever.
    assessments_used: int
    #: §4.2's projection: balance divided by this customer's own 30-day
    #: average cost per completed assessment. Their history, never a platform
    #: guess, unless they have no history at all.
    assessments_remaining: int
    average_credits_per_assessment: Decimal
    balance_credits: Decimal
    #: The figure already on the billing page under "Carried over from last
    #: month". NOT the three-month validity window; see `BalanceSummary`.
    rollover_credits: Decimal
    #: The part of the balance that never expires, and the part that is about
    #: to. Both stated, because a customer holding 40 never-expiring credits
    #: and 5 expiring ones is in a completely different position from one
    #: holding 45 expiring credits, and one number cannot tell them apart.
    non_expiring_credits: Decimal
    expiring_soon_credits: Decimal
    next_expiry_at: datetime | None


async def build_summary(
    session: AsyncSession, tenant_id: uuid.UUID, month: int
) -> UsageSummary:
    """Gather the figures, at SEND time.

    Computed here rather than carried on the dispatch, for the reason
    `send_credit_warning_email` already gives: a number that was true when the
    task queued and false when it arrived is worse than no number.
    """
    summary = await credits.summarize(session, tenant_id)
    average = await credits.average_credits_per_assessment(session, tenant_id)
    remaining = credits.estimated_assessments_remaining(
        summary.balance_subunits, average
    )
    used = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM credit_ledger "
                    "WHERE tenant_id = :tid AND event_type = 'completed_assessment'"
                ),
                {"tid": str(tenant_id)},
            )
        ).scalar_one()
        or 0
    )
    return UsageSummary(
        tenant_id=tenant_id,
        subscription_month=month,
        assessments_used=used,
        assessments_remaining=remaining,
        average_credits_per_assessment=average,
        balance_credits=summary.balance_credits,
        rollover_credits=credits.credits_from_subunits(summary.rollover_subunits),
        non_expiring_credits=credits.credits_from_subunits(
            summary.non_expiring_subunits
        ),
        expiring_soon_credits=credits.credits_from_subunits(
            summary.expiring_soon_subunits
        ),
        next_expiry_at=summary.next_expiry_at,
    )


async def due_tenants(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[tuple[uuid.UUID, int]]:
    """Active subscribers sitting in an alert month who have not had that
    month's letter. Returns (tenant_id, subscription month).

    The month arithmetic is done in SQL so the filter and the ordering are one
    query rather than a scan of every tenant into Python. It is the same
    expression `subscription_month` implements, and
    `tests/test_subscription_usage_alerts.py` compares the two over a range of
    dates: two copies of one fact stay honest only when something checks them.

    Asks the TABLE. A sweep keyed on "last swept at" would skip exactly the
    tenant whose previous run died between the stamp and the send.
    """
    months = ", ".join(str(month) for month in ALERT_MONTHS)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT id, month_index FROM (
                    SELECT
                        id,
                        (
                            (EXTRACT(YEAR FROM CAST(:now AS timestamptz))
                             - EXTRACT(YEAR FROM subscription_started_at)) * 12
                            + (EXTRACT(MONTH FROM CAST(:now AS timestamptz))
                               - EXTRACT(MONTH FROM subscription_started_at))
                            - CASE
                                WHEN EXTRACT(DAY FROM CAST(:now AS timestamptz))
                                     < EXTRACT(DAY FROM subscription_started_at)
                                THEN 1 ELSE 0
                              END
                            + 1
                        )::int AS month_index,
                        usage_alert_last_month
                    FROM tenants
                    WHERE subscription_started_at IS NOT NULL
                      AND subscription_status = :active
                ) AS scoped
                WHERE month_index IN ({months})
                  AND (usage_alert_last_month IS NULL
                       OR usage_alert_last_month < month_index)
                ORDER BY id
                LIMIT :limit
                """
            ),
            {"now": now, "active": SUBSCRIPTION_ACTIVE, "limit": limit},
        )
    ).all()
    return [(row[0], int(row[1])) for row in rows]


async def claim_month(
    session: AsyncSession, tenant_id: uuid.UUID, month: int
) -> bool:
    """Take ownership of this tenant's month-`month` letter. True for the one
    caller that gets it.

    The check and the write are ONE statement, the shape
    `_sync_warning_flags` already uses: a re-run, a redelivered dispatch and
    two sweeps overlapping all resolve to a single letter, because the loser's
    UPDATE matches zero rows rather than reading a value somebody else is
    about to change.

    `<` rather than `IS DISTINCT FROM` so the latch only ever moves forward. A
    tenant who somehow got the month-11 letter first does not then get month
    10, which would be a summary of a period they have already been told about.
    """
    claimed = (
        await session.execute(
            text(
                "UPDATE tenants SET usage_alert_last_month = :month "
                "WHERE id = :tid AND (usage_alert_last_month IS NULL "
                "OR usage_alert_last_month < :month) RETURNING id"
            ),
            {"month": month, "tid": str(tenant_id)},
        )
    ).first()
    return claimed is not None
