"""Inviting applicants to the assessment, and sending the invitation email.

PLAN-p3 WP1 (section 3.2). The one writer of `assessment_conversations` rows.

THE ROW IS THE INVITATION
-------------------------
An `assessment_conversations` row with `invitation_sent_at` stamped is what
lets a candidate open the assessment (`api/assessment_conversation`), what the
matrix lock counts as an issued contract, and what the credit reconciliation
settles. Applying creates none (Phase 6 removed the apply-time row, and
`tests/test_apply_creates_no_assessment.py` pins that nothing outside this
module writes one). Only a recruiter's invitation does, here.

THREE PASSES, AND NOTHING IS WRITTEN UNTIL THE LAST
---------------------------------------------------
1. READ AND LOCK. The job, its readiness, and every ticked application,
   locked `FOR UPDATE` in id order. Two recruiters inviting the same
   applicant at once used to both see `applied`, both insert, and the second
   `apply_transition` raised `InvalidTransition` and 500'd its whole batch.
   Now the second waits for the first, reads `assessment_invited`, and skips
   that applicant with a reason.
2. ONE CREDIT QUESTION FOR THE WHOLE BATCH. `credits.can_start_assessment`
   with `count` = the eligible applicants. It used to ask about ONE report and
   then invite everybody ticked, so a balance holding one assessment let a
   recruiter invite two hundred people, each charged at completion into a
   deficit nobody chose. A shortfall is refused in ONE sentence naming the
   count, the cost, the balance and the gap, and nothing is written. The
   separate "balance above zero" gate is folded into this one: a positive
   count at a positive cost already demands a positive balance.
3. WRITE. The conversation row, the stage change, and two dispatches AFTER
   the commit: the invitation email and the candidate's questions.

THE EMAIL IS DRAFTED BY A WORKER, NEVER BY THE CLICK
----------------------------------------------------
`lifecycle_email.draft` is a model call bounded at thirty seconds. The route
used to draft every invitation inline, so a batch of forty held one request
open for as long as forty model calls take. `pickready.send_assessment_invitation`
drafts it from a worker, through `email_outbox.queue_transition_email`, which
records the tenant's sender and dispatches the send after ITS commit. A lost
invoke is repaired by `credit_reconciliation.reconcile`, which asks the TABLE
for invited applications with no invitation email.

A credit check at invitation is not a reservation: two batches that each fit
the balance are not jointly covered by it. The START of each assessment asks
again (PLAN-p3 3.2 step 5, owned by the start route), and that is the last
moment the work is still a choice.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_log import EMAIL_TYPE_ASSESSMENT_INVITATION
from app.services import credits
from app.services import hiring_pipeline as pipeline
from app.services.audit import audit
from app.workers.dispatch import dispatch_after_commit

logger = logging.getLogger(__name__)

#: Drafts and queues one invitation email (`workers/tasks_invitations.py`).
INVITATION_TASK = "pickready.send_assessment_invitation"
#: Writes this candidate's questions (`workers/tasks_questions.py`).
QUESTIONS_TASK = "pickready.generate_candidate_questions"

#: `jobs.assessment_status` once the skills are saved (`services/skills`).
#: Read by value here because `services/skills` imports the Sutra stack and an
#: invitation has no business loading it; `test_invite_batch_credits` pins
#: that the two agree.
READY_FOR_CANDIDATES = "ready_for_candidates"

NOT_READY_DETAIL = (
    "This job's skills are not saved yet. Save the skills on the job's setup "
    "screen before inviting candidates."
)
CLOSED_DETAIL = (
    "This job is closed, so nobody new can be invited to its assessment. Its "
    "assessment records are no longer available to the team, and a report "
    "written now could not be read."
)


class InvitationRefused(Exception):
    """The whole batch is refused and nothing was written. The route renders
    `detail` verbatim with `status_code`."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class InviteResult:
    invited: tuple[uuid.UUID, ...]
    #: Applications that were not invited, each with the reason. Never
    #: silently dropped: a recruiter who ticked twenty boxes needs to know
    #: which three did not go out and why.
    skipped: tuple[dict, ...] = field(default_factory=tuple)


def _shortfall_detail(
    *, count: int, role_classification: str | None, required: Decimal, balance: Decimal
) -> str:
    role_word = "STEM" if role_classification == "STEM" else "Non-STEM"
    per_report = (required / count).quantize(Decimal("0.01"))
    short = (required - balance).quantize(Decimal("0.01"))
    people = "1 candidate" if count == 1 else f"{count} candidates"
    return (
        f"Insufficient credits. Inviting {people} to this {role_word} role "
        f"requires {required} credits ({per_report} per assessment). Current "
        f"balance: {balance} credits, {short} short. Nobody was invited. Top "
        "up, or invite fewer candidates."
    )


