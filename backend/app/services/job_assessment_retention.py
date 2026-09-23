"""Thirty days between closing a job and losing its assessment data.

OWNER RULING, 2026-09-22, AND IT REVERSES THE ONE BEFORE IT
-------------------------------------------------------------
The 2026-09-18 vivekium C5 ruling made job closure a HARD DELETE, inline and
immediate: `POST /jobs/{id}/close` called `erasure.job_closure_erasure` in the
close transaction and the PRISM reports, the Tatva evaluations, the
transcripts, the per-candidate question sets and the assessment chunks were
gone before the response was written. That is superseded. Closure now begins a
THIRTY DAY soft deletion, and the reason the owner gave is the one this module
is shaped around: closure is terminal and has no reopen, so an immediate
irreversible delete made a misclick, a wrong job id, or a dispute raised the
following week unanswerable. A deletion nobody can pause is a deletion that
eventually destroys the evidence for the argument it caused.

What did NOT change is the promise the candidate was given. Stage B consent
item 3 still says the assessment report lives only as long as the position, and
it still becomes true: the employer and the recruiter lose access at the
instant of closure, and the bytes are gone thirty days later. What is added
between those two moments is a NAMED, CAPABILITY-GATED, AUDITED dispute path
and nothing else.

THE THREE STATES
------------------
    live               The job is not closed. Ordinary access.

    pending_deletion   Closed, inside the retention window. WITHHELD from
                       everybody who does not hold
                       `retrieve_disputed_assessment` AND has not opened a
                       dispute on this job. The rows and the objects are all
                       still there, which is the whole point: the data has to
                       survive for the dispute path to have anything to
                       retrieve.

    purged             The sweep ran and confirmed everything gone. Terminal,
                       with no ordinary recovery path.

`describe` derives all three from columns on `jobs`, the way `profile_age` and
`posting_status` are derived: a stored state word would be a second answer that
can disagree with the timestamps it was computed from.

WHY THE DUE DATE IS STORED AND THE STATE IS NOT
-------------------------------------------------
`jobs.assessment_purge_due_at` is written at closure rather than computed from
`closed_at` plus `RETENTION_DAYS` on every read. The window is a PROMISE made
to the person who clicked Close, in the confirmation dialog, in their own
words: "you have thirty days". Recomputing it from a module constant means
editing that constant silently moves a deadline for every job already closed,
in both directions, which is a thing no deployment change should be able to do.
The state word is derived because nothing was promised about it.

WHY THIS IS NOT `services/deletion_requests`
----------------------------------------------
That machine (`pending -> rows_erased -> completed`) is the CANDIDATE erasure,
and it was examined first, because rule 5 says one implementation per concept.
It does not fit, for three reasons that are about its subject rather than its
quality:

* its `pending` state is reachable only in the instant between opening the
  record and running the cascade, and it exists to describe a crash. This
  feature's middle state is a deliberate thirty day wait, which that machine
  has no edge for and no column to hold;
* it is keyed on `candidate_id` with no job, and a `job_id` is not an optional
  extra here, it IS the scope of the deletion;
* its `object_keys_json` is captured BEFORE the rows go, because after a
  candidate cascade nothing in the database names those objects. The job case
  is the opposite way round and that is what fixes the ORDER below: the rows
  that name a closed job's media survive right up until they are deleted, so
  the objects must go FIRST and the rows only once the store has confirmed.

What IS reused rather than rewritten: `erasure.job_closure_erasure` for the
rows (unchanged blast radius, and `tests/test_job_closure_erasure.py` still
pins it), `assessment_media_retention.object_keys_for_job` and `delete_objects`
for the media, which that module's own docstring names as the job-closure hook,
and the never-give-up discipline of `services/deletion_requests`: there is no
terminal failure state here either, only an attempt counter and a recorded
class name.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.services import assessment_media_retention as media_retention
from app.services import audit, erasure
from app.services.capabilities import RETRIEVE_DISPUTED_ASSESSMENT

logger = logging.getLogger(__name__)

#: The owner's number, 2026-09-22. A MODULE CONSTANT rather than a setting,
#: unlike `assessment_media_retention_days`, and the difference is who the
#: number was promised to. The media window is an operator's retention posture
#: with nobody outside the company relying on a particular value; this one is
#: printed in the confirmation dialog a hiring manager reads before an
#: irreversible click, so a deployment that could quietly change it could
#: quietly make that sentence false.
RETENTION_DAYS = 30

#: Derived, never stored. See the module docstring.
STATE_LIVE = "live"
STATE_PENDING_DELETION = "pending_deletion"
STATE_PURGED = "purged"

#: The audit actions. `job_assessment_data_erased` is UNCHANGED and still
#: written by the purge, because it means the same thing it always did (this
#: job's assessment artifacts are gone) and rewriting the string would make
#: every row already in `audit_log` unreadable against the current vocabulary.
#: What moved is WHEN it is written, not what it says.
ACTION_PENDING_DELETION = "job_assessment_pending_deletion"
ACTION_DISPUTE_OPENED = "job_assessment_dispute_opened"
ACTION_DISPUTE_CLOSED = "job_assessment_dispute_closed"

#: THE ONE AUTHOR OF THE REFUSAL SENTENCE, the rule
#: `components/permission-notice.tsx` already follows on the other side of the
#: wire: a message written at a call site is a message that will eventually
#: contradict the server. Both are deliberately free of any candidate detail,
#: because a refusal that named the report it is refusing would leak the fact
#: that a particular person was assessed.
WITHHELD_MESSAGE = (
    "This job is closed, so its assessment records are no longer available "
    "here. They are retained for a short period and can be retrieved only "
    "through the assessment dispute process."
)
PURGED_MESSAGE = (
    "This job is closed and its assessment records have been permanently "
    "deleted. There is no way to retrieve them."
)


class _ClosableJob(Protocol):
    """What `describe` reads. A Protocol rather than `Job`, so the pure half
    of this module is testable without the ORM, the same split
    `services/deletion_requests` makes for the same reason."""

    closed_at: datetime | None
    assessment_purge_due_at: datetime | None
    assessment_purged_at: datetime | None
    assessment_dispute_opened_at: datetime | None


class _Principal(Protocol):
    """The three fields the grant engine needs from the caller.

    A Protocol rather than `api.deps.CurrentUser`, so a service never imports
    the API layer: the dependency runs one way in this codebase and an import
    the other way is the first half of a cycle nobody meant to create.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: object


