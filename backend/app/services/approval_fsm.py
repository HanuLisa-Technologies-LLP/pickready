"""Job approval finite state machine (ESD §7 / FR-3.2..3.4).

The chain is Requested -> Recommended -> Approved -> Ratified; each tenant's
`companies.approval_levels_config` marks levels active/inactive and assigns an
approver, e.g.:

    {"requested":   {"active": true,  "approver_user_id": "<uuid>"},
     "recommended": {"active": false, "approver_user_id": null},
     "approved":    {"active": true,  "approver_user_id": "<uuid>"},
     "ratified":    {"active": true,  "approver_user_id": "<uuid>"}}

Semantics:
- `job.status` names the level currently PENDING approval (draft before the
  chain starts).
- Inactive levels are skipped with an explicit `skipped` JobApproval row —
  never silently auto-approved (claude.md rule / ESD §7).
- When the FINAL active level passes, the job status becomes `ratified` and
  `ratified_at` is stamped — only then does HR gain access (FR-3.4), even if
  the "ratified" level itself was inactive.
- NOTE: when the "ratified" LEVEL is active, a job pending at that level also
  carries status `ratified` — the TERMINAL marker is `ratified_at`, not the
  status value. All "is the job done / HR-visible?" checks must use
  `ratified_at IS NOT NULL` (see `terminal` below).

The core (`next_active_level`, `plan_submit`, `validate_transition`) is pure
and DB-free; `apply_submit` / `apply_transition` are thin async wrappers that
persist the planned rows.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import APPROVAL_CHAIN, ApprovalDecision, JobStatus


# ── Typed errors ─────────────────────────────────────────────────────────────

class ApprovalError(Exception):
    """Base for all FSM violations."""


class NotAssignedApprover(ApprovalError):
    """Acting user is not the assigned approver for the job's current level."""


class PriorLevelPending(ApprovalError):
    """Acting user is assigned to a LATER level; earlier active levels are
    still pending — out-of-order approval attempt."""


class AlreadyTerminal(ApprovalError):
    """Job is already ratified — no further transitions."""


class NotSubmitted(ApprovalError):
    """Job is still a draft — it must be submitted before approvals."""


class ApprovalConfigError(ApprovalError):
    """Missing/invalid approval_levels_config for the tenant."""


# ── Plan structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ApprovalRow:
    """One job_approvals row to write."""
    level: JobStatus
    decision: ApprovalDecision
    approver_user_id: uuid.UUID | None = None
    remarks: str | None = None


@dataclass(frozen=True)
class TransitionResult:
    new_status: JobStatus
    rows: list[ApprovalRow] = field(default_factory=list)
    ratified: bool = False


# ── Pure core ────────────────────────────────────────────────────────────────

def _entry(config: dict[str, Any], level: JobStatus) -> dict[str, Any]:
    entry = config.get(level.value)
    return entry if isinstance(entry, dict) else {}


def is_active(config: dict[str, Any], level: JobStatus) -> bool:
    # ASSUMPTION: a level missing from the config is treated as inactive —
    # the client explicitly opts levels in (FR-2.3).
    return bool(_entry(config, level).get("active"))


def approver_for(config: dict[str, Any], level: JobStatus) -> str | None:
    raw = _entry(config, level).get("approver_user_id")
    return str(raw) if raw else None


def next_active_level(
    config: dict[str, Any], after: JobStatus | None
) -> JobStatus | None:
    """First active level strictly after `after` (from the start when None);
    None when no active level remains."""
    start = 0 if after is None else APPROVAL_CHAIN.index(after) + 1
    for level in APPROVAL_CHAIN[start:]:
        if is_active(config, level):
            return level
    return None


def _skipped_rows_between(
    config: dict[str, Any], after: JobStatus | None, until: JobStatus | None
) -> list[ApprovalRow]:
    """Explicit `skipped` rows for every inactive level after `after` and
    before `until` (or through the end of the chain when `until` is None)."""
    start = 0 if after is None else APPROVAL_CHAIN.index(after) + 1
    end = len(APPROVAL_CHAIN) if until is None else APPROVAL_CHAIN.index(until)
    return [
        ApprovalRow(level=level, decision=ApprovalDecision.skipped,
                    remarks="level skipped (inactive)")
        for level in APPROVAL_CHAIN[start:end]
        if not is_active(config, level)
    ]


