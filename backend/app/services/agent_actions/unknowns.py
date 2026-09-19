"""The unknowns ledger (W5.4): every fact is VERIFIED, CLAIMED, UNKNOWN or
CONTRADICTED.

THIS MAPS THE EXISTING LEDGER. IT DOES NOT DUPLICATE IT
--------------------------------------------------------
`services/evidence/ledger.py` already derives how a claim reads from the
evidence standing behind it (supported, contradicted, inferred_only,
unsupported), and `services/evidence/tiers.py` already places each piece of
evidence in the Runbook's E0 to E5 lattice by cost of fabrication. A second
four-valued state computed from the same rows would be a second answer to one
question, and the two would drift invisibly.

So nothing is recomputed here. This module states the projection, once:

    CONTRADICTED  evidence stands on both sides. Checked FIRST, exactly as
                  `support_state` checks it first: support never cancels a
                  contradiction, and any rule that let it would be the silent
                  averaging the evidence spec exists to forbid.
    VERIFIED      supported, and standing on evidence ABOVE E1. That is
                  `tiers.above_e1`, the same test Runbook section 14.1's
                  abstention rule uses, so "verified" here means what
                  "assessable" means there.
    CLAIMED       supported, but only by E0/E1 evidence. Somebody said it and
                  nobody has checked. A resume line is exactly this.
    UNKNOWN       nothing live stands behind it, or only the product's own
                  inference does.

WHY AN INFERENCE READS AS UNKNOWN AND NOT AS CLAIMED
------------------------------------------------------
`inferred_only` means every live item is the product concluding something from
something else, which is the product agreeing with itself. The ledger's own
note on that state is that there is something to ask about and nobody has
asked. That is the definition of an unknown, and it is the direction that
sends an investigator to look; reading it as a claim would let the platform's
own inference retire the question.

WHAT AGENTS DO WITH THIS
--------------------------
They work on the UNKNOWNS. That is the whole difference between a summariser
and an investigator, and it is what W5.5's stop condition is computed over.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.services.evidence import ledger, tiers

__all__ = [
    "FACT_CLAIMED",
    "FACT_CONTRADICTED",
    "FACT_STATES",
    "FACT_UNKNOWN",
    "FACT_VERIFIED",
    "Fact",
    "best_tier",
    "contradicted",
    "fact_for_claim",
    "facts_for_claims",
    "outstanding",
    "state_for_claim",
]

FACT_VERIFIED = "VERIFIED"
FACT_CLAIMED = "CLAIMED"
FACT_UNKNOWN = "UNKNOWN"
FACT_CONTRADICTED = "CONTRADICTED"

#: Strongest first, which is also the order a reader should scan them in.
FACT_STATES: tuple[str, ...] = (
    FACT_VERIFIED,
    FACT_CLAIMED,
    FACT_UNKNOWN,
    FACT_CONTRADICTED,
)


@dataclass(frozen=True)
class Fact:
    """One thing the product is asserting, and how well it stands up.

    `key` is the stable identifier the world state's `unknowns_outstanding`
    carries. It is the claim's own id, so a checkpoint written before a
    retrieval and read after it refers to the same fact.

    There is no `text` field, for the same reason `EvidenceItem` has none: the
    ledger stores a locator and never the sentence, and a checkpoint is read by
    more tools than the table is.
    """

    key: str
    subject: str
    dimension: str
    state: str
    #: The strongest tier among live supporting evidence, or None when nothing
    #: live stands behind it. A word from the Runbook's lattice, never a score.
    tier: str | None

    @property
    def is_outstanding(self) -> bool:
        """Whether an investigator still has work to do on it.

        CONTRADICTED is deliberately NOT outstanding. It is not answered by
        more retrieval; it is answered by a person, and no flag in this product
        ever auto-resolves one.
        """
        return self.state == FACT_UNKNOWN


def best_tier(claim: Any) -> str | None:
    """The strongest Runbook tier among the claim's LIVE supporting evidence.

    Strongest rather than typical: section 14.1 asks whether ANY evidence sits
    above E1, so an average would answer a different question and would let two
    weak items outvote the one strong item the rule is looking for.
    """
    live = list(getattr(claim, "live_support", ()) or ())
    if not live:
        return None
    return max((tiers.tier_for_item(item) for item in live), key=tiers.rank)


def state_for_claim(claim: Any) -> str:
    """Project one evidence-ledger claim onto the four fact states."""
    status = claim.status
    if status == ledger.CLAIM_CONTRADICTED:
        return FACT_CONTRADICTED
    if status in (ledger.CLAIM_UNSUPPORTED, ledger.CLAIM_INFERRED_ONLY):
        return FACT_UNKNOWN
    if status != ledger.CLAIM_SUPPORTED:
        # `support_state` returns one of four values and every one is handled
        # above. A fifth would mean the evidence ledger grew a state this
        # projection has no opinion about, and guessing would put a fact in a
        # band nobody chose.
        raise ValueError(f"unmapped claim status: {status!r}")
    tier = best_tier(claim)
    if tier is None:
        return FACT_UNKNOWN
    return FACT_VERIFIED if tiers.above_e1(tier) else FACT_CLAIMED


def fact_for_claim(claim: Any) -> Fact:
    claim_id = getattr(claim, "claim_id", None)
    if claim_id is None:
        # The key is what a checkpoint carries across a crash. A fact with no
        # stable key would be re-derived as a NEW unknown on every resume, so
        # the investigation would never finish and nothing would say why.
        raise ValueError(
            f"a claim with no id cannot be tracked as a fact: {type(claim).__name__}"
        )
    return Fact(
        key=str(claim_id),
        subject=str(getattr(claim, "subject", "")),
        dimension=str(getattr(claim, "dimension", "")),
        state=state_for_claim(claim),
        tier=best_tier(claim),
    )


def facts_for_claims(claims: Sequence[Any]) -> tuple[Fact, ...]:
    return tuple(fact_for_claim(claim) for claim in claims)


def outstanding(facts: Sequence[Fact]) -> tuple[str, ...]:
    """The fact keys an investigator still has work on, in the given order."""
    return tuple(fact.key for fact in facts if fact.is_outstanding)


def contradicted(facts: Sequence[Fact]) -> tuple[str, ...]:
    """Facts that need a PERSON, listed separately from the unknowns.

    Kept apart on purpose: an unknown is answered by looking harder, and a
    contradiction is answered by somebody deciding. Folding them into one work
    list would have an agent retrieve its way around a disagreement that a
    recruiter is supposed to see.
    """
    return tuple(fact.key for fact in facts if fact.state == FACT_CONTRADICTED)
