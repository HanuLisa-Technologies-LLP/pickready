"""The Runbook's six-tier evidence hierarchy, E0 to E5, and how this product's
ledger rows map onto it.

    E0  Asserted                              unverifiable self-claim
    E1  Self-described with specificity       checkable specifics in a self-report
    E2  Artefact provided by candidate        portfolio, sample, repository link
    E3  Structured response under control     validation form, take-home, unproctored
    E4  Demonstrated under observation        live probe, proctored assessment
    E5  Third-party verified                  issuing body, reference, employer

WHY THIS EXISTS SEPARATELY FROM `ledger.TRUST_*`
--------------------------------------------------
They answer different questions and this codebase already had the first one.
`ledger`'s four trust levels are the PRODUCT's lattice: authoritative >
validated > observed > inferred, which is "how far do we trust the row". The
Runbook's six tiers are a lattice over the COST OF FABRICATION (Runbook section
6.2): "strength is assigned by how expensive the evidence is to fake, not how
impressive it looks". Two different orderings over the same rows.

The reason the second one has to exist is Runbook section 14.1, which states an
abstention rule whose trigger is an evidence TIER and not a score:

    A must-have competency has no evidence above E1
        -> Competency reported as Unassessed; candidate cannot be Ready to Pick

That rule catches a case a score-based control structurally cannot. Section
10.2 defines a competency score as a weighted mean in which evidence strength
appears in BOTH the numerator and the denominator, so for a competency
supported by a single claim the strength terms cancel and the score equals the
rubric level exactly, at every tier from E0 to E5. The Runbook says so in its
own words in that section: a dazzling claim with weak evidence and a modest
claim with strong evidence land in the same place, deliberately. A fabricated
Must-have resting on one weakest-tier resume bullet therefore scores high and
never trips a score-based cap. Section 14.1 is the control that catches it, and
it needs a tier to read.

THE MAPPING IS DETERMINISTIC AND CALLS NO MODEL
-------------------------------------------------
Every tier below is decided by the ledger row's own recorded source type, trust
level and whether the text carried checkable specifics. Asking a model what
tier a piece of evidence is would put a sampled value underneath a hard
abstention rule, which is the failure mode `TASK_TEMPERATURE` already argues
against for scoring calls, applied to the one control that decides whether a
candidate can be delivered at all.

STRENGTHS ARE READ FROM `runbook_data`, NEVER TYPED HERE
----------------------------------------------------------
Section 6.1's default strengths (0.10 / 0.25 / 0.40 / 0.55 / 0.80 / 0.95) live
in `runbook_data/evidence_tiers.yaml` with their citation, and
`tests/test_runbook_parity.py` fails if that file and the Runbook disagree in
either direction. A literal here would be a number nobody could trace.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services.evidence import ledger
from app.services.hiring import runbook_data

__all__ = [
    "E0",
    "E1",
    "E2",
    "E3",
    "E4",
    "E5",
    "TIERS",
    "ABOVE_E1_FLOOR",
    "EvidenceTierError",
    "above_e1",
    "rank",
    "strength",
    "tier_for",
    "tier_for_item",
]

E0 = "E0"
E1 = "E1"
E2 = "E2"
E3 = "E3"
E4 = "E4"
E5 = "E5"

#: Weakest first. Runbook section 6.1's own order, and the one `rank` reads.
TIERS: tuple[str, ...] = (E0, E1, E2, E3, E4, E5)

#: The tier section 14.1's abstention rule sits ON: a Must-have with no
#: evidence strictly ABOVE this tier is Unassessed. Named rather than inlined
#: so the rule and the constant cannot drift apart.
ABOVE_E1_FLOOR = E1


class EvidenceTierError(ValueError):
    """An unknown tier, or a Runbook data file that cannot supply one.

    Raised rather than defaulted. A tier substituted for a failed lookup would
    decide, silently and wrongly, whether a Must-have is Unassessed.
    """


def rank(tier: str) -> int:
    """Position in the E0 to E5 lattice. Raises for an unknown tier."""
    try:
        return TIERS.index(tier)
    except ValueError as exc:
        raise EvidenceTierError(
            f"Unknown evidence tier {tier!r}; Runbook section 6.1 defines "
            f"{list(TIERS)}."
        ) from exc


def above_e1(tier: str) -> bool:
    """Runbook section 14.1's test, stated once.

    True when this tier is strictly stronger than E1. Note that "no evidence at
    all" is not a tier and is handled by the caller: a competency with an empty
    evidence set trivially has nothing above E1, and section 14.1's consequence
    applies to it exactly as it does to a competency resting on one resume line.
    """
    return rank(tier) > rank(ABOVE_E1_FLOOR)


def strength(tier: str) -> float:
    """Section 6.1's default strength for a tier, read from `runbook_data`.

    Raises `EvidenceTierError` when the data file has no entry, rather than
    substituting a middling default: a strength nobody wrote down would enter
    section 10.2's arithmetic looking exactly like one somebody did.
    """
    table = runbook_data.evidence_tiers().get("tiers")
    if not isinstance(table, Mapping):
        raise EvidenceTierError(
            "runbook_data/evidence_tiers.yaml carries no `tiers` mapping; "
            "section 6.1's six tiers are the source for every strength here."
        )
    entry = table.get(tier)
    if not isinstance(entry, Mapping) or "default_strength" not in entry:
        raise EvidenceTierError(
            f"runbook_data/evidence_tiers.yaml has no default_strength for "
            f"tier {tier!r} (section 6.1)."
        )
    return float(entry["default_strength"])


# -- The mapping from this product's evidence rows onto the six tiers ---------
#
# Every branch below names the Runbook row it implements. Where two rows could
# apply, the WEAKER is taken: section 6.2's fabrication-cost principle means an
# over-stated tier is the dangerous direction, because it is the one that makes
# a fabricated claim look corroborated.

#: Sources that are the EMPLOYER's account of the role rather than evidence
#: about a candidate. A JD or a SWOT tells us what the job needs; it can never
#: corroborate a claim about the person, so it carries the weakest tier and
#: contributes nothing to section 14.1's coverage test.
_EMPLOYER_SOURCES: frozenset[str] = frozenset({ledger.SOURCE_JD, ledger.SOURCE_SWOT})


def tier_for(
    *,
    source_type: str,
    trust: str,
    has_specifics: bool = False,
) -> str:
    """The Runbook tier for one piece of evidence. Deterministic, no model.

    `has_specifics` is `claims.has_specifics` over the evidence text, taken at
    the point the row was written, and it separates section 6.1's E0 from its
    E1: "self-report containing checkable specifics (numbers, systems, names,
    mechanisms)". It defaults to False, which resolves an unknown to E0. That
    is the restrictive direction and it is deliberate: the Runbook is silent on
    a self-report whose specificity was never recorded, and reading it as E1
    would let an unexamined resume line satisfy the one test written to catch
    an unexamined resume line.

    THE ASSESSMENT CONVERSATION IS E3, NOT E4. Section 6.1 puts "unproctored
    assessment" at E3 and reserves E4 for evidence produced "under
    observation"; section 7.5 files "take-home / async assessment" at E3 with
    the note "assume AI assistance; score process not output". This product
    does not proctor, so nothing it collects from a candidate reaches E4 by
    this route, and claiming otherwise would overstate the cost of fabricating
    every answer in the transcript.
    """
    if trust == ledger.TRUST_AUTHORITATIVE:
        # Section 7.5: employment verification, issuing-body credential check
        # and documented employer confirmation all produce E5. The ledger's
        # `authoritative` is exactly "a document the employer or a system
        # issued", which is that row.
        return E5
    if source_type in _EMPLOYER_SOURCES:
        # Not evidence about the candidate at all. Section 5.2's graph keeps
        # role material and candidate material apart, and grouping them would
        # let a job description corroborate the applicant.
        return E0
    if trust == ledger.TRUST_INFERRED:
        # "The product concluded it from something else." Section 6.1's E0 is
        # "unverifiable self-claim"; an inference the platform made about a
        # candidate is weaker still, and section 5.4 forbids it from forming an
        # independent group for the same reason.
        return E0
    if source_type == ledger.SOURCE_ANSWER:
        # Section 6.1 E3, "structured response under controlled conditions:
        # validation questionnaire, take-home, unproctored assessment". The
        # assessment conversation is a structured, unseen, unproctored probe.
        return E3
    if source_type == ledger.SOURCE_VALIDATION:
        # Section 7.5: "validation questionnaire -> E3".
        return E3
    if source_type == ledger.SOURCE_RESUME:
        # Section 6.1's E0/E1 split, and the whole reason `has_specifics` is
        # carried on the row.
        return E1 if has_specifics else E0
    # Platform memory and anything else a future source type introduces.
    # Section 6.1's weakest tier, for the same reason `tiering` puts an unknown
    # source in the candidate independence group: assuming strength that was
    # never established manufactures corroboration.
    return E1 if has_specifics else E0


def tier_for_item(item: Any) -> str:
    """The tier for one `ledger.EvidenceItem`, reading its own recorded fields.

    `has_specifics` is taken from the row's provenance rather than from its
    text, because the ledger deliberately stores a locator and never the
    sentence: a copy of the candidate's words in a table that anyone with
    database access can read would be a quiet route around the capability that
    guards the transcript itself. Whoever writes the row is the only party that
    holds the text, so specificity is decided there and travels as a fact.
    """
    provenance = getattr(item, "provenance", None) or {}
    return tier_for(
        source_type=str(getattr(item, "source_type", "")),
        trust=str(getattr(item, "trust", ledger.TRUST_INFERRED)),
        has_specifics=bool(provenance.get("has_specifics", False)),
    )
