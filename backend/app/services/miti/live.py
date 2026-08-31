"""Miti on the live scoring path: stages 2 to 6, against real rows.

    G1   frozen scorecard          `hiring.scorecard.require_frozen_matrix`
    2/3  claims and tiering        read back from the evidence ledger
    4    five isolated evaluators  `pipeline.build_evaluator_inputs`, concurrent
    5    triangulation             `evidence.contradictions` -> `triangulation`
    6    aggregation               deterministic, and where the three caps bind

WHAT THIS MODULE IS FOR
-------------------------
Until now the whole of Part A was reachable from exactly one place,
`app/scripts/worked_example.py`, and from no route or worker. Every gate was
real and every gate guarded nothing. This is the module that puts the five
evaluators, the gates and the band caps on the path a candidate's report is
actually written from; `services/functional_assessment.synthesis_node` is its
only caller.

THERE IS NO DEFAULT MATRIX, AND THAT IS THE POINT OF G1
---------------------------------------------------------
`require_frozen_matrix` IS gate G1: an approved, frozen scorecard or nothing.
Runbook section 14.1 states the consequence in one line -- "the scorecard was
not approved -> scoring blocked entirely (Gate G1)" -- and section 4.3 repeats
it. So this module RAISES when the matrix is missing, unapproved, or when the
module that owns it has not landed. It does not substitute a generic matrix,
does not fall back to the job's competency rows, and does not score a candidate
against criteria no human confirmed. Doing any of those would let the first
candidate assessed set the criteria for everyone, which is the one thing the
approval gate exists to prevent.

THE MODEL IS INJECTED, NEVER IMPORTED BY THE PIPELINE
-------------------------------------------------------
`pipeline.evaluate` takes its `invoke` as a parameter and this module is where
the real one is supplied. That keeps `aggregation.py` structurally unable to
reach a provider through a sibling, which is the property
`test_miti_pipeline.py` asserts by walking the AST rather than by reading a
docstring.
"""
from __future__ import annotations

import inspect
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.services import rating
from app.services.miti import aggregation, dimensions, pipeline, tiering
from app.services.miti.dimensions import EvidenceView

logger = logging.getLogger(__name__)

__all__ = [
    "LiveEvaluation",
    "ScorecardUnavailable",
    "evaluate_application",
    "frozen_matrix",
    "gather_evidence",
]

#: The task type the five evaluators run under. The reasoning tier,
#: temperature 0.0, and
#: `config/llm_providers.py` is the closed mapping that says so. Named here
#: rather than passed in so no caller can route a grading call somewhere else.
EVALUATION_TASK = "dimension_evaluation"


class ScorecardUnavailable(RuntimeError):
    """Gate G1 could not even be asked, or it refused.

    A distinct exception rather than a generic one because the caller's
    response is specific: this is not a transient failure to retry, it is a
    configuration state a human has to change. Runbook section 14.1 files it
    under abstention, not under error handling.
    """


async def frozen_matrix(session: Any, job_id: Any) -> Any:
    """The approved, frozen Tatva matrix for this job. GATE G1.

    `app.services.hiring.scorecard` is imported INSIDE this function on
    purpose. It is owned by another workstream and may not have landed; a
    module-scope import would take every importer of `miti` down with it, and
    the honest failure is at the moment a candidate is scored rather than at
    the moment a worker starts.

    `require_frozen_matrix` may be written sync or async. Both are awaited
    correctly here rather than assumed, because guessing wrong would produce a
    coroutine object that reads as a truthy matrix and would be the worst
    possible failure: scoring against an object with no items at all.
    """
    try:
        from app.services.hiring import scorecard
    except ImportError as exc:
        raise ScorecardUnavailable(
            "app.services.hiring.scorecard is not present, so gate G1 cannot be "
            "asked whether this job's Tatva matrix is approved and frozen. "
            "Scoring is blocked (Runbook section 14.1). There is no default "
            "matrix and there must not be one: scoring against unapproved "
            "criteria lets the first candidate set the criteria for everyone."
        ) from exc
    require = getattr(scorecard, "require_frozen_matrix", None)
    if require is None:
        raise ScorecardUnavailable(
            "app.services.hiring.scorecard exposes no require_frozen_matrix, "
            "which is the function that IS gate G1."
        )
    result = require(session, job_id)
    if inspect.isawaitable(result):
        result = await result
    return result


