"""The checkpoint's world state (W5.3). NOT a transcript.

WHY A TRANSCRIPT IS THE WRONG THING TO CHECKPOINT
---------------------------------------------------
A transcript is what was said. It grows without bound, it carries a real
candidate's answers, and reading it back does not tell a resuming run what it
already DID. The transcript is not the database.

What a resuming run needs is what is true: the objective it is serving, which
actions have already committed an external effect, which are outstanding, what
evidence it has gathered, what it may still spend, which approvals it holds,
and which facts it has not established. That is exactly the field set below,
and the field set is the enforcement: a frozen dataclass with no free-form
context dictionary cannot quietly grow a `notes` field that somebody puts a
transcript in. `EvaluatorInput` in `services/miti` is pinned by its field set
for the same reason, and `tests/test_action_ledger.py` pins this one.

IDENTIFIERS, NEVER CONTENT
----------------------------
Completed and pending actions are IDEMPOTENCY KEYS; evidence is EVIDENCE IDS.
A checkpoint is written to durable storage and read by tooling, and the same
rule `agent_execution_traces` follows applies here: a trace carries
identifiers, counts and timings, and never content.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any, Mapping, Sequence

__all__ = [
    "CHECKPOINT_VERSION",
    "BudgetRemaining",
    "WorldState",
    "WorldStateError",
]

#: Bumped when the serialised shape changes in a way a reader must notice.
#: Carried IN the payload so a replay reading an old checkpoint finds out
#: rather than mis-parsing it.
CHECKPOINT_VERSION = 1


class WorldStateError(ValueError):
    """A checkpoint that cannot be read as one."""


@dataclass(frozen=True)
class BudgetRemaining:
    """What is left to spend, taken from `reliability.budget.Budget`.

    A snapshot rather than the Budget object itself: the Budget is mutable and
    carries its refusal list and its task type, and a checkpoint needs the
    three numbers a stop condition reads. `from_budget` is the only intended
    constructor, so the two cannot disagree about what "remaining" means.
    """

    cost_usd: float = 0.0
    iterations: int = 0
    replans: int = 0

    @classmethod
    def from_budget(cls, budget: Any) -> "BudgetRemaining":
        return cls(
            cost_usd=float(budget.remaining_usd),
            iterations=max(0, int(budget.max_iterations) - int(budget.iterations)),
            replans=max(0, int(budget.max_replans) - int(budget.replans)),
        )

    @property
    def exhausted(self) -> bool:
        """No further model call or retrieval is affordable.

        Any one of the three ceilings is enough. They are separate because a
        loop can spin without spending, which is what the iteration and replan
        ceilings catch and the cost ceiling does not.
        """
        return self.cost_usd <= 0 or self.iterations <= 0 or self.replans <= 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cost_usd": self.cost_usd,
            "iterations": self.iterations,
            "replans": self.replans,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BudgetRemaining":
        return cls(
            cost_usd=float(payload.get("cost_usd") or 0.0),
            iterations=int(payload.get("iterations") or 0),
            replans=int(payload.get("replans") or 0),
        )


@dataclass(frozen=True)
class WorldState:
    """Everything a resuming run needs, and nothing it does not."""

    #: What this investigation is for, in the product's own words.
    objective: str
    #: Idempotency keys of actions whose external effect has committed.
    completed_actions: tuple[str, ...] = ()
    #: Idempotency keys recorded but not yet committed. A resuming run reads
    #: their state from the ledger; it must never assume from this list alone,
    #: because an action can commit between the checkpoint and the crash.
    pending_actions: tuple[str, ...] = ()
    #: Evidence ledger row ids gathered so far. Ids, never text.
    evidence_ids: tuple[str, ...] = ()
    budget_remaining: BudgetRemaining = field(default_factory=BudgetRemaining)
    #: Approval ids already held, so a resume does not ask a person twice.
    approvals_held: tuple[str, ...] = ()
    #: Fact keys still in the UNKNOWN state. This is the work list: it is what
    #: turns a summariser into an investigator.
    unknowns_outstanding: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The serialised checkpoint. JSON-safe by construction."""
        return {
            "checkpoint_version": CHECKPOINT_VERSION,
            "objective": self.objective,
            "completed_actions": list(self.completed_actions),
            "pending_actions": list(self.pending_actions),
            "evidence_ids": list(self.evidence_ids),
            "budget_remaining": self.budget_remaining.as_dict(),
            "approvals_held": list(self.approvals_held),
            "unknowns_outstanding": list(self.unknowns_outstanding),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorldState":
        """Read a checkpoint back.

        A version this reader does not know RAISES. The alternative is reading
        an older shape with today's rules, which is a resume that believes it
        knows what already committed and does not.
        """
        version = payload.get("checkpoint_version")
        if version != CHECKPOINT_VERSION:
            raise WorldStateError(
                f"checkpoint version {version!r} cannot be read by version "
                f"{CHECKPOINT_VERSION}"
            )
        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise WorldStateError("a checkpoint without an objective serves nothing")
        return cls(
            objective=objective,
            completed_actions=_strings(payload.get("completed_actions")),
            pending_actions=_strings(payload.get("pending_actions")),
            evidence_ids=_strings(payload.get("evidence_ids")),
            budget_remaining=BudgetRemaining.from_dict(
                payload.get("budget_remaining") or {}
            ),
            approvals_held=_strings(payload.get("approvals_held")),
            unknowns_outstanding=_strings(payload.get("unknowns_outstanding")),
        )

    def with_completed_action(self, idempotency_key: str) -> "WorldState":
        """The state after one action's effect has committed.

        Moves the key from pending to completed rather than only appending, so
        a checkpoint never lists one action in both places: a resuming run
        reading it in the pending list would be told to consider doing it
        again.
        """
        return replace(
            self,
            completed_actions=_appended(self.completed_actions, idempotency_key),
            pending_actions=tuple(
                key for key in self.pending_actions if key != idempotency_key
            ),
        )

    def with_pending_action(self, idempotency_key: str) -> "WorldState":
        if idempotency_key in self.completed_actions:
            return self
        return replace(
            self, pending_actions=_appended(self.pending_actions, idempotency_key)
        )

    def with_unknowns(self, fact_keys: Sequence[str]) -> "WorldState":
        """Replace the outstanding set wholesale.

        Replaced rather than merged because the unknowns are DERIVED from the
        evidence every time they are computed, exactly as a claim's support
        state is. Merging would keep a fact on the list after the evidence that
        answered it arrived.
        """
        return replace(self, unknowns_outstanding=tuple(fact_keys))


#: The exact field set, so a test can pin it without restating the names.
WORLD_STATE_FIELDS: frozenset[str] = frozenset(f.name for f in fields(WorldState))


def _appended(existing: tuple[str, ...], value: str) -> tuple[str, ...]:
    return existing if value in existing else existing + (value,)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise WorldStateError(f"expected a list of identifiers, got {type(value).__name__}")
    return tuple(str(item) for item in value)
