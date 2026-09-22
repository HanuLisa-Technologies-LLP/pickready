"""When a candidate last did, or received, anything (feature 8).

WHY THIS IS A SERVICE AND NOT A HELPER IN `api/portal`
--------------------------------------------------------
It was a helper in `api/portal`, reachable only through `_candidate_for_user`,
which is to say reachable only from an authenticated HTTP request that the
candidate themselves made. The specification counts a JOB MATCHING EMAIL THE
CANDIDATE MERELY RECEIVES as engagement, which is deliberate: it resets the
clock for somebody the platform is still actively putting in front of
employers, and it is the one engagement signal that does not require the
person to do anything. A worker cannot call a request-scoped helper, so under
the old shape that half of the rule could not be implemented at all.

The clock this feeds erases profiles. A rule that is only half recorded is a
rule that erases the people it was written to protect: somebody the platform
mails about a job every month, who never signs in, reads as dormant.

WHAT IS UNCHANGED
-------------------
The debounce, the chokepoint discipline, and the transaction coupling. The
stamp is still written at most once a day, the portal still records it in ONE
place rather than per handler, and this function still does not commit: the
caller's session owns the transaction, so a request or a task that rolled back
leaves no evidence that it happened.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: How stale the stamp has to be before it is rewritten. A day, because the
#: clock it feeds is measured in MONTHS: writing on every request would add an
#: UPDATE to every authenticated candidate page load to sharpen a number
#: nothing reads at that resolution.
ENGAGEMENT_DEBOUNCE = timedelta(days=1)


class _HasEngagementStamp(Protocol):
    """Just enough of `models.Candidate` to be stamped.

    A protocol rather than the model, so this module imports no ORM mapping and
    the pure half of the rule (does this stamp need rewriting) can be tested
    against a plain object.
    """

    last_engagement_at: datetime | None


def is_due(previous: datetime | None, now: datetime) -> bool:
    """Whether the stamp is stale enough to rewrite.

    NULL means nothing has ever been recorded, which is due: the alternative,
    treating it as recent, would leave a candidate's first engagement unwritten
    and start their dormancy clock at registration for ever.
    """
    return previous is None or now - previous >= ENGAGEMENT_DEBOUNCE


def record_engagement(
    candidate: _HasEngagementStamp, *, now: datetime | None = None
) -> bool:
    """Note that this candidate is still in play. True when the stamp moved.

    NOT A COMMIT, and not a flush. The caller's session owns the transaction so
    the stamp lands with whatever the caller was already doing, or with nothing
    if that failed.
    """
    moment = now or datetime.now(timezone.utc)
    if not is_due(candidate.last_engagement_at, moment):
        return False
    candidate.last_engagement_at = moment
    return True


async def record_engagement_by_id(
    session: AsyncSession,
    candidate_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
) -> bool:
    """The same rule, for a caller that holds an id rather than a loaded row.

    This is the worker's door. A task sending a job-matching email has a
    candidate id and no ORM object, and loading one to move a single timestamp
    would pull a whole profile through the session for nothing.

    THE DEBOUNCE IS IN THE STATEMENT, not around it, so two workers writing at
    the same moment cannot both decide the stamp is due and then both write:
    the second UPDATE matches no row and reports False. Returns whether THIS
    call moved it.

    Does not commit, for the same reason the in-memory form does not.
    """
    moment = now or datetime.now(timezone.utc)
    result = await session.execute(
        text(
            "UPDATE candidates SET last_engagement_at = :now "
            "WHERE id = :candidate_id "
            "AND (last_engagement_at IS NULL OR last_engagement_at <= :cutoff) "
            "RETURNING id"
        ),
        {
            "now": moment,
            "candidate_id": str(uuid.UUID(str(candidate_id))),
            "cutoff": moment - ENGAGEMENT_DEBOUNCE,
        },
    )
    return result.first() is not None
