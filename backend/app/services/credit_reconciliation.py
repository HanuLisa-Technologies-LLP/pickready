"""Nightly credit reconciliation for abandoned assessments (killer-spec §3.2).

A completed assessment charges itself the moment it completes. The other two
consumption events have no moment: nothing happens when a candidate stops
replying, and nothing happens when they never open the link at all. Those are
charged here, by a daily sweep, once the reminder sequence has been exhausted
and the outcome is settled.

Two invariants:

- **Charge once.** `assessment_conversations.credit_reconciled_at` is stamped in
  the same transaction as the ledger entry, and the ledger's idempotency key is
  derived from the conversation id, so neither a re-run nor a redelivered task
  can charge twice.
- **Never charge an open assessment.** A conversation is only settled once the
  reminder window has fully elapsed. Until then it is left alone, because a
  candidate who finishes on day six must be billed as completed (60), not as
  incomplete (20).

It also REPAIRS a lost invitation email (PLAN-p3 WP1). The invitation email is
dispatched after the invitation commits (`services/assessment_invitations`),
so an invoke that fails leaves an invited candidate who was never told, and
who would then be settled here as a no-show a week later for an assessment
they never heard of. Each run asks the TABLE, never a timestamp, for invited
applications with no invitation email and re-dispatches the task, which is
idempotent on the application. The window closes at the first reminder: from
then on the reminder carries the same assessment link, and an invitation
arriving after a reminder reads as a mistake. Anything older is COUNTED and
reported at ERROR for a person to look at, never silently dropped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import EVENT_INCOMPLETE, EVENT_NO_SHOW
from app.models.email_log import EMAIL_TYPE_ASSESSMENT_INVITATION
from app.services import credits
from app.services import hiring_pipeline as pipeline
from app.workers.dispatch import dispatch_after_commit

log = logging.getLogger(__name__)

#: Hours after the invitation at which each reminder goes out. Two nudges, then
#: the candidate has had their chance.
REMINDER_SCHEDULE_HOURS: tuple[int, ...] = (24, 72)

#: An invitation is settled this long after it was sent — comfortably past the
#: last reminder, so "reminders exhausted" is true by construction rather than
#: by a race against the reminder task.
SETTLE_AFTER_HOURS = 24 * 7

#: An invitation with no email this long after it committed was not picked up
#: by the dispatch that followed the commit. Fifteen minutes is past every
#: draft's retries (`send_assessment_invitation` is three attempts at the
#: thirty second drafting budget), so a gap this old is a lost invoke, not a
#: slow one. Re-dispatching one that IS still running is safe: the task is
#: idempotent on the application's invitation email.
INVITATION_REPAIR_AFTER_MINUTES = 15

#: The task that drafts and queues one invitation email.
INVITATION_TASK = "pickready.send_assessment_invitation"


def ledger_key(conversation_id, event_type: str) -> str:
    """One key per (conversation, outcome). The outcome is part of the key so a
    conversation that was charged as a no-show and later somehow completes is
    charged for the completion too, rather than being silently free."""
    return f"assessment:{conversation_id}:{event_type}"


@dataclass
class ReconciliationResult:
    incomplete_charged: int = 0
    no_show_charged: int = 0
    reminders_queued: int = 0
    skipped_already_charged: int = 0
    tenants_in_deficit: list[str] = field(default_factory=list)
    #: Lost invitation emails re-dispatched this run.
    invitations_redispatched: int = 0
    #: Invited applications with no invitation email, past the repair window.
    #: Reported, never sent: see the module docstring.
    invitations_missing_unrepaired: int = 0

    def as_dict(self) -> dict:
        return {
            "incomplete_charged": self.incomplete_charged,
            "no_show_charged": self.no_show_charged,
            "reminders_queued": self.reminders_queued,
            "invitations_redispatched": self.invitations_redispatched,
            "invitations_missing_unrepaired": self.invitations_missing_unrepaired,
            "skipped_already_charged": self.skipped_already_charged,
            "tenants_in_deficit": self.tenants_in_deficit,
        }


_SETTLED_SQL = text(
    """
    SELECT c.id,
           c.tenant_id,
           c.job_candidate_link_id,
           c.started_at,
           j.role_classification
      FROM assessment_conversations c
      LEFT JOIN job_candidate_links l ON l.id = c.job_candidate_link_id
      LEFT JOIN jobs j ON j.id = l.job_id
     WHERE c.credit_reconciled_at IS NULL
       AND c.completed_at IS NULL
       AND c.invitation_sent_at IS NOT NULL
       AND c.invitation_sent_at < :cutoff
     ORDER BY c.invitation_sent_at
     LIMIT :batch
    """
)

#: Invited, still waiting to start, and no invitation email of ANY status.
#: A failed or bounced email is a delivery problem with its own record and is
#: not re-sent from here; only a MISSING row is a lost dispatch. A candidate
#: with no address is left out: the task could only skip them again.
_MISSING_INVITATION_WHERE = """
      FROM assessment_conversations c
      JOIN job_candidate_links l ON l.id = c.job_candidate_link_id
      JOIN candidates cand ON cand.id = l.candidate_id
     WHERE c.invitation_sent_at IS NOT NULL
       AND c.started_at IS NULL
       AND c.completed_at IS NULL
       AND c.credit_reconciled_at IS NULL
       AND l.status = :invited_status
       AND NULLIF(btrim(cand.email), '') IS NOT NULL
       AND NOT EXISTS (
             SELECT 1 FROM email_log e
              WHERE e.job_candidate_link_id = c.job_candidate_link_id
                AND e.email_type = :email_type
           )
