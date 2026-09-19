"""Reading and writing the action ledger, and the state machine over it (W5.1).

WHY EVERY WRITE HERE COMMITS
------------------------------
A row that is written before the call but not COMMITTED before the call is not
a record: the process that would have committed it is the one that dies. So
each state change is made durable immediately, which is the same contract
`services/video/processing.py` already keeps for its recording lifecycle, and
for the same stated reason: a kill mid-pipeline must leave a truthful status
rather than a rolled-back lie.

The consequence for callers is explicit and is part of the contract: THE
SESSION HANDED TO THIS MODULE IS THE ACTION'S OWN UNIT OF WORK. It is a worker
session, or a request session at a point where the caller has nothing else
uncommitted. Passing a session that carries unrelated pending work would
commit that work as a side effect of recording an action.

The RLS settings are captured and restored around each commit, because
`tenant_scope` and `superadmin_scope` set them with SET LOCAL and Postgres
resets those at COMMIT. Without the restore, a commit in here would silently
widen or narrow the caller's NEXT query, which is a tenant boundary moving as
a side effect of an unrelated write.

THE TRANSITIONS ARE A TABLE, AND UNKNOWN HAS NO WAY BACK TO RUNNING
--------------------------------------------------------------------
That absence is the enforcement. Retrying a FAILED action is correct because
FAILED means the attempt definitely had no effect. Retrying an UNKNOWN action
issues the effect a second time. A caller cannot reach the retry by forgetting
the rule, because the edge does not exist: UNKNOWN is left only by reading the
world back.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_action import (
    ACTION_FAILED,
    ACTION_PENDING,
    ACTION_ROLLED_BACK,
    ACTION_RUNNING,
    ACTION_STATES,
    ACTION_SUCCEEDED,
    ACTION_UNKNOWN,
    AUTHORIZATION_HUMAN,
    AUTHORIZATION_POLICY,
    AUTHORIZATIONS,
    RISK_CLASSES,
    RISK_IRREVERSIBLE,
    RISK_REVERSIBLE,
    AgentAction,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ACTION_FAILED",
    "ACTION_PENDING",
    "ACTION_ROLLED_BACK",
    "ACTION_RUNNING",
    "ACTION_STATES",
    "ACTION_SUCCEEDED",
    "ACTION_UNKNOWN",
    "AUTHORIZATION_HUMAN",
    "AUTHORIZATION_POLICY",
    "RISK_IRREVERSIBLE",
    "RISK_REVERSIBLE",
    "TRANSITIONS",
    "UNRESOLVED_STATES",
    "ActionLedgerError",
    "IdempotencyKeyReused",
    "IllegalActionTransition",
    "begin_attempt",
    "load",
    "record_failure",
    "record_rollback",
    "record_success",
    "record_unknown",
    "reserve",
    "resolve_absent",
    "resolve_committed",
    "seal",
    "unresolved",
]


#: From state to the states it may legally reach. Read it for the two edges
#: that are deliberately ABSENT:
#:
#:   UNKNOWN -> RUNNING    a blind retry of a possible duplicate.
#:   ROLLED_BACK -> *      a compensated effect happened and was undone; doing
#:                         it again is a NEW logical action with a new key, not
#:                         a retry of this one.
TRANSITIONS: dict[str, frozenset[str]] = {
    ACTION_PENDING: frozenset({ACTION_RUNNING}),
    ACTION_RUNNING: frozenset({ACTION_SUCCEEDED, ACTION_FAILED, ACTION_UNKNOWN}),
    # A definite failure had no effect, so another attempt is a first attempt.
    ACTION_FAILED: frozenset({ACTION_RUNNING}),
    # Left only by reading the world back, which establishes one of the two
    # answers the attempt did not give.
    ACTION_UNKNOWN: frozenset({ACTION_SUCCEEDED, ACTION_FAILED}),
    ACTION_SUCCEEDED: frozenset({ACTION_ROLLED_BACK}),
    ACTION_ROLLED_BACK: frozenset(),
}

#: States whose real-world outcome is not established. The read-back sweep's
#: work list. RUNNING belongs here: a process that died between the invoke and
#: the answer leaves exactly that row.
UNRESOLVED_STATES: tuple[str, ...] = (ACTION_RUNNING, ACTION_UNKNOWN)

#: Captured and restored around every commit. `role` is included because
#: `tenant_scope` drops to the RLS-enforcing app role with SET LOCAL ROLE,
#: which resets at COMMIT exactly as the other two do.
_CARRIED_SETTINGS: tuple[str, ...] = ("app.tenant_id", "app.bypass_rls", "role")

#: `current_setting('role')` answers this when no role has been set. Restoring
#: it would be a no-op that reads like a decision, so it is skipped.
_ROLE_UNSET = "none"


class ActionLedgerError(RuntimeError):
    """A write the action ledger refuses."""


class IllegalActionTransition(ActionLedgerError):
    """A state change the machine does not have an edge for.

    Raised, never coerced into the nearest legal state. The most important
    instance is UNKNOWN to RUNNING: a caller that reaches it is about to issue
    a duplicate side effect, and quietly allowing it would make this table a
    record of the duplicate rather than a defence against it.
    """


class IdempotencyKeyReused(ActionLedgerError):
    """One key, two different sets of arguments.

    A caller bug, and an expensive one: without this check the second, genuinely
    different action reads as "already done" and is never performed, with
    nothing anywhere saying so.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _commit(session: AsyncSession) -> None:
    """Make the ledger row durable, leaving the session's scope as found.

    The `AgentAction` object stays readable afterwards because the application
    session factory sets `expire_on_commit=False`. That is not incidental: an
    expiring session would try to refresh the row on the next attribute read,
    which under asyncio raises rather than reloading, and it would do so in the
    narrow window between recording an attempt and making the call.
    """
    carried: dict[str, str | None] = {}
    for name in _CARRIED_SETTINGS:
        carried[name] = (
            await session.execute(
                text("SELECT current_setting(:name, true)"), {"name": name}
            )
        ).scalar()
    await session.commit()
    for name, value in carried.items():
        if not value or (name == "role" and value == _ROLE_UNSET):
            continue
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


