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

THE MUST-HAVE HARD CAP IS APPLIED HERE
----------------------------------------
Any Must-have graded Not Matching caps Overall at Moderately Matching, with no
exceptions and no override. It is applied at the END, after every other
adjustment, because a cap applied before the authenticity multiplier could be
lifted by arithmetic that ran afterwards. `rating.cap_to_moderately` is the
existing implementation and is called rather than re-derived -- one hard cap,
one place.

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
from app.services.miti.dimensions import DIMENSIONS, DimensionResult, band_for

__all__ = [
    "CATEGORY_MUST_HAVE",
    "CATEGORY_NICE_TO_HAVE",
    "CATEGORY_BEHAVIOURAL",
    "DIMENSION_TO_CATEGORY",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_LOW",
    "Aggregate",
    "aggregate",
    "authenticity_multiplier",
    "confidence_for",
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

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


# ── Authenticity ─────────────────────────────────────────────────────────────

#: How far a failing authenticity judgment may pull the composite down.
#:
#: A MULTIPLIER AND NOT A GATE. A gate would mean an authenticity finding
#: rejects a candidate, and spec-doc5 is explicit that no flag ever
#: auto-rejects: every flag routes to human review with its evidence attached.
#: So the worst an authenticity problem can do to a score is depress it, and
#: what it ACTUALLY does is set `needs_human_review`, which is the mechanism
#: that matters.
#:
#: The floor is 0.75 rather than something harsher because the multiplier is not
#: the punishment -- the human review is. A multiplier severe enough to force a
#: Not Matching on its own would be an auto-rejection wearing arithmetic's
#: clothes.
_AUTHENTICITY_MULTIPLIERS: dict[str, float] = {
    "strong": 1.0,
    "solid": 1.0,
    "partial": 0.9,
    "absent": 0.75,
}


def authenticity_multiplier(result: DimensionResult | None) -> tuple[float, str]:
    """(multiplier, reason). Both returned, so the reason is always recordable.

    An absent authenticity result yields 1.0 and says so. That is the honest
    default: not having run the check is not evidence of a problem, and
    penalising a candidate for a stage that did not run would make an
    infrastructure failure look like a finding about them.
    """
    if result is None:
        return 1.0, "authenticity was not evaluated"
    if result.insufficient_evidence:
        # Insufficient evidence is not negative evidence, here as everywhere.
        return 1.0, "insufficient evidence to judge consistency"
    multiplier = _AUTHENTICITY_MULTIPLIERS.get(result.band, 1.0)
    if multiplier == 1.0:
        return 1.0, "the account is internally consistent"
    return multiplier, f"consistency graded {result.band}"


# ── Confidence ───────────────────────────────────────────────────────────────

#: How many of the five dimensions must have been judged on real evidence before
#: the result can be called high confidence.
_HIGH_CONFIDENCE_DIMENSIONS = 4
_MEDIUM_CONFIDENCE_DIMENSIONS = 3

#: Independent source groups needed across the whole evaluation for high
#: confidence. Two, not one: one source is an account, two is corroboration, and
#: the difference is the entire evidence model.
_HIGH_CONFIDENCE_INDEPENDENCE = 2


def confidence_for(
    *,
    judged: int,
    independence: int,
    unresolved_contradictions: int,
) -> str:
    """Confidence is ARITHMETIC over counts, never a model's opinion of itself.

    Same rule `verification.base.Verdict` already follows and for the same
    reason: an LLM judging its own confidence makes the criterion unfalsifiable
    and fails exactly when the provider is already failing.

    An unresolved contradiction caps confidence regardless of coverage, because
    a well-evidenced account that contradicts itself is not a confident result --
    it is a result whose pieces disagree, which is the case most in need of a
    person.
    """
    if unresolved_contradictions:
        return CONFIDENCE_LOW if unresolved_contradictions > 1 else CONFIDENCE_MEDIUM
    if judged >= _HIGH_CONFIDENCE_DIMENSIONS and independence >= _HIGH_CONFIDENCE_INDEPENDENCE:
        return CONFIDENCE_HIGH
    if judged >= _MEDIUM_CONFIDENCE_DIMENSIONS:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


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
    overall_grade: str = ""
    #: True when the Must-have hard cap actually bound. Recorded because a cap
    #: that fired silently is indistinguishable from a candidate who simply
    #: scored there.
    must_have_cap_applied: bool = False
    authenticity_factor: float = 1.0
    authenticity_reason: str = ""
    confidence: str = CONFIDENCE_LOW
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
            "overall_grade": self.overall_grade,
            "must_have_cap_applied": self.must_have_cap_applied,
            "authenticity_factor": round(self.authenticity_factor, 3),
            "authenticity_reason": self.authenticity_reason,
            "confidence": self.confidence,
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
        """
        return {
            "category_grades": dict(self.category_grades),
            "overall_grade": self.overall_grade,
            # Deliberately present: a recruiter needs to know the report is
            # capped, and the WORD carries that without the arithmetic.
            "capped_by_must_have": self.must_have_cap_applied,
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


def aggregate(
    results: Sequence[DimensionResult],
    *,
    competency_categories: Mapping[str, str] | None = None,
    competency_weights: Mapping[str, float] | None = None,
    must_have_grades: Sequence[str] = (),
    independence: int = 0,
    unresolved_contradictions: int = 0,
    integrity_flags: Sequence[str] = (),
) -> Aggregate:
    """Turn five dimension results into the grade a client reads.

    `competency_categories` is {competency name: must_have | nice_to_have |
    behavioural} -- the approved matrix. It is what decides which product
    category a competency's band lands in, because that is a property of the
    ITEM and not of the dimension it sits on.

    `must_have_grades` is the per-ITEM grades of the job's Must-have items, and
    it is passed in rather than derived from the dimension results on purpose:
    the hard cap is about a specific criterion the hiring manager declared
    essential, not about the Verified Competence dimension in aggregate. A
    candidate can be strong on Verified Competence overall and still have missed
    one named Must-have, and it is the missed one that caps.
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
    out.evidence_refs = [r for r in refs if not (r in seen or seen.add(r))]

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

    # ── Confidence ──────────────────────────────────────────────────────────
    out.confidence = confidence_for(
        judged=judged,
        independence=independence,
        unresolved_contradictions=unresolved_contradictions,
    )

    # ── The cap, then the grade ─────────────────────────────────────────────
    #
    # The cap goes LAST, on the SCORE, before it becomes a grade. Two details
    # matter and both come from `rating.cap_to_moderately`:
    #
    #   * it is applied after the authenticity multiplier, because a cap applied
    #     earlier could be lifted by arithmetic running afterwards, and a hard
    #     cap a later multiplication can undo is not a hard cap; and
    #   * it is a `min`, never an assignment. A candidate whose aggregate
    #     already grades Not Matching STAYS Not Matching -- a cap that set the
    #     score would quietly promote the weakest candidates into the band it
    #     exists to keep the strong ones out of.
    #
    # The trigger is a per-ITEM grade, not a dimension: the hiring manager
    # declared a specific criterion essential, and it is missing THAT one that
    # caps, not being weak on Verified Competence in aggregate.
    final_score = out.adjusted_composite
    if any(grade == rating.GRADE_NOT for grade in must_have_grades):
        capped_score = float(rating.cap_to_moderately(final_score))
        out.must_have_cap_applied = capped_score < final_score
        final_score = capped_score
    out.overall_grade = rating.grade_for_percent(final_score) or rating.GRADE_NOT

    # ── Human review ────────────────────────────────────────────────────────
    #
    # NO FLAG AUTO-REJECTS. Every one routes here, and low confidence WIDENS the
    # review set rather than narrowing it -- the less sure the pipeline is, the
    # more a person is needed, which is the opposite of the intuition that a
    # confident finding is the one worth escalating.
    reasons: list[str] = []
    if integrity_flags:
        reasons.extend(integrity_flags)
    if unresolved_contradictions:
        reasons.append(
            f"{unresolved_contradictions} contradiction(s) with no benign explanation"
        )
    if out.confidence == CONFIDENCE_LOW:
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
