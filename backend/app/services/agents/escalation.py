"""Handing a decision to a person, with enough for them to actually make it.

WHY AN ESCALATION HAS A SHAPE
------------------------------
"The agent was not confident" is not an escalation, it is an apology. A person
picking this up has to decide something, and to decide it they need five things:
what is uncertain, what evidence exists, what evidence is missing, why automation
stopped, and what specifically they must resolve. Spec 38 names all five, and
this module refuses to build an escalation missing any of them -- because an
escalation that only says "needs review" gets routed to a queue, aged, and
resolved by someone guessing, which is the outcome escalating was meant to avoid.

A SENSITIVE ACTION ESCALATES AT ANY CONFIDENCE
-----------------------------------------------
Reject, revoke an offer, override a ranking. `safety.actions.evaluate` already
owns that rule and this module CALLS it rather than restating it: low confidence
only WIDENS the review set, never narrows it, and building it the other way
round would mean the agent's own opinion of itself authorises an irreversible
act. A confidently wrong agent is the one that should be stopped.

Enforcement is still the absence of a write tool. This module says when a person
must be asked; `AGENT_TOOLS` is what makes it impossible for an agent to proceed
without one.

A LOCKED ARTIFACT IS NEVER EDITED, ONLY ESCALATED
---------------------------------------------------
A frozen matrix is the product's only comparability guarantee between two
reports on one job. An agent that "just needed to add a criterion" would be
editing the criteria a report already states grades against. So needing to
modify a locked artifact is a trigger, not a branch.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

from app.services.safety import actions
from app.services.verification import base as verification

__all__ = [
    "Escalation",
    "EscalationContract",
    "EVIDENCE_SHAPED_REASONS",
    "REASONS",
    "escalate",
    "for_action",
    "from_verdict",
]

# ── Triggers (spec 38) ───────────────────────────────────────────────────────
REASON_CONTRADICTORY_EVIDENCE = "critical_evidence_contradictory"
REASON_CONTEXT_UNAVAILABLE = "required_context_unavailable"
REASON_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
REASON_POLICY_BLOCK = "policy_block"
REASON_LOCKED_ARTIFACT = "locked_artifact_modification"
REASON_SENSITIVE_ACTION = "sensitive_action"
REASON_LOW_CONFIDENCE = "confidence_below_bound"

REASONS: frozenset[str] = frozenset(
    {
        REASON_CONTRADICTORY_EVIDENCE,
        REASON_CONTEXT_UNAVAILABLE,
        REASON_INSUFFICIENT_EVIDENCE,
        REASON_POLICY_BLOCK,
        REASON_LOCKED_ARTIFACT,
        REASON_SENSITIVE_ACTION,
        REASON_LOW_CONFIDENCE,
    }
)

#: The reasons that are ABOUT evidence, and therefore have to say which evidence
#: is missing. The others are not: a sensitive action escalates because of what
#: it IS, a policy block because of a rule, a locked artifact because it is
#: locked, and a low-confidence run because of a threshold. Requiring a missing
#: evidence list from those would be answered with a placeholder, and a
#: placeholder reads like an answer to the person who has to act on it.
EVIDENCE_SHAPED_REASONS: frozenset[str] = frozenset(
    {
        REASON_CONTRADICTORY_EVIDENCE,
        REASON_CONTEXT_UNAVAILABLE,
        REASON_INSUFFICIENT_EVIDENCE,
    }
)


class EscalationContract(ValueError):
    """An escalation missing one of the five required parts. Refused.

    Refused rather than filled in with a placeholder: a placeholder reads like
    an answer to the person who has to act on it.
    """


@dataclass(frozen=True)
class Escalation:
    """One structured request for a human decision."""

    reason: str
    #: What the agent could not settle, in one sentence a recruiter can read.
    uncertainty: str
    #: What is known, as short statements with their source. Never the raw
    #: transcript: the point is that a person can act, not that they re-read
    #: everything the agent read.
    evidence_present: tuple[str, ...]
    #: What is absent, which is the half that usually decides the outcome.
    evidence_missing: tuple[str, ...]
    #: Why automation stopped HERE. Distinct from `reason`: the reason is the
    #: category, this is the specific thing.
    stopped_because: str
    #: The decision being asked for, phrased as a question with an answer.
    human_must_resolve: str
    agent_id: str | None = None
    tenant_id: str | None = None
    job_id: str | None = None
    candidate_id: str | None = None
    action: str | None = None
    #: INTERNAL engineering value. Recorded so an operator can see whether the
    #: escalation came from a low-confidence run or from a rule, and never
    #: rendered to a client.
    confidence: float | None = None
    escalation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    raised_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, object]:
        return {
            "escalation_id": self.escalation_id,
            "reason": self.reason,
            "uncertainty": self.uncertainty,
            "evidence_present": list(self.evidence_present),
            "evidence_missing": list(self.evidence_missing),
            "stopped_because": self.stopped_because,
            "human_must_resolve": self.human_must_resolve,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "job_id": self.job_id,
            "candidate_id": self.candidate_id,
            "action": self.action,
            "confidence": self.confidence,
            "raised_at": self.raised_at.isoformat(),
        }


def escalate(
    *,
    reason: str,
    uncertainty: str,
    evidence_present: Iterable[str],
    evidence_missing: Iterable[str],
    stopped_because: str,
    human_must_resolve: str,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    job_id: str | None = None,
    candidate_id: str | None = None,
    action: str | None = None,
    confidence: float | None = None,
) -> Escalation:
    """Build an escalation, refusing an incomplete one.

    `evidence_present` may legitimately be empty -- "we have nothing" is a real
    and important state, and it is exactly what `REASON_CONTEXT_UNAVAILABLE`
    describes. The four PROSE parts may not be, because each of them is
    something only the run that stopped can supply and nobody downstream can
    reconstruct.
    """
    if reason not in REASONS:
        raise EscalationContract(f"unknown escalation reason {reason!r}")
    for name, value in (
        ("uncertainty", uncertainty),
        ("stopped_because", stopped_because),
        ("human_must_resolve", human_must_resolve),
    ):
        if not str(value or "").strip():
            raise EscalationContract(f"an escalation must state {name}")

    missing = tuple(str(item) for item in evidence_missing)
    if not missing and reason in EVIDENCE_SHAPED_REASONS:
        raise EscalationContract(
            "an escalation must state what evidence is missing; "
            "if nothing is missing, the trigger is not evidence"
        )

    return Escalation(
        reason=reason,
        uncertainty=str(uncertainty).strip(),
        evidence_present=tuple(str(item) for item in evidence_present),
        evidence_missing=missing,
        stopped_because=str(stopped_because).strip(),
        human_must_resolve=str(human_must_resolve).strip(),
        agent_id=agent_id,
        tenant_id=tenant_id,
        job_id=job_id,
        candidate_id=candidate_id,
        action=action,
        confidence=confidence,
    )


def for_action(
    action: str,
    *,
    confidence: float = 1.0,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    job_id: str | None = None,
    candidate_id: str | None = None,
    evidence_present: Sequence[str] = (),
    evidence_missing: Sequence[str] = (),
) -> Escalation | None:
    """The escalation this action requires, or None if it needs no person.

    Delegates the decision to `safety.actions.evaluate` rather than deciding it
    again here. Two copies of "which actions are sensitive" is one copy that
    eventually omits an action somebody added to the other, and the omission
    reads as an agent quietly gaining the authority to take it.
    """
    decision = actions.evaluate(action, confidence=confidence)
    if not decision.requires_human:
        return None

    sensitive = action in actions.SENSITIVE_ACTIONS
    reason = REASON_SENSITIVE_ACTION if sensitive else REASON_LOW_CONFIDENCE
    return escalate(
        reason=reason,
        uncertainty=(
            f"whether {action.replace('_', ' ')} is the right decision for this candidate"
        ),
        evidence_present=evidence_present,
        evidence_missing=evidence_missing,
        stopped_because=decision.reason,
        human_must_resolve=(
            f"Confirm or refuse {action.replace('_', ' ')}, and record why."
        ),
        agent_id=agent_id,
        tenant_id=tenant_id,
        job_id=job_id,
        candidate_id=candidate_id,
        action=action,
        confidence=confidence,
    )


def from_verdict(
    verdict: verification.Verdict,
    *,
    reason: str = REASON_INSUFFICIENT_EVIDENCE,
    uncertainty: str,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    job_id: str | None = None,
    candidate_id: str | None = None,
) -> Escalation | None:
    """Turn a failed gate into an escalation, or None if the gate passed.

    The findings are already written as INSTRUCTIONS (see `base.py`), so they
    make a usable "what the human must resolve" without rewriting: a person
    reading "Add a criterion covering Kubernetes" knows what to do, where "the
    matrix scored low on coverage" leaves them to work it out.

    A gate failure is not automatically an escalation. It is one when the loop
    has already spent its bounded retries and the defect is still there, which
    is the caller's judgement and not this module's.
    """
    if verdict.passed:
        return None
    high = verdict.by_severity(verification.SEVERITY_HIGH)
    findings = high or verdict.findings
    return escalate(
        reason=reason,
        uncertainty=uncertainty,
        evidence_present=tuple(f"{f.location}: {f.detail}" for f in verdict.findings),
        evidence_missing=tuple(f.location for f in findings),
        stopped_because=(
            f"{verdict.verifier} did not pass after the loop's bounded retries"
        ),
        human_must_resolve="; ".join(f.recommendation for f in findings),
        agent_id=agent_id,
        tenant_id=tenant_id,
        job_id=job_id,
        candidate_id=candidate_id,
        confidence=verdict.confidence,
    )
