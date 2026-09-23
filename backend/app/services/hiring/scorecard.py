"""The READ half of the retired Tatva matrix: the frozen matrix and gate G1.

WHAT IS LEFT HERE, AND WHY IT IS ONLY THE READ HALF
---------------------------------------------------
Until the Vivekium release this module BUILT the matrix (`compile_matrix`,
Sutra's seven stages over the department menu, the situation layer and the
SWOT), ENRICHED a reviewed one (`_enrich_reviewed_rows`) and FROZE it
(`freeze`). All of that is DELETED (owner decision D1): Sutra drafts skills and
writes a hidden assessment context (`services/hiring/sutra`, `services/skills`),
and what every candidate is assessed against is the skills CONTRACT
(`services/assessment_contract`), locked and snapshotted at the first start.

What survives is what the scoring path still reads until the grading phase
moves it onto the contract: `MatrixItem`, `FrozenMatrix`, `item_from_row`,
`load_frozen_matrix`, `require_frozen_matrix` (gate G1) and `plain_provenance`.
Readers today: `functional_assessment`, `hiring/evidence_graph`, `miti/live`,
`siddhi/synthesis`, `orchestration/enforcement` and `api/jobs`' publication
check. The grading phase deletes this module when the last of them reads the
contract instead.

A ROW WRITTEN BY THE SKILLS FLOW IS NOT A MATRIX ITEM. It carries no
`dimension` and no `weight`, so `item_from_row` returns None for it and
`load_frozen_matrix` refuses the job. That is the honest answer for a reader
still asking the retired question, and it is why the skills, assessment and
grading phases ship in one deploy.

WHAT CROSSES A CLIENT BOUNDARY
-------------------------------
Nothing numeric. `MatrixItem.weight` and `.threshold` are internal ranking data
of the same status `report_dimensions.required_level` has always had.
`plain_provenance` is sentences.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import JobCompetency
from app.models.job_scorecard_binding import JobScorecardBinding
from app.models.job import Job
from app.services import ppi
from app.services.hiring import gates, situations

logger = logging.getLogger(__name__)

__all__ = [
    "MatrixItem",
    "FrozenMatrix",
    "ScorecardError",
    "ScorecardNotFrozen",
    "load_frozen_matrix",
    "require_frozen_matrix",
    "item_from_row",
    "plain_provenance",
]


# ── Errors ───────────────────────────────────────────────────────────────────


class ScorecardError(RuntimeError):
    """Base class. Every refusal in this module names what is missing."""


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
        # means the rows carry no seven-stage derivation: either the retired
        # single-pass generator wrote them, or the skills flow did, and skills
        # carry a priority and an evidence line rather than a dimension and a
        # weight. Stated as its own reason rather than folded into the gate's,
        # because the fix is different: this reader has to move onto the
        # skills contract, which the grading phase does.
        result = gates.GateResult(
            gates.G1,
            False,
            blocking=True,
            reasons=(
                "This job's criteria carry no seven-stage derivation, so this "
                "scoring path cannot read them. It reads the saved skills "
                "contract once the grading phase moves it there.",
            ),
        )
    raise ScorecardNotFrozen(job_id, result)


# ── Plain-language provenance of a frozen matrix item ──────────────────────


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