def plan_submit(config: dict[str, Any] | None) -> TransitionResult:
    """draft -> first active level. Leading inactive levels get explicit
    skipped rows. If NO level is active the job ratifies immediately.
    # ASSUMPTION: an all-inactive config means the client requires no
    # approvals, so submission ratifies directly (with all 4 levels logged
    # as skipped) rather than erroring."""
    if not config:
        raise ApprovalConfigError("approval_levels_config is not set for this tenant")
    first = next_active_level(config, after=None)
    rows = _skipped_rows_between(config, after=None, until=first)
    if first is None:
        return TransitionResult(new_status=JobStatus.ratified, rows=rows, ratified=True)
    return TransitionResult(new_status=first, rows=rows)


def validate_transition(
    config: dict[str, Any] | None,
    current_status: JobStatus,
    acting_user_id: uuid.UUID | str,
    decision: ApprovalDecision,
    remarks: str | None = None,
    *,
    terminal: bool | None = None,
) -> TransitionResult:
    """Validate an approve/reject attempt at the job's current level and
    return the job_approvals rows to write plus the new status.

    `terminal` disambiguates status `ratified`: when the "ratified" LEVEL is
    active, a job pending at it also has status ratified. Callers with the
    Job row pass `terminal=job.ratified_at is not None`; when None it is
    inferred from the config (status ratified + inactive ratified level can
    only mean the chain already completed).

    Raises: AlreadyTerminal, NotSubmitted, NotAssignedApprover,
    PriorLevelPending, ApprovalConfigError.
    """
    if not config:
        raise ApprovalConfigError("approval_levels_config is not set for this tenant")
    if current_status == JobStatus.ratified:
        if terminal is None:
            terminal = not is_active(config, JobStatus.ratified)
        if terminal:
            raise AlreadyTerminal("job is already ratified")
    if current_status == JobStatus.draft:
        raise NotSubmitted("job has not been submitted for approval")
    if decision not in (ApprovalDecision.approved, ApprovalDecision.rejected):
        raise ApprovalError("decision must be approved or rejected")

    level = JobStatus(current_status)
    actor = str(acting_user_id)

    if approver_for(config, level) != actor:
        # Assigned to a later active level? -> out-of-order attempt.
        for later in APPROVAL_CHAIN[APPROVAL_CHAIN.index(level) + 1:]:
            if is_active(config, later) and approver_for(config, later) == actor:
                raise PriorLevelPending(
                    f"level '{level.value}' is still pending; "
                    f"you approve at '{later.value}'"
                )
        raise NotAssignedApprover(
            f"user is not the assigned approver for level '{level.value}'"
        )

    rows = [ApprovalRow(
        level=level, decision=decision,
        approver_user_id=uuid.UUID(actor), remarks=remarks,
    )]

    if decision == ApprovalDecision.rejected:
        # ASSUMPTION: a rejection returns the job to draft so the Hiring
        # Manager can revise and resubmit; the rejection row stays in the
        # audit trail (job_approvals).
        return TransitionResult(new_status=JobStatus.draft, rows=rows)

    nxt = next_active_level(config, after=level)
    rows.extend(_skipped_rows_between(config, after=level, until=nxt))
    if nxt is None:
        # Final active level passed -> visible to HR (FR-3.4).
        return TransitionResult(new_status=JobStatus.ratified, rows=rows, ratified=True)
    return TransitionResult(new_status=nxt, rows=rows)


# ── Async persistence wrappers ───────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _persist(session: AsyncSession, job, result: TransitionResult) -> None:
    from app.models.job import JobApproval  # local import keeps the core DB-free

    for row in result.rows:
        session.add(JobApproval(
            tenant_id=job.tenant_id,
            job_id=job.id,
            level=row.level,
            approver_user_id=row.approver_user_id,
            decision=row.decision,
            remarks=row.remarks,
        ))
    job.status = result.new_status
    if result.ratified:
        job.ratified_at = _now()
    await session.flush()


async def apply_submit(session: AsyncSession, job, config: dict[str, Any] | None) -> TransitionResult:
    result = plan_submit(config)
    await _persist(session, job, result)
    return result


async def apply_transition(
    session: AsyncSession,
    job,
    config: dict[str, Any] | None,
    *,
    acting_user_id: uuid.UUID | str,
    decision: ApprovalDecision,
    remarks: str | None = None,
) -> TransitionResult:
    result = validate_transition(
        config, job.status, acting_user_id, decision, remarks,
        terminal=job.ratified_at is not None,
    )
    await _persist(session, job, result)
    return result