async def load(session: AsyncSession, idempotency_key: str) -> AgentAction | None:
    """The action for this key, or None."""
    return (
        await session.execute(
            select(AgentAction).where(AgentAction.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()


async def reserve(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent: str,
    tool: str,
    idempotency_key: str,
    args_sha256: str,
    risk_class: str,
    authorization: str,
    requested_by: uuid.UUID | None = None,
    approval_id: uuid.UUID | None = None,
) -> tuple[AgentAction, bool]:
    """Record the intent to act, BEFORE anything is called.

    Returns `(action, created)`. `created` is False when this exact logical
    action was already recorded, which is the answer that makes a second
    attempt a no-op instead of a second effect.

    The dedupe is the UNIQUE constraint and not a prior SELECT: two workers
    racing the same logical action would both read "not present" and both
    proceed. A SAVEPOINT keeps the session usable when the constraint fires,
    because the error means "already recorded", which is a normal outcome here
    and not a failure of the surrounding work.
    """
    if risk_class not in RISK_CLASSES:
        raise ActionLedgerError(f"unknown risk class: {risk_class!r}")
    if authorization not in AUTHORIZATIONS:
        raise ActionLedgerError(f"unknown authorization: {authorization!r}")
    if risk_class == RISK_IRREVERSIBLE and authorization == AUTHORIZATION_POLICY:
        # Standing policy cannot authorise an effect the person on the other
        # end experiences immediately and cannot experience being undone. The
        # database enforces the same rule on `sealed_at`; this refuses the row
        # earlier, where the caller can still be told which of the two fields
        # is wrong.
        raise ActionLedgerError(
            f"{tool}: an irreversible action is not authorised by policy alone"
        )
    action = AgentAction(
        tenant_id=tenant_id,
        agent=agent,
        tool=tool,
        idempotency_key=idempotency_key,
        args_sha256=args_sha256,
        risk_class=risk_class,
        authorization=authorization,
        requested_by=requested_by,
        approval_id=approval_id,
        state=ACTION_PENDING,
        attempt=0,
    )
    try:
        async with session.begin_nested():
            session.add(action)
            await session.flush()
    except IntegrityError:
        existing = await load(session, idempotency_key)
        if existing is None:
            # The constraint fired and the row is not there. Something other
            # than the idempotency key was violated, and guessing which one
            # would be a fallback substituted for a failed write.
            raise ActionLedgerError(
                f"{tool}: the action could not be recorded and no existing row "
                f"holds key {idempotency_key!r}"
            ) from None
        if existing.args_sha256 != args_sha256:
            raise IdempotencyKeyReused(
                f"key {idempotency_key!r} was recorded for different arguments; "
                "the key must be derived from the logical inputs of this exact "
                "action"
            )
        return existing, False
    await _commit(session)
    logger.info(
        "agent_actions.reserved tool=%s key=%s risk=%s",
        tool, idempotency_key, risk_class,
    )
    return action, True


async def _transition(
    session: AsyncSession, action: AgentAction, target: str, **stamps: object
) -> AgentAction:
    allowed = TRANSITIONS.get(action.state, frozenset())
    if target not in allowed:
        raise IllegalActionTransition(
            f"{action.tool} ({action.idempotency_key}): {action.state} to "
            f"{target} is not an edge of the action state machine"
        )
    action.state = target
    for field, value in stamps.items():
        setattr(action, field, value)
    await session.flush()
    await _commit(session)
    return action


async def seal(
    session: AsyncSession,
    action: AgentAction,
    *,
    authorization: str,
    approval_id: uuid.UUID | None = None,
) -> AgentAction:
    """Stamp the commit point past which the adapter becomes reachable.

    For a reversible action the commit point is the decision to execute, so
    the gate seals it as it goes. For an irreversible one it is the moment a
    HUMAN APPROVAL was recorded, and the approval must be named: an approval
    the ledger cannot point at is an approval nobody can check.

    Sealing is idempotent. Re-sealing an already sealed action with the same
    approval leaves the original timestamp, because `sealed_at` is a record of
    WHEN the commitment was made and moving it forward would rewrite that.
    """
    if authorization not in AUTHORIZATIONS:
        raise ActionLedgerError(f"unknown authorization: {authorization!r}")
    if action.risk_class == RISK_IRREVERSIBLE:
        if authorization != AUTHORIZATION_HUMAN or approval_id is None:
            raise ActionLedgerError(
                f"{action.tool}: an irreversible action is sealed only by a "
                "recorded human approval"
            )
    if action.sealed_at is not None:
        if approval_id is not None and action.approval_id != approval_id:
            raise ActionLedgerError(
                f"{action.tool}: already sealed under a different approval"
            )
        return action
    action.authorization = authorization
    if approval_id is not None:
        action.approval_id = approval_id
    action.sealed_at = _now()
    await session.flush()
    await _commit(session)
    return action


async def begin_attempt(session: AsyncSession, action: AgentAction) -> AgentAction:
    """Move to RUNNING and count the attempt, before the adapter is invoked.

    Counting here rather than after the call is the point: an attempt that
    never answered is the one worth knowing about.
    """
    if action.sealed_at is None:
        raise ActionLedgerError(
            f"{action.tool}: an unsealed action never reaches the adapter"
        )
    return await _transition(
        session, action, ACTION_RUNNING, attempt=action.attempt + 1
    )


async def record_success(
    session: AsyncSession, action: AgentAction, *, external_id: str | None
) -> AgentAction:
    """The adapter answered. The effect happened."""
    return await _transition(
        session,
        action,
        ACTION_SUCCEEDED,
        external_id=external_id,
        committed_at=_now(),
    )


async def record_failure(session: AsyncSession, action: AgentAction) -> AgentAction:
    """The attempt DEFINITELY had no effect, so another attempt is safe.

    Only an adapter that can say so may reach this. Everything else is UNKNOWN,
    which is the safe direction and the whole reason the third state exists.
    """
    return await _transition(session, action, ACTION_FAILED)


async def record_unknown(session: AsyncSession, action: AgentAction) -> AgentAction:
    """The attempt MAY have had an effect."""
    logger.warning(
        "agent_actions.unknown tool=%s key=%s attempt=%s",
        action.tool, action.idempotency_key, action.attempt,
    )
    return await _transition(session, action, ACTION_UNKNOWN)


async def resolve_committed(
    session: AsyncSession, action: AgentAction, *, external_id: str | None
) -> AgentAction:
    """A read-back found the effect. The action succeeded after all.

    `verified_at` is what separates this from `record_success`: one is the
    adapter's own answer, the other is the world's. A report that cannot tell
    them apart cannot tell an operator why an action has two timestamps.
    """
    now = _now()
    return await _transition(
        session,
        action,
        ACTION_SUCCEEDED,
        external_id=external_id or action.external_id,
        committed_at=action.committed_at or now,
        verified_at=now,
    )


async def resolve_absent(session: AsyncSession, action: AgentAction) -> AgentAction:
    """A read-back established that the effect did NOT happen.

    This is the only route from UNKNOWN to a retryable state, and it is the
    only one there should be: the retry is safe precisely because somebody
    looked.
    """
    return await _transition(session, action, ACTION_FAILED, verified_at=_now())


async def record_rollback(session: AsyncSession, action: AgentAction) -> AgentAction:
    """The effect happened and has been compensated."""
    return await _transition(session, action, ACTION_ROLLED_BACK)


async def unresolved(
    session: AsyncSession, *, limit: int = 100
) -> Sequence[AgentAction]:
    """Actions whose real-world outcome is not established, oldest first.

    The work list for a read-back sweep. Ordered by `requested_at` because the
    oldest unresolved action is the one whose effect somebody is waiting on,
    and bounded because a sweep that tries to resolve everything at once turns
    one outage into one very long invocation.
    """
    return (
        (
            await session.execute(
                select(AgentAction)
                .where(AgentAction.state.in_(UNRESOLVED_STATES))
                .order_by(AgentAction.requested_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
