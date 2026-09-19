"""The agent action ledger and what surrounds it (RPN-AI-UP-001 W5).

Re-exports only. Nothing is computed here: a package `__init__` that did work
would run it at import time for every module that only wanted a constant, the
same convention `services/evidence/__init__.py` already keeps.

    ledger        the table, and the state machine whose missing edge is the
                  control: UNKNOWN never goes back to RUNNING.
    gate          the one door an irreversible effect goes through. Unreachable
                  until the action is sealed.
    idempotency   keys derived from stable logical inputs, never a timestamp.
    world_state   what a checkpoint carries. Not a transcript.
    unknowns      VERIFIED / CLAIMED / UNKNOWN / CONTRADICTED, projected from
                  the evidence ledger rather than computed a second time.
    stopping      continue only while another call could move the band.
"""
from __future__ import annotations

from app.services.agent_actions.gate import (
    READ_BACK_ABSENT,
    READ_BACK_FOUND,
    READ_BACK_INDETERMINATE,
    ActionRequest,
    AdapterRefused,
    AdapterResult,
    ApprovalRequired,
    GateError,
    IndeterminateAction,
    Outcome,
    ReadBack,
    RiskClassRefused,
    execute,
    resolve_unknown,
)
from app.services.agent_actions.idempotency import (
    IdempotencyKeyError,
    args_digest,
    assessment_invitation_key,
    derive,
    prism_report_key,
)
from app.services.agent_actions.ledger import (
    ACTION_FAILED,
    ACTION_PENDING,
    ACTION_ROLLED_BACK,
    ACTION_RUNNING,
    ACTION_SUCCEEDED,
    ACTION_UNKNOWN,
    AUTHORIZATION_HUMAN,
    AUTHORIZATION_POLICY,
    RISK_IRREVERSIBLE,
    RISK_REVERSIBLE,
    TRANSITIONS,
    UNRESOLVED_STATES,
    ActionLedgerError,
    IdempotencyKeyReused,
    IllegalActionTransition,
    reserve,
    seal,
    unresolved,
)
from app.services.agent_actions.stopping import (
    REASON_BUDGET,
    REASON_NOTHING_OUTSTANDING,
    REASON_NOT_CONVERGING,
    REASON_PROVED,
    REASON_WORK_REMAINS,
    BandProjection,
    StopDecision,
    should_continue,
)
from app.services.agent_actions.unknowns import (
    FACT_CLAIMED,
    FACT_CONTRADICTED,
    FACT_UNKNOWN,
    FACT_VERIFIED,
    Fact,
    facts_for_claims,
    outstanding,
    state_for_claim,
)
from app.services.agent_actions.world_state import (
    CHECKPOINT_VERSION,
    BudgetRemaining,
    WorldState,
    WorldStateError,
)

__all__ = [
    "ACTION_FAILED",
    "ACTION_PENDING",
    "ACTION_ROLLED_BACK",
    "ACTION_RUNNING",
    "ACTION_SUCCEEDED",
    "ACTION_UNKNOWN",
    "AUTHORIZATION_HUMAN",
    "AUTHORIZATION_POLICY",
    "CHECKPOINT_VERSION",
    "FACT_CLAIMED",
    "FACT_CONTRADICTED",
    "FACT_UNKNOWN",
    "FACT_VERIFIED",
    "READ_BACK_ABSENT",
    "READ_BACK_FOUND",
    "READ_BACK_INDETERMINATE",
    "REASON_BUDGET",
    "REASON_NOTHING_OUTSTANDING",
    "REASON_NOT_CONVERGING",
    "REASON_PROVED",
    "REASON_WORK_REMAINS",
    "RISK_IRREVERSIBLE",
    "RISK_REVERSIBLE",
    "TRANSITIONS",
    "UNRESOLVED_STATES",
    "ActionLedgerError",
    "ActionRequest",
    "AdapterRefused",
    "AdapterResult",
    "ApprovalRequired",
    "BandProjection",
    "BudgetRemaining",
    "Fact",
    "GateError",
    "IdempotencyKeyError",
    "IdempotencyKeyReused",
    "IllegalActionTransition",
    "IndeterminateAction",
    "Outcome",
    "ReadBack",
    "RiskClassRefused",
    "StopDecision",
    "WorldState",
    "WorldStateError",
    "args_digest",
    "assessment_invitation_key",
    "derive",
    "execute",
    "facts_for_claims",
    "outstanding",
    "prism_report_key",
    "reserve",
    "resolve_unknown",
    "seal",
    "should_continue",
    "state_for_claim",
    "unresolved",
]