@dataclass(frozen=True)
class RetentionState:
    """Where one job's assessment data is in its lifecycle.

    `days_remaining` is None outside `pending_deletion`, never 0: a live job
    has no countdown, and reporting zero for one would read as "it goes
    today".
    """

    state: str
    closed_at: datetime | None
    purge_due_at: datetime | None
    purged_at: datetime | None
    dispute_open: bool
    days_remaining: int | None

    @property
    def withheld(self) -> bool:
        """True when ordinary access is refused. Both closed states refuse."""
        return self.state != STATE_LIVE

    @property
    def retrievable(self) -> bool:
        """True while the dispute path still has something to hand back."""
        return self.state == STATE_PENDING_DELETION

    def message(self) -> str | None:
        if self.state == STATE_PENDING_DELETION:
            return WITHHELD_MESSAGE
        if self.state == STATE_PURGED:
            return PURGED_MESSAGE
        return None

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "purge_due_at": (
                self.purge_due_at.isoformat() if self.purge_due_at else None
            ),
            "purged_at": self.purged_at.isoformat() if self.purged_at else None,
            "dispute_open": self.dispute_open,
            "days_remaining": self.days_remaining,
        }


def purge_due_at(closed_at: datetime) -> datetime:
    """The instant this job's assessment data becomes deletable."""
    return closed_at + timedelta(days=RETENTION_DAYS)


