"""The adapter gate: the one door an irreversible effect goes through (W5.1).

THE GATE IS THE CONTROL, NOT THE DURABILITY
---------------------------------------------
It is tempting to think a durable runtime makes outbound effects safe. It does
not. The Atomix evaluation the specification cites measured checkpoint replay
alone leaking forty percent of invalid irreversible sends, and Saga-style
compensation leaking eighty: compensation runs AFTER the email has arrived, and
the recipient has already read it. What makes an irreversible effect safe is
that the adapter is UNREACHABLE until the action is sealed, so the send never
happens rather than being undone.

That is why this module exists separately from `ledger`. The ledger records;
this decides whether the call is made at all.

THE THREE ANSWERS AN ADAPTER MAY GIVE, AND WHY THE DEFAULT IS "WE DO NOT KNOW"
-------------------------------------------------------------------------------
An adapter returns `AdapterResult` when the effect happened. It raises
`AdapterRefused` when it can state that the effect DEFINITELY did not happen: a
provider that answered 400, an SMTP 550, a validation refusal made before
anything left the process. Every OTHER exception, a timeout above all, is
recorded as UNKNOWN.

The direction of that default is the whole design. Treating an unclassified
failure as FAILED makes it retryable, and a retried timeout is a second email.
Treating it as UNKNOWN costs a read-back and nothing else.

WHAT THE ADAPTER IS GIVEN
---------------------------
The `AgentAction` row, which carries `idempotency_key`. An adapter calling a
provider that supports idempotency keys of its own passes that exact value, so
the provider's dedupe and this ledger's dedupe agree on what "the same action"
means. That is what makes resolving an UNKNOWN by reading back possible at all.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_action import (
    ACTION_ROLLED_BACK,
    ACTION_SUCCEEDED,
    AUTHORIZATION_HUMAN,
    AUTHORIZATION_POLICY,
    RISK_IRREVERSIBLE,
    AgentAction,
)
from app.services.agent_actions import idempotency, ledger
from app.services.safety import actions as safety_actions

logger = logging.getLogger(__name__)

__all__ = [
    "READ_BACK_ABSENT",
    "READ_BACK_FOUND",
    "READ_BACK_INDETERMINATE",
    "ActionRequest",
    "AdapterRefused",
    "AdapterResult",
    "ApprovalRequired",
    "GateError",
    "IndeterminateAction",
    "Outcome",
    "ReadBack",
    "RiskClassRefused",
    "execute",
    "resolve_unknown",
]

#: The read-back established that the effect exists in the world.
READ_BACK_FOUND = "found"
#: The read-back established that it does not. Only this makes a retry safe.
READ_BACK_ABSENT = "absent"
#: The read-back could not tell. The action stays UNKNOWN, which is honest.
READ_BACK_INDETERMINATE = "indeterminate"


class GateError(RuntimeError):
    """The gate refused to perform the action."""


class ApprovalRequired(GateError):
    """An irreversible action with no committed approval.

    The adapter is not invoked. Not "invoked and rolled back", not "queued for
    later": the call does not happen, which is the only form of protection an
    irreversible effect has.
    """


class IndeterminateAction(GateError):
    """The previous attempt's outcome is not known, so this one is refused.

    Raised for UNKNOWN and for RUNNING alike, because they mean the same thing
    about the world: the effect may already exist. Resolve it by reading back
    (`resolve_unknown`), never by trying again.
    """


class RiskClassRefused(GateError):
    """A sensitive action was declared reversible.

    `services/safety/actions.py` already names the actions a person experiences
    immediately and cannot experience being undone. Accepting a lower risk
    class for one of them here would let a caller opt out of the approval gate
    by mislabelling the request.
    """


class AdapterRefused(Exception):
    """The adapter states that the effect DEFINITELY did not happen.

    An adapter raises this only when it can honestly say so. Guessing here is
    the expensive direction: it makes the action retryable, and a retried
    timeout is a duplicate side effect.
    """


@dataclass(frozen=True)
class AdapterResult:
    """The adapter's answer when the effect happened."""

    #: The provider's own identifier for the effect: a message id, an object
    #: key, a row id. What a later read-back looks for.
    external_id: str | None = None


@dataclass(frozen=True)
class ReadBack:
    """What looking at the world established about an unresolved action."""

    verdict: str
    external_id: str | None = None


@dataclass(frozen=True)
class ActionRequest:
    """One side-effecting action, as the caller describes it."""

    tenant_id: uuid.UUID
    agent: str
    tool: str
    #: Derived by `agent_actions.idempotency`, from stable logical inputs.
    idempotency_key: str
    risk_class: str
    #: What the effect is being requested with. Digested, never stored raw:
    #: these arguments can carry a candidate's address and a job's text, and a
    #: ledger row is far more widely readable than the tables they came from.
    args: Mapping[str, Any] = field(default_factory=dict)
    authorization: str = AUTHORIZATION_POLICY
    requested_by: uuid.UUID | None = None
    #: The approval an irreversible action rests on. Without it the adapter is
    #: unreachable.
    approval_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Outcome:
    """What happened, from the gate's point of view."""

    action: AgentAction
    #: True when THIS call reached the adapter and the effect happened here.
    #: False when the action was already SUCCEEDED, which is the answer that
    #: makes a second attempt a no-op rather than a second effect.
    performed: bool

    @property
    def external_id(self) -> str | None:
        return self.action.external_id

    @property
    def state(self) -> str:
        return self.action.state


