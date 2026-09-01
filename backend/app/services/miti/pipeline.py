"""Miti's pipeline: stages 2-6 wired together, with G1-G4 in their places.

    G1  scorecard approved                        <- before anything runs
    2   NORMALISE & EXTRACT    claims.py
    3   EVIDENCE TIERING       tiering.py
    4   DIMENSION EVALUATORS   dimensions.py      five, isolated, in parallel
    5   TRIANGULATION          triangulation.py
    G2  evidence sufficiency                      <- at aggregation
    G3  integrity                                 <- at aggregation
    6   AGGREGATION            aggregation.py     deterministic, no model
    G4  human review disposition                  <- before delivery

WHY THIS MODULE IS THIN
------------------------
Every stage's logic lives in its own module and is testable without this one.
What is here is the ORDER, the ISOLATION at the fan-out, and the gates -- which
is exactly the set of things that cannot be tested one stage at a time.

The order is not arbitrary and two edges in it are load-bearing:

  * TRIANGULATION RUNS AFTER THE EVALUATORS, NOT BEFORE. A contradiction is
    between what sources SAY, and the evaluators are what establish which claims
    the evidence actually supports. Running triangulation first would have it
    comparing raw claims, where every difference in phrasing looks like a
    disagreement.
  * AGGREGATION RUNS LAST AND READS EVERYTHING. It is the only stage that sees
    all five dimensions, and it is the only one that may.

THE FAN-OUT IS WHERE ISOLATION IS ENFORCED
--------------------------------------------
`_build_inputs` constructs five `EvaluatorInput` objects, each carrying only its
own dimension's competencies and only the evidence routed to them. The isolation
is structural -- `EvaluatorInput` has no field for a name, another dimension's
score, or the composite -- and this function is where the routing half happens.
`test_miti_pipeline.py` asserts both halves.

The five run CONCURRENTLY. Not for speed, though it is faster: concurrently
means none of them can observe another's result, because there is no ordering in
which one has finished before another starts. A sequential loop would make it
possible for a future edit to thread an earlier result into a later prompt, and
"possible" is the whole thing being designed out.

EVERY FAILURE PATH PRODUCES A RESULT, NEVER AN EXCEPTION
----------------------------------------------------------
An evaluator that times out yields `insufficient_evidence=True` for its
dimension. That is the honest reading: we did not learn anything about that
dimension. It is NOT a low band -- that would convert a provider outage into a
finding about a candidate, which is the same class of error as gibberish being
hashed into a grade.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any, Mapping, Sequence

from app.services.evidence import contradictions as detector
from app.services.hiring import gates
from app.services.miti import aggregation, dimensions, triangulation
from app.services.miti.dimensions import (
    DIMENSIONS,
    DimensionResult,
    EvaluatorInput,
    EvidenceView,
)

logger = logging.getLogger(__name__)

__all__ = [
    "EvaluationInputs",
    "EvaluationOutcome",
    "build_evaluator_inputs",
    "evaluate",
    "must_have_evidence",
]


@dataclass
class EvaluationInputs:
    """Everything the pipeline needs, gathered by the caller.

    A PLAIN DATA OBJECT WITH NO SESSION AND NO ORM ROWS. The whole pipeline is
    therefore runnable and testable without a database, which is what lets the
    isolation and the gate ordering be asserted directly rather than through an
    integration test that also needs Postgres.
    """

    #: {competency name: category} -- the approved matrix.
    matrix: dict[str, str] = field(default_factory=dict)
    #: {competency name: internal dimension}.
    competency_dimensions: dict[str, str] = field(default_factory=dict)
    #: {competency name: derived weight} from Sutra's stage 5.
    competency_weights: dict[str, float] = field(default_factory=dict)
    #: Every piece of evidence, already tiered.
    evidence: list[EvidenceView] = field(default_factory=list)
    #: {evidence ref: [competency names]}.
    evidence_competencies: dict[str, list[str]] = field(default_factory=dict)
    rubric_anchor: str = ""
    role_context: str = ""
    #: G1's inputs.
    matrix_items: list[dict[str, Any]] = field(default_factory=list)
    scorecard_approved_at: Any = None
    #: {Must-have ITEM name: grade}, for section 12.1's competency threshold.
    #: Keyed by name rather than a bare list of grades so the control can say
    #: WHICH named competency failed its minimum.
    must_have_grades: dict[str, str] = field(default_factory=dict)
    #: {Must-have name: internal score}, and {Must-have name: the minimum the
    #: approved scorecard sets}. Both optional: where the frozen matrix
    #: declares no numeric threshold, section 12.1's minimum is the product's
    #: published floor for an essential criterion and the grade carries it.
    must_have_scores: dict[str, float] = field(default_factory=dict)
    must_have_thresholds: dict[str, float] = field(default_factory=dict)
    #: The contradiction report from `evidence/contradictions.detect`.
    contradiction_report: detector.ContradictionReport | None = None
    #: Model-generated benign explanations, per axis.
    benign_explanations: dict[str, list[triangulation.BenignExplanation]] = field(
        default_factory=dict
    )
    #: G4's inputs. Both None on a first pass, which is correct: a disposition
    #: cannot exist before the flags that need one.
    review_disposition: str | None = None
    review_decided_by: Any = None


@dataclass
class EvaluationOutcome:
    """What the pipeline concluded, and every gate's verdict.

    `deliverable` is the single question a caller asks. It is False when a
    BLOCKING gate failed, and blocking is a property of the gate rather than of
    the finding -- G2 and G3 can fail loudly without stopping anything, because
    a blocking integrity gate would be an auto-rejection.
    """

    results: list[DimensionResult] = field(default_factory=list)
    triangulation: triangulation.TriangulationResult | None = None
    aggregate: aggregation.Aggregate | None = None
    gate_results: list[gates.GateResult] = field(default_factory=list)
    degraded_dimensions: list[str] = field(default_factory=list)

    @property
    def deliverable(self) -> bool:
        return not any(g.blocking and not g.passed for g in self.gate_results)

    @property
    def blocking_reasons(self) -> list[str]:
        return [
            reason
            for gate in self.gate_results
            if gate.blocking and not gate.passed
            for reason in gate.reasons
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "deliverable": self.deliverable,
            "gates": [g.as_dict() for g in self.gate_results],
            "dimensions": [r.as_dict() for r in self.results],
            "degraded_dimensions": list(self.degraded_dimensions),
            "triangulation": (
                self.triangulation.as_dict() if self.triangulation else None
            ),
            "aggregate": self.aggregate.as_dict() if self.aggregate else None,
        }

    def client_projection(self) -> dict[str, Any]:
        """Words only. The five dimensions are NEVER named here.

        A client sees Must-have / Nice-to-have / Behavioural and the overall
        grade. "Verified Competence" and "Trajectory & Potential" are how the
        grade was arrived at and are internal; naming them would both leak the
        internal model and imply a fourth and fifth thing the client can act on.
        """
        if self.aggregate is None:
            return {}
        return self.aggregate.client_projection()


def build_evaluator_inputs(inputs: EvaluationInputs) -> list[EvaluatorInput]:
    """One isolated input per dimension. THE ISOLATION BOUNDARY.

    Each carries only its own competencies and only the evidence routed to them.
    `EvaluatorInput` structurally cannot hold a candidate name, another
    dimension's score, or the composite; this is what stops the wrong EVIDENCE
    being put in it.
    """
    payloads: list[EvaluatorInput] = []
    for dimension in DIMENSIONS:
        competencies = tuple(
            sorted(
                name
                for name, dim in inputs.competency_dimensions.items()
                if dim == dimension
            )
        )
        evidence = dimensions.route_evidence(
            inputs.evidence,
            inputs.competency_dimensions,
            inputs.evidence_competencies,
            dimension,
        )
        # THIS DIMENSION'S OWN section 9.x anchors, plus whatever role-level
        # anchor Sutra derived. Not one shared string: sections 9.1 to 9.5
        # state a different six-band table per dimension, and giving all five
        # evaluators the same one anchors four of them against a rubric written
        # for a question they were not asked.
        anchor = dimensions.rubric_anchor_text(dimension)
        if inputs.rubric_anchor:
            anchor = f"{anchor}\n\nFor this role specifically:\n  {inputs.rubric_anchor}"
        payloads.append(
            EvaluatorInput(
                dimension=dimension,
                competencies=competencies,
                rubric_anchor=anchor,
                evidence=evidence,
                role_context=inputs.role_context,
            )
        )
    return payloads


def must_have_evidence(
    inputs: EvaluationInputs,
) -> dict[str, aggregation.MustHaveEvidence]:
    """What the record holds for each Must-have: its tiers and its independent
    groups.

    Stage 3's output, read per competency. Section 14.1 needs the tiers and
    section 10.7 needs all three, and both are computed from the SAME
    `EvidenceView` objects the five evaluators were handed. Deriving them here
    rather than accepting them as a separate argument is what makes it
    impossible for a cap to act on a tier the evaluators never saw.

    A Must-have with nothing mapped to it is present with an empty tuple rather
    than absent, so a reader of the result can tell "examined and found nothing"
    from "never asked about".
    """
    out: dict[str, aggregation.MustHaveEvidence] = {}
    for name, category in inputs.matrix.items():
        if category != aggregation.CATEGORY_MUST_HAVE:
            continue
        views = [
            view
            for view in inputs.evidence
            if name in set(inputs.evidence_competencies.get(view.ref, ()))
        ]
        out[name] = aggregation.MustHaveEvidence(
            tiers=tuple(view.tier for view in views),
            # GROUPS, never documents. A resume line and the candidate
            # restating it in the interview are one source saying one thing
            # twice, and counting them as two is how a confidently written
            # resume becomes a well-corroborated candidate.
            independence_groups=len({view.independence_group for view in views}),
        )
    return out


def _degraded(dimension: str, reason: str) -> DimensionResult:
    """What an evaluator that could not run yields.

    `insufficient_evidence`, never a low band. A provider outage is not a
    finding about a candidate, and converting one into the other is the same
    class of error as a hash deciding whether gibberish failed.
    """
    logger.warning("miti.evaluator_degraded dimension=%s reason=%s", dimension, reason)
    return DimensionResult(
        dimension=dimension,
        band="partial",
        evidence_refs=(),
        rationale="",
        insufficient_evidence=True,
    )


def parse_result(payload: Mapping[str, Any], dimension: str) -> DimensionResult:
    """Turn one evaluator's JSON into a `DimensionResult`.

    A band the model invented is REFUSED rather than defaulted, because a silent
    default would convert a malformed response into a real grade for a real
    person. The refusal surfaces as `insufficient_evidence`, which is the honest
    reading: this evaluator did not produce a usable answer.
    """
    band = str(payload.get("band") or "").strip().lower()
    if band not in dict(dimensions.BANDS):
        return _degraded(dimension, f"unknown band {band!r}")
    refs = tuple(
        str(ref) for ref in (payload.get("evidence_refs") or []) if str(ref).strip()
    )
    insufficient = bool(payload.get("insufficient_evidence"))
    if not refs and not insufficient:
        # A band with no citation. Not usable: Siddhi cannot write a sentence
        # about it, and an uncitable score is one that would be reported without
        # a citation or not reported at all.
        return _degraded(dimension, "band given with no evidence citation")
    per_competency = {
        str(k): str(v)
        for k, v in (payload.get("per_competency") or {}).items()
        if str(v).strip().lower() in dict(dimensions.BANDS)
    }
    return DimensionResult(
        dimension=dimension,
        band=band,
        evidence_refs=refs,
        rationale=str(payload.get("rationale") or "").strip(),
        insufficient_evidence=insufficient,
        per_competency=per_competency,
    )


async def _run_evaluator(
    payload: EvaluatorInput, invoke: Callable[..., Awaitable[str]]
) -> DimensionResult:
    """One evaluator. Never raises.

    `invoke` is injected rather than imported so this module has no import of
    `llm_router` at all -- which keeps the pipeline unit-testable offline and,
    more usefully, makes it impossible for `aggregation` to reach a model
    through a sibling.
    """
    try:
        raw = await invoke(
            "dimension_evaluation",
            dimensions.render_prompt(payload),
            response_format_json=True,
        )
    except Exception as exc:  # noqa: BLE001 -- an outage is not a finding
        return _degraded(payload.dimension, type(exc).__name__)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return _degraded(payload.dimension, "malformed JSON")
    if not isinstance(parsed, Mapping):
        return _degraded(payload.dimension, "response was not an object")
    return parse_result(parsed, payload.dimension)


async def evaluate(
    inputs: EvaluationInputs, *, invoke: Callable[..., Awaitable[str]]
) -> EvaluationOutcome:
    """Run stages 2-6 and every gate, in order.

    Returns an outcome even when a gate fails. A caller wanting "may I deliver
    this" reads `deliverable`; a caller wanting "what is wrong" reads
    `gate_results`. Raising instead would lose the second answer, which is the
    one a recruiter actually needs.
    """
    outcome = EvaluationOutcome()

    # ── G1: nothing runs against an unapproved scorecard ────────────────────
    g1 = gates.scorecard_gate(
        matrix_items=inputs.matrix_items,
        approved_at=inputs.scorecard_approved_at,
    )
    outcome.gate_results.append(g1)
    if not g1.passed:
        # BLOCKING, and it returns here rather than continuing. Evaluating
        # against a draft scorecard would let the first candidate set the
        # criteria for everyone by being assessed against it.
        return outcome

    # ── Stage 4: five isolated evaluators, concurrently ─────────────────────
    payloads = build_evaluator_inputs(inputs)
    outcome.results = list(
        await asyncio.gather(*(_run_evaluator(p, invoke) for p in payloads))
    )
    outcome.degraded_dimensions = [
        r.dimension for r in outcome.results if r.insufficient_evidence
    ]

    # ── Stage 5: triangulation ──────────────────────────────────────────────
    report = inputs.contradiction_report or detector.ContradictionReport()
    outcome.triangulation = triangulation.triangulate(
        report,
        sources=[
            {"ref": e.ref, "independence_group": e.independence_group}
            for e in inputs.evidence
        ],
        generated=inputs.benign_explanations,
    )

    judged = len([r for r in outcome.results if not r.insufficient_evidence])
    independence = outcome.triangulation.independence

    # ── G2: evidence sufficiency. NON-BLOCKING. ─────────────────────────────
    must_have_coverage = {
        name: sum(
            1
            for refs in inputs.evidence_competencies.values()
            if name in refs
        )
        for name, category in inputs.matrix.items()
        if category == aggregation.CATEGORY_MUST_HAVE
    }
    g2 = gates.evidence_sufficiency_gate(
        independent_sources=independence,
        judged_dimensions=judged,
        must_have_coverage=must_have_coverage,
    )
    outcome.gate_results.append(g2)

    # ── G3: integrity. NON-BLOCKING, deliberately. ──────────────────────────
    authenticity = next(
        (
            r
            for r in outcome.results
            if r.dimension == aggregation.DIM_AUTHENTICITY
        ),
        None,
    )
    g3 = gates.integrity_gate(
        unresolved_contradictions=outcome.triangulation.unresolved,
        contradiction_severity=outcome.triangulation.severity,
        authenticity_band=authenticity.band if authenticity else None,
    )
    outcome.gate_results.append(g3)

    # ── Stage 6: aggregation. Deterministic. ────────────────────────────────
    #
    # The failed non-blocking gates feed in as integrity flags, so a gate that
    # blocked nothing still reaches `needs_human_review`. A gate whose failure
    # had no consequence at all would be documentation.
    integrity_flags = list(outcome.triangulation.integrity_flags)
    integrity_flags.extend(g3.reasons)
    integrity_flags.extend(g2.reasons)

    outcome.aggregate = aggregation.aggregate(
        outcome.results,
        # The MATRIX decides which product category a competency's band lands
        # in, because Must-have and Nice-to-have are properties of the item the
        # hiring manager declared essential -- not of the internal dimension the
        # criterion happens to sit on.
        competency_categories=inputs.matrix,
        competency_weights=inputs.competency_weights,
        must_have_grades=inputs.must_have_grades,
        must_have_scores=inputs.must_have_scores,
        must_have_thresholds=inputs.must_have_thresholds,
        # Section 14.1's trigger and three of section 10.7's four confidence
        # terms, both read off the SAME evidence the evaluators saw. Derived
        # here rather than passed in so that the tiers a cap acts on cannot
        # differ from the evidence a grade was written from.
        must_have_evidence=must_have_evidence(inputs),
        unresolved_contradictions=outcome.triangulation.unresolved,
        unresolved_severities=[
            item.severity
            for item in outcome.triangulation.contradictions
            if not item.settled_benignly
        ],
        integrity_flags=integrity_flags,
    )

    # ── G4: a human decided, before delivery ────────────────────────────────
    g4 = gates.human_review_gate(
        needs_review=outcome.aggregate.needs_human_review,
        disposition=inputs.review_disposition,
        decided_by=inputs.review_decided_by,
    )
    outcome.gate_results.append(g4)
    return outcome
