"""Contradiction detection with severity, and the action a severity obliges.

WHY THIS IS NOT MORE OF `verification/contradiction.py`
-------------------------------------------------------
That module answers one question for the loop: is this OUTPUT sound enough to
ship, expressed as a `Verdict` whose confidence is arithmetic over finding
severity. This module answers a different one: two SOURCES disagree, how much
does it matter, and what must happen next. Four of the six pairs it watches are
not about a candidate's output at all -- a JD against a hiring manager's SWOT,
a scoring conclusion against the evidence under it, a report draft against the
state it was drafted from.

Folding those into `verify_consistency` would have given one function two
return contracts and two severity scales, and the second one would have been
discovered by whoever first read a `Verdict.confidence` that had quietly started
counting a JD/SWOT mismatch. So the existing detector is REUSED, not copied:
`verify_consistency` still owns the resume/validation and resume/answers checks
exactly as it always has, with its thresholds and its posture unchanged, and
this module lifts its findings onto the contradiction scale.

TWO SEVERITY SCALES, ON PURPOSE, AND THEY ARE DIFFERENT AXES
--------------------------------------------------------------
`verification.base` grades a FINDING: high / medium / low, costing confidence
against a floor, answering "regenerate or ship". Spec 14 grades a
CONTRADICTION: NONE / MINOR / MATERIAL / CRITICAL, answering "how much more
work is owed before anything may be concluded". They are mapped in one place
(`_FROM_FINDING`) and nowhere else, so a reader tracing a severity never has to
wonder which scale a value came from.

THE RULE WITH THE TEETH
------------------------
MATERIAL or CRITICAL must trigger additional retrieval or re-evaluation, and
must never be averaged away. A comment saying so is a comment somebody edits
around, so it is a RETURNED VALUE: every contradiction carries the actions its
severity obliges, and `ContradictionReport.settle()` -- the only function here
that hands back a single concluded answer -- RAISES while any of them stands.
The path that would silently average is the path that has to say out loud that
it is doing so.

NOTHING HERE CALLS A MODEL
---------------------------
Same reason `answer_classification` settles empty and gibberish
deterministically: the moment a guard matters most is the moment the provider is
already failing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.services.evidence import ledger
from app.services.verification import base, contradiction as cross_source

# ── Severity (spec 14) ───────────────────────────────────────────────────────
NONE = "none"
MINOR = "minor"
MATERIAL = "material"
CRITICAL = "critical"

#: Ordinals for COMPARISON. Never summed: three minor disagreements are not one
#: material one, and a detector that added them up would escalate on the noise
#: it was tuned to tolerate.
_SEVERITY_RANK: dict[str, int] = {NONE: 0, MINOR: 1, MATERIAL: 2, CRITICAL: 3}

SEVERITIES: tuple[str, ...] = (NONE, MINOR, MATERIAL, CRITICAL)

#: The one mapping between the two scales. A `Finding` is about an output; a
#: `Contradiction` is about two sources. High means the thing a client reads is
#: wrong, which is the definition of CRITICAL here.
_FROM_FINDING: dict[str, str] = {
    base.SEVERITY_HIGH: CRITICAL,
    base.SEVERITY_MEDIUM: MATERIAL,
    base.SEVERITY_LOW: MINOR,
}

# ── Actions a severity obliges ───────────────────────────────────────────────
#: Recorded, and nothing else is owed. MINOR lives here, which is the whole
#: reason MINOR exists: a detector that demanded work for every rounding
#: difference would be switched off inside a week.
ACTION_RECORD = "record"
#: Go back to the corpus. Retrieve more around the disputed dimension and score
#: it again. This is the "never silent averaging" half of spec 14.
ACTION_RETRIEVE = "retrieve_and_reevaluate"
#: Ask the candidate, while there is still a conversation to ask in (spec 32).
ACTION_FOLLOW_UP = "ask_follow_up"
#: The conversation is over. The disagreement is carried forward as
#: uncertainty rather than collapsed into whichever side scored higher.
ACTION_PRESERVE_UNCERTAINTY = "preserve_uncertainty"
#: A human decides. Reserved for CRITICAL, matching the standing rule that a
#: sensitive action requires a person at ANY confidence.
ACTION_HUMAN_REVIEW = "human_review"

# ── Phases (spec 32) ─────────────────────────────────────────────────────────
PHASE_CONVERSATIONAL = "conversational"
PHASE_POST_CONVERSATION = "post_conversation"

# ── The pairs of sources this watches (spec 14) ──────────────────────────────
AXIS_JD_VS_SWOT = "jd_vs_swot"
AXIS_RESUME_VS_VALIDATION = "resume_vs_validation"
AXIS_RESUME_VS_ANSWERS = "resume_vs_answers"
AXIS_ANSWERS_ACROSS_TURNS = "answers_across_turns"
AXIS_CONCLUSIONS_VS_EVIDENCE = "conclusions_vs_evidence"
AXIS_DRAFT_VS_STATE = "draft_vs_state"

#: Which `verify_consistency` issue belongs to which pair. Its findings cover
#: two of the six axes and it does not label them, so the mapping lives here
#: rather than becoming a new field on `Finding` that only this caller reads.
_ISSUE_AXIS: dict[str, str] = {
    "experience_conflict": AXIS_RESUME_VS_VALIDATION,
    "claimed_but_unevidenced": AXIS_RESUME_VS_ANSWERS,
    "claimed_beyond_resume": AXIS_RESUME_VS_ANSWERS,
}


class UnresolvedContradiction(RuntimeError):
    """Raised when a caller asks for a settled answer that is not owed to it.

    The failure this prevents is the one spec 14 names: a MATERIAL disagreement
    quietly turning into the mean of the two sides, shipping as a grade, and
    leaving nothing anywhere that says two sources disagreed.
    """


def escalate(*severities: str) -> str:
    """The highest severity present. The only combination this module performs."""
    return max((*severities, NONE), key=lambda value: _SEVERITY_RANK.get(value, 0))


def at_least(severity: str, floor: str) -> bool:
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(floor, 0)


def actions_for(severity: str, *, phase: str) -> tuple[str, ...]:
    """What a severity obliges, given where in the assessment we are.

    The phase branch is spec 32 and it is not a preference. Inside the
    conversation the cheapest and most informative move is to ask; once the
    candidate has gone, asking is not available and the honest alternative is to
    carry the uncertainty into the report rather than to pick a side on their
    behalf. Both branches still re-retrieve, because a disagreement is also
    evidence that the wrong context was assembled.
    """
    if not at_least(severity, MINOR):
        return ()
    if severity == MINOR:
        return (ACTION_RECORD,)

    actions = [ACTION_RECORD, ACTION_RETRIEVE]
    actions.append(
        ACTION_FOLLOW_UP
        if phase == PHASE_CONVERSATIONAL
        else ACTION_PRESERVE_UNCERTAINTY
    )
    if severity == CRITICAL:
        actions.append(ACTION_HUMAN_REVIEW)
    return tuple(actions)


@dataclass(frozen=True)
class Contradiction:
    """Two sources disagreeing, and what is owed because of it."""

    axis: str
    severity: str
    location: str
    #: What disagrees, in terms a recruiter can act on. Never a quoted answer:
    #: this travels into traces and operator views, and the ledger's rule about
    #: excerpts applies to anything that reads like one.
    detail: str
    #: Phrased as the thing to DO, matching `verification.base.Finding`, because
    #: a rejection fed back to a loop verbatim has to be actionable.
    recommendation: str
    actions: tuple[str, ...] = ()

    @property
    def requires_reevaluation(self) -> bool:
        return ACTION_RETRIEVE in self.actions

    def as_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "severity": self.severity,
            "location": self.location,
            "detail": self.detail,
            "recommendation": self.recommendation,
            "actions": list(self.actions),
        }

    def as_finding(self) -> base.Finding:
        """Back onto the verifier's scale, for a caller inside `agent_loop`.

        Deliberately lossy in one direction only: MINOR and NONE both become a
        low finding, because the loop's question is whether to regenerate and
        neither answer is yes.
        """
        severity = {
            CRITICAL: base.SEVERITY_HIGH,
            MATERIAL: base.SEVERITY_MEDIUM,
        }.get(self.severity, base.SEVERITY_LOW)
        return base.Finding(
            severity, self.axis, self.location, self.detail, self.recommendation
        )


@dataclass(frozen=True)
class ContradictionReport:
    """Everything found in one pass, and the work it obliges."""

    contradictions: tuple[Contradiction, ...] = ()
    phase: str = PHASE_POST_CONVERSATION

    @property
    def severity(self) -> str:
        return escalate(*(item.severity for item in self.contradictions))

    @property
    def actions(self) -> tuple[str, ...]:
        """The union, in a stable order, so a caller can act on one list."""
        order = (
            ACTION_RECORD,
            ACTION_RETRIEVE,
            ACTION_FOLLOW_UP,
            ACTION_PRESERVE_UNCERTAINTY,
            ACTION_HUMAN_REVIEW,
        )
        present = {action for item in self.contradictions for action in item.actions}
        return tuple(action for action in order if action in present)

    @property
    def requires_reevaluation(self) -> bool:
        return ACTION_RETRIEVE in self.actions

    @property
    def needs_human_review(self) -> bool:
        return ACTION_HUMAN_REVIEW in self.actions

    def by_axis(self, axis: str) -> tuple[Contradiction, ...]:
        return tuple(item for item in self.contradictions if item.axis == axis)

    def settle(self, value: Any) -> Any:
        """Hand back a concluded answer, or refuse to.

        This is where the rule has teeth. A caller holding two disagreeing
        numbers and wanting one is exactly the caller spec 14 is written
        against, so the call that produces the one number is the call that
        raises while a MATERIAL or CRITICAL contradiction is outstanding. There
        is no `force` argument: a caller that genuinely must proceed records the
        uncertainty and reads `value` itself, which is a visible line in a diff.
        """
        if at_least(self.severity, MATERIAL):
            raise UnresolvedContradiction(
                f"{self.severity} contradiction outstanding on "
                f"{sorted({item.axis for item in self.contradictions})}; "
                f"owed: {list(self.actions)}"
            )
        return value

    def to_verdict(self) -> base.Verdict:
        """For a caller running inside `agent_loop`.

        Reuses the existing severity arithmetic rather than inventing a second
        confidence: the loop already knows what to do with a `Verdict`, and a
        parallel accept/reject rule would be a second thing to keep in step.
        """
        return base.verdict(
            "contradiction_severity",
            [item.as_finding() for item in self.contradictions],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "phase": self.phase,
            "actions": list(self.actions),
            "contradictions": [item.as_dict() for item in self.contradictions],
        }


def _terms(values: Sequence[Any]) -> set[str]:
    return {
        " ".join(str(value).casefold().split())
        for value in values or ()
        if str(value or "").strip()
    }


# ── The six axes ─────────────────────────────────────────────────────────────


def _from_cross_source(verdict: base.Verdict, *, phase: str) -> list[Contradiction]:
    """Lift `verify_consistency`'s findings onto the contradiction scale.

    Its thresholds, its wording and its posture are untouched. Only the scale
    changes, and only here.
    """
    lifted: list[Contradiction] = []
    for finding in verdict.findings:
        severity = _FROM_FINDING.get(finding.severity, MATERIAL)
        lifted.append(
            Contradiction(
                axis=_ISSUE_AXIS.get(finding.issue, AXIS_RESUME_VS_ANSWERS),
                severity=severity,
                location=finding.location,
                detail=finding.detail,
                recommendation=finding.recommendation,
                actions=actions_for(severity, phase=phase),
            )
        )
    return lifted


def _jd_versus_swot(
    *,
    jd_requirements: Sequence[str],
    swot_requirements: Sequence[str],
    swot_dismissed: Sequence[str],
    phase: str,
) -> list[Contradiction]:
    """Bodha's reading of the role against the JD it was read from.

    A hiring manager naming something the JD does not is NORMAL and is most of
    the value of the intake, so it is MINOR at worst. A hiring manager dismissing
    something the JD states as a requirement is MATERIAL: the matrix Sutra
    generates is downstream of both, and building it while the two disagree
    produces criteria every candidate on the job is then graded against.
    """
    found: list[Contradiction] = []
    jd = _terms(jd_requirements)
    dismissed = _terms(swot_dismissed) & jd
    for term in sorted(dismissed):
        found.append(
            Contradiction(
                axis=AXIS_JD_VS_SWOT,
                severity=MATERIAL,
                location=f"requirements.{term}",
                detail=(
                    f"the job description requires {term} and the reporting "
                    "authority's intake set it aside"
                ),
                recommendation=(
                    f"confirm with the hiring manager whether {term} is a "
                    "requirement before the matrix is locked; do not drop it "
                    "and do not keep it on one source's word"
                ),
                actions=actions_for(MATERIAL, phase=phase),
            )
        )

    added = _terms(swot_requirements) - jd
    for term in sorted(added):
        found.append(
            Contradiction(
                axis=AXIS_JD_VS_SWOT,
                severity=MINOR,
                location=f"requirements.{term}",
                detail=(
                    f"{term} came from the intake and does not appear in the "
                    "job description"
                ),
                recommendation=(
                    f"treat {term} as context the hiring manager added, not as "
                    "a discrepancy"
                ),
                actions=actions_for(MINOR, phase=phase),
            )
        )
    return found


def _answers_across_turns(
    *, turn_claims: Sequence[Mapping[str, Any]], phase: str
) -> list[Contradiction]:
    """The candidate affirming something in one turn and denying it in another.

    `turn_claims` are already-classified stances, not raw text: deciding whether
    a paragraph affirms or denies is scoring work and belongs to Miti, while
    noticing that two stances cannot both hold is arithmetic and belongs here.

    MATERIAL rather than CRITICAL. Someone correcting themselves under a
    follow-up is a normal and honest thing to do, and a detector that treated it
    as a disqualifying finding would punish precisely the candidates who answer
    carefully. What it must not do is average the two into a grade.
    """
    affirmed: dict[str, set[str]] = {}
    denied: dict[str, set[str]] = {}
    for turn in turn_claims or ():
        key = str(turn.get("question_key") or "")
        affirmed.setdefault(key, set()).update(_terms(turn.get("affirmed") or ()))
        denied.setdefault(key, set()).update(_terms(turn.get("denied") or ()))

    found: list[Contradiction] = []
    for key in sorted(affirmed):
        for term in sorted(affirmed[key] & denied.get(key, set())):
            found.append(
                Contradiction(
                    axis=AXIS_ANSWERS_ACROSS_TURNS,
                    severity=MATERIAL,
                    location=f"{key}.{term}",
                    detail=(
                        f"the candidate both affirmed and denied {term} while "
                        f"answering under {key}"
                    ),
                    recommendation=(
                        f"establish which answer about {term} stands before "
                        "scoring it; do not score the two answers together"
                    ),
                    actions=actions_for(MATERIAL, phase=phase),
                )
            )
    return found


def _conclusions_versus_evidence(
    *, claims: Sequence[ledger.Claim], phase: str
) -> list[Contradiction]:
    """Miti's conclusions against what the ledger actually holds.

    Three shapes, and the severities are the reason the ledger distinguishes
    them at all:

      * a conclusion standing on NOTHING is CRITICAL. That is a grade with no
        evidence under it, and it is what a degraded scoring pass produces.
      * a conclusion standing only on `inferred` evidence is MATERIAL. The
        product agreeing with itself is the failure the trust lattice exists to
        name, and it is invisible without one.
      * a conclusion whose claim is already contradicted is MATERIAL, carried
        forward, never collapsed.
    """
    found: list[Contradiction] = []
    for claim in claims or ():
        state = claim.status
        location = f"{claim.dimension}.{claim.subject}"
        if state == ledger.CLAIM_UNSUPPORTED:
            found.append(
                Contradiction(
                    axis=AXIS_CONCLUSIONS_VS_EVIDENCE,
                    severity=CRITICAL,
                    location=location,
                    detail=(
                        "a conclusion was reached on this dimension with no "
                        "live evidence recorded for it"
                    ),
                    recommendation=(
                        "retrieve evidence for this dimension and score it "
                        "again; do not report a conclusion nothing stands behind"
                    ),
                    actions=actions_for(CRITICAL, phase=phase),
                )
            )
        elif state == ledger.CLAIM_INFERRED_ONLY:
            found.append(
                Contradiction(
                    axis=AXIS_CONCLUSIONS_VS_EVIDENCE,
                    severity=MATERIAL,
                    location=location,
                    detail=(
                        "everything supporting this dimension was inferred "
                        "rather than stated or confirmed"
                    ),
                    recommendation=(
                        "seek evidence the candidate actually gave for this "
                        "dimension before treating it as established"
                    ),
                    actions=actions_for(MATERIAL, phase=phase),
                )
            )
        elif state == ledger.CLAIM_CONTRADICTED:
            found.append(
                Contradiction(
                    axis=AXIS_CONCLUSIONS_VS_EVIDENCE,
                    severity=MATERIAL,
                    location=location,
                    detail=(
                        "live evidence stands on both sides of this dimension"
                    ),
                    recommendation=(
                        "surface both sides to the recruiter; do not choose "
                        "one and do not blend them into a single reading"
                    ),
                    actions=actions_for(MATERIAL, phase=phase),
                )
            )
    return found


def _draft_versus_state(
    *,
    draft_dimensions: Mapping[str, str],
    scored_dimensions: Mapping[str, str],
    phase: str,
) -> list[Contradiction]:
    """Siddhi's draft against the state Miti actually produced.

    CRITICAL in both directions, and this is the one axis where that is not an
    over-reaction: the draft is the artifact a client reads, and a grade in it
    that scoring did not produce is a number-free sentence about a real person
    that nothing in the system supports. A dimension present in the draft and
    absent from scoring is the same defect wearing a different shape.
    """
    found: list[Contradiction] = []
    for dimension, drafted in (draft_dimensions or {}).items():
        scored = (scored_dimensions or {}).get(dimension)
        if scored is None:
            found.append(
                Contradiction(
                    axis=AXIS_DRAFT_VS_STATE,
                    severity=CRITICAL,
                    location=f"report.{dimension}",
                    detail=(
                        "the draft grades a dimension that scoring produced no "
                        "state for"
                    ),
                    recommendation=(
                        "remove the dimension from the draft or score it; a "
                        "report may not state a grade scoring did not reach"
                    ),
                    actions=actions_for(CRITICAL, phase=phase),
                )
            )
        elif str(scored) != str(drafted):
            found.append(
                Contradiction(
                    axis=AXIS_DRAFT_VS_STATE,
                    severity=CRITICAL,
                    location=f"report.{dimension}",
                    detail="the draft and the scoring state grade this dimension differently",
                    recommendation=(
                        "regenerate the draft from the scoring state; never "
                        "reconcile the two by choosing the kinder grade"
                    ),
                    actions=actions_for(CRITICAL, phase=phase),
                )
            )
    return found


def detect(
    *,
    phase: str = PHASE_POST_CONVERSATION,
    # resume vs validation, resume vs answers. Passed straight through to the
    # existing detector so its tolerances stay the only place they are written.
    resume_skills: Sequence[str] = (),
    resume_experience_years: Any = None,
    validation_experience_years: Any = None,
    claimed_skills: Sequence[str] = (),
    unanswered_skills: Sequence[str] = (),
    # jd vs swot
    jd_requirements: Sequence[str] = (),
    swot_requirements: Sequence[str] = (),
    swot_dismissed: Sequence[str] = (),
    # answers across turns
    turn_claims: Sequence[Mapping[str, Any]] = (),
    # conclusions vs evidence
    claims: Sequence[ledger.Claim] = (),
    # draft vs state
    draft_dimensions: Mapping[str, str] | None = None,
    scored_dimensions: Mapping[str, str] | None = None,
) -> ContradictionReport:
    """Every axis in spec 14, in one pass, with the work each one obliges.

    Every input is optional because the axes fire at different moments: JD
    against SWOT during job setup, answers against each other mid-conversation,
    draft against state at synthesis. A caller supplies what it holds, and an
    axis with no inputs contributes nothing rather than a false clean bill.
    """
    found: list[Contradiction] = []

    found.extend(
        _from_cross_source(
            cross_source.verify_consistency(
                resume_skills=resume_skills,
                resume_experience_years=resume_experience_years,
                validation_experience_years=validation_experience_years,
                claimed_skills=claimed_skills,
                unanswered_skills=unanswered_skills,
            ),
            phase=phase,
        )
    )
    found.extend(
        _jd_versus_swot(
            jd_requirements=jd_requirements,
            swot_requirements=swot_requirements,
            swot_dismissed=swot_dismissed,
            phase=phase,
        )
    )
    found.extend(_answers_across_turns(turn_claims=turn_claims, phase=phase))
    found.extend(_conclusions_versus_evidence(claims=claims, phase=phase))
    found.extend(
        _draft_versus_state(
            draft_dimensions=draft_dimensions or {},
            scored_dimensions=scored_dimensions or {},
            phase=phase,
        )
    )

    return ContradictionReport(tuple(found), phase=phase)
