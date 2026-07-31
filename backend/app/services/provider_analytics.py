"""Per-customer analytics for the Provider Portal (spec §2.2 / §4.1).

Four counters per customer, plus a recent-activity one for the detail panel:

    jobs_posted                  every job the customer has ever created
    jobs_closed                  the 30-day posting window has ended
    jobs_ongoing                 still inside the window or its 5-day grace
    total_candidates_interacted  distinct candidates linked to any of its jobs
    jobs_last_30_days            jobs created in the trailing 30 days

WHY THE COUNTERS ARE COMPUTED IN SQL, IN ONE PASS
--------------------------------------------------
The Provider list renders every customer at once. Computing five counts per
customer in Python would be one query per customer per counter; instead each
counter is a grouped aggregate over all requested tenants and the results are
zipped together. `counts_for_tenants` is therefore the only function the list
endpoint calls, and it issues exactly two statements regardless of customer
count.

THE WINDOW DEFINITIONS COME FROM `services/job_posting`
-------------------------------------------------------
`posting_end_date` and `grace_period_end_date` are GENERATED columns (migration
0018) holding the fixed 30-day window and its 5-day grace tail; nothing here
recomputes them. Boundaries stay INCLUSIVE at the end of each window, matching
`job_posting.posting_status` and claude.md rule 8 — a job exactly on its
`posting_end_date` is still ongoing, not closed.

    NOTE (deliberate, spec §2.2): "closed" and "ongoing" OVERLAP during the
    5-day grace period. The spec defines closed as `now > posting_end_date` and
    ongoing as `now <= grace_period_end_date`, so a job on day 32 counts in
    both — correctly, since its posting has ended while its applicants can
    still edit. They are two independent questions ("is it still taking
    part?" / "has the posting run out?"), not two halves of a partition, and
    the columns are not expected to sum to `jobs_posted`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import JobCandidateLink
from app.models.job import Job

#: The trailing window for `jobs_last_30_days`. Independent of the posting
#: window's 30 days — this one is "recent activity", that one is a lifecycle.
RECENT_ACTIVITY_DAYS = 30


@dataclass(frozen=True)
class CustomerAnalytics:
    """The counters for one customer. Zero-valued for a customer with no jobs
    — a missing group is an absent row, never a null column."""

    jobs_posted: int = 0
    jobs_closed: int = 0
    jobs_ongoing: int = 0
    total_candidates_interacted: int = 0
    jobs_last_30_days: int = 0


EMPTY_ANALYTICS = CustomerAnalytics()


@dataclass
class _Accumulator:
    """Mutable per-tenant tally used while zipping the grouped results."""

    values: dict[str, int] = field(default_factory=dict)


def _now(now: datetime | None) -> datetime:
    """UTC, always. Every timestamp in this database is UTC; reading a naive
    `now` as local time would move a 30-day boundary by hours."""
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


# ── Query builders (pure, so the predicates can be asserted directly) ────────

def jobs_counts_query(tenant_ids: list[uuid.UUID], now: datetime) -> Select:
    """One grouped row per tenant carrying all four job counters.

    Each counter is a FILTERed aggregate rather than a separate statement, so
    adding a fifth counter costs nothing at query time.
    """
    recent_cutoff = now - timedelta(days=RECENT_ACTIVITY_DAYS)
    return (
        select(
            Job.tenant_id,
            func.count().label("jobs_posted"),
            # Closed: the 30-day posting window has ended. Strictly greater —
            # an instant exactly on `posting_end_date` is still active.
            func.count()
            .filter(Job.posting_end_date.is_not(None), Job.posting_end_date < now)
            .label("jobs_closed"),
            # Ongoing: inside the posting window or its grace tail. Inclusive
            # at the end, so ties go to the job still being live.
            func.count()
            .filter(
                Job.grace_period_end_date.is_not(None),
                Job.grace_period_end_date >= now,
            )
            .label("jobs_ongoing"),
            func.count()
            .filter(Job.created_at >= recent_cutoff)
            .label("jobs_last_30_days"),
        )
        .where(Job.tenant_id.in_(tenant_ids))
        .group_by(Job.tenant_id)
    )


def candidates_count_query(tenant_ids: list[uuid.UUID]) -> Select:
    """Distinct candidates who interacted with ANY of the tenant's jobs.

    Grouped on `job_candidate_links.tenant_id` rather than joining `jobs`:
    the link row already carries the tenant (it is the RLS key), so the join
    would only re-derive what is already there.

    DISTINCT matters — one candidate applying to four of a customer's jobs is
    one candidate interacted with, not four.
    """
    return (
        select(
            JobCandidateLink.tenant_id,
            func.count(distinct(JobCandidateLink.candidate_id)),
        )
        .where(JobCandidateLink.tenant_id.in_(tenant_ids))
        .group_by(JobCandidateLink.tenant_id)
    )


# ── Execution ────────────────────────────────────────────────────────────────

async def counts_for_tenants(
    session: AsyncSession,
    tenant_ids: list[uuid.UUID],
    *,
    now: datetime | None = None,
) -> dict[uuid.UUID, CustomerAnalytics]:
    """Analytics for many customers in two statements.

    Returns a mapping keyed by tenant id. A tenant with no jobs is ABSENT from
    the mapping rather than present-and-zero; callers use
    `.get(tid, EMPTY_ANALYTICS)` so the "no activity yet" case renders as
    zeroes without a branch.
    """
    if not tenant_ids:
        return {}
    moment = _now(now)

    tallies: dict[uuid.UUID, dict[str, int]] = {}
    for row in (await session.execute(jobs_counts_query(tenant_ids, moment))).all():
        tallies[row.tenant_id] = {
            "jobs_posted": int(row.jobs_posted or 0),
            "jobs_closed": int(row.jobs_closed or 0),
            "jobs_ongoing": int(row.jobs_ongoing or 0),
            "jobs_last_30_days": int(row.jobs_last_30_days or 0),
        }

    for tenant_id, candidates in (
        await session.execute(candidates_count_query(tenant_ids))
    ).all():
        tallies.setdefault(tenant_id, {})["total_candidates_interacted"] = int(
            candidates or 0
        )

    return {
        tenant_id: CustomerAnalytics(**values)
        for tenant_id, values in tallies.items()
        if tenant_id is not None
    }


async def counts_for_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> CustomerAnalytics:
    """Single-customer convenience for the detail panel."""
    result = await counts_for_tenants(session, [tenant_id], now=now)
    return result.get(tenant_id, EMPTY_ANALYTICS)
