"""Stop when proved (W5.5), not when tired.

THE CONDITION IS "COULD THIS CHANGE THE BAND", NOT "AM I CONFIDENT"
--------------------------------------------------------------------
An investigation continues only while one more retrieval or model call has
positive expected value, and in a product whose delivered output is one of
four words, expected value has an exact meaning: the call could plausibly move
the band. Nothing else counts. A call that would refine a number nobody sees
is a call that costs money and changes no decision.

That is measured, not estimated. The caller resolves every outstanding unknown
in the candidate's favour and computes the band, then resolves every one
against them and computes it again. If the two bands agree, no arrangement of
the remaining evidence can move the delivered grade, and the investigation is
PROVED: it stops, with that reason recorded.

The projection is supplied by the caller rather than computed here on purpose.
The band comes from the scoring stack (`services/rating`, the Miti aggregator),
this module decides only whether to keep going, and a second implementation of
banding living inside a loop control would be exactly the dual code path this
repository forbids.

WHY EVERY STOP RECORDS ITS REASON
-----------------------------------
Four reasons stop a loop and they mean entirely different things: the answer is
settled, there was never a question, the money ran out, or the loop is not
converging. A run that stopped without recording which is indistinguishable
from a run that simply finished, which is the same argument `Budget` makes for
recording every refusal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.services.agent_actions.world_state import WorldState
from app.services.rating import GRADES

__all__ = [
    "MAX_ROUNDS_WITHOUT_NEW_EVIDENCE",
    "REASON_BUDGET",
    "REASON_NOTHING_OUTSTANDING",
    "REASON_NOT_CONVERGING",
    "REASON_PROVED",
    "REASON_WORK_REMAINS",
    "BandProjection",
    "StopDecision",
    "StopConditionError",
    "should_continue",
]

#: The answer cannot move, whatever the outstanding unknowns turn out to be.
REASON_PROVED = "proved"
#: There were no unknowns to work on.
REASON_NOTHING_OUTSTANDING = "nothing_outstanding"
#: A ceiling was reached. The unknowns are still unknown and the report must
#: say so; this is not a proof.
REASON_BUDGET = "budget_exhausted"
#: Rounds are passing and no new evidence is arriving.
REASON_NOT_CONVERGING = "not_converging"
#: Keep going.
REASON_WORK_REMAINS = "work_remains"

#: Rounds that may add no evidence before the loop is called not-converging.
#: Two, not one: the first empty round is often one badly formed query, and
#: stopping on it would abandon an investigation over a single miss. The third
#: is a loop. Same shape of argument as `reliability.budget.MAX_REPLANS`.
MAX_ROUNDS_WITHOUT_NEW_EVIDENCE = 2


class StopConditionError(ValueError):
    """A projection the stop condition cannot read.

    Raised rather than defaulted. A band substituted for one that could not be
    read would decide whether an investigation continues, and both directions
    are wrong: stopping early delivers a grade the evidence does not support,
    continuing forever spends a budget on a settled answer.
    """


@dataclass(frozen=True)
class BandProjection:
    """The band if every outstanding unknown resolves well, and if none does.

    Both are one of `services.rating.GRADES`. When they are equal the answer is
    pinned: no further evidence can move the delivered word.
    """

    best_case: str
    worst_case: str

    def __post_init__(self) -> None:
        for band in (self.best_case, self.worst_case):
            if band not in GRADES:
                raise StopConditionError(
                    f"{band!r} is not one of the four delivered grades"
                )

    @property
    def pinned(self) -> bool:
        return self.best_case == self.worst_case


@dataclass(frozen=True)
class StopDecision:
    """Whether to keep investigating, and why not if not."""

    proceed: bool
    reason: str

    @property
    def proved(self) -> bool:
        """True only when the answer is settled, never when a ceiling stopped it.

        The distinction is the point: a report written after a budget stop
        carries unknowns that are still unknown, and calling that proved would
        present a run that ran out of money as a run that finished.
        """
        return not self.proceed and self.reason in (
            REASON_PROVED,
            REASON_NOTHING_OUTSTANDING,
        )


def should_continue(
    *,
    state: WorldState,
    projection: BandProjection,
    rounds_without_new_evidence: int = 0,
) -> StopDecision:
    """Decide whether another retrieval or model call is worth making.

    The order of the checks decides which reason is RECORDED when more than one
    applies, so it is chosen rather than incidental: the strongest true
    statement about the world comes first. "The answer cannot move" is a fact
    about the evidence and stays true tomorrow; "the budget ran out" is a fact
    about this run and does not.
    """
    if projection.pinned:
        return StopDecision(proceed=False, reason=REASON_PROVED)
    if not state.unknowns_outstanding:
        return StopDecision(proceed=False, reason=REASON_NOTHING_OUTSTANDING)
    if state.budget_remaining.exhausted:
        return StopDecision(proceed=False, reason=REASON_BUDGET)
    if rounds_without_new_evidence >= MAX_ROUNDS_WITHOUT_NEW_EVIDENCE:
        return StopDecision(proceed=False, reason=REASON_NOT_CONVERGING)
    return StopDecision(proceed=True, reason=REASON_WORK_REMAINS)


def rounds_without_new_evidence(
    evidence_ids_by_round: Sequence[Sequence[str]],
) -> int:
    """How many of the most recent rounds added no evidence id.

    Counted from the END backwards and stops at the first round that added
    something, because the question is whether the loop is converging NOW. A
    total over the whole run would let three productive rounds hide two barren
    ones and keep a stuck loop alive.
    """
    barren = 0
    for round_ids in reversed(list(evidence_ids_by_round)):
        if round_ids:
            break
        barren += 1
    return barren
