"""Sutra on the live path: the seven stages, the frozen matrix, and gate G1.

WHAT THIS MODULE IS
-------------------
The one place a job's Tatva matrix is BUILT, FROZEN and READ BACK. spec-doc6
§4.3 activates the job-setup pipeline, and this is the module the activation
runs through:

    Bodha's validated SWOT artifact (Layer 3)
  + the department model and its rubric anchors (Layer 1)
        -> `compile_matrix`   seven stages, one row per item, provenance stored
        -> `freeze`           the Hiring Manager's explicit finalisation
        -> `require_frozen_matrix`   which IS gate G1

WHAT IT REPLACED, AND WHY THAT CODE IS GONE
---------------------------------------------
`ppi.generate_framework` asked one model, in one pass, for a whole matrix, and
fell back to a deterministic matrix assembled from the JD's own noun phrases
when the model was unavailable. Both halves are now deleted rather than flagged
off, per spec-doc6 D1 and §4.1.

The single-pass generator had no way to satisfy the requirement this phase is
built around: "every item in the frozen matrix stores which Layer 1 / Layer 3
input produced its weight and the multiplier each contributed". A weight
a model chose in one pass has no terms to store. The fallback was worse in a
quieter way -- it produced a matrix that LOOKED like the real thing, was graded
against for the life of the job, and rested on nothing but the JD's own words,
which §18 says is "almost never an accurate specification of the hiring
problem".

So there is no fallback here. A missing or rejected Layer 3 artifact, or a
naming step that could not run, are all refusals that
name what is missing and what to do about it. That is spec-doc6 §4.1: "If
retrieval returns nothing, if a required artifact is missing, if a gate fails:
raise, audit, and surface an actionable message."

WHERE THE COMPETENCIES COME FROM
---------------------------------
Runbook Part VI states the rule once and it governs the whole selection: "the
competency list in each model is the MENU. The scorecard for a given role
selects at most six from it, weighted by SWOT force-ranking. No role uses the
whole menu."

So the SWOT does not have to supply names. It supplies EMPHASIS, and §18.1 says
what each quadrant's emphasis is:

    Weaknesses     the gap competencies, "the highest-weighted items"  -> Must-have
    Strengths      "competencies to deprioritise"                      -> Nice-to-have
    Opportunities  "trajectory and adjacency signals to reward"        -> Nice-to-have
    Threats        "risk probes, thresholds, disqualifiers"            -> Behavioural

Where an aspect would otherwise be empty, the remaining competencies come
from the Layer 1 department menu with `swot_origin` NULL, which is an honest
provenance ("Layer 1 only") rather than an invented Layer 3 input.

THE CATEGORY COMES FROM THE ITEM
---------------------------------
Never from a dimension-to-category table. That defect is on record: keying the
composite on `dimension -> category` produced an EMPTY Must-have grade for any
job whose essentials all sat on one dimension, and the Must-have hard cap then
had nothing to bind against. `MatrixItem.category` is stored per row and is
what every consumer reads.

WHAT CROSSES A CLIENT BOUNDARY
-------------------------------
Nothing numeric. `MatrixItem.weight` and `.threshold` are internal ranking data
of the same status `report_dimensions.required_level` has always had. The
Hiring Manager's review screen reads `plain_provenance`, which is sentences.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import JobCompetency
from app.models.job_scorecard_binding import JobScorecardBinding
from app.models.job import Job
from app.models.job_setup import SWOT_AREAS, JobSwotAnalysis
from app.prompts import fragments, registry
from app.services import agent_loop, llm_router, ppi
from app.services.hiring import (
    gates,
    layers,
    pipeline_halt,
    situations,
    swot_quality,
    transformation,
)
from app.services.hiring.department_models import DepartmentModel, department_for

logger = logging.getLogger(__name__)

__all__ = [
    "MatrixItem",
    "FrozenMatrix",
    "CompileResult",
    "ScorecardError",
    "ScorecardInputMissing",
    "ScorecardNotFrozen",
    "QUADRANT_CATEGORY",
    "SCORED_CATEGORIES",
    "compile_matrix",
    "freeze",
    "load_frozen_matrix",
    "require_frozen_matrix",
    "item_from_row",
    "plain_provenance",
    "scorecard_version",
]


# ── §18.1: which quadrant produces which aspect ──────────────────────────────
#
# ONE mapping, held in `swot_quality` because Bodha's §18.5 quality control and
# Sutra's categorisation both read it. Re-exported here so a reader of this
# module can find it without knowing which of the two owns it.
QUADRANT_CATEGORY: dict[str, str] = swot_quality.QUADRANT_CATEGORY

# The two modules must agree with `ppi`'s aspect names or a matrix item would be
# written under a category no consumer reads. Asserted at import rather than
# tested, because the failure is a silent miscategorisation and the cost of
# checking is one set comparison per process.
assert set(QUADRANT_CATEGORY.values()) <= set(ppi.CATEGORIES)

#: The aspects §20.1's scorecard table actually holds, and therefore the ones
#: §20.2's six-competency ceiling and §20.3's force-ranking apply to. §20.1
#: prints Must / Nice rows only; Behavioural is this product's third
#: client-facing dimension and spec-doc6 states in its own scope line that it
#: "does not touch Tatva Assessment's three product-facing dimensions".
#:
#: SOURCE: RPN-PHIL-001 §20.2 with §20.3 (v1.3): the Runbook writes "maximum six" of a scorecard
#: that has no behavioural rows in it, and says nothing about a product whose
#: matrix has a third aspect. Behavioural is bounded here too, by the same
#: ceiling and additionally by the grade's own question ceiling, which is the
#: reading that restricts more. Recorded in RUNBOOK_OPEN_QUESTIONS.md.
SCORED_CATEGORIES: tuple[str, ...] = (
    ppi.CATEGORY_MUST_HAVE,
    ppi.CATEGORY_NICE_TO_HAVE,
)

#: How strongly each quadrant emphasises its competency, as a multiplier passed
#: through `layers.resolve` (which clamps it, records the clamp, and refuses to
#: let a Layer 3 input exceed the declared bound).
#:
#: THE ENDPOINTS ARE READ OFF `layers.BOUNDS`, not written here. §18.1 states
#: the DIRECTION for each quadrant and no magnitude anywhere, so "the highest
#: weighted" is expressed as the top of the declared bound and "deprioritise" as
#: the bottom of it. A literal would be a magic number with no section behind
#: it, and worse, one that could silently disagree with the bound it is meant to
#: sit inside.
def _quadrant_emphasis() -> dict[str, float]:
    bound = layers.BOUNDS["competency_weight"]
    return {
        # §18.1: the gap competencies, the highest-weighted items.
        "weaknesses": bound.high,
        # §19.2: a team strength REDUCES the weight, because the hire does not
        # need to supply it.
        "strengths": bound.low,
        # Neither quadrant states a magnitude, and the identity is what "no
        # opinion expressed" has to mean here. The trajectory emphasis an
        # opportunity implies is already carried by the SITUATION TYPE, which
        # acts on the dimension (§18.4); applying it twice would double-count
        # one statement.
        "opportunities": 1.0,
        "threats": 1.0,
    }


# ── Errors ───────────────────────────────────────────────────────────────────


class ScorecardError(RuntimeError):
    """Base class. Every refusal in this module names what is missing."""


class ScorecardInputMissing(ScorecardError):
    """A layer Sutra cannot compile without is absent or unusable.

    RAISED, never worked around. spec-doc6 §4.1 forbids a default weight
    standing in for a failed retrieval. `detail` is written for the
    person who has to act on it, because the actionable half of this refusal is
    always "go and complete the thing that is missing".
    """

    def __init__(self, layer: str, detail: str) -> None:
        super().__init__(detail)
        self.layer = layer
        self.detail = detail


class ScorecardNotFrozen(ScorecardError):
    """GATE G1. This job has no approved, frozen scorecard.

    Carries the `GateResult` so a caller renders the gate's own reasons rather
    than paraphrasing them, and so an audit row records which of G1's two
    conditions failed: no items at all, or items nobody approved. Those are
    different operational problems with different fixes and this codebase has
    already paid for confusing them once, when nineteen live jobs carried a
    generation timestamp and zero competency rows.
    """

    def __init__(self, job_id: Any, result: gates.GateResult) -> None:
        super().__init__(
            f"Job {job_id} has no approved, frozen scorecard. "
            + " ".join(result.reasons)
        )
        self.job_id = job_id
        self.result = result


# ── The contract every downstream stage codes against ────────────────────────


@dataclass(frozen=True)
class MatrixItem:
    """One frozen criterion, with all seven stages and its provenance.

    FROZEN, and read-only by construction. A consumer that could mutate an item
    could change the criteria a candidate was graded against after the fact,
    which is the one property the freeze exists to guarantee.
    """

    competency_id: uuid.UUID
    #: Stage 1.
    competency: str
    #: must_have / nice_to_have / behavioural. FROM THE ITEM, never derived from
    #: `dimension`. See the module docstring.
    category: str
    #: The internal dimension this competency speaks to. Routing information for
    #: Miti's five evaluators; never rendered, never named to a client.
    dimension: str
    #: Stage 2.
    observable_evidence: str
    #: Stage 3.
    evidence_sources: tuple[str, ...]
    #: Stage 4.
    assessment_method: str
    #: Stage 5. Internal ranking data, normalised across the scored aspects.
    weight: float
    #: Stage 6, as `transformation.Threshold.as_dict()`.
    threshold: Mapping[str, Any]
    #: Stage 7, the only optional stage.
    disqualifier: str | None
    #: Which Layer 1 / Layer 2 / Layer 3 input produced the weight, and the
    #: multiplier each contributed. The acceptance criterion of spec-doc6 §4.3.
    provenance: Mapping[str, Any]
    #: The hiring manager's own sentence, when a Layer 3 input produced this.
    swot_origin: str | None
    #: The Layer 1 competency stage 1 named this from, or None.
    anchor_key: str | None
    #: §20.3's position in the force-ranking, 1..n within the scored aspects.
    force_rank: int | None
    #: The client-facing requirement WORD. Never the stored integer.
    required_level: str
    ordinal: int


@dataclass(frozen=True)
class FrozenMatrix:
    """One job's approved, frozen Tatva matrix.

    The exact object `require_frozen_matrix` hands to the scoring and report
    stages. Everything a downstream stage needs to know which criteria were in
    force, and which versions of which layers produced them, without a second
    query and without a chance of reading a later version by accident.
    """

    job_id: uuid.UUID
    tenant_id: uuid.UUID
    #: §20.5's scorecard version. 1 on the first freeze, then 2, 3 and so on.
    version: int
    approved_at: datetime
    #: The confirmed §18.4 situation type, or None for a job whose intake
    #: predates the classification step.
    situation_key: str | None
    #: spec-doc6 §4.1's one id per flow.
    correlation_id: str | None
    items: tuple[MatrixItem, ...]

    def by_category(self, category: str) -> tuple[MatrixItem, ...]:
        return tuple(item for item in self.items if item.category == category)

    @property
    def must_have(self) -> tuple[MatrixItem, ...]:
        return self.by_category(ppi.CATEGORY_MUST_HAVE)

    def as_dict(self) -> dict[str, Any]:
        """INTERNAL. Carries weights, so it never crosses an API boundary."""
        return {
            "job_id": str(self.job_id),
            "version": self.version,
            "approved_at": self.approved_at.isoformat(),
            "situation_key": self.situation_key,
            "correlation_id": self.correlation_id,
            "items": [
                {
                    "competency": item.competency,
                    "category": item.category,
                    "dimension": item.dimension,
                    "weight": round(item.weight, 6),
                    "threshold": dict(item.threshold),
                    "evidence_sources": list(item.evidence_sources),
                    "assessment_method": item.assessment_method,
                    "disqualifier": item.disqualifier,
                    "provenance": dict(item.provenance),
                    "force_rank": item.force_rank,
                }
                for item in self.items
            ],
        }


def item_from_row(row: JobCompetency) -> MatrixItem | None:
    """One stored row as a `MatrixItem`, or None if it never ran the stages.

    None rather than a filled-in default. A row written by the retired
    single-pass generator has no dimension, no weight and no provenance, and
    substituting values for them would present a criterion nobody derived as
    though the pipeline had derived it. `load_frozen_matrix` turns the None into
    a refusal naming the job.
    """
    if not row.dimension or row.weight is None or not row.observable_evidence:
        return None
    return MatrixItem(
        competency_id=row.id,
        competency=row.name,
        category=row.category,
        dimension=row.dimension,
        observable_evidence=row.observable_evidence,
        evidence_sources=tuple(row.evidence_sources or ()),
        assessment_method=row.assessment_method or "",
        weight=float(row.weight),
        threshold=dict(row.threshold_json or {}),
        disqualifier=row.disqualifier,
        provenance=dict(row.provenance_json or {}),
        swot_origin=row.swot_origin,
        anchor_key=row.anchor_key,
        force_rank=row.force_rank,
        required_level=ppi.requirement_word(row.required_level),
        ordinal=row.ordinal,
    )


async def _latest_binding(
    session: AsyncSession, job_id: uuid.UUID
) -> JobScorecardBinding | None:
    """The freeze in force: the highest `freeze_sequence` for this job.

    The bindings are append-only, so this reads the current one and the earlier
    rows stay as the record of what every previously assessed candidate was
    graded under.
    """
    return (
        await session.execute(
            select(JobScorecardBinding)
            .where(JobScorecardBinding.job_id == job_id)
            .order_by(JobScorecardBinding.freeze_sequence.desc())
            .limit(1)
        )
    ).scalars().first()


async def load_frozen_matrix(
    session: AsyncSession, job_id: uuid.UUID
) -> FrozenMatrix | None:
    """This job's frozen matrix, or None when it has none.

    None means one of three things and the caller does not need to tell them
    apart: the job has no matrix, its matrix has not been approved, or its rows
    predate the seven-stage pipeline. `require_frozen_matrix` is what turns any
    of them into a refusal with the reason attached.
    """
    job = await session.get(Job, job_id)
    if job is None or job.framework_approved_at is None:
        return None
    rows = await ppi.load_framework(session, job_id)
    items = [item for item in (item_from_row(row) for row in rows) if item is not None]
    if not items or len(items) != len(rows):
        # A PARTIAL matrix is refused as hard as an absent one. Half the
        # criteria carrying derivations and half not would mean a candidate
        # graded against a mixture, and no consumer could tell which half it
        # had.
        if rows:
            logger.warning(
                "scorecard.pre_pipeline_rows job_id=%s rows=%d derived=%d",
                job_id,
                len(rows),
                len(items),
            )
        return None
    binding = await _latest_binding(session, job_id)
    # The frozen rows are the record of what was approved. Historical matrices
    # retain their situation in provenance even when the old intake is retired.
    situation_key = next(
        (item.provenance.get("situation_key") for item in items
         if item.provenance.get("situation_key")),
        None,
    )
    return FrozenMatrix(
        job_id=job.id,
        tenant_id=job.tenant_id,
        version=int(binding.scorecard_version) if binding else 1,
        approved_at=job.framework_approved_at,
        situation_key=situation_key,
        correlation_id=job.correlation_id,
        items=tuple(items),
    )


async def require_frozen_matrix(
    session: AsyncSession, job_id: uuid.UUID
) -> FrozenMatrix:
    """GATE G1. Raises `ScorecardNotFrozen` unless this job has one.

    THIS IS THE GATE, not a wrapper around it. `gates.scorecard_gate` states the
    rule; this function is the only place the rule is applied to a real job on a
    live path, which is what closes the gap spec-doc6 §4.3 names: G1 existed and
    its only caller was a module nothing imported.

    Every stage that scores, grades or reports on a candidate calls this FIRST,
    before reading a resume or a transcript. Ordering, not politeness: a refusal
    that ran the work first has already spent the credit it was refusing.
    """
    matrix = await load_frozen_matrix(session, job_id)
    if matrix is not None:
        return matrix
    job = await session.get(Job, job_id)
    rows = await ppi.load_framework(session, job_id) if job is not None else []
    result = gates.scorecard_gate(
        matrix_items=[{"name": row.name} for row in rows],
        approved_at=job.framework_approved_at if job is not None else None,
        frozen=bool(job and job.framework_approved_at),
    )
    if result.passed:
        # G1's two conditions are met and the matrix still did not load, which
        # can only mean the rows predate the seven-stage pipeline. Stated as its
        # own reason rather than folded into the gate's, because the fix is
        # different: this job needs re-defining, not approving.
        result = gates.GateResult(
            gates.G1,
            False,
            blocking=True,
            reasons=(
                "This job's criteria were written by the retired single-pass "
                "generator and carry no derivation. They cannot be scored "
                "against: reopen the job's setup and let Sutra rebuild the "
                "matrix from the SWOT.",
            ),
        )
    raise ScorecardNotFrozen(job_id, result)


def scorecard_version(binding: JobScorecardBinding | None) -> int:
    """The version the NEXT freeze will carry. 1 when there has been none."""
    return int(binding.scorecard_version) + 1 if binding else 1


# ── Sutra: the seven stages ──────────────────────────────────────────────────


@dataclass
class CompileResult:
    """What one compile produced, and everything it refused.

    `rejections` is not a log line. A SWOT phrase that could not be transformed
    is a thing the hiring manager said and the platform declined to grade
    anybody on, and they are entitled to see which and why -- a matrix quietly
    shorter than the intake it came from is how a criterion somebody cared
    about disappears without anyone noticing.
    """

    items: list[JobCompetency]
    rejections: list[dict[str, Any]]
    situation_key: str | None
    department: str
    correlation_id: str | None
    degraded_naming: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": len(self.items),
            "rejections": list(self.rejections),
            "situation_key": self.situation_key,
            "department": self.department,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class _Candidate:
    """One thing that might become a matrix item, before the stages run."""

    phrase: str
    category: str
    quadrant: str | None
    swot_origin: str | None
    #: Set only once stage 1 has named it (or the Layer 1 anchor did).
    observable: str | None = None


async def _layer3(session: AsyncSession, job: Job) -> tuple[dict[str, list[str]], int]:
    """Read the saved Job SWOT document that the hiring team can review.

    The retired intake row is deliberately never queried here. An existing job
    with historic intake data follows the same path as a newly created job.
    Each nonempty paragraph is a source phrase, with the section recorded on
    every matrix item's provenance. Sutra still refuses phrases it cannot turn
    into observable criteria rather than inventing a competency.
    """
    analysis = (
        await session.execute(
            select(JobSwotAnalysis).where(JobSwotAnalysis.job_id == job.id)
        )
    ).scalar_one_or_none()
    if analysis is None or not analysis.has_content:
        raise ScorecardInputMissing(
            "layer3",
            "Save a Job SWOT Analysis before building the evaluation matrix.",
        )
    captured = {
        area: [text.strip() for text in (getattr(analysis, area) or "").split("\n\n")
               if text.strip()]
        for area in SWOT_AREAS
    }
    return captured, analysis.version


def _candidates_from_swot(
    captured: Mapping[str, Sequence[str]],
) -> list[_Candidate]:
    """Every SWOT point, in §18.1's quadrant order, tagged with its aspect."""
    out: list[_Candidate] = []
    for quadrant in SWOT_AREAS:
        category = QUADRANT_CATEGORY[quadrant]
        for point in captured.get(quadrant) or ():
            text = str(point or "").strip()
            if text:
                out.append(
                    _Candidate(
                        phrase=text,
                        category=category,
                        quadrant=quadrant,
                        swot_origin=text,
                    )
                )
    return out


