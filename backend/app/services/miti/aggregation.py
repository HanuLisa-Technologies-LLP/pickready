"""Stage 6: AGGREGATION. Deterministic arithmetic, zero model involvement.

    competency -> dimension -> weighted composite -> authenticity multiplier
               -> confidence -> the product-facing grade band

spec-doc5 §B.3 assigns this component "**No model.** Deterministic code only",
and gives the reason: Runbook §57.5 requires it be reproducible and testable.

THERE IS NO IMPORT OF `llm_router` IN THIS FILE AND THERE MUST NEVER BE ONE.
`test_miti_pipeline.py` reads this module's source and asserts it, rather than
trusting a comment. That is the same technique `test_question_count_range.py`
and `test_vaada_miti_loop.py` already use for the same reason: a rule that lives
only in a docstring is a rule the next person adding "just one quick call to
clean up the phrasing" will not see.

WHY DETERMINISM HERE SPECIFICALLY
-----------------------------------
Every stage before this one has a model in it, so any single answer could come
out slightly differently on a rerun. That is tolerable for extraction and even
for a dimension band, because both are checkable against the evidence they
cite. It is NOT tolerable for the step that turns five bands into the grade a
client reads and a hiring decision rests on. If aggregation sampled, then two
runs over identical inputs could produce different grades, and there would be no
way to tell a rubric problem from noise -- which is precisely the argument
`TASK_TEMPERATURE` already makes for scoring calls, applied to the one step that
cannot be re-asked.

THE THREE BAND CAPS ARE APPLIED HERE
--------------------------------------
The Runbook states THREE controls that cap a band and this module implemented
one of them until 2026-08-29:

    section 12.1  a named Must-have fails its minimum score
    section 12.2  a dimension breaches its Layer 1 floor (D1 45, D4 45, D3 40)
    section 14.1  a Must-have has no evidence above E1

`caps.py` carries all three, with the ceiling read from
`runbook_data/bands.yaml` rather than typed, and the delivered band is the
MINIMUM of whichever ceilings fired (RUNBOOK_OPEN_QUESTIONS.md Q7, option C).
Section 14.1 is the one that closes the hole: section 10.2 puts evidence
strength in both the numerator and the denominator of a competency score, so a
single-claim competency scores exactly its rubric level at every tier from E0
to E5, and a fabricated Must-have on one resume bullet grades high and trips no
score-based control at all.

The caps are applied at the END, on the score, as a `min`. See `caps.py` for
why cap-last is the only ordering that holds a ceiling absolutely, and why a
`min` rather than an assignment is what stops the cap promoting the weakest
candidates into the band it exists to keep the strong ones out of.

INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE
-------------------------------------------------
spec-doc5 §A.3, quoted: "Missing evidence should reduce confidence, not silently
reduce score ... they are not the same thing and conflating them is a fairness
failure." So a dimension flagged `insufficient_evidence` is EXCLUDED from the
composite rather than scored low, and its absence is paid for in confidence. The
practical consequence matters: a career-changer with a thin track record ends up
with a lower-confidence report that goes to a human, rather than a confidently
poor grade that does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.services import rating
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)
from app.services.miti import caps
from app.services.miti.dimensions import DimensionResult, band_for

__all__ = [
    # Re-exported deliberately: `pipeline` reads the authenticity dimension
    # through this module because that is where the multiplier it feeds
    # lives. Declared so the re-export is intentional rather than a
    # side effect of an import.
    "DIM_AUTHENTICITY",
    "CATEGORY_MUST_HAVE",
    "CATEGORY_NICE_TO_HAVE",
    "CATEGORY_BEHAVIOURAL",
    "DIMENSION_TO_CATEGORY",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MODERATE",
    "CONFIDENCE_LOW",
    "CONFIDENCE_INSUFFICIENT",
    "CONFIDENCE_LABELS",
    "Aggregate",
    "MustHaveEvidence",
    "aggregate",
    "authenticity_multiplier",
    "confidence_label",
    "confidence_score",
]

# ── The product-facing categories ────────────────────────────────────────────
#
# Unchanged. These are what a client sees; the five dimensions are not.
CATEGORY_MUST_HAVE = "must_have"
CATEGORY_NICE_TO_HAVE = "nice_to_have"
CATEGORY_BEHAVIOURAL = "behavioural"

#: FALLBACK ONLY. How a dimension maps onto a product category when the
#: evaluators returned no per-competency bands.
#:
#: THE PRIMARY MAPPING IS BY ITEM CATEGORY, NOT BY DIMENSION, and getting that
#: backwards was a real defect in the first version of this file. Must-have and
#: Nice-to-have are properties of the ITEM -- the hiring manager declared this
#: specific criterion essential -- not of the internal dimension the criterion
#: happens to sit on. A Must-have competency whose dimension is Track Record
#: must count toward Must-have, and under a dimension-keyed map it counted
#: toward Nice-to-have instead. The symptom was silent and severe: a job whose
#: essentials all sat on one dimension produced an EMPTY Must-have grade, and
#: the Must-have hard cap had nothing to bind against.
#:
#: So `aggregate` groups the per-competency bands by the matrix category, and
#: falls back to this table only when an evaluator returned a dimension band
#: with no per-competency breakdown -- which is a degraded answer, and the
#: fallback is correspondingly coarse.
#:
#: Authenticity is absent from BOTH mappings. It does not map to a category at
#: all -- it is a MULTIPLIER on the composite, because "this account does not
#: hold together" is not a fourth thing a candidate is good or bad at, it is a
#: reason to trust everything else less. Mapping it to a category would let a
#: strong Authenticity score compensate for a weak Must-have, which is
#: backwards: consistency is a precondition for reading the other scores, not a
#: credit against them.
DIMENSION_TO_CATEGORY: dict[str, str] = {
    DIM_VERIFIED_COMPETENCE: CATEGORY_MUST_HAVE,
    DIM_TRACK_RECORD: CATEGORY_NICE_TO_HAVE,
    DIM_ROLE_FIT: CATEGORY_BEHAVIOURAL,
    DIM_TRAJECTORY: CATEGORY_NICE_TO_HAVE,
    # DIM_AUTHENTICITY is deliberately absent. See above.
}

#: The Runbook's own four confidence labels (section 10.7), not a fifth
#: vocabulary. The product previously carried high / medium / low here, which
#: is three words for a four-level scale and left "Insufficient" -- the only
#: level with a DELIVERY consequence -- unrepresentable.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MODERATE = "moderate"
CONFIDENCE_LOW = "low"
CONFIDENCE_INSUFFICIENT = "insufficient"

#: Best to worst. Nothing may be inserted into the middle of it.
CONFIDENCE_LABELS: tuple[str, ...] = (
    CONFIDENCE_HIGH,
    CONFIDENCE_MODERATE,
    CONFIDENCE_LOW,
    CONFIDENCE_INSUFFICIENT,
)


# ── Authenticity ─────────────────────────────────────────────────────────────

# Section 10.5's piecewise function on D4, read from `bands.yaml`.
#
# A MULTIPLIER AND NOT A GATE. A gate would mean an authenticity finding
# rejects a candidate, and no flag ever auto-rejects: every flag routes to
# human review with its evidence attached. So the worst an authenticity problem
# can do to a score is depress it, and what it ACTUALLY does is set
# `needs_human_review`, which is the mechanism that matters.
#
# The asymmetry is deliberate and stated: "high authenticity does not inflate a
# score above what the evidence supports (multiplier caps at 1.00). Low
# authenticity suppresses it. Authenticity is a licence to believe the other
# four dimensions, not a fifth way to win."
#
# THIS REPLACED A BAND TABLE OF INVENTED NUMBERS. The previous version mapped
# the four internal bands onto 1.0 / 1.0 / 0.9 / 0.75, with a comment arguing
# for the 0.75 floor. The argument was sound and the numbers were nobody's:
# section 10.5 states a piecewise linear function of the D4 SCORE, its floor is
# 0.50, and below 25 there is no multiplier at all because the candidate is
# HELD rather than scored.


def _authenticity_branches() -> list[Mapping[str, Any]]:
    table = caps.bands_data().get("authenticity_multiplier")
    rows = table.get("piecewise") if isinstance(table, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise caps.CapDataError(
            "runbook_data/bands.yaml carries no section 10.5 piecewise table; "
            "the authenticity multiplier cannot be computed without it."
        )
    return [row for row in rows if isinstance(row, Mapping)]


def _exact_slope(row: Mapping[str, Any]) -> float:
    """A branch's slope as the EXACT fraction section 10.5 now writes.

    Derived from the branch's own stated endpoints, `(high - low) / (upper -
    lower)`, rather than read from the rounded `slope` field beside them. The
    rounding is not cosmetic: 0.90 + (74.99 - 60) x 0.0067 evaluates to 1.0004,
    which breaches the 1.00 cap the very next paragraph of section 10.5 calls
    deliberate, and a multiplier above 1.00 would let a clean authenticity
    record inflate a score above what the evidence supports. Runbook v1.2
    replaced the rounded slopes with exact fractions and added an outer `min`
    for exactly this; `bands.yaml` keeps the rounded values because the Runbook
    still quotes them when explaining the defect, and the parity test checks
    what the document says rather than what the arithmetic needs.
    """
    stated = str(row.get("stated_range") or "")
    parts = [part.strip() for part in stated.split("->")]
    if len(parts) != 2:
        raise caps.CapDataError(
            f"runbook_data/bands.yaml section 10.5 branch {row.get('condition')!r} "
            f"states no endpoint range, so its exact slope cannot be derived."
        )
    low, high = float(parts[0]), float(parts[1])
    span = float(row["d4_high_exclusive"]) - float(row["d4_low"])
    if span <= 0:
        raise caps.CapDataError(
            f"runbook_data/bands.yaml section 10.5 branch "
            f"{row.get('condition')!r} has an empty interval."
        )
    return (high - low) / span


def authenticity_multiplier_for_score(d4: float) -> tuple[float | None, str]:
    """Section 10.5's multiplier for a D4 score. None means HOLD, not zero.

    The outer `min(1.00, ...)` is applied unconditionally and is NOT redundant
    even with exact fractions: it makes a ceiling breach unreachable if anybody
    ever re-rounds a slope, which is the failure this branch has already had
    once.

    Below the HOLD floor the Runbook says "not scored, mandatory human review",
    so this returns None rather than a harsh multiplier. Returning, say, 0.4
    would deliver the candidate with a suppressed number, which is a quieter
    outcome than the document asks for and hides the one case it wants a person
    to look at.

    THE None BRANCH BECAME REACHABLE ON 2026-09-02, and every one of the five
    branches below now is. Until then the band scale bottomed out at 40 against
    this floor of 25, so no evaluator output could enter it; `dimensions.BANDS`
    now carries a word for each of the six rows of the section 9.x rubric the
    evaluator is actually shown, and the bottom row scores 12. See Q24 in
    RUNBOOK_OPEN_QUESTIONS.md for what was wrong and why it was fixed by adding
    the missing rows rather than by moving a number.
    """
    # Read the branches FIRST. `_authenticity_branches` carries the guard
    # that explains a missing or malformed section 10.5 table; a bare
    # subscript here fired ahead of it and turned that explanation into a
    # KeyError, so the careful message could never reach anyone.
    branches = _authenticity_branches()
    table = caps.bands_data().get("authenticity_multiplier") or {}
    cap_value = float(table.get("caps_at", 1.0))
    for row in branches:
        low = row.get("d4_low")
        high = row.get("d4_high_exclusive")
        if low is not None and high is None:
            if d4 >= float(low):
                return min(cap_value, float(row["value"])), (
                    "the account is internally consistent"
                )
            continue
        if low is None and high is not None:
            if d4 < float(high):
                return None, (
                    "the account's internal consistency is below the floor at "
                    "which the Runbook requires a human disposition"
                )
            continue
        if low is not None and high is not None:
            if float(low) <= d4 < float(high):
                value = float(row["intercept"]) + (d4 - float(low)) * _exact_slope(row)
                return min(cap_value, value), (
                    "the composite is suppressed because the account does not "
                    "hold together across sources"
                )
    raise caps.CapDataError(
        f"runbook_data/bands.yaml section 10.5's branches do not cover a D4 "
        f"score of {d4!r}."
    )


def authenticity_multiplier(result: DimensionResult | None) -> tuple[float, str]:
    """(multiplier, reason). Both returned, so the reason is always recordable.

    An absent authenticity result yields 1.0 and says so. That is the honest
    default: not having run the check is not evidence of a problem, and
    penalising a candidate for a stage that did not run would make an
    infrastructure failure look like a finding about them.

    A HELD candidate also yields 1.0 here, and the HOLD is carried separately
    on the aggregate. Folding it into the multiplier would turn a routing
    decision into a number and deliver the candidate anyway.

    "Carried separately" means `Aggregate.hold`, which `aggregate` sets from
    `caps.hold_reason` and appends to `review_reasons` in its own right, so a
    held candidate always carries a reason and always needs a human. That is
    correct only while `caps.hold_reason` reads the same floor as this function
    does. Both read 25 today, from two separate data entries reading two
    separate Runbook sections, so a change to one without the other would leave
    a candidate suppressed by one rule and held by neither.

    AND A HELD CANDIDATE MUST NOT BE RANKED. Returning 1.0 here means the
    composite passes through unsuppressed, so a `contradicted` account would
    otherwise grade ABOVE an `absent` one. `client_projection` withholds the
    grade entirely for a held candidate, which is where that is resolved.
    """
    if result is None:
        return 1.0, "authenticity was not evaluated"
    if result.insufficient_evidence:
        # Insufficient evidence is not negative evidence, here as everywhere.
        return 1.0, "insufficient evidence to judge consistency"
    multiplier, reason = authenticity_multiplier_for_score(float(result.score))
    if multiplier is None:
        return 1.0, reason
    return multiplier, reason


# ── Confidence ───────────────────────────────────────────────────────────────
#
# Section 10.7's four weighted terms, and section 6.7's independent sufficiency
# floor, both of which the Runbook v1.2 now states BOTH BIND:
#
#     "A candidate is Insufficient if EITHER condition fires. Whichever is
#      stricter for that candidate is the one that decides."
#
# They can disagree in both directions and that is why both are kept. Coverage
# is only 35% of `confidence_score`, so a candidate with evidence on 40% of the
# must-haves can still clear 0.40 on depth, independence and consistency while
# more than half of what the job requires has never been examined; section 6.7
# catches that. Section 6.7 counts competencies and cannot see contradictions,
# so a candidate evidenced on every must-have passes it outright with
# unresolved contradictions dragging consistency below 0.40; section 10.7
# catches that. One is a breadth test, one is a quality test.


@dataclass(frozen=True)
class MustHaveEvidence:
    """What the record actually holds for one Must-have competency.

    THREE OF SECTION 10.7's FOUR TERMS ARE COMPUTED FROM THIS, so it is one
    input rather than three parallel mappings that could disagree about which
    competencies exist.

    A Must-have ABSENT from the mapping is not a missing input, it is a
    competency with no evidence: `tiers` empty, `independence_groups` zero.
    That is the true reading and it is what makes section 14.1 fire on a
    Must-have nobody probed, which is one of the two cases it exists for.
    """

    #: Every evidence tier (E0 to E5) mapped to this competency, in no order.
    tiers: tuple[str, ...] = ()
    #: Distinct independence GROUPS behind it, never a document count.
    independence_groups: int = 0


#: Section 6.5's contradiction penalties, used for section 10.7's "weighted
#: unresolved contradiction severity". SOURCE: RPN-PHIL-001 §10.7 with §6.7 (v1.3): the
#: term is named and never defined, and section 6.5's minor / moderate / severe
#: table is the only severity weighting the document states. The detector's
#: four levels are aligned onto it top-down. The Board owes the definition;
#: RUNBOOK_OPEN_QUESTIONS.md carries the question.
_SEVERITY_PENALTY_KEY: dict[str, str] = {
    "minor": "minor",
    "material": "moderate",
    "critical": "severe",
}


def _confidence_data() -> Any:
    from app.services.hiring import runbook_data

    return runbook_data.bands().get("confidence") or {}


def _coefficient(term: str) -> float:
    """One of section 10.7's four coefficients, read from `bands.yaml`.

    0.35 / 0.30 / 0.20 / 0.15 are Runbook numbers and are never typed here.
    """
    terms = _confidence_data().get("terms") or {}
    entry = terms.get(term) or {}
    if "coefficient" not in entry:
        raise caps.CapDataError(
            f"runbook_data/bands.yaml section 10.7 has no coefficient for "
            f"{term!r}."
        )
    return float(entry["coefficient"])


def _independence_divisor() -> float:
    """The 3 in "min(1, mean independence group count on must-haves / 3)".

    Taken out of section 10.7's own transcribed definition string rather than
    typed, so `test_runbook_parity.py` guards it like every other Runbook
    number. A literal here would be the one value in this function that no test
    could tie back to the document.
    """
    import re

    definition = str(
        ((_confidence_data().get("terms") or {}).get("independence") or {}).get(
            "definition", ""
        )
    )
    match = re.search(r"/\s*(\d+(?:\.\d+)?)\s*\)", definition)
    if not match:
        raise caps.CapDataError(
            "runbook_data/bands.yaml section 10.7's independence definition "
            "states no divisor; the term cannot be computed without it."
        )
    return float(match.group(1))


def _contradiction_penalty(severity: str) -> float:
    """Section 6.5's penalty for one unresolved contradiction.

    The detector's `none` level carries no penalty, and that is stated rather
    than defaulted: section 6.5's table has three numeric rows and "no
    contradiction" is not one of them because it is not a contradiction. Any
    OTHER unrecognised severity raises, so a new level added to the detector
    stops the build instead of silently costing nothing.
    """
    from app.services.hiring import runbook_data

    if severity == "none":
        return 0.0
    table = (
        (runbook_data.evidence_tiers().get("effective_strength") or {}).get(
            "contradiction_penalty"
        )
        or {}
    )
    key = _SEVERITY_PENALTY_KEY.get(severity)
    if key is None or key not in table:
        raise caps.CapDataError(
            f"runbook_data/evidence_tiers.yaml section 6.5 states no penalty "
            f"for contradiction severity {severity!r}."
        )
    return float(table[key])


def confidence_score(
    *,
    evidence_coverage: float | None,
    evidence_depth: float | None,
    independence: float | None,
    consistency: float | None,
) -> float:
    """Section 10.7's weighted sum. ARITHMETIC, never a model's self-estimate.

    Same rule `verification.base.Verdict` already follows and for the same
    reason: an LLM judging its own confidence makes the criterion unfalsifiable
    and fails exactly when the provider is already failing. Section 14.3 puts
    it in the Runbook's own words: confidence is reported as strength of the
    evidence base, "never as a probability of success -- we have not earned
    that claim".

    A term passed as None is UNKNOWN in section 6.6's sense -- the question was
    never asked, as opposed to asked and answered badly -- and section 6.6's
    handling applies: the term is excluded and the remaining coefficients
    renormalise. Three of these four terms are means over the Must-have set, so
    a scorecard with no Must-have at all makes them undefined rather than zero.
    Scoring an undefined term as zero would be the exact mistake section 6.6
    names: "a missing signal gets scored as zero, which is mathematically
    identical to negative evidence, which is wrong and unfair."

    Every present term is a fraction in [0, 1] and the coefficients sum to
    1.00, so the result is too. Clamped at both ends rather than trusted: a
    consistency term driven below zero by several severe contradictions would
    otherwise make the whole score negative and read as a data error rather
    than as the worst possible evidence base.
    """
    present = [
        (_coefficient(term), value)
        for term, value in (
            ("evidence_coverage", evidence_coverage),
            ("evidence_depth", evidence_depth),
            ("independence", independence),
            ("consistency", consistency),
        )
        if value is not None
    ]
    if not present:
        raise caps.CapDataError(
            "section 10.7 has no term left to compute a confidence score from. "
            "Every one was UNKNOWN, which is a pipeline defect rather than a "
            "confidence level."
        )
    total_weight = sum(coefficient for coefficient, _ in present)
    return _clamp01(
        sum(coefficient * _clamp01(value) for coefficient, value in present)
        / total_weight
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def confidence_label(
    score: float, *, sufficiency_breached: bool = False
) -> str:
    """Section 10.7's label, with section 6.7's floor OR'd in.

    `sufficiency_breached` is section 6.7's own test: fewer than half the
    Must-have competencies have any evidence above E1. When it fires the label
    is Insufficient whatever the weighted score says, because the Runbook v1.2
    states that either condition firing is decisive. OR'd rather than blended:
    an average of a breadth test and a quality test is neither.
    """
    if sufficiency_breached:
        return CONFIDENCE_INSUFFICIENT
    data = _confidence_data()
    for key, label in (
        ("high_threshold", CONFIDENCE_HIGH),
        ("moderate_threshold", CONFIDENCE_MODERATE),
        ("low_threshold", CONFIDENCE_LOW),
    ):
        if key not in data:
            raise caps.CapDataError(
                f"runbook_data/bands.yaml section 10.7 has no {key}."
            )
        if score >= float(data[key]):
            return label
    return CONFIDENCE_INSUFFICIENT


# ── The aggregate ────────────────────────────────────────────────────────────


@dataclass
class Aggregate:
    """The whole deterministic outcome. Every intermediate value is kept.

    Kept rather than discarded because "why did this candidate get Moderately
    Matching" is the question a recruiter actually asks, and an aggregate that
    reported only its answer would make it unanswerable without a rerun.
    """

    #: {category: internal score 0-100}. INTERNAL. Never rendered.
    category_scores: dict[str, float] = field(default_factory=dict)
    #: {category: grade word}. What a client eventually sees.
    category_grades: dict[str, str] = field(default_factory=dict)
    #: Before the authenticity multiplier and before the cap.
    raw_composite: float = 0.0
    #: After the multiplier, before the cap.
    adjusted_composite: float = 0.0
    #: After the caps. THE SCORE `overall_grade` WAS READ FROM, and the one a
    #: caller stores. Kept beside the uncapped value rather than replacing it,
    #: because "what did this candidate score" and "what were they delivered
    #: at" are different questions and a recruiter asks both.
    delivered_score: float = 0.0
    overall_grade: str = ""
    #: True when a band cap actually bound. Recorded because a cap that fired
    #: silently is indistinguishable from a candidate who simply scored there.
    must_have_cap_applied: bool = False
    #: EVERY control that fired, with its Runbook citation and its ceiling.
    #: Kept in full rather than reduced to the binding one, because "which
    #: rules did this candidate breach" and "which one decided the number" are
    #: different questions and a recruiter asks the first.
    applied_caps: list[caps.BandCap] = field(default_factory=list)
    #: Section 14.1: Must-haves resting on nothing above E1. Reported as
    #: Unassessed, never as a low grade -- the scorecard called them essential
    #: and nothing in the record examines them.
    unassessed_must_haves: list[str] = field(default_factory=list)
    #: Section 12.2's D4 floor of 25. Not ranked pending a human disposition.
    hold: bool = False
    authenticity_factor: float = 1.0
    authenticity_reason: str = ""
    confidence: str = CONFIDENCE_INSUFFICIENT
    #: Section 10.7's weighted sum, in [0, 1]. INTERNAL, like every score here.
    confidence_score: float = 0.0
    #: Dimensions excluded from the composite for want of evidence. Named, so
    #: the report can say what it could not assess rather than implying it did.
    insufficient_dimensions: list[str] = field(default_factory=list)
    #: The union of every evidence ref every dimension cited. Siddhi's citation
    #: enforcement checks against this set.
    evidence_refs: list[str] = field(default_factory=list)
    needs_human_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """INTERNAL projection. Carries scores, so it must never reach a client.

        `client_projection` is the one a report may render.
        """
        return {
            "category_scores": {k: round(v, 2) for k, v in sorted(self.category_scores.items())},
            "category_grades": dict(self.category_grades),
            "raw_composite": round(self.raw_composite, 2),
            "adjusted_composite": round(self.adjusted_composite, 2),
            "delivered_score": round(self.delivered_score, 2),
            "overall_grade": self.overall_grade,
            "must_have_cap_applied": self.must_have_cap_applied,
            "applied_caps": [
                {
                    "control": cap.control,
                    "citation": cap.citation,
                    "subject": cap.subject,
                    "reason": cap.reason,
                    "ceiling": cap.ceiling,
                }
                for cap in self.applied_caps
            ],
            "unassessed_must_haves": list(self.unassessed_must_haves),
            "hold": self.hold,
            "authenticity_factor": round(self.authenticity_factor, 3),
            "authenticity_reason": self.authenticity_reason,
            "confidence": self.confidence,
            "confidence_score": round(self.confidence_score, 4),
            "insufficient_dimensions": list(self.insufficient_dimensions),
            "evidence_refs": list(self.evidence_refs),
            "needs_human_review": self.needs_human_review,
            "review_reasons": list(self.review_reasons),
        }

    def client_projection(self) -> dict[str, Any]:
        """Words only. No score, no percentage, no multiplier, no count.

        Built by CONSTRUCTION rather than by filtering `as_dict`, because a
        filter is a list somebody has to remember to extend when a field is
        added, and the failure mode is a number reaching a client.

        A HELD CANDIDATE CARRIES NO GRADE, and this is section 10.8 taken
        literally: a hold is "not ranked pending human disposition", and a
        projection that ranked them anyway would be delivering the ranking the
        hold exists to withhold.

        It also closes a trap the hold created the moment it became reachable.
        Below section 10.5's floor the multiplier is not a suppression, it is
        None, because the Runbook does not score that row -- so the composite
        passes through UNSUPPRESSED and a `contradicted` account grades ABOVE an
        `absent` one, which is the worst possible reading of a worse result.
        Nothing downstream would have caught it: a grade is a plausible word
        whatever produced it. Withholding the grade removes the contradiction
        rather than papering over it with a number the Runbook declines to
        state.
        """
        if self.hold:
            return {
                "category_grades": {},
                "overall_grade": "",
                "capped_by_must_have": self.must_have_cap_applied,
                "unassessed_must_haves": list(self.unassessed_must_haves),
                "held_for_integrity_review": True,
                "confidence": self.confidence,
            }
        return {
            "category_grades": dict(self.category_grades),
            "overall_grade": self.overall_grade,
            # Deliberately present: a recruiter needs to know the report is
            # capped, and the WORD carries that without the arithmetic.
            "capped_by_must_have": self.must_have_cap_applied,
            # Section 14.1 asks for the competency to be REPORTED as Unassessed,
            # so the names travel. A name is not a number, and the alternative
            # is a report that quietly omits an essential criterion.
            "unassessed_must_haves": list(self.unassessed_must_haves),
            "held_for_integrity_review": self.hold,
            "confidence": self.confidence,
        }


def _weighted(scores: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Weighted mean, with a plain mean as the no-weights case.

    Falling back to the plain mean rather than to zero matters: a matrix whose
    weights failed to derive should produce a slightly less discriminating
    grade, not a Not Matching for everybody.
    """
    if not scores:
        return 0.0
    total_weight = sum(max(0.0, weights.get(name, 1.0)) for name in scores)
    if total_weight <= 0:
        return sum(scores.values()) / len(scores)
    return sum(
        value * max(0.0, weights.get(name, 1.0)) for name, value in scores.items()
    ) / total_weight


def _must_have_terms(
    names: Sequence[str],
    evidence: Mapping[str, MustHaveEvidence],
) -> tuple[float | None, float | None, float | None, bool]:
    """Section 10.7's three Must-have terms, plus section 6.7's own verdict.

    Returns (coverage, depth, independence, sufficiency_breached). The first
    three are None when no Must-have is declared, which is UNKNOWN rather than
    zero (section 6.6) and is renormalised away by `confidence_score`.

    Section 6.7's floor is computed HERE rather than beside the label, because
    it reads the same evidence map and computing it twice from two places is
    how two definitions of one word drift apart -- which is precisely the
    defect Runbook v1.2 fixed by declaring that both bind.
    """
    from app.services.evidence import tiers as evidence_tier_model

    if not names:
        return None, None, None, False
    above_e1 = 0
    depths: list[float] = []
    groups: list[int] = []
    for name in names:
        item = evidence.get(name) or MustHaveEvidence()
        tiers = [str(tier) for tier in item.tiers]
        if any(evidence_tier_model.above_e1(tier) for tier in tiers):
            above_e1 += 1
        # "mean over must-haves of (best tier strength achieved)". A Must-have
        # with nothing behind it achieved no strength, which is a real zero
        # rather than a substituted one.
        depths.append(
            max((evidence_tier_model.strength(tier) for tier in tiers), default=0.0)
        )
        groups.append(int(item.independence_groups))
    coverage = above_e1 / len(names)
    depth = sum(depths) / len(depths)
    independence = min(
        1.0, (sum(groups) / len(groups)) / _independence_divisor()
    )
    # Section 6.7: "fewer than half of must-haves have any evidence above E1".
    # Strictly fewer than half, so a two-item scorecard with one covered is not
    # breached and a three-item scorecard with one is.
    sufficiency_breached = above_e1 * 2 < len(names)
    return coverage, depth, independence, sufficiency_breached


def _consistency_term(
    severities: Sequence[str], unresolved_contradictions: int
) -> float:
    """Section 10.7's `1 - (weighted unresolved contradiction severity)`.

    When severities are supplied they are weighted individually by section
    6.5's penalty table. When only a COUNT is available, each unresolved
    contradiction is weighted as MATERIAL -- not a guess, but the definition:
    `triangulation.triangulate` increments `unresolved` only for a
    contradiction that reached at least material severity with no supported
    benign explanation, so material is the floor of what the count can mean.
    """
    listed = [str(severity) for severity in severities]
    if not listed and unresolved_contradictions:
        listed = ["material"] * int(unresolved_contradictions)
    penalty = sum(_contradiction_penalty(severity) for severity in listed)
    return max(0.0, 1.0 - penalty)


def aggregate(
    results: Sequence[DimensionResult],
    *,
    competency_categories: Mapping[str, str] | None = None,
    competency_weights: Mapping[str, float] | None = None,
    must_have_grades: Mapping[str, str] | None = None,
    must_have_scores: Mapping[str, float] | None = None,
    must_have_thresholds: Mapping[str, float] | None = None,
    must_have_evidence: Mapping[str, MustHaveEvidence] | None = None,
    unresolved_contradictions: int = 0,
    unresolved_severities: Sequence[str] = (),
    integrity_flags: Sequence[str] = (),
) -> Aggregate:
    """Turn five dimension results into the grade a client reads.

    `competency_categories` is {competency name: must_have | nice_to_have |
    behavioural} -- the approved matrix. It is what decides which product
    category a competency's band lands in, because that is a property of the
    ITEM and not of the dimension it sits on.

    `must_have_grades` is {Must-have ITEM name: grade}, and it is keyed by name
    rather than passed as a bare list of grades so that section 12.1's control
    can say WHICH named competency failed its minimum. It is passed in rather
    than derived from the dimension results on purpose: the control is about a
    specific criterion the hiring manager declared essential, not about the
    Verified Competence dimension in aggregate. A candidate can be strong on
    Verified Competence overall and still have missed one named Must-have, and
    it is the missed one that caps.

    `must_have_evidence` is {Must-have name: MustHaveEvidence}. A Must-have
    absent from it has no evidence, which is section 14.1's trigger and three
    of section 10.7's four confidence terms.
    """
    out = Aggregate()
    weights = dict(competency_weights or {})

    by_dimension = {r.dimension: r for r in results}
    authenticity = by_dimension.get(DIM_AUTHENTICITY)

    # ── Competency and dimension -> category ───────────────────────────────
    categories = dict(competency_categories or {})
    category_inputs: dict[str, dict[str, float]] = {}
    refs: list[str] = []
    judged = 0
    for result in results:
        refs.extend(result.evidence_refs)
        if result.dimension == DIM_AUTHENTICITY:
            # Not a category. It multiplies.
            if not result.insufficient_evidence:
                judged += 1
            else:
                out.insufficient_dimensions.append(result.dimension)
            continue
        if result.insufficient_evidence:
            # EXCLUDED, not scored low. The whole fairness distinction.
            out.insufficient_dimensions.append(result.dimension)
            continue
        judged += 1

        # PRIMARY PATH: the evaluator gave per-competency bands, so each one
        # lands in the category the hiring manager put that competency in.
        placed = False
        for name, band in result.per_competency.items():
            category = categories.get(name)
            if category is None:
                continue
            try:
                score = float(band_for(band))
            except ValueError:
                continue
            category_inputs.setdefault(category, {})[name] = score
            placed = True
        if placed:
            continue

        # FALLBACK: a dimension band with no per-competency breakdown. Coarse,
        # and it says so -- but a degraded answer must still reach a category,
        # or a whole dimension's judgment silently disappears.
        #
        # If the matrix tells us which categories THIS dimension's competencies
        # belong to, use that rather than the static table: it is still the
        # item's own category, just applied at dimension granularity.
        category = DIMENSION_TO_CATEGORY.get(result.dimension)
        if category is None:
            continue
        category_inputs.setdefault(category, {})[result.dimension] = float(result.score)

    # Preserve ref order while de-duplicating: two dimensions citing the same
    # evidence is normal and must not double-count in the citation set.
    #
    # THIS IS THE CITATION TRAIL. `siddhi.citations.Report` is constructed from
    # it, so an evaluation whose refs were dropped would refuse to render any
    # statement at all -- which is how a refactor that quietly removed this line
    # was caught by `test_the_aggregates_evidence_refs_are_the_known_set` rather
    # than by a report failing in production.
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    out.evidence_refs = deduped

    # ── Category scores ─────────────────────────────────────────────────────
    for category, dimension_scores in category_inputs.items():
        out.category_scores[category] = _weighted(dimension_scores, weights)
        out.category_grades[category] = (
            rating.grade_for_percent(out.category_scores[category]) or ""
        )

    # A category with no judged dimension is ABSENT rather than zero. Zero would
    # read as "assessed and failed"; absent reads as "we could not assess this",
    # which is what happened.
    for category in (CATEGORY_MUST_HAVE, CATEGORY_NICE_TO_HAVE, CATEGORY_BEHAVIOURAL):
        out.category_grades.setdefault(category, "")

    # ── Composite ───────────────────────────────────────────────────────────
    out.raw_composite = (
        sum(out.category_scores.values()) / len(out.category_scores)
        if out.category_scores
        else 0.0
    )

    # ── Authenticity multiplier ─────────────────────────────────────────────
    out.authenticity_factor, out.authenticity_reason = authenticity_multiplier(authenticity)
    out.adjusted_composite = out.raw_composite * out.authenticity_factor

    # ── Confidence: section 10.7's arithmetic, section 6.7's floor OR'd in ──
    grades = dict(must_have_grades or {})
    evidence = dict(must_have_evidence or {})
    must_have_names = sorted(
        set(grades)
        | set(evidence)
        | {
            name
            for name, category in categories.items()
            if category == CATEGORY_MUST_HAVE
        }
    )
    coverage, depth, independence_term, sufficiency_breached = _must_have_terms(
        must_have_names, evidence
    )
    out.confidence_score = confidence_score(
        evidence_coverage=coverage,
        evidence_depth=depth,
        independence=independence_term,
        consistency=_consistency_term(unresolved_severities, unresolved_contradictions),
    )
    out.confidence = confidence_label(
        out.confidence_score, sufficiency_breached=sufficiency_breached
    )

    # ── The caps, then the grade ────────────────────────────────────────────
    #
    # THREE CONTROLS, and the delivered band is the MINIMUM of whichever fired.
    # See `caps.py` for the Runbook citations and for why cap-last on the score
    # as a `min` is the only ordering that holds a ceiling absolutely.
    #
    # The section 12.1 trigger is a per-ITEM grade, not a dimension: the hiring
    # manager declared a specific criterion essential, and it is missing THAT
    # one that caps, not being weak on Verified Competence in aggregate.
    dimension_scores = {
        result.dimension: float(result.score)
        for result in results
        if not result.insufficient_evidence
    }
    out.unassessed_must_haves = caps.unassessed_must_haves(
        {name: item.tiers for name, item in evidence.items()}, must_have_names
    )
    applied = (
        caps.competency_threshold_caps(
            grades=grades,
            scores=must_have_scores,
            thresholds=must_have_thresholds,
        )
        + caps.dimension_floor_caps(dimension_scores)
        + caps.unassessed_must_have_caps(
            {name: item.tiers for name, item in evidence.items()}, must_have_names
        )
    )
    out.applied_caps = applied
    out.delivered_score = caps.apply(out.adjusted_composite, applied)
    out.must_have_cap_applied = out.delivered_score < out.adjusted_composite
    out.overall_grade = (
        rating.grade_for_percent(out.delivered_score) or rating.GRADE_NOT
    )

    # ── HOLD: section 12.2's D4 floor of 25 ─────────────────────────────────
    #
    # Not a band and not a ceiling. "Not ranked pending human disposition"
    # (section 10.8), which is a routing consequence. It does not reject: no
    # flag ever does, and G3 is non-blocking for exactly that reason.
    hold = caps.hold_reason(dimension_scores)
    out.hold = hold is not None

    # ── Human review ────────────────────────────────────────────────────────
    #
    # NO FLAG AUTO-REJECTS. Every one routes here, and low confidence WIDENS the
    # review set rather than narrowing it -- the less sure the pipeline is, the
    # more a person is needed, which is the opposite of the intuition that a
    # confident finding is the one worth escalating.
    reasons: list[str] = []
    if hold is not None:
        reasons.append(hold)
    if integrity_flags:
        reasons.extend(integrity_flags)
    if unresolved_contradictions:
        reasons.append(
            f"{unresolved_contradictions} contradiction(s) with no benign explanation"
        )
    if out.unassessed_must_haves:
        reasons.append(
            "unassessed Must-have(s): " + ", ".join(out.unassessed_must_haves)
        )
    if out.confidence in (CONFIDENCE_LOW, CONFIDENCE_INSUFFICIENT):
        reasons.append("low confidence: too little independent evidence to be sure")
    if out.insufficient_dimensions:
        reasons.append(
            "insufficient evidence on: "
            + ", ".join(sorted(out.insufficient_dimensions))
        )
    if out.authenticity_factor < 1.0:
        reasons.append(out.authenticity_reason)
    out.review_reasons = reasons
    out.needs_human_review = bool(reasons)
    return out