async def invite_batch(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_ids: list[uuid.UUID],
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> InviteResult:
    """Invite the ticked applicants of one job. See the module docstring.

    Raises `InvitationRefused` (404 job, 409 not ready or closed, 402 short of
    credits) BEFORE the first write, so a refusal leaves every application
    exactly as it was.
    """
    now = now or datetime.now(timezone.utc)

    # ── Pass 1: read and lock ────────────────────────────────────────────────
    job = (
        await session.execute(
            text(
                "SELECT id, tenant_id, assessment_grade, assessment_status, "
                "       role_classification, closed_at "
                "FROM jobs WHERE id = :jid"
            ),
            {"jid": str(job_id)},
        )
    ).mappings().first()
    # Explicit tenant check is defense in depth; RLS is the boundary.
    if job is None or str(job["tenant_id"]) != str(tenant_id):
        raise InvitationRefused(404, "Job not found")
    if job["closed_at"] is not None:
        raise InvitationRefused(409, CLOSED_DETAIL)
    if job["assessment_status"] != READY_FOR_CANDIDATES:
        raise InvitationRefused(409, NOT_READY_DETAIL)

    # The same application ticked twice is one application, invited once.
    requested = list(dict.fromkeys(link_ids))
    rows = (
        await session.execute(
            text(
                "SELECT l.id, l.tenant_id, l.job_id, l.status, c.full_name "
                "FROM job_candidate_links l "
                "JOIN candidates c ON c.id = l.candidate_id "
                "WHERE l.id = ANY(CAST(:ids AS uuid[])) "
                "ORDER BY l.id "
                "FOR UPDATE OF l"
            ),
            {"ids": [str(link_id) for link_id in requested]},
        )
    ).mappings().all()
    by_id = {
        uuid.UUID(str(row["id"])): row
        for row in rows
        if str(row["tenant_id"]) == str(tenant_id)
    }

    eligible: list[uuid.UUID] = []
    skipped: list[dict] = []
    for link_id in requested:
        row = by_id.get(link_id)
        if row is None:
            skipped.append({"link_id": str(link_id), "reason": "Application not found"})
            continue
        if str(row["job_id"]) != str(job_id):
            skipped.append(
                {"link_id": str(link_id), "reason": "Application belongs to another job"}
            )
            continue
        current = pipeline.normalize(row["status"])
        if current != pipeline.APPLIED:
            # Re-inviting somebody already mid-assessment would restart their
            # clock and re-mail them; somebody rejected is not invited at all.
            skipped.append(
                {
                    "link_id": str(link_id),
                    "name": row["full_name"],
                    "reason": (
                        "Already at stage "
                        f"'{pipeline.STAGE_LABELS.get(current, current)}'"
                    ),
                }
            )
            continue
        eligible.append(link_id)

    # ── Pass 2: one credit question for the whole batch ─────────────────────
    if eligible:
        allowed, required, balance = await credits.can_start_assessment(
            session,
            tenant_id,
            role_classification=job["role_classification"],
            count=len(eligible),
        )
        if not allowed:
            raise InvitationRefused(
                402,
                _shortfall_detail(
                    count=len(eligible),
                    role_classification=job["role_classification"],
                    required=required,
                    balance=balance,
                ),
            )

    # ── Pass 3: write, and dispatch after the commit ────────────────────────
    grade = job["assessment_grade"] or "non_managerial"
    for link_id in eligible:
        # ON CONFLICT covers a legacy row written at APPLY time before Phase 6
        # removed that (none in pilot, CONTRACT v3): it becomes an invitation
        # now, keeping its id, and takes the job's grade only if it was never
        # invited, so an invitation already sent keeps what it was sent with.
        await session.execute(
            text(
                """
                INSERT INTO assessment_conversations
                    (id, tenant_id, job_id, job_candidate_link_id, grade, status,
                     next_question_index, invitation_sent_at, invited_by, created_at)
                VALUES
                    (gen_random_uuid(), :tid, :jid, :lid, :grade, 'active', 0,
                     :at, :actor, :at)
                ON CONFLICT (job_candidate_link_id) DO UPDATE
                SET grade = CASE
                        WHEN assessment_conversations.invitation_sent_at IS NULL
                        THEN EXCLUDED.grade
                        ELSE assessment_conversations.grade
                    END,
                    invitation_sent_at = COALESCE(
                        assessment_conversations.invitation_sent_at,
                        EXCLUDED.invitation_sent_at
                    ),
                    invited_by = COALESCE(
                        assessment_conversations.invited_by, EXCLUDED.invited_by
                    )
                """
            ),
            {
                "tid": str(tenant_id),
                "jid": str(job_id),
                "lid": str(link_id),
                "grade": grade,
                "at": now,
                "actor": str(actor_user_id),
            },
        )
        result = await pipeline.apply_transition(
            session,
            link_id=link_id,
            tenant_id=tenant_id,
            target=pipeline.ASSESSMENT_INVITED,
            actor_user_id=actor_user_id,
            now=now,
        )
        if result.email_type != EMAIL_TYPE_ASSESSMENT_INVITATION:
            # The stage's email is data in `hiring_pipeline.TRANSITION_EMAIL`.
            # If it ever changes, this batch must not quietly stop telling
            # people they were invited; it must fail and be looked at.
            raise RuntimeError(
                f"{pipeline.ASSESSMENT_INVITED} now sends {result.email_type!r}, "
                f"not {EMAIL_TYPE_ASSESSMENT_INVITATION!r}"
            )
        # Both AFTER the commit: a batch that rolls back mails nobody and
        # generates nothing. A lost invitation invoke is repaired by
        # `credit_reconciliation.reconcile`; lost question generation is
        # re-dispatched by the start route while questions are missing.
        dispatch_after_commit(
            session, INVITATION_TASK, args=[str(link_id), str(actor_user_id)]
        )
        dispatch_after_commit(session, QUESTIONS_TASK, args=[str(link_id)])

    await audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="assessment_invitations_sent",
        target_type="job",
        target_id=job_id,
        metadata={"invited": len(eligible), "skipped": len(skipped)},
    )
    return InviteResult(invited=tuple(eligible), skipped=tuple(skipped))