def describe(job: _ClosableJob, *, now: datetime | None = None) -> RetentionState:
    """Derive the lifecycle state from the columns. Pure, no database.

    A job whose window has EXPIRED but whose sweep has not run yet reads as
    `purged`, deliberately. The alternative is to keep answering
    `pending_deletion` until a worker happens to run, which would let a
    scheduler outage silently extend a retention window the employer was told
    the end of, and would let the dispute path hand back data past the date
    the candidate was promised it would be gone. The sweep catches up; the
    access answer does not wait for it.
    """
    moment = now or datetime.now(timezone.utc)
    closed_at = job.closed_at
    if closed_at is None:
        return RetentionState(
            state=STATE_LIVE,
            closed_at=None,
            purge_due_at=None,
            purged_at=None,
            dispute_open=False,
            days_remaining=None,
        )
    due = job.assessment_purge_due_at
    purged = job.assessment_purged_at
    dispute_open = job.assessment_dispute_opened_at is not None
    # A job closed BEFORE this feature shipped carries no due date and its data
    # was hard deleted by the ruling this one supersedes. `purged` is the only
    # truthful answer for it: there is nothing to retain and nothing to
    # retrieve. Migration 0112 stamps `assessment_purged_at` on exactly those
    # rows so the sweep never enumerates them, and this branch is what keeps a
    # row the migration could not reach honest anyway.
    if due is None or purged is not None or moment >= due:
        return RetentionState(
            state=STATE_PURGED,
            closed_at=closed_at,
            purge_due_at=due,
            purged_at=purged,
            dispute_open=dispute_open,
            days_remaining=None,
        )
    remaining = due - moment
    return RetentionState(
        state=STATE_PENDING_DELETION,
        closed_at=closed_at,
        purge_due_at=due,
        purged_at=None,
        dispute_open=dispute_open,
        # Rounded UP, and the floor of 1 is the load-bearing half: a job with
        # nineteen hours left has to read as "1 day", never as "0 days", or a
        # reader deciding whether to raise a dispute today is told the window
        # has already shut while the gate is still letting them in.
        days_remaining=max(1, -(-remaining // timedelta(days=1))),
    )


async def require_readable(
    session: AsyncSession,
    user: _Principal,
    job: _ClosableJob,
    *,
    now: datetime | None = None,
) -> RetentionState:
    """THE ACCESS GATE. Raises 410 unless this caller may read the data.

    Called at every recruiter-facing read of a job's assessment artifacts,
    AFTER the tenant check and BEFORE the artifact is loaded. Ordering is the
    rule `services/tools` already states: a refusal that ran the handler first
    has already read the row it was refusing to show.

    410 GONE rather than 403 or 404. 403 would say "ask somebody for
    permission", which is wrong: nobody on the employer's side can grant this
    except through the dispute path. 404 would say the application does not
    exist, which is false and would make the recruiter doubt their own
    pipeline. 410 is the one status that means the thing was here and is not
    any more, which is exactly what happened.

    THE DISPUTE PATH NEEDS BOTH HALVES. Holding
    `retrieve_disputed_assessment` is not enough on its own and neither is an
    open dispute: the capability says this person may work a dispute, and the
    open dispute says THIS job is the one being disputed. Either alone would
    turn a narrow retrieval into a standing ability to read every closed job
    in the tenant.
    """
    state = describe(job, now=now)
    if not state.withheld:
        return state
    if state.retrievable and state.dispute_open:
        if await _may_retrieve_disputed(session, user):
            logger.info(
                "job_assessment_retention.dispute_read job_id=%s user_id=%s",
                getattr(job, "id", None),
                user.user_id,
            )
            return state
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=state.message())


async def _may_retrieve_disputed(
    session: AsyncSession, user: _Principal
) -> bool:
    """Does this caller hold the dispute capability right now?

    Resolved per request through the same engine `require_capability` uses,
    never from a snapshot taken at sign-in: revoking the dispute capability
    from somebody who has left the dispute team has to take effect now.
    """
    from app.services import rbac  # noqa: PLC0415 -- avoids an import cycle

    return await rbac.has_capability(
        session,
        user.tenant_id,
        user.role,
        RETRIEVE_DISPUTED_ASSESSMENT,
        user.user_id,
    )


@dataclass(frozen=True)
class PurgeOutcome:
    """What one purge pass achieved for one job. Counts, never content."""

    job_id: uuid.UUID
    completed: bool
    media_deleted: int
    media_remaining: int
    media_failure: str | None
    rows: erasure.JobClosureReceipt | None

    def as_json(self) -> dict[str, object]:
        return {
            "job_id": str(self.job_id),
            "completed": self.completed,
            "media_deleted": self.media_deleted,
            "media_remaining": self.media_remaining,
            "media_failure": self.media_failure,
            "rows": self.rows.as_json() if self.rows is not None else None,
        }


async def jobs_due_for_purge(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 200
) -> list[Job]:
    """Closed jobs whose retention window has expired and whose data is still
    here.

    ASKS THE TABLE, never a "swept at" stamp on something else (rule 8).
    `assessment_purged_at IS NULL` is what makes the sweep converge: a job the
    purge finished is never enumerated again, and a job whose object store
    refused is enumerated on the very next run with its attempt counter one
    higher.

    THE SESSION MUST BE IN A BYPASS SCOPE. The sweep has no tenant, and a
    tenant-scoped session would purge the subset it can see while reporting a
    complete pass.
    """
    moment = now or datetime.now(timezone.utc)
    return list(
        (
            await session.execute(
                select(Job)
                .where(
                    Job.closed_at.is_not(None),
                    Job.assessment_purge_due_at.is_not(None),
                    Job.assessment_purge_due_at <= moment,
                    Job.assessment_purged_at.is_(None),
                )
                .order_by(Job.assessment_purge_due_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def purge_job(session: AsyncSession, job: Job) -> PurgeOutcome:
    """Permanently delete one closed job's assessment data. Idempotent.

    OBJECTS FIRST, ROWS SECOND, AND THE ORDER IS THE WHOLE DESIGN.
    `video_recordings.conversation_id` is ON DELETE CASCADE, so deleting
    `assessment_conversations` takes the recording ROWS with it, and those rows
    are the only thing in the database that names the S3 keys. Deleting the
    rows first would leave the media addressable for ever with nothing left to
    enumerate it: the same orphan-object trap `erasure.candidate_object_keys`
    exists for, arriving from the other direction.

    So a pass that cannot confirm every object gone does NOT delete the rows.
    It records the failure, leaves `assessment_purged_at` null, and the hourly
    sweep tries again with a shorter list. There is no terminal failure state,
    for the reason `services/deletion_requests` gives: "we stopped trying to
    delete this" is not an outcome this product may reach.

    Safe to call on a job that is already purged, which is what makes a
    redelivered dispatch and a sweep arriving together harmless.

    Does not commit. The caller owns the transaction, because the row deletion
    and the audit row that records it must land together or not at all.
    """
    from fastapi.concurrency import run_in_threadpool  # noqa: PLC0415

    if job.assessment_purged_at is not None:
        return PurgeOutcome(
            job_id=job.id,
            completed=True,
            media_deleted=0,
            media_remaining=0,
            media_failure=None,
            rows=None,
        )

    entries = await media_retention.object_keys_for_job(session, job.id)
    # `delete_objects` is synchronous, like every object-store call in this
    # codebase, and it HEAD-confirms each key. The threadpool hop is the
    # caller's job by that module's own contract.
    media = await run_in_threadpool(media_retention.delete_objects, entries)

    now = datetime.now(timezone.utc)
    job.assessment_purge_attempts = int(job.assessment_purge_attempts or 0) + 1
    if not media.finished or media.failure is not None:
        job.assessment_purge_last_failure = media.failure or "ObjectStillPresent"
        logger.warning(
            "job_assessment_retention.purge_incomplete job_id=%s remaining=%d "
            "attempts=%d failure=%s",
            job.id,
            len(media.remaining),
            job.assessment_purge_attempts,
            job.assessment_purge_last_failure,
        )
        await session.flush()
        return PurgeOutcome(
            job_id=job.id,
            completed=False,
            media_deleted=media.deleted,
            media_remaining=len(media.remaining),
            media_failure=job.assessment_purge_last_failure,
            rows=None,
        )

    receipt = await erasure.job_closure_erasure(session, job_id=job.id)
    job.assessment_purged_at = now
    job.assessment_purge_last_failure = None
    # The dispute is closed by the data going. Leaving the stamp would make a
    # purged job read as "somebody has unlocked this", and the gate would then
    # be relying on `retrievable` alone to refuse it.
    job.assessment_dispute_opened_at = None
    outcome = PurgeOutcome(
        job_id=job.id,
        completed=True,
        media_deleted=media.deleted,
        media_remaining=0,
        media_failure=None,
        rows=receipt,
    )
    await audit.record_action(
        session,
        action=erasure.ACTION_JOB_ASSESSMENT_ERASED,
        # No human principal: a clock decided. The REASON goes in the facts
        # rather than in `actor_role`, which is varchar(30) and means the role
        # of the person who acted; the dormancy erasure learned that the
        # expensive way.
        actor_user_id=None,
        actor_role=None,
        tenant_id=job.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        new_state=outcome.as_json(),
        metadata={"reason": "retention_window_expired"},
    )
    await session.flush()
    logger.info(
        "job_assessment_retention.purged job_id=%s media=%d reports=%d "
        "conversations=%d chunks=%d",
        job.id,
        media.deleted,
        receipt.reports_deleted,
        receipt.conversations_deleted,
        receipt.chunks_deleted,
    )
    return outcome
