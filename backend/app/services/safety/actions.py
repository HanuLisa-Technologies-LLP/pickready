"""Actions an agent may never take alone, and the confidence gate around them.

THE LIST IS SHORT AND EVERY ENTRY IS IRREVERSIBLE TO A PERSON
--------------------------------------------------------------
Rejecting a candidate, withdrawing an offer, overriding a ranking. What they
share is not that they are important to the business -- plenty of reversible
things are -- but that the person on the other end experiences them immediately
and cannot experience them being undone.

CONFIDENCE IS NOT THE ONLY GATE, AND MUST NOT BE
-------------------------------------------------
A sensitive action requires a human decision REGARDLESS of confidence. The
confidence threshold only widens the set: below it, ordinary actions need review
too. Building it the other way round -- "high confidence proceeds
automatically" -- would mean the agent's own estimate of itself is what
authorises an irreversible act, and a confidently wrong agent is exactly the one
that should be stopped.

THIS IS A POLICY MODULE, NOT AN ENFORCEMENT POINT
--------------------------------------------------
Enforcement stays where it already is: `require_capability` on the route, and
the fact that no agent holds a tool that writes a decision. This says what
requires review; the absence of a write tool is what makes it true.
"""
from __future__ import annotations

from dataclasses import dataclass

REJECT_CANDIDATE = "reject_candidate"
REVOKE_OFFER = "revoke_offer"
OVERRIDE_RANKING = "override_ranking"
CHANGE_ROLE_ASSIGNMENT = "change_role_assignment"
SEND_BULK_EMAIL = "send_bulk_email"

#: Never automated, at any confidence.
SENSITIVE_ACTIONS: frozenset[str] = frozenset(
    {
        REJECT_CANDIDATE,
        REVOKE_OFFER,
        OVERRIDE_RANKING,
        CHANGE_ROLE_ASSIGNMENT,
        SEND_BULK_EMAIL,
    }
)

#: Below this, even an ordinary action wants a person to look first.
AUTONOMY_CONFIDENCE_FLOOR = 0.8


@dataclass(frozen=True)
class Decision:
    action: str
    requires_human: bool
    reason: str


def evaluate(action: str, *, confidence: float = 1.0) -> Decision:
    """Whether this action may proceed without a person."""
    if action in SENSITIVE_ACTIONS:
        return Decision(
            action=action,
            requires_human=True,
            reason=(
                f"{action} is irreversible for the person it affects and always "
                "requires a human decision"
            ),
        )
    if confidence < AUTONOMY_CONFIDENCE_FLOOR:
        return Decision(
            action=action,
            requires_human=True,
            reason=(
                f"confidence {confidence:.2f} is below the {AUTONOMY_CONFIDENCE_FLOOR:.2f} "
                "floor for acting without review"
            ),
        )
    return Decision(action=action, requires_human=False, reason="within autonomous limits")
