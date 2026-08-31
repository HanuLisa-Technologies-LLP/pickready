"""Stage 5: TRIANGULATION -- cross-source consistency and the benign-explanation rule.

spec-doc5 §A.3 specifies this stage as: "cross-source consistency, contradiction
detection with type/severity, independence counting, mandatory
benign-explanation generation before any severity above Minor (§57.4 -- at least
two benign explanations per contradiction, always)".

WHAT ALREADY EXISTED, AND WHY THIS IS NOT A SECOND COPY OF IT
---------------------------------------------------------------
`services/evidence/contradictions.py` already detects contradictions across six
axes, grades them NONE / MINOR / MATERIAL / CRITICAL, returns the actions each
severity obliges, and raises rather than settling while a MATERIAL one stands.
That module is REUSED unchanged. Rewriting it here would have produced two
severity scales that must agree and eventually would not -- the exact failure
its own docstring is written against.

What this module adds is the two things spec-doc5 asks for that it does not do:
INDEPENDENCE COUNTING, and the BENIGN-EXPLANATION REQUIREMENT.

THE BENIGN-EXPLANATION RULE, AND WHY IT IS A GATE RATHER THAN A HABIT
-----------------------------------------------------------------------
Before any contradiction may be escalated above MINOR, at least two ordinary,
non-damning explanations must be generated and considered. Not one. Two.

The reason is a specific and well-documented failure of automated integrity
checks: the first explanation a system reaches for is the one that confirms the
suspicion, and stopping there turns every coincidence into a finding. A resume
saying "2021-2023" and an answer saying "about eighteen months" is a
contradiction. It is also: rounding; a probation period counted differently; a
contract-to-permanent conversion; a company that dates from the offer and a
person who dates from the start; a candidate recalling a two-year-old job
imprecisely. Requiring TWO benign explanations forces the second one, and the
second one is where the honest answer usually is.

`REQUIRES_BENIGN_EXPLANATIONS = 2` and `escalate` REFUSES to raise severity
without them. It does not warn, it does not log -- the escalation simply does
not happen, and the contradiction stays MINOR with its reason recorded. A rule
that could be skipped by forgetting to call something is not a rule.

WHAT A FLAG MAY AND MAY NOT DO
--------------------------------
It may lower confidence, it may oblige a follow-up, and it may set
`needs_human_review`. It may NEVER reject a candidate. spec-doc5 states this as
"a hard constraint, not a tuning knob", and this module has no code path to a
rejection: `TriangulationResult` has no `reject` field and nothing here writes a
pipeline status. The enforcement is the absence of the capability, which is the
same technique `tools/permissions.AGENT_TOOLS` uses -- reach an agent does not
have is reach a future prompt cannot start using.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services.evidence import contradictions as detector

__all__ = [
    "REQUIRES_BENIGN_EXPLANATIONS",
    "BenignExplanation",
    "TriangulatedContradiction",
    "TriangulationResult",
    "independence_groups",
    "count_independence",
    "escalate",
    "triangulate",
    "STANDARD_BENIGN_EXPLANATIONS",
    "RUNBOOK_BENIGN_EXPLANATIONS",
    "OUR_OWN_DATA_EXPLANATIONS",
]

#: At least two, always. See the module docstring for why the number is two and
#: not one.
REQUIRES_BENIGN_EXPLANATIONS = 2


# ── Independence ─────────────────────────────────────────────────────────────


def independence_groups(sources: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Group evidence by what would make two pieces genuinely independent.

    THE GROUPING RULE IS THE WHOLE THING. Two pieces of evidence corroborate
    each other only if they could have disagreed, and a resume line plus the
    candidate restating it in the interview could not: that is one person saying
    one thing twice. So the group key is the ORIGINATOR, not the document.

      candidate-originated   the resume, every assessment answer, the
                             validation form -- ONE group, because the candidate
                             wrote all of it
      employer-originated    a verification reply, an offer letter
      third-party            a reference, a public record
      artefact               something the candidate produced that can be
                             inspected independently of their account of it

    A system that counted documents instead would read a confidently written
    resume plus a confident interview as two agreeing sources, which is exactly
    how a fabricated account passes a consistency check.
    """
    groups: dict[str, list[str]] = {}
    for source in sources:
        ref = str(source.get("ref") or source.get("id") or "")
        if not ref:
            continue
        groups.setdefault(str(source.get("independence_group") or "candidate"), []).append(ref)
    return groups


