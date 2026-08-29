"""Stage 3: EVIDENCE TIERING -- tier, provenance, independence, and four modifiers.

spec-doc5 §A.3 specifies this stage as "tier, provenance, independence group,
specificity/attribution/scale/decay modifiers", and §B.3 assigns it to
"Haiku 4.5 (deterministic where possible) -- mostly rule-based; only the
specificity modifier needs model judgment".

THIS MODULE IS THE "DETERMINISTIC WHERE POSSIBLE" HALF, and it turns out that is
all four modifiers, not three. Specificity is the one spec-doc5 flags as needing
judgment, and a regex over numbers, dates and named systems answers it well
enough that spending a model call -- and accepting run-to-run variance in an
input to a grade -- is the worse trade. `refine_specificity` exists as the hook
for a model to OVERRIDE the deterministic answer where it genuinely disagrees,
and it can only move the modifier within the same bounds, so the model can
sharpen the judgment and cannot invent one.

THE TIER IS NOT THE MODIFIER
------------------------------
Two separate things, and conflating them is the classic error here:

  TIER      is about the SOURCE. Who is telling us? `evidence/ledger.py` already
            owns this as an ordered lattice -- authoritative > validated >
            observed > inferred -- and it is REUSED rather than restated. A
            second ordered trust scale in this codebase would be the two
            five-label rating scales all over again.

  MODIFIERS are about THIS PARTICULAR PIECE. The same source can produce a
            precise, self-attributed, recent, scaled claim and a vague,
            team-attributed, five-year-old one, and they are not worth the same.

So `weight` is `tier_base * specificity * attribution * scale * decay`, and each
term is returned separately, because "why is this evidence weighted 0.31" has to
be answerable by reading a row.

WHY A FLOOR, AND WHY IT IS NOT ZERO
-------------------------------------
Every modifier has a floor above zero. A piece of evidence that is vague,
team-attributed, unscaled and four years old is WEAK EVIDENCE, and weak evidence
is not the same as no evidence -- it still corroborates, it still counts toward
independence, and it is still something a human reviewer would want to see. A
zero would silently delete it, and the deletion would be invisible: the report
would simply read as though the candidate had never said the thing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.services.evidence import ledger
from app.services.miti.claims import SUBJECT_AMBIGUOUS, SUBJECT_SELF, SUBJECT_TEAM

__all__ = [
    "TIER_BASE",
    "GROUP_CANDIDATE",
    "GROUP_EMPLOYER",
    "GROUP_THIRD_PARTY",
    "GROUP_ARTEFACT",
    "INDEPENDENCE_GROUPS",
    "independence_group_for",
    "TieredEvidence",
    "tier_evidence",
    "specificity_modifier",
    "attribution_modifier",
    "scale_modifier",
    "decay_modifier",
    "refine_specificity",
]

# ── Tier ─────────────────────────────────────────────────────────────────────
#
# The base weight per trust level, reusing the ledger's lattice rather than
# restating it. The gap between `observed` and `inferred` is the widest one on
# purpose: `inferred` is "the product concluded it from something else", which
# is the exact shape of evidence that looks like corroboration and carries none.
TIER_BASE: dict[str, float] = {
    ledger.TRUST_AUTHORITATIVE: 1.0,
    ledger.TRUST_VALIDATED: 0.8,
    ledger.TRUST_OBSERVED: 0.6,
    ledger.TRUST_INFERRED: 0.25,
}

DEFAULT_TIER_BASE = TIER_BASE[ledger.TRUST_INFERRED]


# ── Independence ─────────────────────────────────────────────────────────────
#
# The group key is the ORIGINATOR, never the document. See
# `triangulation.independence_groups` for the argument; it is restated in one
# sentence here because this is where the key is assigned.
#
# A resume line and the candidate restating it in the interview could not have
# disagreed. They are one source saying one thing twice, and counting them as
# two is how a confidently written resume becomes a well-corroborated candidate.

GROUP_CANDIDATE = "candidate"
GROUP_EMPLOYER = "employer"
GROUP_THIRD_PARTY = "third_party"
GROUP_ARTEFACT = "artefact"

INDEPENDENCE_GROUPS: tuple[str, ...] = (
    GROUP_CANDIDATE,
    GROUP_EMPLOYER,
    GROUP_THIRD_PARTY,
    GROUP_ARTEFACT,
)

_GROUP_BY_SOURCE: dict[str, str] = {
    ledger.SOURCE_RESUME: GROUP_CANDIDATE,
    ledger.SOURCE_ANSWER: GROUP_CANDIDATE,
    ledger.SOURCE_VALIDATION: GROUP_CANDIDATE,
    # The JD and the SWOT are the EMPLOYER's account of the role. They are not
    # evidence about the candidate at all, and grouping them with the candidate
    # would let a role description corroborate a candidate's claim.
    ledger.SOURCE_JD: GROUP_EMPLOYER,
    ledger.SOURCE_SWOT: GROUP_EMPLOYER,
    # Platform memory. Derived from things already counted, so it must never
    # form an independent group -- that would let the product corroborate a
    # claim with its own earlier reading of the same claim.
    ledger.SOURCE_MEMORY: GROUP_CANDIDATE,
}


def independence_group_for(source_type: str) -> str:
    """Which group a source belongs to. Unknown sources are CANDIDATE.

    The conservative default: assuming a new source type is independent would
    manufacture corroboration, and manufactured corroboration is the failure
    this whole grouping exists to prevent. Assuming it is not independent costs
    a little confidence and cannot invent any.
    """
    return _GROUP_BY_SOURCE.get(source_type, GROUP_CANDIDATE)


# ── The four modifiers ───────────────────────────────────────────────────────


def specificity_modifier(*, has_specifics: bool, word_count: int) -> float:
    """How CHECKABLE this piece of evidence is.

    Not a quality judgment. "I reduced p99 from 800ms to 180ms by batching the
    writes" is worth more than "I improved performance a lot" because it can be
    interrogated, not because it sounds better -- a candidate who gives the
    second could be describing the same work.

    Word count matters independently of specifics: a three-word answer has
    nothing to check even if it contains a number.
    """
    if word_count < 8:
        return 0.6
    if has_specifics and word_count >= 25:
        return 1.15
    if has_specifics:
        return 1.05
    if word_count >= 40:
        # Long, unspecific prose. Slightly discounted rather than neutral: an
        # answer with plenty of room for a specific and none in it is weaker
        # than a short answer that never had room.
        return 0.9
    return 1.0


def attribution_modifier(subject: str) -> float:
    """WHO did the thing.

    The single most common way a true statement is misleading. "We migrated to
    Kafka" and "I migrated us to Kafka" are both true of the same person and say
    very different things about them.

    Team attribution is discounted, NOT discarded: being on the team that did it
    is real evidence of proximity, exposure and probably contribution. It is
    weaker evidence of personal capability, which is what the discount says.
    """
    return {
        SUBJECT_SELF: 1.1,
        SUBJECT_TEAM: 0.75,
        SUBJECT_AMBIGUOUS: 0.9,
    }.get(subject, 0.9)


def scale_modifier(*, role_seniority: str, evidence_scale: str | None) -> float:
    """Whether the evidence is at the scale the ROLE operates at.

    Deliberately mild in both directions. Someone who ran a five-person team
    applying for a fifty-person one has real, relevant, insufficient evidence;
    someone who ran a five-hundred-person function applying for a fifty-person
    one has real evidence too and a different question hanging over them. Neither
    is a reason to heavily reweight what they actually said -- that is a
    Role & Context Fit judgment, which is a dimension with a rubric, not a
    multiplier.
    """
    if not evidence_scale:
        return 1.0
    order = {"individual": 0, "team": 1, "function": 2, "organisation": 3}
    wanted = {
        "non_managerial": 0,
        "managerial": 1,
        "leadership": 2,
        "cxo": 3,
    }.get(role_seniority, 0)
    have = order.get(evidence_scale.lower(), wanted)
    gap = have - wanted
    if gap == 0:
        return 1.05
    if gap > 0:
        return 1.0   # bigger than needed: neutral, not a bonus
    return 0.9 if gap == -1 else 0.8


#: Where decay starts and where it bottoms out. Two years and six years.
#:
#: The floor is 0.5 and not lower because OLD EVIDENCE IS STILL EVIDENCE. A
#: candidate who led a migration eight years ago did lead it. What decays is how
#: much it tells you about them now, and halving is a strong statement of that
#: without pretending it did not happen.
_DECAY_START_DAYS = 730
_DECAY_FLOOR_DAYS = 2190
_DECAY_FLOOR = 0.5


def decay_modifier(
    as_of: datetime | None,
    *,
    now: datetime | None = None,
    max_age_days: int | None = None,
) -> float:
    """How much age discounts this evidence.

    `max_age_days` is Layer 2's answer to "how recent does relevant experience
    have to be" and REPLACES the platform curve when the client set one -- a
    client who said "it has to be current" is answering exactly this question
    and their answer should bind.

    An unknown date does NOT decay. Penalising evidence for missing a timestamp
    would penalise the candidate for the platform's own gap in provenance, and
    "we do not know when this was" is not "this was long ago".
    """
    if as_of is None:
        return 1.0
    reference = now or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (reference - as_of).total_seconds() / 86400.0)

    if max_age_days:
        if age_days <= max_age_days:
            return 1.0
        # Past the client's own stated horizon. Floored rather than zeroed, for
        # the same reason the platform curve is: it still happened.
        return _DECAY_FLOOR

    if age_days <= _DECAY_START_DAYS:
        return 1.0
    if age_days >= _DECAY_FLOOR_DAYS:
        return _DECAY_FLOOR
    span = _DECAY_FLOOR_DAYS - _DECAY_START_DAYS
    travelled = (age_days - _DECAY_START_DAYS) / span
    return 1.0 - travelled * (1.0 - _DECAY_FLOOR)


# ── The tiered piece ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TieredEvidence:
    """One piece of evidence, weighed, with every term kept.

    `weight` is INTERNAL. It orders evidence for a prompt and for an operator;
    it is not a score, not a grade, and it must never appear in a response
    schema. That is the identical rule `ledger.EvidenceItem.relevance` carries
    and it is restated here because this is a new place a number lives.
    """

    ref: str
    trust: str
    independence_group: str
    tier_base: float
    specificity: float
    attribution: float
    scale: float
    decay: float

    @property
    def weight(self) -> float:
        return (
            self.tier_base
            * self.specificity
            * self.attribution
            * self.scale
            * self.decay
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "trust": self.trust,
            "independence_group": self.independence_group,
            "weight": round(self.weight, 4),
            "modifiers": {
                "tier_base": round(self.tier_base, 4),
                "specificity": round(self.specificity, 4),
                "attribution": round(self.attribution, 4),
                "scale": round(self.scale, 4),
                "decay": round(self.decay, 4),
            },
        }


def tier_evidence(
    *,
    ref: str,
    trust: str,
    source_type: str,
    subject: str,
    text: str,
    has_specifics: bool,
    as_of: datetime | None = None,
    role_seniority: str = "non_managerial",
    evidence_scale: str | None = None,
    max_age_days: int | None = None,
    now: datetime | None = None,
) -> TieredEvidence:
    """Run one piece of evidence through the tier and all four modifiers."""
    return TieredEvidence(
        ref=ref,
        trust=trust,
        independence_group=independence_group_for(source_type),
        tier_base=TIER_BASE.get(trust, DEFAULT_TIER_BASE),
        specificity=specificity_modifier(
            has_specifics=has_specifics, word_count=len((text or "").split())
        ),
        attribution=attribution_modifier(subject),
        scale=scale_modifier(
            role_seniority=role_seniority, evidence_scale=evidence_scale
        ),
        decay=decay_modifier(as_of, now=now, max_age_days=max_age_days),
    )


#: How far a model may move the deterministic specificity answer.
#:
#: Bounded for the same reason every other layer's modifier is: a model that
#: could set specificity freely could set an input to a grade freely, and the
#: run-to-run variance would land directly on a candidate. Within these bounds
#: it can sharpen a judgment the regex got roughly right; it cannot invent one.
_SPECIFICITY_MIN, _SPECIFICITY_MAX = 0.6, 1.15


def refine_specificity(
    tiered: TieredEvidence, proposed: float | None
) -> TieredEvidence:
    """Let the model adjust specificity within bounds. The hook §B.3 describes.

    A None or an out-of-type value leaves the deterministic answer standing,
    which is the correct degradation: an outage costs the refinement and nothing
    else.
    """
    if proposed is None:
        return tiered
    try:
        value = float(proposed)
    except (TypeError, ValueError):
        return tiered
    clamped = max(_SPECIFICITY_MIN, min(_SPECIFICITY_MAX, value))
    return TieredEvidence(
        ref=tiered.ref,
        trust=tiered.trust,
        independence_group=tiered.independence_group,
        tier_base=tiered.tier_base,
        specificity=clamped,
        attribution=tiered.attribution,
        scale=tiered.scale,
        decay=tiered.decay,
    )
