"""Operational Hygiene: unresolved applicant statuses on earlier jobs.

Provenance: add-features-specdoc (2026-09-05), "Operational Hygiene" section.
The employer should update all applicant statuses from earlier jobs before
posting a new one. The locked consideration is that this is a STRONG REMINDER
and never a hard block: an urgent hire must not stall on unrelated old-job
cleanup. Nothing in this module refuses anything; it only reports.

WHAT COUNTS AS AN "EARLIER" JOB
--------------------------------
A job whose posting is over: closed early by the client (`jobs.closed_at`,
migration 0077, checked through `job_posting.posting_status` so the closure
dominates the dates exactly as it does everywhere else), or past its 30-day
active window (grace and expired both qualify -- the grace tail accepts no new
application, so the posting is over for hygiene purposes too). A job still
ACTIVE or merely SCHEDULED is not "earlier": its applicants are being worked,
and nagging about them would train recruiters to dismiss the reminder.
Archived jobs are excluded: archiving is the deliberate "stop showing me this"
switch, and a reminder that resurrects what a client explicitly hid would
contradict the one control they have for silencing it.

WHAT COUNTS AS "UNRESOLVED"
----------------------------
Any application not in a terminal pipeline stage. The terminal set is read
from `hiring_pipeline.TERMINAL` (rejected, joined) rather than restated here,
so a stage added to the FSM cannot silently drift this module. Two deliberate
additions on top of it:

* `sourced` is NOT unresolved. Gate 5: a sourced row is a resume in the
  recruiter's filing cabinet, not an application. Nobody applied, so nobody is
  owed a status update, and counting them would inflate the reminder with
  people the workflow never promised anything to.
* `hold` IS unresolved. A pause is a decision to decide later, and on a job
  that has ended "later" has arrived. The reminder is exactly the surface
  where a forgotten hold should resurface.

The legacy `offered` synonym is non-terminal and counted, like the
`offer_extended` it normalises to.

THE COUNTS ARE ALLOWED HERE
----------------------------
The no-numbers rule forbids a candidate's score, grade or rank reaching a
client. These counts are the tenant's own operational metric -- how many of
their applications they have left hanging -- and say nothing about any
candidate's quality. Same category as the dashboard's funnel counts.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import hiring_pipeline, job_posting

#: Stages that need no further action from the recruiter. Terminal stages from
#: the FSM, plus `sourced`, which is before the funnel rather than in it.
RESOLVED_STATUSES: frozenset[str] = hiring_pipeline.TERMINAL | {
    hiring_pipeline.SOURCED
}

#: Posting states in which a job counts as "earlier". Read-time states from
#: `job_posting.posting_status`, which already makes closure dominate dates.
EARLIER_POSTING_STATUSES: frozenset[str] = frozenset(
    {
        job_posting.STATUS_CLOSED,
        job_posting.STATUS_GRACE,
        job_posting.STATUS_EXPIRED,
    }
)


class JobHygieneItem(BaseModel):
    """One earlier job that still has applications awaiting a status update."""

    job_id: uuid.UUID
    title: str
    posting_status: str
    unresolved_count: int


class StatusHygieneSummary(BaseModel):
    """Everything the pre-check reminder renders. Empty lists mean all clear."""

    jobs: list[JobHygieneItem]
    total_unresolved: int


async def unresolved_summary(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
) -> StatusHygieneSummary:
    """Per-job counts of unresolved applications on this tenant's earlier jobs.

    One grouped query over the mirror column (`job_candidate_links.status`,
    which `apply_transition` keeps in step with the authoritative history),
    then the posting-state filter applied through `job_posting.posting_status`
    in Python. The window boundaries live in ONE module by rule; duplicating
    the five-state arithmetic in SQL here would be a second implementation of
    the concept that drifts the day the window rules change.

    The tenant filter is defense in depth; RLS is the real boundary.
    """
    rows = (
        await session.execute(
            text(
                "SELECT j.id, j.title, j.posting_start_date, "
                "       j.posting_end_date, j.grace_period_end_date, "
                "       j.closed_at, COUNT(l.id) AS unresolved "
                "FROM jobs j "
                "JOIN job_candidate_links l ON l.job_id = j.id "
                "WHERE j.tenant_id = :tid "
                "  AND j.archived_at IS NULL "
                "  AND l.archived_at IS NULL "
                "  AND l.status NOT IN :resolved "
                "GROUP BY j.id, j.title, j.posting_start_date, "
                "         j.posting_end_date, j.grace_period_end_date, "
                "         j.closed_at "
                "ORDER BY COUNT(l.id) DESC, j.title, j.id"
            ).bindparams(bindparam("resolved", expanding=True)),
            {"tid": str(tenant_id), "resolved": sorted(RESOLVED_STATUSES)},
        )
    ).mappings().all()

    items: list[JobHygieneItem] = []
    for row in rows:
        status = job_posting.posting_status(
            row["posting_start_date"],
            row["posting_end_date"],
            row["grace_period_end_date"],
            now,
            row["closed_at"],
        )
        if status not in EARLIER_POSTING_STATUSES:
            continue
        items.append(
            JobHygieneItem(
                job_id=row["id"],
                title=row["title"],
                posting_status=status,
                unresolved_count=int(row["unresolved"]),
            )
        )
    return StatusHygieneSummary(
        jobs=items,
        total_unresolved=sum(item.unresolved_count for item in items),
    )