def count_independence(sources: Iterable[Mapping[str, Any]]) -> int:
    return len(independence_groups(sources))


# ── Benign explanations ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BenignExplanation:
    """An ordinary reason two sources could differ without anyone lying."""

    text: str
    #: Whether anything in the evidence supports this explanation specifically.
    #: A supported benign explanation should usually SETTLE the contradiction
    #: rather than merely be considered; an unsupported one is a hypothesis that
    #: was entertained, which is still the point.
    supported: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "supported": self.supported}


#: Per-axis stock explanations, used as the DETERMINISTIC floor.
#:
#: The model generates explanations too (that is the reasoning half of this stage),
#: but the requirement cannot depend on it: a provider outage would otherwise
#: leave every contradiction unable to reach two explanations and therefore
#: permanently un-escalatable, which sounds safe and is not -- it would mean an
#: outage silently disables integrity escalation. These stock explanations are
#: always available, so the rule holds when the provider does not.
#:
#: PROVENANCE: RPN-PHIL-001 §13.2 STEP 3 and §57.4. §57.4 requires "at least two
#: benign explanations per contradiction before assigning severity above Minor",
#: which this already implemented. What it did not have was §13.2's own list,
#: and the Runbook enumerates seven by name:
#:
#:     Company renamed. Team restructured. Title differs from function.
#:     Contract-to-permanent conversion. Confidentiality restriction.
#:     NDA on the artefact. Regional title conventions.
#:
#: Not one of the seven appeared in the pre-Runbook set, which was written from
#: the ordinary ways each axis diverges and reached for imprecision, recall and
#: form wording. Those are real, and they are a different class: the Runbook's
#: seven are all EMPLOYMENT-RECORD explanations, and employment-record
#: divergence is where the expensive contradictions actually live. A resume
#: saying "Acme" where a reference says "Acme Systems India" is a company
#: rename, not a candidate misrepresenting an employer, and a system whose
#: benign-explanation list could not produce that reading would work the
#: contradiction and reach the wrong disposition with the protocol satisfied.
#:
#: `RUNBOOK_BENIGN_EXPLANATIONS` therefore applies to EVERY axis, and the
#: axis-specific lists extend it rather than replace it. Two explanations is a
#: floor, not a quota, and an axis that can offer nine honest readings should.
RUNBOOK_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    "The company was renamed, or was acquired and renamed.",
    "The team was restructured, so the same work sat under a different name.",
    "The title differs from the function the person actually performed.",
    "A contract engagement was converted to a permanent one, which moves a "
    "start date without moving the work.",
    "A confidentiality restriction limited what could be said about it.",
    "An NDA covers the artefact, so it cannot be shown or described in full.",
    "Regional title conventions differ, and the same seniority is named "
    "differently in different markets.",
)

#: RPN-PHIL-001 §13.2 STEP 2, which runs BEFORE the benign-explanation search.
#:
#: "Parsing errors, date-format errors, name collisions, and translation
#: artefacts cause a large share of apparent contradictions. Rule out our error
#: before attributing to the candidate."
#:
#: Carried separately because it is a different claim about a different party:
#: these say the contradiction may be OURS, and the seven above say it may be
#: nobody's. A protocol that only searched for innocent explanations on the
#: candidate's side would attribute our own parsing failure to them.
OUR_OWN_DATA_EXPLANATIONS: tuple[str, ...] = (
    "Our parser may have read one of the two sources incorrectly.",
    "A date-format difference between the two sources, read one way here and "
    "the other way there.",
    "A name collision: two people, or two systems, with the same name.",
    "A translation artefact between the language a source was written in and "
    "the language it was read in.",
)

STANDARD_BENIGN_EXPLANATIONS: dict[str, tuple[str, ...]] = {
    detector.AXIS_RESUME_VS_VALIDATION: (
        "The resume was written at a different time and has not been updated.",
        "The two forms ask the question slightly differently, so the same "
        "situation is described two ways.",
        "A rounding or a date convention: offer date versus start date, notice "
        "period included or not.",
    ),
    detector.AXIS_RESUME_VS_ANSWERS: (
        "The candidate is recalling something from years ago and is imprecise "
        "rather than inaccurate.",
        "The resume compresses a role into a title; the answer describes what "
        "the work actually was.",
        "The candidate is understating, which is common and is the opposite of "
        "the failure mode being looked for.",
    ),
    detector.AXIS_ANSWERS_ACROSS_TURNS: (
        "The two questions were about different periods or different projects.",
        "The candidate added detail on the second telling rather than "
        "contradicting the first.",
        "The first answer was abbreviated because the question was broad.",
    ),
    detector.AXIS_CONCLUSIONS_VS_EVIDENCE: (
        "The evidence supports the conclusion but was not cited alongside it.",
        "The conclusion draws on several pieces jointly, none of which carries "
        "it alone.",
    ),
    detector.AXIS_JD_VS_SWOT: (
        "The JD was drafted before the SWOT session and the session refined it.",
        "The hiring manager and the recruiter are describing the same need in "
        "different vocabulary.",
    ),
    detector.AXIS_DRAFT_VS_STATE: (
        "The draft was generated from an earlier snapshot of the state.",
        "The phrasing differs while the substance does not.",
    ),
}