def _matrix_items(matrix: Any) -> list[Any]:
    items = getattr(matrix, "items", None)
    if items is None:
        raise ScorecardUnavailable(
            "the frozen matrix carries no `items`, so there is nothing to score "
            "against."
        )
    # `items` on a Mapping is a method; a FrozenMatrix's is a sequence. Calling
    # one and indexing the other are both wrong, so the shape is checked rather
    # than assumed.
    if callable(items):
        raise ScorecardUnavailable(
            "the frozen matrix's `items` is callable, which means a mapping was "
            "passed where a FrozenMatrix was expected."
        )
    return list(items)


def _approved_at(matrix: Any) -> Any:
    """When a human froze this matrix, for G1's second question.

    G1 asks the TABLE first and the stamp second, in that order, because this
    codebase has already paid for believing a timestamp: 19 of 35 live jobs
    carried a generation stamp and zero competency rows. Both are checked, and
    a matrix that can supply neither name for the stamp is a contract mismatch
    rather than an unapproved scorecard, so it says so.
    """
    for name in ("approved_at", "frozen_at"):
        value = getattr(matrix, name, None)
        if value is not None:
            return value
    raise ScorecardUnavailable(
        "the frozen matrix states neither `approved_at` nor `frozen_at`, so "
        "gate G1 cannot tell an approved scorecard from a draft."
    )


@dataclass
class LiveEvaluation:
    """The pipeline's outcome plus what only the live path can know.

    `unresolved_evidence` names ledger rows whose text could not be fetched.
    They are EXCLUDED from the evaluators rather than passed as empty strings,
    and they are named here rather than dropped: an excluded piece of evidence
    lowers coverage, lowers confidence and can trip section 14.1, all of which
    are visible consequences. Silently handing an evaluator an empty excerpt
    would be a grade written from evidence nobody read.
    """

    outcome: pipeline.EvaluationOutcome
    unresolved_evidence: list[str] = field(default_factory=list)
    evidence_count: int = 0
    #: The frozen matrix this ran against. Carried so the caller can COPY its
    #: version numbers onto the evaluation row rather than joining them later:
    #: an evaluation is a permanent record of the criteria it was run against,
    #: and the job's matrix may be re-frozen afterwards.
    matrix: Any = None

    @property
    def aggregate(self) -> aggregation.Aggregate | None:
        return self.outcome.aggregate

    @property
    def review_reasons(self) -> list[str]:
        reasons = list(self.aggregate.review_reasons) if self.aggregate else []
        if self.unresolved_evidence:
            reasons.append(
                f"{len(self.unresolved_evidence)} piece(s) of recorded evidence "
                f"could not be read back and were excluded from the evaluation"
            )
        return reasons


async def gather_evidence(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    subject_names: Sequence[str] = (),
) -> tuple[list[EvidenceView], dict[str, list[str]], list[str]]:
    """Stages 2 and 3, read back off the ledger. Returns (views, mapping, lost).

    The ledger is written DURING scoring, one row per substantive answer, keyed
    to the matrix item the question probed. So the claims already exist by the
    time this runs, and stage 2's extraction has already happened at the point
    the text was in hand.

    The text is fetched through `ledger.resolve_text`, which goes back to the
    source table under the caller's own tenant scope. That is deliberate: the
    ledger stores a LOCATOR and never the sentence, so that a copy of a
    candidate's words does not sit in a table readable by anyone who can reach
    the database, outside the capability that guards the transcript.

    `subject_names` is the CANDIDATE's own name parts, and every excerpt is
    scrubbed of them before an evaluator sees it. The structural guarantee is
    that `EvaluatorInput` has no name field; this is defence in depth on top of
    it, and it is needed because the excerpts are the candidate's own prose. A
    name carries inferred gender, ethnicity and nationality, and an evaluator
    that can see one is an evaluator whose output can correlate with one.
    """
    from app.services.evidence import ledger

    claims = await ledger.load_claims(
        session, tenant_id=tenant_id, job_id=job_id, link_id=link_id
    )
    views: dict[str, EvidenceView] = {}
    mapping: dict[str, list[str]] = {}
    lost: list[str] = []
    for claim in claims:
        competency = claim.dimension
        for item in claim.live_support:
            ref = str(item.evidence_id)
            mapping.setdefault(ref, [])
            if competency not in mapping[ref]:
                mapping[ref].append(competency)
            if ref in views:
                continue
            text = await ledger.resolve_text(
                session, tenant_id=tenant_id, item=item
            )
            if not text:
                lost.append(ref)
                mapping.pop(ref, None)
                continue
            provenance = dict(item.provenance or {})
            views[ref] = EvidenceView(
                ref=ref,
                text=dimensions.scrub(text, subject_names=subject_names),
                trust=item.trust,
                source_kind=item.source_type,
                independence_group=tiering.independence_group_for(item.source_type),
                freshness=str(item.freshness.get("band") or ""),
                has_specifics=bool(provenance.get("has_specifics", False)),
            )
    return list(views.values()), mapping, lost