"""

_MISSING_INVITATION_SQL = text(
    "SELECT c.job_candidate_link_id, c.invited_by"
    + _MISSING_INVITATION_WHERE
    + """
       AND c.invitation_sent_at < :stale_before
       AND c.invitation_sent_at >= :window_start
     ORDER BY c.invitation_sent_at
     LIMIT :batch
    """
)

_UNREPAIRED_INVITATION_SQL = text(
    "SELECT count(*)"
    + _MISSING_INVITATION_WHERE
    + "   AND c.invitation_sent_at < :window_start"
)

_LINK_CLASSIFICATION_SQL = text(
    """
    SELECT j.id AS job_id, j.role_classification
      FROM job_candidate_links l
      JOIN jobs j ON j.id = l.job_id
     WHERE l.id = :lid
    """
)


async def _job_classification(
    session: AsyncSession, job_candidate_link_id
) -> tuple[str | None, str | None]:
    """(job_id, role_classification) for one link, or (None, None).

    Part 5 Rule 9: the deduction reads the STEM flag from the Job record and
    nowhere else. A missing row bills at the non-STEM rate downstream, which
    is the specified NULL fallback, and is logged there.
    """
    if job_candidate_link_id is None:
        return None, None
    row = (
        await session.execute(
            _LINK_CLASSIFICATION_SQL, {"lid": str(job_candidate_link_id)}
        )
    ).mappings().first()
    if row is None:
        return None, None
    return str(row["job_id"]), row["role_classification"]

_DUE_REMINDER_SQL = text(
    """
    SELECT c.id, c.job_candidate_link_id, c.reminders_sent, c.invitation_sent_at
      FROM assessment_conversations c
     WHERE c.completed_at IS NULL
       AND c.credit_reconciled_at IS NULL
       AND c.invitation_sent_at IS NOT NULL
       AND c.reminders_sent < :max_reminders
       AND c.invitation_sent_at < :due_before
     ORDER BY c.invitation_sent_at
     LIMIT :batch
    """
)


async def _repair_missing_invitations(
    session: AsyncSession,
    *,
    now: datetime,
    batch_size: int,
    result: ReconciliationResult,
) -> None:
    """Re-dispatch the invitation email for invited applications that have
    none, inside the window; count and report the ones outside it.

    Dispatched AFTER the caller's commit, like every other dispatch this run
    makes, so a run that rolls back sends nothing and the next run finds the
    same rows. The window closes at the first reminder (see the module
    docstring)."""
    params = {
        "invited_status": pipeline.ASSESSMENT_INVITED,
        "email_type": EMAIL_TYPE_ASSESSMENT_INVITATION,
        "window_start": now - timedelta(hours=REMINDER_SCHEDULE_HOURS[0]),
    }
    missing = (
        await session.execute(
            _MISSING_INVITATION_SQL,
            {
                **params,
                "stale_before": now
                - timedelta(minutes=INVITATION_REPAIR_AFTER_MINUTES),
                "batch": batch_size,
            },
        )
    ).mappings().all()
    for row in missing:
        dispatch_after_commit(
            session,
            INVITATION_TASK,
            args=[
                str(row["job_candidate_link_id"]),
                str(row["invited_by"]) if row["invited_by"] else None,
            ],
        )
        result.invitations_redispatched += 1
    unrepaired = int(
        (await session.execute(_UNREPAIRED_INVITATION_SQL, params)).scalar_one()
    )
    result.invitations_missing_unrepaired = unrepaired
    if result.invitations_redispatched:
        log.warning(
            "credits.invitation_email_redispatched count=%d",
            result.invitations_redispatched,
        )
    if unrepaired:
        log.error(
            "credits.invitation_email_missing count=%d older_than_hours=%d",
            unrepaired,
            REMINDER_SCHEDULE_HOURS[0],
        )


async def charge_completed(
    session: AsyncSession, *, conversation_id, tenant_id, job_candidate_link_id
) -> bool:
    """Deduct one full credit for a finished assessment. Idempotent.

    Called from the request that sets `completed_at` rather than from a sweep,
    because the customer should see the deduction on the billing page at the
    same time they see the report.
    """
    from app.models.billing import EVENT_COMPLETED

    job_id, role_classification = await _job_classification(
        session, job_candidate_link_id
    )
    charged = await credits.consume(
        session,
        tenant_id=tenant_id,
        event_type=EVENT_COMPLETED,
        idempotency_key=ledger_key(conversation_id, EVENT_COMPLETED),
        job_candidate_link_id=job_candidate_link_id,
        metadata={"conversation_id": str(conversation_id)},
        role_classification=role_classification,
    )
    await session.execute(
        text(
            "UPDATE assessment_conversations "
            "SET credit_reconciled_at = COALESCE(credit_reconciled_at, now()), "
            "    credit_event = COALESCE(credit_event, :event) "
            "WHERE id = :cid"
        ),
        {"event": EVENT_COMPLETED, "cid": str(conversation_id)},
    )
    if job_id is not None:
        # Master Directive Part 3 Rule 5: the first COMPLETED assessment locks
        # the classification for good. Support can only compensate with a
        # credit adjustment after this point, never reclassify.
        await session.execute(
            text(
                "UPDATE jobs SET classification_locked = TRUE "
                "WHERE id = :jid AND classification_locked = FALSE"
            ),
            {"jid": job_id},
        )
    return charged


async def reconcile(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    batch_size: int = 500,
    queue_reminder=None,
) -> ReconciliationResult:
    """Charge every settled abandoned assessment and queue any due reminders.

    `queue_reminder(link_id, hours_elapsed, stage_hours)` is injected so this
    function stays testable without a dispatcher; the task passes the real one.
    `stage_hours` is the `REMINDER_SCHEDULE_HOURS` entry that fell due.
    """
    now = now or datetime.now(timezone.utc)
    result = ReconciliationResult()

    # ── Lost invitation emails, before any reminder ─────────────────────────
    await _repair_missing_invitations(
        session, now=now, batch_size=batch_size, result=result
    )

    # ── Reminders first ─────────────────────────────────────────────────────
    # Ordering matters: doing this after the settle pass would mean an
    # invitation that crosses the settle threshold gets charged in the same run
    # that sends its final nudge, which is not a sequence anyone would defend.
    for stage, hours in enumerate(REMINDER_SCHEDULE_HOURS):
        due = (
            await session.execute(
                _DUE_REMINDER_SQL,
                {
                    "max_reminders": stage + 1,
                    "due_before": now - timedelta(hours=hours),
                    "batch": batch_size,
                },
            )
        ).mappings().all()
        for row in due:
            if queue_reminder is not None:
                elapsed = int((now - row["invitation_sent_at"]).total_seconds() // 3600)
                # THE STAGE travels with the reminder, because it is what the
                # send is idempotent on. Derived from elapsed time alone, a
                # conversation found late at 80 hours would label its FIRST
                # reminder 72 and the second would then be deduplicated away.
                queue_reminder(str(row["job_candidate_link_id"]), elapsed, hours)
            await session.execute(
                text(
                    "UPDATE assessment_conversations "
                    "SET reminders_sent = reminders_sent + 1, last_reminder_at = :at "
                    "WHERE id = :cid"
                ),
                {"at": now, "cid": str(row["id"])},
            )
            result.reminders_queued += 1

    # ── Settle the abandoned ────────────────────────────────────────────────
    settled = (
        await session.execute(
            _SETTLED_SQL,
            {"cutoff": now - timedelta(hours=SETTLE_AFTER_HOURS), "batch": batch_size},
        )
    ).mappings().all()

    deficit_tenants: set[str] = set()
    for row in settled:
        # started_at set but never finished -> incomplete (1/3 credit).
        # never opened at all              -> no-show    (1/15 credit).
        event = EVENT_INCOMPLETE if row["started_at"] is not None else EVENT_NO_SHOW
        charged = await credits.consume(
            session,
            tenant_id=row["tenant_id"],
            event_type=event,
            idempotency_key=ledger_key(row["id"], event),
            job_candidate_link_id=row["job_candidate_link_id"],
            metadata={"conversation_id": str(row["id"]), "settled_at": now.isoformat()},
            role_classification=row["role_classification"],
        )
        await session.execute(
            text(
                "UPDATE assessment_conversations "
                "SET credit_reconciled_at = :at, credit_event = :event WHERE id = :cid"
            ),
            {"at": now, "event": event, "cid": str(row["id"])},
        )
        if not charged:
            result.skipped_already_charged += 1
        elif event == EVENT_INCOMPLETE:
            result.incomplete_charged += 1
        else:
            result.no_show_charged += 1
        if await credits.balance_subunits(session, row["tenant_id"]) < 0:
            deficit_tenants.add(str(row["tenant_id"]))

    result.tenants_in_deficit = sorted(deficit_tenants)
    log.info("credits.reconciled %s", result.as_dict())
    return result