def _candidates_from_menu(
    model: DepartmentModel,
    seniority: str,
    *,
    category: str,
    used: set[str],
    wanted: int,
) -> list[_Candidate]:
    """Layer 1 menu competencies, for an aspect the higher layers left empty.

    Part VI: "the competency list in each model is the menu". Drawing from it is
    Layer 1 doing its declared job, not a fallback: these items carry
    `swot_origin` NULL and an `anchor_key`, so their provenance reads "Layer 1
    department model, no Layer 3 input" -- which is exactly what happened.
    """
    from app.services.hiring.department_models import baseline_for  # noqa: PLC0415

    picked: list[_Candidate] = []
    for competency in sorted(
        baseline_for(model, seniority),
        key=lambda c: (-c.baseline_weight, c.key),
    ):
        if len(picked) >= wanted:
            break
        if competency.name.casefold() in used:
            continue
        used.add(competency.name.casefold())
        picked.append(
            _Candidate(
                phrase=competency.name,
                category=category,
                quadrant=None,
                swot_origin=None,
                observable=competency.observable_evidence,
            )
        )
    return picked


#: What the naming prompt is told `function_strategic_context` is for, sent
#: ONLY when a Drishti profile actually supplies lines. Separated from the
#: prompt file's own body so the rendered instruction is byte-identical to
#: what it was before Drishti existed whenever the function has no profile:
#: an enhancement layer that changed every prompt in the product on the days
#: it supplied nothing would not be one.
_STRATEGIC_CONTEXT_RULE = (
    "\n\nYou may also be given \"function_strategic_context\": a few "
    "statements the head of this function wrote about what the function is "
    "for and what it rewards. It is BACKGROUND for choosing between two "
    "honest names for a phrase you were already given, and for nothing else. "
    "It is not a source of phrases. Never convert a line of it into a "
    "competency, never let it add a requirement nobody stated, and if it "
    "conflicts with the phrase you are naming, the phrase wins."
)