async def send_invitation_email(
    session: AsyncSession,
    *,
    link_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
) -> dict:
    """Draft and queue the invitation email for one invited application.

    The body of `pickready.send_assessment_invitation`. Idempotent: an
    application that already has an invitation email (any status, including a
    row written before invitations carried a dedupe key) is not mailed again,
    and the outbox's unique key makes two concurrent runs one row. The caller
    COMMITS, and the commit is what dispatches the send.

    Returns a status the task logs. Skips are reasons, never errors: the
    application may have moved on (a recruiter rejected somebody in the
    seconds between the click and this run), and then an invitation would be
    a false promise.
    """
    from app.services import email_outbox  # noqa: PLC0415

    state = (
        await session.execute(
            text(
                "SELECT c.invitation_sent_at, c.completed_at, l.status "
                "FROM job_candidate_links l "
                "LEFT JOIN assessment_conversations c "
                "  ON c.job_candidate_link_id = l.id "
                "WHERE l.id = :lid"
            ),
            {"lid": str(link_id)},
        )
    ).mappings().first()
    if state is None:
        logger.warning("assessment_invitation.skipped link_id=%s reason=no_application", link_id)
        return {"status": "skipped", "reason": "application not found"}
    if state["invitation_sent_at"] is None:
        # Dispatched only after the invitation committed, so this is a
        # dispatch naming an application nobody invited.
        logger.warning("assessment_invitation.skipped link_id=%s reason=not_invited", link_id)
        return {"status": "skipped", "reason": "not invited"}
    if pipeline.normalize(state["status"]) != pipeline.ASSESSMENT_INVITED:
        logger.info(
            "assessment_invitation.skipped link_id=%s reason=moved_on status=%s",
            link_id,
            state["status"],
        )
        return {"status": "skipped", "reason": "application moved on"}
    already = (
        await session.execute(
            text(
                "SELECT 1 FROM email_log WHERE job_candidate_link_id = :lid "
                "AND email_type = :type LIMIT 1"
            ),
            {"lid": str(link_id), "type": EMAIL_TYPE_ASSESSMENT_INVITATION},
        )
    ).first()
    if already is not None:
        return {"status": "skipped", "reason": "already queued"}

    row = await email_outbox.queue_transition_email(
        session,
        link_id=link_id,
        email_type=EMAIL_TYPE_ASSESSMENT_INVITATION,
        sent_by=actor_user_id,
        dedupe_key=email_outbox.invitation_key(link_id),
    )
    if row is None:
        return {"status": "skipped", "reason": "no recipient or already queued"}
    return {"status": "queued", "email_log_id": str(row.id)}
