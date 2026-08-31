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
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import EVENT_INCOMPLETE, EVENT_NO_SHOW
from app.services import credits

log = logging.getLogger(__name__)

#: Hours after the invitation at which each reminder goes out. Two nudges, then
#: the candidate has had their chance.
REMINDER_SCHEDULE_HOURS: tuple[int, ...] = (24, 72)

#: An invitation is settled this long after it was sent — comfortably past the
#: last reminder, so "reminders exhausted" is true by construction rather than
#: by a race against the reminder task.
SETTLE_AFTER_HOURS = 24 * 7


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

    def as_dict(self) -> dict:
        return {
            "incomplete_charged": self.incomplete_charged,
            "no_show_charged": self.no_show_charged,
            "reminders_queued": self.reminders_queued,
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

    `queue_reminder` is injected so this function stays testable without a
    broker; the Celery task passes the real enqueue.
    """
    now = now or datetime.now(timezone.utc)
    result = ReconciliationResult()

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
                queue_reminder(str(row["job_candidate_link_id"]), elapsed)
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