def standard_explanations(axis: str) -> tuple[BenignExplanation, ...]:
    """Every stock benign explanation available for an axis.

    §13.2's own seven come FIRST and are offered on every axis, because they are
    properties of employment records rather than of any particular pair of
    sources: a company rename can put a resume at odds with a reference, with a
    validation field, or with the candidate's own answer two turns apart.

    §13.2 STEP 2's four "check our own data" readings are included too, and
    they are ordered ahead of the candidate-side ones for the reason the Runbook
    orders its steps that way: rule out our error before attributing to the
    candidate. An axis with no list of its own is not an axis where escalation
    is free; it inherits eleven.
    """
    axis_specific = STANDARD_BENIGN_EXPLANATIONS.get(axis, ())
    texts = OUR_OWN_DATA_EXPLANATIONS + RUNBOOK_BENIGN_EXPLANATIONS + axis_specific
    return tuple(BenignExplanation(text=t) for t in texts)


# ── Escalation ───────────────────────────────────────────────────────────────


@dataclass
class TriangulatedContradiction:
    """A detected contradiction, plus everything the benign rule requires."""

    base: detector.Contradiction
    #: The severity the detector proposed, before the benign-explanation rule.
    proposed_severity: str
    #: The severity actually applied.
    severity: str
    explanations: tuple[BenignExplanation, ...] = ()
    #: Set when the rule HELD DOWN an escalation. Recorded, because a severity
    #: that was silently capped is indistinguishable from one that was never
    #: proposed, and the difference is exactly what a reviewer needs.
    escalation_withheld: bool = False
    withheld_reason: str = ""
    #: How many independent source groups the contradiction spans. A
    #: contradiction inside ONE group is a person disagreeing with themselves,
    #: which is far weaker evidence of a problem than two independent sources
    #: disagreeing -- so it is recorded and it caps severity.
    independence: int = 1

    @property
    def settled_benignly(self) -> bool:
        """True when a benign explanation is actually SUPPORTED by evidence.

        The distinction from merely having been considered: an explanation with
        evidence behind it is a resolution, and one without is due diligence.
        """
        return any(e.supported for e in self.explanations)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.base.as_dict(),
            "severity": self.severity,
            "proposed_severity": self.proposed_severity,
            "independence": self.independence,
            "escalation_withheld": self.escalation_withheld,
            "withheld_reason": self.withheld_reason,
            "settled_benignly": self.settled_benignly,
            "benign_explanations": [e.as_dict() for e in self.explanations],
        }