Adapter = Callable[[AgentAction], Awaitable[AdapterResult]]
ReadBackFn = Callable[[AgentAction], Awaitable[ReadBack]]


def _require_risk_class(request: ActionRequest) -> None:
    if (
        request.tool in safety_actions.SENSITIVE_ACTIONS
        and request.risk_class != RISK_IRREVERSIBLE
    ):
        raise RiskClassRefused(
            f"{request.tool} is a sensitive action and cannot be declared "
            f"{request.risk_class}"
        )


async def execute(
    session: AsyncSession, request: ActionRequest, adapter: Adapter
) -> Outcome:
    """Record, gate, and perform one side-effecting action exactly once.

    The order is the design:

      1. the row is written and COMMITTED, so the intent survives the process;
      2. an already-succeeded action returns without calling anything;
      3. an unresolved action is refused, because its effect may exist;
      4. an irreversible action with no committed approval is refused HERE,
         before the adapter is reached;
      5. only then is the adapter invoked, once, with the attempt counted
         beforehand.
    """
    _require_risk_class(request)
    action, _ = await ledger.reserve(
        session,
        tenant_id=request.tenant_id,
        agent=request.agent,
        tool=request.tool,
        idempotency_key=request.idempotency_key,
        args_sha256=idempotency.args_digest(request.args),
        risk_class=request.risk_class,
        authorization=request.authorization,
        requested_by=request.requested_by,
        approval_id=request.approval_id,
    )

    if action.state == ACTION_SUCCEEDED:
        logger.info(
            "agent_actions.already_done tool=%s key=%s",
            action.tool, action.idempotency_key,
        )
        return Outcome(action=action, performed=False)
    if action.state == ACTION_ROLLED_BACK:
        raise GateError(
            f"{action.tool} ({action.idempotency_key}) was performed and "
            "compensated; performing it again is a new action with a new key"
        )
    if action.state in ledger.UNRESOLVED_STATES:
        raise IndeterminateAction(
            f"{action.tool} ({action.idempotency_key}) is {action.state}: the "
            "effect may already exist. Resolve it by reading back, never by "
            "retrying."
        )

    if action.sealed_at is None:
        if action.risk_class == RISK_IRREVERSIBLE:
            if request.approval_id is None:
                raise ApprovalRequired(
                    f"{action.tool} ({action.idempotency_key}) is irreversible "
                    "and carries no committed approval"
                )
            await ledger.seal(
                session,
                action,
                authorization=AUTHORIZATION_HUMAN,
                approval_id=request.approval_id,
            )
        else:
            # For a reversible action the commit point IS the decision to
            # execute, and it is stamped so that every performed action has a
            # sealed_at and the column means one thing.
            await ledger.seal(
                session, action, authorization=AUTHORIZATION_POLICY
            )

    await ledger.begin_attempt(session, action)
    try:
        result = await adapter(action)
    except AdapterRefused:
        await ledger.record_failure(session, action)
        raise
    except Exception:
        # Everything unclassified is UNKNOWN, including a timeout. See the
        # module docstring: the alternative default makes a timeout retryable
        # and a retried timeout is a duplicate side effect.
        await ledger.record_unknown(session, action)
        raise
    await ledger.record_success(session, action, external_id=result.external_id)
    return Outcome(action=action, performed=True)


async def resolve_unknown(
    session: AsyncSession, action: AgentAction, read_back: ReadBackFn
) -> AgentAction:
    """Settle an unresolved action by looking at the world, never by retrying.

    `read_back` asks the provider whether the effect exists, keyed on the same
    `idempotency_key` the adapter sent. Three answers, and the third is a real
    one: an inconclusive read-back leaves the action UNKNOWN rather than
    guessing, because a guess in either direction is either a duplicate effect
    or an effect reported as delivered that never was.
    """
    if action.state not in ledger.UNRESOLVED_STATES:
        raise GateError(
            f"{action.tool} ({action.idempotency_key}) is {action.state}, which "
            "is already resolved"
        )
    answer = await read_back(action)
    if answer.verdict == READ_BACK_FOUND:
        return await ledger.resolve_committed(
            session, action, external_id=answer.external_id
        )
    if answer.verdict == READ_BACK_ABSENT:
        return await ledger.resolve_absent(session, action)
    if answer.verdict != READ_BACK_INDETERMINATE:
        raise GateError(f"unknown read-back verdict: {answer.verdict!r}")
    logger.warning(
        "agent_actions.read_back_indeterminate tool=%s key=%s",
        action.tool, action.idempotency_key,
    )
    return action
