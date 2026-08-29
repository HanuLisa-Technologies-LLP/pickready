"""Sutra's seven-stage transformation pipeline (spec-doc5 §A.3, Runbook §19).

    SWOT INPUT -> COMPETENCY        (named from the department model, Layer 1)
               -> OBSERVABLE EVIDENCE (what would we SEE if this were true?)
               -> EVIDENCE SOURCES
               -> ASSESSMENT METHOD
               -> WEIGHT             (Layer 1 baseline, modified by Layer 2
                                      company DNA, modified again by this role's
                                      Layer 3 SWOT and situation type)
               -> THRESHOLD
               -> DISQUALIFIER       (if applicable)

NOTHING ENTERS THE TATVA MATRIX WITHOUT COMPLETING ALL SEVEN STAGES. That is
spec-doc5's wording and `Item.is_complete` is what enforces it: an item missing
any of the first six is refused at `build`, not filtered later, because a
partially-transformed item is one whose grade rests on a stage nobody ran.

WHAT THIS MODULE CHANGES, AND WHAT IT DOES NOT
------------------------------------------------
It does not change the product. The matrix Sutra produces is still Must-have /
Nice-to-have / Behavioural, still human-reviewed and locked, still frozen once
anyone is assessed, still fully agent-decided with no manual override and no
fixed item count. `services/ppi.py` remains the module that generates and
persists it.

What changes is that each item's WEIGHT is now the product of three named terms
instead of an opaque model output, and every term is recorded on the item. The
acceptance criterion is exactly this: "a change to a Layer 2 or Layer 3 input
demonstrably moves a weight in the output -- not just appears in a summary".
`Weight.provenance` is what makes that demonstrable by reading a row.

WHY THE ARITHMETIC IS HERE AND NOT IN A PROMPT
------------------------------------------------
A weight a model chose is a weight nobody can reproduce, and a weight nobody can
reproduce cannot be traced to a layer. So the MODEL's job in this pipeline is
the two stages that genuinely need judgment -- naming a competency from a
manager's prose (stage 1, when the department model has no anchor for it) and
authoring an observable-evidence statement (stage 2). Stages 3 through 7 are
arithmetic and table lookups over what those produced. That split is the same
one `reasoning/planner.py` already makes and for the same reason: same inputs,
same plan, every time, or a difference cannot be told apart from a provider
sampling differently.

WHY WEIGHTS EXIST HERE AND NOWHERE ELSE
-----------------------------------------
`services/matching.WEIGHTS` was deleted on 2026-07-30 and `test_scoring.py`
asserts its absence. That deletion stands and this does not reverse it: those
weights were a fixed 0.35/0.30/0.20/0.15 table applied to the four AI Score
parameters for every role in the product, and they were SHOWN TO THE CLIENT as
"35% role-fit weighting". Both faults are absent here. These weights are
per-job, derived from three declared layers rather than asserted, and they are
internal ranking data that never crosses an API boundary -- the same status
`report_dimensions.required_level` has had all along.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services.hiring import layers, situations
from app.services.hiring.company_dna import CompanyDNA
from app.services.hiring.department_models import (
    BaselineCompetency,
    DepartmentModel,
    EVIDENCE_SOURCES,
    baseline_for,
    department_for,
    match_competency,
    rubric_anchors,
)

__all__ = [
    "STAGES",
    "STAGE_COMPETENCY",
    "STAGE_OBSERVABLE",
    "STAGE_SOURCES",
    "STAGE_METHOD",
    "STAGE_WEIGHT",
    "STAGE_THRESHOLD",
    "STAGE_DISQUALIFIER",
    "METHODS",
    "Weight",
    "Item",
    "TransformationError",
    "build_item",
    "build",
    "matrix_provenance",
]

# ── The seven stages ─────────────────────────────────────────────────────────

STAGE_COMPETENCY = "competency"
STAGE_OBSERVABLE = "observable_evidence"
STAGE_SOURCES = "evidence_sources"
STAGE_METHOD = "assessment_method"
STAGE_WEIGHT = "weight"
STAGE_THRESHOLD = "threshold"
STAGE_DISQUALIFIER = "disqualifier"

STAGES: tuple[str, ...] = (
    STAGE_COMPETENCY,
    STAGE_OBSERVABLE,
    STAGE_SOURCES,
    STAGE_METHOD,
    STAGE_WEIGHT,
    STAGE_THRESHOLD,
    STAGE_DISQUALIFIER,
)

#: The last stage is the only optional one, and spec-doc5 says so: "DISQUALIFIER
#: (if applicable)". Everything before it is mandatory.
REQUIRED_STAGES: tuple[str, ...] = STAGES[:-1]


class TransformationError(ValueError):
    """An item that did not complete every required stage.

    Raised rather than logged. A half-transformed item that reached the matrix
    would be graded against criteria one of whose stages nobody ran, and the
    only symptom would be a slightly odd report months later.
    """


# ── Stage 4: assessment method ───────────────────────────────────────────────
#
# WHICH METHOD IS DECIDED BY THE EVIDENCE SOURCES, not by preference. That
# ordering matters: picking a method first and then asking what evidence it
# produces is how a competency ends up probed by a question that cannot
# possibly evidence it.

METHOD_CONVERSATION = "conversation"
METHOD_WORKED_EXAMPLE = "worked_example_probe"
METHOD_VALIDATION_FIELD = "validation_field"
METHOD_RESUME_SIGNAL = "resume_signal"
METHOD_OUT_OF_BAND = "out_of_band"

METHODS: tuple[str, ...] = (
    METHOD_CONVERSATION,
    METHOD_WORKED_EXAMPLE,
    METHOD_VALIDATION_FIELD,
    METHOD_RESUME_SIGNAL,
    METHOD_OUT_OF_BAND,
)

#: Source -> the method that actually elicits it. Ordered by strength, so
#: `_method_for` takes the strongest reachable one.
_METHOD_BY_SOURCE: dict[str, str] = {
    "work_artefact": METHOD_OUT_OF_BAND,
    "reference": METHOD_OUT_OF_BAND,
    "employer_verification": METHOD_OUT_OF_BAND,
    "worked_example": METHOD_WORKED_EXAMPLE,
    "validation_field": METHOD_VALIDATION_FIELD,
    "assessment_answer": METHOD_CONVERSATION,
    "resume_claim": METHOD_RESUME_SIGNAL,
}

#: Strongest first among the sources reachable INSIDE an assessment. An
#: out-of-band source is recorded as required and is never counted as satisfied,
#: which is the honest position: the platform cannot call a reference, and
#: pretending a competency is evidenced because a reference would have evidenced
#: it is the same error as a timestamp standing in for work that happened.
_IN_BAND_PRIORITY: tuple[str, ...] = (
    METHOD_WORKED_EXAMPLE,
    METHOD_CONVERSATION,
    METHOD_VALIDATION_FIELD,
    METHOD_RESUME_SIGNAL,
)


def _method_for(sources: Sequence[str]) -> tuple[str, list[str]]:
    """(method, unreachable_sources).

    The second element is what stops this from being a quiet downgrade. A
    competency whose strongest evidence is a work artefact gets a conversation
    probe AND a record that its best source is out of band, which Miti reads as
    a reason to hold confidence down rather than as a satisfied requirement.
    """
    methods = {_METHOD_BY_SOURCE.get(s, METHOD_CONVERSATION) for s in sources}
    unreachable = sorted(
        s
        for s in sources
        if _METHOD_BY_SOURCE.get(s) == METHOD_OUT_OF_BAND
    )
    for candidate in _IN_BAND_PRIORITY:
        if candidate in methods:
            return candidate, unreachable
    # Every named source is out of band. A conversation probe is still the right
    # in-band method -- asking is better than not asking -- and `unreachable`
    # carries the caveat.
    return METHOD_CONVERSATION, unreachable


# ── Stage 5: weight ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Weight:
    """A weight and the three layers that produced it.

        value = baseline (L1) x company (L2) x situation (L3) x role (L3)

    Every term is stored, so "why is this weighted 1.62" is answered by reading
    the row rather than by rerunning the pipeline. That is the acceptance
    criterion, not a nicety.

    The two Layer 3 terms are separate because they come from different places
    and a reader needs to tell them apart: `situation` is the role's situation
    type acting on the competency's DIMENSION, and `role` is this specific
    SWOT emphasising this specific competency.
    """

    value: float
    baseline: float
    company: float
    situation: float
    role: float
    #: Which department model supplied `baseline`, or None when the competency
    #: had no Layer 1 anchor and the baseline is the neutral default.
    baseline_source: str | None
    #: The dimension the situation modifier acted through.
    dimension: str
    #: Every `layers.Adjustment` that contributed, as dicts.
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 4),
            "terms": {
                "baseline_layer1": round(self.baseline, 4),
                "company_layer2": round(self.company, 4),
                "situation_layer3": round(self.situation, 4),
                "role_layer3": round(self.role, 4),
            },
            "baseline_source": self.baseline_source,
            "dimension": self.dimension,
            "provenance": list(self.provenance),
        }


#: The weight a competency gets when the department model has no anchor for it.
#: Neutral rather than low: a genuinely role-specific requirement the hiring
#: manager raised is not less important for being absent from a generic model,
#: it is just less traceable -- and `baseline_source: None` is what records that
#: honestly.
NEUTRAL_BASELINE = 1.0


def derive_weight(
    *,
    anchor: BaselineCompetency | None,
    dimension: str,
    company: CompanyDNA | None,
    situation_key: str | None,
    role_emphasis: Mapping[str, float] | None = None,
    subject: str,
) -> Weight:
    """Stage 5, in full. Pure arithmetic over three declared layers."""
    baseline = anchor.baseline_weight if anchor else NEUTRAL_BASELINE
    baseline_source = anchor.key if anchor else None

    provenance: list[dict[str, Any]] = []

    # Layer 2. The company's modifier is keyed by DIMENSION, because a company
    # philosophy is a statement about what kind of evidence matters and not
    # about a competency it has never heard of.
    company_multiplier = 1.0
    if company and company.weight_modifiers:
        company_multiplier = float(company.weight_modifiers.get(dimension, 1.0))

    # Layer 3a. The situation type, also acting through the dimension.
    _, situation_multiplier = situations.apply_to(1.0, dimension, situation_key)

    # Layer 3b. This SWOT emphasising this specific competency. Passed through
    # `layers.resolve` rather than applied directly, so a hiring manager cannot
    # weight one item past the bound every other layer is held to.
    role_multiplier = 1.0
    if role_emphasis:
        resolution = layers.resolve(
            "competency_weight", role={subject: role_emphasis.get(subject, 1.0)}
        )
        role_multiplier = resolution.multiplier_for(subject)
        provenance.extend(a.as_dict() for a in resolution.adjustments)

    value = baseline * company_multiplier * situation_multiplier * role_multiplier
    return Weight(
        value=value,
        baseline=baseline,
        company=company_multiplier,
        situation=situation_multiplier,
        role=role_multiplier,
        baseline_source=baseline_source,
        dimension=dimension,
        provenance=provenance,
    )


# ── Stage 6: threshold ───────────────────────────────────────────────────────

#: The platform's baseline evidence bar per category, expressed as how many
#: independent supporting pieces of evidence a competency needs before it reads
#: as evidenced at all.
#:
#: A Must-have needs more than a Nice-to-have for the obvious reason: a
#: Must-have graded Not Matching caps the whole report, so the cost of getting
#: one wrong is asymmetric and the evidence bar should be too.
_BASELINE_INDEPENDENCE: dict[str, int] = {
    "must_have": 2,
    "nice_to_have": 1,
    "behavioural": 1,
}

#: The platform floor a Layer 2 threshold modifier is applied to.
BASELINE_THRESHOLD = 1.0


@dataclass(frozen=True)
class Threshold:
    """How much evidence, and how good, before this item can grade well."""

    #: Independent supporting pieces required.
    independence_required: int
    #: Multiplier on the platform pass bar, already clamped.
    level: float
    #: Days after which evidence is discounted. None = no decay.
    max_age_days: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "independence_required": self.independence_required,
            "level": round(self.level, 4),
            "max_age_days": self.max_age_days,
        }


def derive_threshold(category: str, company: CompanyDNA | None) -> Threshold:
    baseline = _BASELINE_INDEPENDENCE.get(category, 1)
    if company is None:
        return Threshold(baseline, BASELINE_THRESHOLD, None)
    # The company may raise the independence requirement, never lower it below
    # the platform baseline for that category. Asymmetric on purpose: demanding
    # more corroboration is always safe, and demanding less is how a Must-have
    # bar stops being one.
    return Threshold(
        independence_required=max(baseline, company.independence_required),
        level=company.threshold_modifier,
        max_age_days=company.evidence_max_age_days,
    )


# ── The item ─────────────────────────────────────────────────────────────────


@dataclass
class Item:
    """One fully-transformed matrix entry. Seven stages, all recorded."""

    # Stage 1
    name: str
    category: str
    #: The Layer 1 competency this was named from, or None when it is genuinely
    #: role-specific. None is an honest provenance, not a failure.
    anchor_key: str | None
    dimension: str
    # Stage 2
    observable_evidence: str
    # Stage 3
    evidence_sources: tuple[str, ...]
    # Stage 4
    assessment_method: str
    unreachable_sources: tuple[str, ...]
    # Stage 5
    weight: Weight
    # Stage 6
    threshold: Threshold
    # Stage 7 -- the only optional stage
    disqualifier: str | None = None
    #: What in the SWOT this came from. Quoted so a hiring manager reviewing the
    #: matrix can see their own words beside the criterion they produced.
    swot_origin: str | None = None
    rubric_anchor: str = ""

    def missing_stages(self) -> list[str]:
        missing: list[str] = []
        if not self.name.strip():
            missing.append(STAGE_COMPETENCY)
        if not self.observable_evidence.strip():
            missing.append(STAGE_OBSERVABLE)
        if not self.evidence_sources:
            missing.append(STAGE_SOURCES)
        if self.assessment_method not in METHODS:
            missing.append(STAGE_METHOD)
        if self.weight is None or self.weight.value <= 0:
            missing.append(STAGE_WEIGHT)
        if self.threshold is None or self.threshold.independence_required < 1:
            missing.append(STAGE_THRESHOLD)
        return missing

    def is_complete(self) -> bool:
        return not self.missing_stages()

    def as_dict(self) -> dict[str, Any]:
        """The internal projection: carries the weights.

        NOT a client projection. `ppi._matrix_item` is what a client-facing
        surface renders, and it converts the required level to a WORD. Nothing
        here may be returned by a report route.
        """
        return {
            "name": self.name,
            "category": self.category,
            "anchor_key": self.anchor_key,
            "dimension": self.dimension,
            "observable_evidence": self.observable_evidence,
            "evidence_sources": list(self.evidence_sources),
            "assessment_method": self.assessment_method,
            "unreachable_sources": list(self.unreachable_sources),
            "weight": self.weight.as_dict(),
            "threshold": self.threshold.as_dict(),
            "disqualifier": self.disqualifier,
            "swot_origin": self.swot_origin,
            "stages_completed": list(STAGES if self.disqualifier else REQUIRED_STAGES),
        }


def build_item(
    *,
    phrase: str,
    category: str,
    department: DepartmentModel | str,
    seniority: str,
    company: CompanyDNA | None = None,
    situation_key: str | None = None,
    role_emphasis: Mapping[str, float] | None = None,
    observable_evidence: str | None = None,
    disqualifier: str | None = None,
    swot_origin: str | None = None,
) -> Item:
    """Run one SWOT phrase through all seven stages.

    `observable_evidence` is the ONE thing a model may supply, and only when the
    department model has no anchor for the phrase. When there IS an anchor, the
    anchor's own observable-evidence statement is used, because a per-job
    rephrasing of a platform-level statement is how two jobs stop being
    comparable.
    """
    model = department if isinstance(department, DepartmentModel) else department_for(department)

    # ── Stage 1: COMPETENCY, named from the department model ────────────────
    anchor = match_competency(phrase, model, seniority)
    name = anchor.name if anchor else (phrase or "").strip()
    if not name:
        raise TransformationError(
            "Stage 1 produced no competency name. A matrix item with no name is "
            "a criterion nobody can be graded against."
        )
    dimension = anchor.primary_dimension if anchor else _infer_dimension(category)

    # ── Stage 2: OBSERVABLE EVIDENCE ────────────────────────────────────────
    evidence_statement = (
        anchor.observable_evidence if anchor else (observable_evidence or "").strip()
    )
    if not evidence_statement:
        raise TransformationError(
            f"Stage 2 produced no observable-evidence statement for {name!r}. "
            "spec-doc5: nothing enters the matrix without completing all seven "
            "stages, and an item with no observable evidence is a criterion "
            "whose grade rests on an adjective."
        )

    # ── Stage 3: EVIDENCE SOURCES ───────────────────────────────────────────
    sources = (
        anchor.evidence_sources
        if anchor
        else ("assessment_answer", "worked_example", "resume_claim")
    )
    sources = tuple(s for s in sources if s in EVIDENCE_SOURCES)
    if not sources:
        raise TransformationError(f"Stage 3 produced no evidence sources for {name!r}.")

    # ── Stage 4: ASSESSMENT METHOD ──────────────────────────────────────────
    method, unreachable = _method_for(sources)

    # ── Stage 5: WEIGHT ─────────────────────────────────────────────────────
    weight = derive_weight(
        anchor=anchor,
        dimension=dimension,
        company=company,
        situation_key=situation_key,
        role_emphasis=role_emphasis,
        subject=name,
    )

    # ── Stage 6: THRESHOLD ──────────────────────────────────────────────────
    threshold = derive_threshold(category, company)

    # ── Stage 7: DISQUALIFIER, if applicable ────────────────────────────────
    item = Item(
        name=name,
        category=category,
        anchor_key=anchor.key if anchor else None,
        dimension=dimension,
        observable_evidence=evidence_statement,
        evidence_sources=sources,
        assessment_method=method,
        unreachable_sources=tuple(unreachable),
        weight=weight,
        threshold=threshold,
        disqualifier=(disqualifier or "").strip() or None,
        swot_origin=(swot_origin or "").strip() or None,
        rubric_anchor=rubric_anchors(model, seniority),
    )
    missing = item.missing_stages()
    if missing:
        raise TransformationError(
            f"{name!r} did not complete stage(s) {missing}. Nothing enters the "
            f"Tatva matrix without completing all seven."
        )
    return item


#: Which dimension a category speaks to when there is no Layer 1 anchor to ask.
#: A coarse mapping, and it says so: the alternative is refusing to build an item
#: for a genuinely role-specific requirement, which would silently drop exactly
#: the criteria a hiring manager cared most about raising.
def _infer_dimension(category: str) -> str:
    from app.services.hiring.department_models import (
        DIM_ROLE_FIT,
        DIM_TRACK_RECORD,
        DIM_VERIFIED_COMPETENCE,
    )

    return {
        "must_have": DIM_VERIFIED_COMPETENCE,
        "nice_to_have": DIM_TRACK_RECORD,
        "behavioural": DIM_ROLE_FIT,
    }.get(category, DIM_VERIFIED_COMPETENCE)


def build(
    inputs: Iterable[Mapping[str, Any]],
    *,
    department: DepartmentModel | str,
    seniority: str,
    company: CompanyDNA | None = None,
    situation_key: str | None = None,
    role_emphasis: Mapping[str, float] | None = None,
) -> tuple[list[Item], list[dict[str, Any]]]:
    """(items, rejections). Never raises for one bad input.

    A single un-transformable phrase must not cost the whole matrix -- the same
    partial-success reasoning the databank bulk upload uses, where one unreadable
    PDF may not discard the other twenty-four. Each rejection carries the phrase
    and the stage that refused it, so a reviewer sees what was dropped rather
    than a matrix that is quietly shorter than the SWOT it came from.
    """
    items: list[Item] = []
    rejections: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for raw in inputs:
        phrase = str(raw.get("phrase") or raw.get("name") or "").strip()
        category = str(raw.get("category") or "must_have")
        try:
            item = build_item(
                phrase=phrase,
                category=category,
                department=department,
                seniority=seniority,
                company=company,
                situation_key=situation_key,
                role_emphasis=role_emphasis,
                observable_evidence=raw.get("observable_evidence"),
                disqualifier=raw.get("disqualifier"),
                swot_origin=raw.get("swot_origin") or phrase,
            )
        except TransformationError as exc:
            rejections.append({"phrase": phrase, "category": category, "reason": str(exc)})
            continue
        # Two SWOT phrases can name the same competency -- "ownership" in
        # strengths and "sees things through" in weaknesses both resolve to
        # delivery ownership. The matrix takes one row, because
        # `job_competencies` is UNIQUE on (job, category, name) and, more to the
        # point, grading a candidate twice on one axis double-counts it.
        key = (category, item.name.lower())
        if key in seen:
            rejections.append(
                {
                    "phrase": phrase,
                    "category": category,
                    "reason": (
                        f"Resolves to {item.name!r}, which this category already "
                        f"has. Two SWOT phrases naming one competency would "
                        f"double-count it."
                    ),
                }
            )
            continue
        seen.add(key)
        items.append(item)

    return items, rejections


def matrix_provenance(items: Sequence[Item]) -> dict[str, Any]:
    """The whole matrix's weight derivation, for the artifact and the reviewer.

    INTERNAL. Carries weights, so it must never cross a client-facing boundary;
    `test_tatva_transformation.py` pins that. What it is for is the acceptance
    criterion: change a Layer 2 or Layer 3 input, regenerate, diff this.
    """
    return {
        "stages": list(STAGES),
        "items": [
            {
                "name": item.name,
                "category": item.category,
                "weight": item.weight.as_dict(),
                "threshold": item.threshold.as_dict(),
                "anchor_key": item.anchor_key,
                "unreachable_sources": list(item.unreachable_sources),
            }
            for item in items
        ],
    }