def escalate(
    contradiction: detector.Contradiction,
    *,
    explanations: Sequence[BenignExplanation],
    independence: int = 1,
) -> TriangulatedContradiction:
    """Apply the benign-explanation rule to one contradiction.

    Three ways severity is held down, in order:

      1. FEWER THAN TWO BENIGN EXPLANATIONS. Capped at MINOR. Not a warning --
         the escalation does not happen. This is the rule spec-doc5 states.
      2. A SUPPORTED BENIGN EXPLANATION EXISTS. Capped at MINOR, because the
         disagreement has an ordinary explanation with evidence behind it, and
         escalating anyway would be ignoring the answer we went looking for.
      3. ONLY ONE INDEPENDENT SOURCE GROUP. Capped at MATERIAL rather than
         CRITICAL. A candidate being imprecise about their own history twice is
         a real signal and a weaker one than an independent source disagreeing
         with them.

    Severity is never RAISED here, only held. The detector owns the proposal.
    """
    proposed = contradiction.severity
    severity = proposed
    withheld = False
    reason = ""

    if detector.at_least(proposed, detector.MATERIAL):
        if len(explanations) < REQUIRES_BENIGN_EXPLANATIONS:
            severity = detector.MINOR
            withheld = True
            reason = (
                f"Held at minor: {len(explanations)} benign explanation(s) were "
                f"generated and at least {REQUIRES_BENIGN_EXPLANATIONS} are "
                f"required before any escalation above minor."
            )
        elif any(e.supported for e in explanations):
            severity = detector.MINOR
            withheld = True
            reason = (
                "Held at minor: an ordinary explanation for the difference is "
                "supported by the evidence."
            )
        elif independence < 2 and proposed == detector.CRITICAL:
            severity = detector.MATERIAL
            withheld = True
            reason = (
                "Held at material: the disagreement is within a single source "
                "group, so it is one account being imprecise rather than two "
                "sources conflicting."
            )

    if severity != proposed:
        # Rebuild the base contradiction at the applied severity so its ACTIONS
        # match. A contradiction carrying MINOR severity and CRITICAL's actions
        # would send a human-review obligation the severity does not justify.
        contradiction = detector.Contradiction(
            axis=contradiction.axis,
            severity=severity,
            location=contradiction.location,
            detail=contradiction.detail,
            recommendation=contradiction.recommendation,
            actions=detector.actions_for(severity, phase=detector.PHASE_POST_CONVERSATION),
        )

    return TriangulatedContradiction(
        base=contradiction,
        proposed_severity=proposed,
        severity=severity,
        explanations=tuple(explanations),
        escalation_withheld=withheld,
        withheld_reason=reason,
        independence=independence,
    )


@dataclass
class TriangulationResult:
    """Everything stage 5 concluded.

    NOTE WHAT IS ABSENT: there is no `reject`, no `disqualify`, no status. A
    flag may lower confidence, oblige a follow-up and require a human. It may
    never end a candidacy, and the enforcement is that there is nothing here to
    end one with.
    """

    contradictions: list[TriangulatedContradiction] = field(default_factory=list)
    independence: int = 0
    #: Contradictions at MATERIAL or above with no supported benign explanation.
    #: This is what `aggregation.confidence_for` counts.
    unresolved: int = 0
    #: Human-readable reasons, passed to `aggregate(integrity_flags=...)`.
    integrity_flags: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return detector.escalate(*(c.severity for c in self.contradictions))

    @property
    def needs_human_review(self) -> bool:
        return any(
            detector.ACTION_HUMAN_REVIEW in c.base.actions for c in self.contradictions
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "independence": self.independence,
            "unresolved": self.unresolved,
            "needs_human_review": self.needs_human_review,
            "integrity_flags": list(self.integrity_flags),
            "contradictions": [c.as_dict() for c in self.contradictions],
        }

    def client_projection(self) -> dict[str, Any]:
        """Nothing. Deliberately.

        A contradiction is INTERNAL. A client sees the grade and, where a
        recruiter needs it, the fact that the report is held for review. They
        never see "we think this candidate may have overstated something", which
        is an accusation the platform is in no position to make and which routes
        to a person precisely so a person can decide.
        """
        return {}


def triangulate(
    report: detector.ContradictionReport,
    *,
    sources: Sequence[Mapping[str, Any]] = (),
    generated: Mapping[str, Sequence[BenignExplanation]] | None = None,
) -> TriangulationResult:
    """Apply independence counting and the benign rule to a whole report.

    `generated` is the model's per-axis explanations from the reasoning half of
    this stage. It is MERGED with the deterministic stock list rather than
    replacing it, so the two-explanation floor holds during a provider outage --
    an outage that silently disabled integrity escalation would be the worst
    possible failure mode for this stage, because it looks like a clean run.
    """
    result = TriangulationResult()
    result.independence = count_independence(sources) or 1
    supplied = dict(generated or {})

    for contradiction in report.contradictions:
        explanations = list(supplied.get(contradiction.axis, ()))
        for stock in standard_explanations(contradiction.axis):
            if len(explanations) >= REQUIRES_BENIGN_EXPLANATIONS:
                break
            if not any(e.text == stock.text for e in explanations):
                explanations.append(stock)
        triangulated = escalate(
            contradiction,
            explanations=explanations,
            independence=result.independence,
        )
        result.contradictions.append(triangulated)
        if (
            detector.at_least(triangulated.severity, detector.MATERIAL)
            and not triangulated.settled_benignly
        ):
            result.unresolved += 1
            result.integrity_flags.append(
                f"{triangulated.severity} contradiction on {triangulated.base.axis}: "
                f"{triangulated.base.recommendation}"
            )
    return result