def _role_context(job: Any) -> str:
    """One sentence about the ROLE, and nothing about the person.

    Role and Context Fit is unanswerable without it and the other four are
    sharper with it. It is safe to include for the same reason the candidate's
    name is not: it says nothing about who is being evaluated.
    """
    parts = [str(getattr(job, "title", "") or "").strip()]
    grade = str(getattr(job, "assessment_grade", "") or "").strip()
    if grade:
        parts.append(f"seniority band {grade.replace('_', ' ')}")
    return ", ".join(part for part in parts if part)


def _competency_categories(items: Sequence[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name = str(getattr(item, "competency", "") or "")
        category = str(getattr(item, "category", "") or "")
        if not name or not category:
            raise ScorecardUnavailable(
                "a frozen matrix item carries no competency name or no "
                "category, so nothing can be graded against it. Runbook "
                "section 14.1: a competency with no defined assessment route "
                "is a configuration rejected back to the recruiter."
            )
        out[name] = category
    return out


async def evaluate_application(
    session: Any,
    *,
    job: Any,
    link: Any,
    item_scores: Mapping[str, float],
    subject_names: Sequence[str] = (),
    invoke: Any = None,
    review_disposition: str | None = None,
    review_decided_by: Any = None,
) -> LiveEvaluation:
    """Run stages 2 to 6 for one application. RAISES when G1 cannot be met.

    `item_scores` is {matrix item name: internal score}, the per-ITEM result of
    the product's own rubric scoring. It supplies section 12.1's trigger, which
    is a named competency failing its minimum -- keyed by ITEM, never by
    dimension. That distinction was a real defect: keying it on a
    dimension-to-category table produced an empty Must-have grade for a job
    whose essentials all sat on one dimension, and the cap had nothing to bind
    against.
    """
    matrix = await frozen_matrix(session, getattr(job, "id", None))
    items = _matrix_items(matrix)
    if not items:
        raise ScorecardUnavailable(
            "the frozen matrix has no items. A stamp is not evidence that "
            "generation happened; gate G1 asks the table."
        )
    categories = _competency_categories(items)

    views, mapping, lost = await gather_evidence(
        session,
        tenant_id=job.tenant_id,
        job_id=job.id,
        link_id=link.id,
        subject_names=subject_names,
    )

    must_haves = [
        name
        for name, category in categories.items()
        if category == aggregation.CATEGORY_MUST_HAVE
    ]
    # THE TWO NAME SPACES MUST BE THE SAME ONE. `item_scores` is keyed by the
    # matrix item names the product scored, and `categories` by the frozen
    # matrix's. If they have drifted apart, every section 12.1 lookup misses,
    # every Must-have reads as unscored, and the caps quietly stop binding --
    # which is the silent failure this whole phase exists to end. So a total
    # disjunction is refused rather than absorbed. A PARTIAL overlap is not an
    # error: a Must-have with no answers legitimately has no score, and section
    # 14.1 reports it Unassessed on the evidence tier.
    if must_haves and item_scores and not set(must_haves) & set(item_scores):
        raise ScorecardUnavailable(
            "the frozen matrix's Must-have names and the scored item names do "
            "not overlap at all, so no competency threshold could ever be "
            "evaluated. Matrix: "
            f"{sorted(must_haves)}; scored: {sorted(item_scores)}."
        )

    grades: dict[str, str] = {}
    scores: dict[str, float] = {}
    for name in must_haves:
        score = item_scores.get(name)
        if score is None:
            # Not scored is not zero. The item has no grade, section 14.1 will
            # report it Unassessed on the evidence tier, and inventing a grade
            # here would put a number under a control that must read a fact.
            continue
        scores[name] = float(score)
        grades[name] = rating.grade_for_percent(score) or rating.GRADE_NOT

    dimensions_by_competency: dict[str, str] = {}
    weights: dict[str, float] = {}
    for item in items:
        name = str(item.competency)
        dimension = getattr(item, "dimension", None)
        if dimension:
            dimensions_by_competency[name] = str(dimension)
        weight = getattr(item, "weight", None)
        if weight is not None:
            weights[name] = float(getattr(weight, "value", weight))

    # SECTION 12.1's MINIMUM COMES FROM THE GRADE, NOT FROM THE MATRIX, and the
    # reason is that today's frozen matrix does not carry one.
    # `MatrixItem.threshold` is `transformation.Threshold.as_dict()`:
    # `independence_required` (a count of supporting pieces),
    # `level` (a MULTIPLIER around 1.0 on an unstated platform floor) and
    # `max_age_days`. None of those is a minimum SCORE on the 0 to 100 scale
    # section 12.1 speaks about, and reading `level` as one would compare a
    # candidate's 0 to 100 score against a number near 1.0 and pass every
    # candidate silently -- a control that looks implemented and enforces
    # nothing, which is the exact failure mode this phase exists to end.
    #
    # So `must_have_thresholds` is deliberately left empty here and section
    # 12.1's minimum is the product's published floor for a criterion the
    # hiring manager declared essential: the item grades Not Matching.
    # SOURCE: RPN-PHIL-001 section 12.1 (v1.3), "A competency threshold has no
    # Layer 1 default, and that is deliberate." The Hiring Manager proposes it
    # at intake and the HR Manager approves it; a platform default would be a
    # minimum applied to every role regardless of what the role needs, which is
    # the free assignment section 20.3 forbids one paragraph later.
    #
    # So this reads what the matrix declared and caps nothing where nothing was
    # declared. That is not silently uncapped: section 12.2's dimension floors
    # and section 14.1's unassessed-Must-have rule both still apply, and both
    # are EVIDENCE-based rather than score-based, which is what catches the case
    # a missing threshold would otherwise let through.
    thresholds: dict[str, float] = {
        item.competency: float(item.threshold)
        for item in getattr(matrix, "items", ())
        if getattr(item, "threshold", None) is not None
    }

    inputs = pipeline.EvaluationInputs(
        matrix=categories,
        competency_dimensions=dimensions_by_competency,
        competency_weights=weights,
        evidence=views,
        evidence_competencies=mapping,
        role_context=_role_context(job),
        matrix_items=[_as_dict(item) for item in items],
        scorecard_approved_at=_approved_at(matrix),
        must_have_grades=grades,
        must_have_scores=scores,
        must_have_thresholds=thresholds,
        contradiction_report=await _contradictions(session, job=job, link=link),
        review_disposition=review_disposition,
        review_decided_by=review_decided_by,
    )
    outcome = await pipeline.evaluate(inputs, invoke=invoke or _invoke)
    if lost:
        logger.warning(
            "miti.live.evidence_unresolved link_id=%s count=%s",
            getattr(link, "id", None), len(lost),
        )
    return LiveEvaluation(
        outcome=outcome,
        unresolved_evidence=lost,
        evidence_count=len(views),
        matrix=matrix,
    )


def _as_dict(item: Any) -> dict[str, Any]:
    """A matrix item as G1's `scorecard_gate` reads it.

    `as_dict` when the item offers one, because the owning module knows its own
    shape better than this one does; a minimal projection otherwise, so a
    contract change on the other side does not silently produce empty items and
    a passing gate.
    """
    projection = getattr(item, "as_dict", None)
    if callable(projection):
        return dict(projection())
    return {
        "competency": str(getattr(item, "competency", "")),
        "category": str(getattr(item, "category", "")),
        "dimension": str(getattr(item, "dimension", "")),
        "assessment_method": str(getattr(item, "assessment_method", "")),
    }


async def _contradictions(session: Any, *, job: Any, link: Any) -> Any:
    """The contradiction report stage 5 triangulates.

    Read from the same ledger the evidence came from. An empty ledger yields an
    empty report, which is correct: nothing recorded is not the same as nothing
    disagreeing, and the difference is paid for in coverage and confidence
    rather than manufactured into a finding here.
    """
    from app.services.evidence import contradictions, ledger

    claims = await ledger.load_claims(
        session, tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
    )
    if not claims:
        return contradictions.ContradictionReport()
    return contradictions.detect(
        claims=claims, phase=contradictions.PHASE_POST_CONVERSATION
    )


async def _invoke(task: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
    """The real model call, injected into the pipeline.

    Imported inside the function for the same reason the scorecard is: keeping
    `llm_router` out of this module's import graph is what lets the AST test
    over `aggregation.py` and `pipeline.py` mean something.
    """
    from app.services import llm_router

    return await llm_router.chat_completion(
        task, messages, response_format_json=bool(kwargs.get("response_format_json"))
    )