def _naming_payload(
    job: Job,
    pending: Sequence[_Candidate],
    model: DepartmentModel,
    seniority: str,
    strategic_context: Sequence[str],
) -> str:
    """The user message for the naming call.

    Pulled out of `_name_unanchored` so the ONE property Drishti has to keep
    is testable without a model or a database: with no profile, the key is
    ABSENT rather than present and empty, so the request is byte-identical to
    what it was before this layer had a supplier. A key carrying `[]` would
    still be a different prompt, and "the platform still works without a
    profile" has to mean the same bytes, not merely the same intent.
    """
    payload: dict[str, Any] = {
        "job_title": job.title,
        "seniority": seniority,
        "department": model.label,
        "known_competencies": [c.name for c in model.for_seniority(seniority)],
        "phrases": [
            {"index": index, "text": candidate.phrase}
            for index, candidate in enumerate(pending)
        ],
    }
    if strategic_context:
        payload["function_strategic_context"] = list(strategic_context)
    return json.dumps(payload)


async def _name_unanchored(
    session: AsyncSession,
    job: Job,
    pending: Sequence[_Candidate],
    model: DepartmentModel,
    seniority: str,
    strategic_context: Sequence[str] = (),
) -> tuple[dict[int, tuple[str, str]], list[dict[str, Any]], bool]:
    """Stages 1 and 2 for the phrases the department menu has no anchor for.

    Returns (named-by-index, refusals, degraded). ONE model call for the whole
    batch: naming is a judgment about each phrase in isolation, so batching
    changes nothing about the answer and turns N interactive calls into one.

    THERE IS NO FABRICATED FALLBACK. `run_loop` takes an empty result as its
    fallback, and an empty result means every phrase in the batch is REFUSED and
    recorded, with the reason stated. That is spec-doc6 §4.1: a stage that
    cannot run raises and surfaces an actionable message rather than inventing a
    criterion every candidate on the job would then be graded against.

    `strategic_context` is Drishti (vivekium C3), and it is the ONE thing in
    this prompt the client wrote. It arrives already derived from the compiled
    artifact, already through the observable-evidence detector and already
    capped in lines and characters (`drishti.prompt_context`); raw section
    text cannot reach here, which is the property the 2026-09-09 removal was
    about. Empty is the normal case and costs nothing: no key in the payload,
    no clause in the instruction, the same bytes as before.
    """
    if not pending:
        return {}, [], False

    payload = _naming_payload(job, pending, model, seniority, strategic_context)
    system_prompt = registry.render(
        "sutra_competency_naming",
        authority_text_is_data=fragments.AUTHORITY_TEXT_IS_DATA,
        strategic_context_rule=(
            _STRATEGIC_CONTEXT_RULE if strategic_context else ""
        ),
    )

    async def _execute(reflection: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.chat_completion(
            "jd_generation", messages, response_format_json=True, session=session
        )
        # `json.loads` is typed Any; name the shape once here rather than
        # returning Any out of a function that declares a dict.
        parsed: dict[str, Any] = json.loads(raw)
        return parsed

    def _evaluate(candidate: dict[str, Any]) -> agent_loop.Critique:
        """Deterministic, and it checks the two things that matter.

        A name that is a whole sentence is a name that cannot be rendered in a
        matrix column, and an observable statement that is an adjective is
        exactly what §18.5 rule 4 refuses from a human. The same detector is
        used on both, so the bar the hiring manager is held to is the bar the
        model is held to.
        """
        from app.services.hiring import (  # noqa: PLC0415
            observable as observable_detector,
        )

        named = candidate.get("named")
        if not isinstance(named, list):
            return agent_loop.reject(
                'return a "named" list, one entry per phrase you could convert'
            )
        for entry in named:
            if not isinstance(entry, dict):
                return agent_loop.reject("every entry in \"named\" is an object")
            name = str(entry.get("competency") or "").strip()
            observable = str(entry.get("observable") or "").strip()
            if not name or len(name.split()) > _MAX_NAME_WORDS:
                return agent_loop.reject(
                    f"{name!r} is not a short competency name; give a capability "
                    f"of at most {_MAX_NAME_WORDS} words, not a sentence"
                )
            if not observable or not observable_detector.is_observable(observable):
                return agent_loop.reject(
                    f"the observable statement for {name!r} is not something "
                    f"anyone could have watched happen; state an action taken, "
                    f"a decision made and defended, or an artefact produced"
                )
        return agent_loop.ok()

    result: agent_loop.LoopResult[dict[str, Any]] = await agent_loop.run_loop(
        name="sutra_competency_naming",
        execute=_execute,
        evaluate=_evaluate,
        fallback={"named": [], "refused": []},
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )

    named: dict[int, tuple[str, str]] = {}
    for entry in result.value.get("named") or []:
        try:
            index = int(entry["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < len(pending):
            named[index] = (
                str(entry.get("competency") or "").strip()[:_MAX_NAME],
                str(entry.get("observable") or "").strip(),
            )

    refusals: list[dict[str, Any]] = []
    stated = {
        int(entry["index"]): str(entry.get("reason") or "").strip()
        for entry in (result.value.get("refused") or [])
        if isinstance(entry, dict) and str(entry.get("index", "")).isdigit()
    }
    for index, candidate in enumerate(pending):
        if index in named:
            continue
        refusals.append(
            {
                "phrase": candidate.phrase,
                "category": candidate.category,
                "stage": transformation.STAGE_COMPETENCY,
                "reason": stated.get(index)
                or (
                    "Sutra could not name a competency for this without "
                    "inventing one, so nothing was added to the matrix for it. "
                    "Rephrase it as something a candidate could be seen doing, "
                    "or leave it as context."
                ),
            }
        )
    if result.degraded:
        logger.warning(
            "scorecard.naming_degraded job_id=%s attempts=%d refused=%d reasons=%s",
            job.id,
            result.attempts,
            len(refusals),
            list(result.reasons),
        )
    return named, refusals, result.degraded


#: A competency name is a column heading, not a sentence. Five words is what
#: §20.1's own scorecard table uses ("Production cloud infrastructure
#: ownership", "Distributed systems debugging", "Migration execution under
#: deadline"), so the bar is read off the Runbook's own examples rather than
#: chosen.
_MAX_NAME_WORDS = 5

#: `job_competencies.name` is String(255).
_MAX_NAME = 255


def _rank_and_normalise(
    built: Sequence[transformation.Item],
) -> dict[int, tuple[int | None, float]]:
    """§20.3's force-ranking, as an ORDER over the derived weights.

    SOURCE: RPN-PHIL-001 §20.3. §20.3 says "weights derive from the ranking, not
    from free assignment" and gives a default distribution for counts of four,
    five and six. Read literally, that would replace the derived weight with a
    figure that depends only on POSITION -- at which point Layer 1's baseline
    and Layer 3's emphasis stop being observable in the output at all, and the
    §11 layering the rest of the Runbook specifies has nowhere to land.

    What §20.3 is guarding against is stated in its own next sentence: "Weight
    assignment by free choice reliably produces five items at 'high
    importance,' which is the same as no ranking at all." A weight derived
    arithmetically from three declared layers is not free assignment, and it
    cannot flatten: the ranking below is total, ties are broken deterministically
    so no two items share a rank, and the ordering is never flattened.

    So the implementation keeps the derived weight, NORMALISES the scored
    aspects to sum to 1.0 (which is what §20.1's own table does: 0.35 + 0.25 +
    0.20 + 0.12 + 0.08 = 1.00), and records the rank the weights imply. The raw
    product and its four terms stay in `provenance`, so a reviewer can see both
    the share and the arithmetic behind it. Recorded in
    RUNBOOK_OPEN_QUESTIONS.md.
    """
    scored = [
        (index, item)
        for index, item in enumerate(built)
        if item.category in SCORED_CATEGORIES
    ]
    # Total order. A tie broken by name is arbitrary; a tie LEFT is a rank two
    # items share, which §20.3 forbids in as many words ("No ties.").
    scored.sort(key=lambda pair: (-pair[1].weight.value, pair[1].name.casefold()))
    out: dict[int, tuple[int | None, float]] = {}
    total = sum(item.weight.value for _index, item in scored)
    for rank, (index, item) in enumerate(scored, start=1):
        share = item.weight.value / total if total > 0 else 0.0
        out[index] = (rank, share)
    # Behavioural carries no force-rank, because §20.1's scorecard has no
    # behavioural row to rank. Its weights are normalised among themselves so
    # the aspect's internal ordering is still meaningful.
    behavioural = [
        (index, item)
        for index, item in enumerate(built)
        if item.category not in SCORED_CATEGORIES
    ]
    b_total = sum(item.weight.value for _index, item in behavioural)
    for index, item in behavioural:
        out[index] = (None, item.weight.value / b_total if b_total > 0 else 0.0)
    return out


def _required_level(item: transformation.Item) -> str:
    """The client-facing requirement WORD for one item.

    Driven by the CATEGORY, which is a property of the criterion the hiring
    manager declared essential, and never by the dimension. A Must-have is
    something the role cannot be performed without, so the job requires it
    highly; a Nice-to-have "materially strengthens performance but is not
    disqualifying"; a Behavioural competency is a demand of the role rather than
    a differentiator.
    """
    from app.services.rating import GRADE_HIGHLY, GRADE_MATCHING  # noqa: PLC0415

    return GRADE_HIGHLY if item.category == ppi.CATEGORY_MUST_HAVE else GRADE_MATCHING


async def compile_matrix(
    session: AsyncSession,
    job: Job,
    *,
    actor_user_id: uuid.UUID | None,
    correlation_id: str | None = None,
    replace: bool = False,
) -> CompileResult:
    """Sutra, end to end. Seven stages per item, three layers, no fallback.

    Refuses -- loudly, with a message somebody can act on -- when a layer is
    missing. Refuses per item, and records what it refused, when one phrase
    cannot be transformed: one un-nameable sentence must not cost the matrix the
    other eleven, which is the same partial-success reasoning the databank bulk
    upload uses.

    APPROVES NOTHING. The matrix it writes is a draft the Hiring Manager
    reviews. `freeze` is the only thing that stamps an approval, and it is
    reached only from an explicit human act.
    """
    await pipeline_halt.enforce(
        pipeline_halt.STAGE_SUTRA_MATRIX,
        tenant_id=job.tenant_id,
        actor_user_id=actor_user_id,
        job_id=job.id,
        correlation_id=correlation_id or job.correlation_id,
        agent="sutra",
    )

    existing = (
        await session.execute(
            select(JobCompetency)
            .where(JobCompetency.job_id == job.id)
            .order_by(JobCompetency.ordinal)
        )
    ).scalars().all()
    active = [row for row in existing if row.is_active]
    if active and not replace:
        # Idempotent by default, so a redelivery cannot discard a matrix
        # a human has already edited.
        binding = await _latest_binding(session, job.id)
        return CompileResult(
            items=list(active),
            rejections=[],
            situation_key=next(
                (row.provenance_json.get("situation_key") for row in active
                 if row.provenance_json and row.provenance_json.get("situation_key")),
                None,
            ),
            department=job.department or "",
            correlation_id=correlation_id or job.correlation_id,
        )
    if job.framework_approved_at is not None:
        raise ScorecardInputMissing(
            "frozen",
            "This job's criteria are frozen. Reopen the matrix for editing "
            "before rebuilding it, so nothing already graded against it moves "
            "underneath a report.",
        )

    captured, swot_version = await _layer3(session, job)
    model = department_for(job.department, job.title, (job.jd_markdown or "")[:400])
    seniority = job.assessment_grade
    # A situation type required the retired interview's explicit confirmation.
    # The four-section document contains no such confirmation, so applying a
    # guessed multiplier would misrepresent what the hiring team approved.
    situation_key = None

    # ── Assemble the candidate set ──────────────────────────────────────────
    candidates = _candidates_from_swot(captured)

    # Stages 1 and 2 for whatever the Layer 1 menu cannot anchor.
    from app.services.hiring.department_models import match_competency  # noqa: PLC0415

    pending = [
        candidate
        for candidate in candidates
        if candidate.observable is None
        and match_competency(candidate.phrase, model, seniority) is None
    ]
    # Layer 2, Drishti (vivekium C3): the function's COMPILED strategic
    # profile, matched on the job's own department. Resolved HERE, ahead of
    # the naming call, because the naming call is now one of its two readers:
    # it reaches WEIGHTS through `emphasis_map` further down and it reaches
    # Sutra's one model call through `prompt_context` just below. Empty when
    # the function has no profile, and then both the prompt and every weight
    # are exactly what they were before this layer had a live supplier.
    from app.services.hiring import drishti

    company_compiled, drishti_context = await drishti.compiled_for(
        session, tenant_id=job.tenant_id, department=job.department
    )
    named, refusals, degraded = await _name_unanchored(
        session,
        job,
        pending,
        model,
        seniority,
        strategic_context=drishti.prompt_context(company_compiled),
    )
    resolved: dict[str, tuple[str, str]] = {
        candidate.phrase: named[index]
        for index, candidate in enumerate(pending)
        if index in named
    }
    refused_phrases = {row["phrase"] for row in refusals}

    emphasis = _quadrant_emphasis()
    usable = [
        _Candidate(
            phrase=resolved.get(
                candidate.phrase, (candidate.phrase, candidate.observable or "")
            )[0],
            category=candidate.category,
            quadrant=candidate.quadrant,
            swot_origin=candidate.swot_origin,
            observable=resolved.get(
                candidate.phrase, (candidate.phrase, candidate.observable or "")
            )[1],
        )
        for candidate in candidates
        if candidate.phrase not in refused_phrases
    ]

    built, build_rejections = _build_all(
        usable,
        model=model,
        seniority=seniority,
        situation_key=situation_key,
        emphasis=emphasis,
        company_compiled=company_compiled,
    )
    refusals.extend(build_rejections)

    # ── §20.2's ceilings, applied after the stages and before the write ─────
    built = _apply_ceilings(built, job, refusals)

    # ── Every aspect needs at least one item, from Layer 1 if nowhere else ──
    used = {item.name.casefold() for item, _quadrant in built}
    for category in ppi.CATEGORIES:
        if any(item.category == category for item, _quadrant in built):
            continue
        extra, extra_rejections = _build_all(
            _candidates_from_menu(
                model, seniority, category=category, used=used, wanted=1
            ),
            model=model,
            seniority=seniority,
            situation_key=situation_key,
            emphasis=emphasis,
            company_compiled=company_compiled,
        )
        refusals.extend(extra_rejections)
        built.extend(extra)

    if not built:
        raise ScorecardInputMissing(
            "items",
            "Nothing in the SWOT session or the company's philosophy could be "
            "turned into a criterion a candidate can be assessed on. Reopen the "
            "session and describe what someone in this role would be seen "
            "doing.",
        )

    ranking = _rank_and_normalise([item for item, _quadrant in built])

    # ── Persist ─────────────────────────────────────────────────────────────
    #
    # A COMPETENCY THAT SURVIVES A RECOMPILE KEEPS ITS ROW. The first version of
    # this deactivated every existing row and inserted a fresh set, which is
    # what the retired generator did, and it is wrong twice over.
    #
    # It is wrong at the database: `uq_job_competency_name` is UNIQUE on
    # (job_id, category, name) across active AND inactive rows, so recompiling a
    # job whose SWOT still names the same competency raises an IntegrityError.
    # The old generator never hit it only because a model invented a slightly
    # different name each time -- which is to say the constraint was being
    # satisfied by non-determinism.
    #
    # It is wrong for the product: a `CandidateQuestion` points at a competency
    # id, so replacing the row orphans every question already written against
    # it. Updating in place keeps the id stable and refreshes all seven stages,
    # which is what a recompile actually means.
    #
    # A competency the new matrix no longer contains is DEACTIVATED, never
    # deleted, for that same reason.
    by_key = {
        (row.category, row.name.casefold()): row for row in existing
    }
    ordinal_by_category = {category: 0 for category in ppi.CATEGORIES}
    created: list[JobCompetency] = []
    kept_keys: set[tuple[str, str]] = set()
    for index, (item, quadrant) in sorted(
        enumerate(built), key=lambda pair: ppi.CATEGORIES.index(pair[1][0].category)
    ):
        rank, share = ranking[index]
        ordinal_by_category[item.category] += 1
        provenance = item.weight.as_dict()
        provenance["raw_value"] = round(item.weight.value, 6)
        provenance["normalised_share"] = round(share, 6)
        provenance["quadrant"] = quadrant
        provenance["situation_key"] = situation_key
        provenance["swot_source"] = "job_analysis"
        provenance["swot_analysis_version"] = swot_version
        provenance["department_model"] = model.key
        # Drishti (C3): the compiled context lines this matrix was built
        # under, and nothing else of the profile. Recorded so "what was this
        # job built on" includes the function's strategic profile version in
        # force at freeze, the same reason raw_value is recorded.
        if drishti_context:
            provenance["drishti_context"] = list(drishti_context)
        provenance["unreachable_sources"] = list(item.unreachable_sources)
        name = item.name[:_MAX_NAME]
        key = (item.category, name.casefold())
        kept_keys.add(key)
        row = by_key.get(key)
        if row is None:
            row = JobCompetency(
                tenant_id=job.tenant_id,
                job_id=job.id,
                category=item.category,
                name=name,
                ordinal=ordinal_by_category[item.category],
            )
            session.add(row)
        else:
            row.ordinal = ordinal_by_category[item.category]
            row.updated_at = datetime.now(timezone.utc)
        row.is_active = True
        row.description = item.observable_evidence
        row.required_level = ppi.required_level_score(_required_level(item))
        row.dimension = item.dimension
        row.observable_evidence = item.observable_evidence
        row.evidence_sources = list(item.evidence_sources)
        row.assessment_method = item.assessment_method
        row.weight = share
        row.threshold_json = item.threshold.as_dict()
        row.disqualifier = item.disqualifier
        row.provenance_json = provenance
        row.swot_origin = item.swot_origin
        row.anchor_key = item.anchor_key
        row.force_rank = rank
        created.append(row)
    for key, row in by_key.items():
        if key not in kept_keys:
            row.is_active = False
    job.framework_generated_at = datetime.now(timezone.utc)
    job.question_target = ppi.resolve_question_target(
        job.assessment_grade, len(created), job.role_classification
    )
    await session.flush()
    logger.info(
        "scorecard.compiled job_id=%s items=%d rejected=%d situation=%s "
        "department=%s correlation_id=%s",
        job.id,
        len(created),
        len(refusals),
        situation_key,
        model.key,
        correlation_id or job.correlation_id,
    )
    # Sutra's hand-off, published LAST so a contract bug in the artifact layer
    # cannot cost a job the matrix it just built.
    ppi.publish_tatva_matrix(
        job,
        created,
        # The version this matrix WOULD be frozen as (§20.5: "frozen as v1. Any
        # change creates v2"). Read off the append-only binding trail rather
        # than counted from the rows, which no longer works now that a surviving
        # competency keeps its row and therefore its `created_at`.
        version=scorecard_version(await _latest_binding(session, job.id)),
        correlation_id=correlation_id or job.correlation_id,
    )
    return CompileResult(
        items=created,
        rejections=refusals,
        situation_key=situation_key,
        department=model.key,
        correlation_id=correlation_id or job.correlation_id,
        degraded_naming=degraded,
    )


def _build_all(
    candidates: Sequence[_Candidate],
    *,
    model: DepartmentModel,
    seniority: str,
    situation_key: str | None,
    emphasis: Mapping[str, float],
    company_compiled: Mapping[str, Any] | None = None,
) -> tuple[list[tuple[transformation.Item, str | None]], list[dict[str, Any]]]:
    """Run the seven stages over a batch, one emphasis per §18.1 quadrant.

    Two things this has to get right and neither is obvious.

    THE EMPHASIS IS KEYED ON THE RESOLVED COMPETENCY NAME, not on the phrase
    the hiring manager typed. `transformation.build_item` runs stage 1 first and
    then looks the emphasis up under whatever stage 1 NAMED, so a phrase that
    resolved onto a department-model anchor would silently carry no Layer 3
    emphasis at all if the key were the phrase -- which is the one input the
    acceptance criterion is about. The resolution is done here first, using the
    same `match_competency` call stage 1 makes, so the two agree by construction.

    DEDUPLICATION IS ACROSS THE WHOLE MATRIX, not per group.
    `transformation.build` dedupes within one call, and the batch is split by
    quadrant, so two quadrants naming one competency would each produce a row
    and the candidate would be graded twice on one axis. The second one is
    rejected here with the quadrant it came from named.
    """
    from app.services.hiring.department_models import match_competency  # noqa: PLC0415

    items: list[tuple[transformation.Item, str | None]] = []
    rejections: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    by_quadrant: dict[str | None, list[_Candidate]] = {}
    for candidate in candidates:
        by_quadrant.setdefault(candidate.quadrant, []).append(candidate)

    for quadrant, group in by_quadrant.items():
        multiplier = emphasis.get(quadrant or "", 1.0)
        role_emphasis: dict[str, float] = {}
        payload: list[dict[str, Any]] = []
        for candidate in group:
            anchor = match_competency(candidate.phrase, model, seniority)
            role_emphasis[anchor.name if anchor else candidate.phrase] = multiplier
            payload.append(
                {
                    "phrase": candidate.phrase,
                    "category": candidate.category,
                    "observable_evidence": candidate.observable or "",
                    "swot_origin": candidate.swot_origin,
                }
            )
        # Layer 2, resolved HERE against the names stage 1 will use, for the
        # same reason role_emphasis is keyed on the resolved name above: an
        # emphasis keyed on anything else silently reaches nothing.
        from app.services.hiring import drishti as _drishti

        company_emphasis = _drishti.emphasis_map(
            company_compiled, list(role_emphasis.keys())
        )
        built, refused = transformation.build(
            payload,
            department=model,
            seniority=seniority,
            situation_key=situation_key,
            role_emphasis=role_emphasis,
            company_emphasis=company_emphasis,
        )
        rejections.extend(refused)
        for item in built:
            key = (item.category, item.name.casefold())
            if key in seen:
                rejections.append(
                    {
                        "phrase": item.name,
                        "category": item.category,
                        "stage": transformation.STAGE_COMPETENCY,
                        "reason": (
                            f"{item.name} is already on the matrix from another "
                            f"part of the session, so it was not added twice. "
                            f"Grading a candidate twice on one competency would "
                            f"double-count it."
                        ),
                    }
                )
                continue
            seen.add(key)
            items.append((item, quadrant))
    return items, rejections


def _apply_ceilings(
    built: list[tuple[transformation.Item, str | None]],
    job: Job,
    rejections: list[dict[str, Any]],
) -> list[tuple[transformation.Item, str | None]]:
    """§20.2's six-competency ceiling, plus the grade's own question ceiling.

    "Maximum six. No exceptions." applies to the scored aspects, which is what
    §20.1's scorecard table holds. Behavioural is bounded by the same figure and
    additionally by whatever the grade's question ceiling leaves, because every
    matrix item is probed at least once and an item nobody was asked about must
    never be graded (`ppi.matrix_is_complete` refuses the save otherwise).

    What is dropped is dropped LOUDLY: each removal is a rejection carrying the
    competency's name, so the Hiring Manager reviewing the matrix sees which
    criteria did not make the cut rather than a matrix quietly shorter than the
    session it came from.
    """
    ceiling = swot_quality.MAX_SCORECARD_COMPETENCIES
    ordered = sorted(
        built, key=lambda pair: (-pair[0].weight.value, pair[0].name.casefold())
    )
    scored = [pair for pair in ordered if pair[0].category in SCORED_CATEGORIES]
    behavioural = [
        pair for pair in ordered if pair[0].category not in SCORED_CATEGORIES
    ]

    keep_scored = scored[:ceiling]
    behavioural_room = max(
        1, min(
            ceiling,
            ppi.max_questions(job.assessment_grade, job.role_classification)
            - len(keep_scored),
        )
    )
    keep_behavioural = behavioural[:behavioural_room]
    kept = {id(item) for item, _quadrant in keep_scored + keep_behavioural}
    for item, _quadrant in built:
        if id(item) in kept:
            continue
        rejections.append(
            {
                "phrase": item.name,
                "category": item.category,
                "stage": transformation.STAGE_WEIGHT,
                "reason": (
                    f"{item.name} ranked below the cut. A scorecard holds at "
                    f"most {ceiling} competencies per Runbook §20.2: beyond "
                    f"that the ranking stops discriminating and every "
                    f"imperfect candidate comes back the same."
                ),
            }
        )
    return [pair for pair in built if id(pair[0]) in kept]


# ── The freeze (Runbook §20.5, RBAC §20) ─────────────────────────────────────


async def freeze(
    session: AsyncSession,
    job: Job,
    *,
    actor_user_id: uuid.UUID | None,
    correlation_id: str | None = None,
) -> FrozenMatrix:
    """The Hiring Manager's finalisation. One version, one binding, one stamp.

    Runbook §20.5: "On HR Manager approval the scorecard is frozen as v1. Any
    change creates v2 ... and both versions are retained." RBAC §20 adds what
    the record has to carry: the user, the timestamp, the JD version and the
    hiring-criteria version.

    The binding row is APPEND-ONLY, so the freeze in force is the highest
    `freeze_sequence` and every earlier one stays as the answer to the question
    a candidate's evaluation has to answer later: not what this job is built on
    now, but what it was built on when they applied.
    """
    await pipeline_halt.enforce(
        pipeline_halt.STAGE_SCORECARD_FREEZE,
        tenant_id=job.tenant_id,
        actor_user_id=actor_user_id,
        job_id=job.id,
        correlation_id=correlation_id or job.correlation_id,
    )
    rows = await ppi.load_framework(session, job.id)
    ok, reason = ppi.matrix_is_complete(
        rows, job.assessment_grade, job.role_classification
    )
    if not ok:
        raise ScorecardInputMissing("matrix", reason or "The matrix is incomplete.")
    underived = [row.name for row in rows if row.weight is None or not row.dimension]
    if underived:
        raise ScorecardInputMissing(
            "matrix",
            "These criteria carry no derivation and cannot be frozen: "
            + ", ".join(underived[:5])
            + ". Rebuild the matrix so every item completes all seven stages.",
        )
    previous = await _latest_binding(session, job.id)
    version = scorecard_version(previous)

    job.framework_approved_at = datetime.now(timezone.utc)
    job.question_target = ppi.resolve_question_target(
        job.assessment_grade, len(rows), job.role_classification
    )
    session.add(
        JobScorecardBinding(
            tenant_id=job.tenant_id,
            job_id=job.id,
            freeze_sequence=(int(previous.freeze_sequence) + 1) if previous else 1,
            scorecard_version=version,
            frozen_by=actor_user_id,
            correlation_id=correlation_id or job.correlation_id,
            frozen_at=job.framework_approved_at,
        )
    )
    await session.flush()
    # Republish under the frozen status: `verify_for_consumer` reads status, and
    # a locked matrix published as merely public is a claim no consumer can
    # check.
    ppi.publish_tatva_matrix(
        job,
        rows,
        version=version,
        correlation_id=correlation_id or job.correlation_id,
    )
    matrix = await load_frozen_matrix(session, job.id)
    if matrix is None:  # pragma: no cover - the checks above make this unreachable
        raise ScorecardInputMissing(
            "matrix",
            "The matrix was approved and could not be read back. Nothing may be "
            "scored against it until that is resolved.",
        )
    logger.info(
        "scorecard.frozen job_id=%s version=%d items=%d by=%s",
        job.id,
        version,
        len(rows),
        actor_user_id,
    )
    return matrix


# ── What the Hiring Manager reads before finalising ──────────────────────────


def plain_provenance(item: MatrixItem) -> list[str]:
    """Where this item's weight came from, in sentences with no numbers in them.

    spec-doc6 §4.3: "The Hiring Manager's review screen shows this in plain
    language before finalisation." Plain language, not a multiplier table: a
    hiring manager confirming "1.4850" is confirming that arithmetic looks
    plausible, and a hiring manager confirming "this counts for more because you
    told me the last person never owned anything in production" is confirming
    the thing they actually said.

    NO NUMBERS. Standing product rule, and here it is also the only thing that
    makes the restatement checkable by the person who has to check it.
    """
    terms = dict((item.provenance or {}).get("terms") or {})
    lines: list[str] = []

    if item.anchor_key:
        lines.append(
            "This is a competency the department model already recognises, so "
            "it starts from the platform's own baseline for the role."
        )
    else:
        lines.append(
            "This is specific to your role rather than something the department "
            "model already knew about, so it starts from a neutral baseline."
        )

    situation = _direction(terms.get("situation_layer3"))
    key = (item.provenance or {}).get("situation_key")
    if situation and key and situations.is_valid(key):
        lines.append(
            f"You confirmed this role is "
            f"{situations.SITUATIONS[key].label}, which counts this kind of "
            f"evidence {situation} than it otherwise would."
        )

    role = _direction(terms.get("role_layer3"))
    if item.swot_origin and (item.provenance or {}).get("swot_source") == "job_analysis":
        lines.append(
            f'The Job SWOT Analysis states: "{item.swot_origin}" This informs '
            "the emphasis of this criterion."
        )
    elif role and item.swot_origin:
        lines.append(
            f'You said: "{item.swot_origin}" That moved this criterion '
            f"{role} on the scorecard."
        )
    elif item.swot_origin:
        lines.append(f'This came from what you said: "{item.swot_origin}"')
    elif not item.swot_origin and (item.provenance or {}).get("swot_source") == "job_analysis":
        lines.append(
            "The Job SWOT Analysis did not name this competency, so it carries "
            "the department model's own emphasis."
        )
    elif not item.swot_origin:
        lines.append(
            "Nothing in your SWOT session spoke to this one, so it carries the "
            "department model's own emphasis and nothing more."
        )

    unreachable = list((item.provenance or {}).get("unreachable_sources") or [])
    if unreachable:
        lines.append(
            "The strongest evidence for this sits outside the assessment (for "
            "example a reference or a work artefact), so the assessment probes "
            "it and the report says how far that goes."
        )
    return lines


def _direction(value: Any) -> str | None:
    """"more heavily" / "less heavily", or None when the term did nothing.

    A word rather than the multiplier, and the None case matters: a term that
    came back at the identity is a layer that expressed no opinion, and saying
    "this was unchanged" about four layers in a row is noise a reader learns to
    skip past.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.0:
        return "more heavily"
    if number < 1.0:
        return "less heavily"
    return None
