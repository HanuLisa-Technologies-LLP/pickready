"""Stage 3 of the assessment pipeline: Siddhi writes the PRISM Report.

    evidence (1)  ->  grading (2)  ->  COMPOSITION (3)  ->  persistence (4)

Siddhi states Miti's grades; it never decides one. Every rated line here is a
`SkillGrade` Miti produced, in contract order, and the Overall is Miti's
aggregate. What this stage adds is prose and citations:

  * a 45 to 50 word remark per skill (`siddhi.remarks`), a fixed catalogue
    sentence for an unanswered or a not assessed skill, and the template
    fallback marked `remark_provenance="template"` so it can never read as
    generation;
  * the Gap Analysis groups (`gap_analysis.build_gap_groups`), the Evidence vs
    Claim Summary and the Validation Points, all from the same ledger read;
  * the composed report through Siddhi's citation chokepoint, with the support
    check's third look going through `evidence_retrieval` (the typed tool
    layer) bound to THIS tenant and THIS application;
  * the quality gate, which compares the grades the document RENDERED against
    the grades Miti DECIDED, two sources, so it has teeth;
  * the AI Score section: Yukti's pre-assessment snapshot, frozen.

Nothing here writes a row. The result is a `ComposedReport`, and stage 4 is
the only writer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import evidence_confidence, gap_analysis
from app.services.application_validation import MANDATORY_KEYS
from app.services.assessment_contract import BUCKET_BEHAVIOURAL
from app.services.assessment_pipeline.types import (
    MODE_MITI,
    MODE_MITI_PARTIAL,
    AssessmentInputs,
    ProvenanceRecorder,
)
from app.services.miti import claims as miti_claims
from app.services.miti import grades as miti_grades
from app.services.siddhi import (
    ai_score,
    claim_evidence,
    quality_gate,
    remarks,
    support,
    validation_points,
)
from app.services.siddhi import inputs as siddhi_inputs
from app.services.siddhi import report as siddhi_report
from app.services.siddhi import synthesis as siddhi_synthesis

logger = logging.getLogger(__name__)

__all__ = [
    "ComposedReport",
    "apply_evidence_confidence",
    "claim_records",
    "compose",
    "contradicted_areas",
    "skill_rows",
    "validation_point_rows",
]

#: The evidence prefix a rated skill's remark is written from. Behavioural
#: skills are judged across everything said; the other two are graded answer
#: by answer against the rubric written with each question.
_BEHAVIOURAL_PREFIX = "the candidate's own answers probing this competency: "
_RUBRIC_PREFIX = (
    "the candidate's own answers, each graded against the rubric written for "
    "the question that produced it: "
)

#: How much of a skill's answers reaches the remark writer.
_REMARK_EVIDENCE_CHARS = 800

_SEVERITY_MEDIUM = "medium"


@dataclass
class ComposedReport:
    """What stage 4 writes: the report row's content and its dimension rows.

    `fields` holds every report column this run decides except the row's
    identity and `synthesized_at`, which the writer stamps. `dimensions` are
    the rated rows as Siddhi read them; the writer keeps only the columns.
    """

    fields: dict[str, Any] = field(default_factory=dict)
    dimensions: list[dict[str, Any]] = field(default_factory=list)


async def skill_rows(
    session: AsyncSession,
    miti: Any,
    *,
    provenance: ProvenanceRecorder,
) -> list[dict[str, Any]]:
    """One rated row per contract skill, in contract order, with its remark.

    `grade` is the word Miti decided (None when not assessed) and travels to
    Siddhi; it is not a column. The score is INTERNAL and NULL exactly when
    the skill was not assessed.
    """
    rows: list[dict[str, Any]] = []
    ordinal_by_bucket: dict[str, int] = {}
    for grade in miti.skills:
        ordinal_by_bucket[grade.bucket] = ordinal_by_bucket.get(grade.bucket, 0) + 1
        base = {
            "category": grade.bucket,
            "name": grade.name,
            # The contract carries no description and no required level: what a
            # skill MEANS is the hidden evidence line, which never reaches a
            # report, and the radar draws the job's shape from its default.
            "description": None,
            "required_level": None,
            "ordinal": ordinal_by_bucket[grade.bucket],
            "score": grade.score,
            "grade": grade.grade,
            "assessment_status": grade.status,
        }
        if grade.status == miti_grades.ANSWER_NOT_ASSESSED:
            remark = remarks.not_assessed_remark()
        elif grade.status == miti_grades.ANSWER_UNANSWERED or not grade.used_answers:
            remark = remarks.unanswered_remark(grade.name)
        else:
            combined = "\n".join(f"- {item}" for item in grade.used_answers)
            prefix = (
                _BEHAVIOURAL_PREFIX if grade.bucket == BUCKET_BEHAVIOURAL else _RUBRIC_PREFIX
            )
            remark = await remarks.bounded_remark(
                session,
                grade.name,
                f"{prefix}{combined[:_REMARK_EVIDENCE_CHARS]}",
                rating=grade.grade,
                provenance=provenance,
            )
        rows.append({**base, **remark.row_fields()})
    return rows


def _confidence_sources(
    row: Mapping[str, Any],
    competency_sources: Mapping[str, Sequence[str]],
    evidence_by_item: Mapping[str, Sequence[Any]],
) -> set[str]:
    """Which kinds of source actually stand behind one rated line.

    What the five evaluators were handed for this competency (carried out of
    the Miti run that handed it to them), `answer` when the item has at least
    one recorded exchange, and the hiring manager's requirement when the row
    carries one (the BAR rather than the proof, which `evidence_confidence`
    marks as not evidencing the candidate).
    """
    name = str(row.get("name") or "")
    kinds = {str(kind) for kind in competency_sources.get(name, ())}
    if evidence_by_item.get(name):
        kinds.add(evidence_confidence.SOURCE_ANSWER)
    if row.get("required_level") is not None:
        kinds.add(evidence_confidence.SOURCE_SWOT)
    return kinds


def apply_evidence_confidence(
    dimensions: list[dict[str, Any]],
    *,
    competency_sources: Mapping[str, Sequence[str]],
    evidence_by_item: Mapping[str, Sequence[Any]],
) -> None:
    """Stamp every rated row with its confidence word and its source keys.

    IT RUNS AFTER GRADING AND IT TOUCHES NO SCORE: two fields are added beside
    the score Miti decided and nothing else on the row changes
    (`tests/test_evidence_confidence.py` compares the rows before and after).
    """
    for row in dimensions:
        derived = evidence_confidence.describe(
            _confidence_sources(row, competency_sources, evidence_by_item)
        )
        row["evidence_confidence"] = derived.confidence
        row["evidence_sources"] = list(derived.sources)


def validation_point_rows(
    dimensions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The rated lines the validation points section reads, as WORDS: Miti's
    grade word, never a word recomputed from the score."""
    from app.services.rating import grade_for_percent

    return [
        {
            "name": str(row["name"]),
            "grade": row.get("grade"),
            "required_level": grade_for_percent(row.get("required_level")),
            "evidence_confidence": row.get("evidence_confidence"),
        }
        for row in dimensions
        if row.get("name")
    ]


async def _declared_employments(
    session: AsyncSession, inputs: AssessmentInputs
) -> list[dict[str, Any]]:
    """The candidate's declared employments with THIS TENANT'S verification.

    Three columns and no more: `hr_name` and `hr_email` are absent from the
    SELECT rather than dropped afterwards, and the tenant filter is in the
    join's ON clause so another tenant's decision is invisible without
    dropping the employment. `started_on` orders the rows and is never
    selected: a date is a number, and a delivered report states words.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT e.employer_name, e.designation, v.status
                FROM candidate_employments e
                LEFT JOIN bgv_verifications v
                    ON v.candidate_employment_id = e.id
                   AND v.tenant_id = :tid
                WHERE e.candidate_id = :cid
                ORDER BY e.started_on DESC, e.employer_name
                """
            ),
            {"tid": str(inputs.job.tenant_id), "cid": str(inputs.link.candidate_id)},
        )
    ).all()
    return [
        {"employer_name": row.employer_name, "designation": row.designation, "status": row.status}
        for row in rows
    ]


async def _ledger_claims(session: AsyncSession, inputs: AssessmentInputs) -> list[Any]:
    """Every claim recorded against this application.

    AN UNREADABLE LEDGER RAISES (the b7 silent-except sweep). It used to be
    logged and read as "no claims recorded", which wrote an Evidence vs Claim
    Summary stating that nothing had been claimed. Miti read this same ledger
    in this transaction, so a failure here is a defect, and the scoring task
    is retried.
    """
    from app.services.evidence import ledger

    return list(
        await ledger.load_claims(
            session,
            tenant_id=inputs.job.tenant_id,
            job_id=inputs.job.id,
            link_id=inputs.link.id,
        )
    )


def claim_records(
    claims: Sequence[Any],
    dimensions: Sequence[Mapping[str, Any]],
    evidence_refs: Mapping[str, tuple[str, ...]],
) -> list[claim_evidence.ClaimRecord]:
    """Ledger claims as the Evidence vs Claim Summary needs them.

    MATERIALITY COMES FROM THE MATRIX (`miti.claims.materiality_for`), built
    from the report's own rated rows, never from the claim's wording: a
    confidently written resume must not rate its own assertions material.
    """
    matrix = {
        str(row["name"]): str(row["category"])
        for row in dimensions
        if row.get("name") and row.get("category")
    }
    records: list[claim_evidence.ClaimRecord] = []
    for claim in claims:
        competency = str(getattr(claim, "dimension", "") or "")
        records.append(
            claim_evidence.ClaimRecord(
                claim=str(getattr(claim, "claim", "") or ""),
                materiality=miti_claims.materiality_for([competency], matrix),
                sources=tuple(getattr(claim, "provenance", ()) or ()),
                status=str(getattr(claim, "status", "") or ""),
                evidence_refs=tuple(evidence_refs.get(competency, ())),
                area=competency,
            )
        )
    return records


def contradicted_areas(claims: Sequence[Any]) -> list[str]:
    """The competencies the ledger holds active evidence on BOTH sides of,
    read from the claim's own derived status, never a stored flag."""
    seen: list[str] = []
    for claim in claims:
        if str(getattr(claim, "status", "")) != claim_evidence.CLAIM_CONTRADICTED:
            continue
        name = str(getattr(claim, "dimension", "") or "")
        if name and name not in seen:
            seen.append(name)
    return seen


def _passages_by_skill(miti: Any) -> dict[str, list[dict[str, Any]]]:
    """The related passages each judgement read, for Siddhi's passage nodes."""
    return {
        grade.name: [
            {"chunk_id": str(piece.chunk_id), "content": piece.content}
            for piece in grade.passages
        ]
        for grade in miti.skills
        if grade.passages
    }


def _status_findings(miti: Any) -> list[dict[str, str]]:
    """`not_assessed` and `partially_assessed`, as words. No prose, no score."""
    findings: list[dict[str, str]] = []
    for grade in miti.skills:
        if grade.status == miti_grades.ANSWER_NOT_ASSESSED:
            findings.append(
                {
                    "severity": _SEVERITY_MEDIUM,
                    "issue": "not_assessed",
                    "location": f"report.{grade.bucket}.{grade.name}",
                    "recommendation": (
                        "This skill could not be evaluated after repeated "
                        "attempts. Read the candidate's answers and judge it "
                        "directly."
                    ),
                }
            )
        elif grade.partially_assessed:
            findings.append(
                {
                    "severity": _SEVERITY_MEDIUM,
                    "issue": "partially_assessed",
                    "location": f"report.{grade.bucket}.{grade.name}",
                    "recommendation": (
                        "Some answers for this skill could not be evaluated; the "
                        "grade rests on the rest. Read the transcript for this "
                        "skill before relying on it."
                    ),
                }
            )
    return findings


async def compose(
    session: AsyncSession,
    inputs: AssessmentInputs,
    miti: Any,
    *,
    provenance: ProvenanceRecorder,
    uncertainty: tuple[bool, list[dict[str, Any]]],
) -> ComposedReport:
    """The report for one application, from Miti's result. Writes nothing.

    `miti.aggregate` must exist: the orchestrator reaches this stage only for
    a complete run or for the final attempt, and both run the evaluators.
    """
    from app.services import evidence_retrieval
    from app.services import verification as verification_base
    from app.services.rating import grade_for_percent
    from app.services.yukti import projection as yukti_projection

    aggregate = miti.aggregate
    if aggregate is None:
        raise ValueError(
            "composition needs Miti's aggregate; the run stopped at "
            f"{miti.outcome and '; '.join(miti.outcome.blocking_reasons)}"
        )
    job, link = inputs.job, inputs.link
    stated = aggregate.stated_score
    overall_score = int(round(stated)) if stated is not None else None
    overall_word = grade_for_percent(overall_score) if overall_score is not None else None

    dimensions = await skill_rows(session, miti, provenance=provenance)

    if overall_word is None:
        overall_remark = remarks.not_assessed_overall_remark()
    else:
        assessed = [row for row in dimensions if row["score"] is not None]
        weak = ", ".join(
            row["name"] for row in assessed if row["grade"] in gap_analysis.GAP_GRADES
        ) or "role-specific depth"
        strong = ", ".join(
            row["name"] for row in assessed if row["grade"] not in gap_analysis.GAP_GRADES
        ) or "the areas evidenced in the conversation"
        overall_remark = await remarks.bounded_remark(
            session,
            "this candidate's overall suitability",
            (
                "the candidate's demonstrated skills and behavioural competencies "
                f"against this job. Stronger evidence: {strong}. Weaker or "
                f"unevidenced: {weak}."
            ),
            *remarks.OVERALL_REMARK_WORDS,
            rating=overall_word,
            provenance=provenance,
        )
    overall = overall_remark.text

    evidence_by_item = siddhi_inputs.evidence_by_item(
        questions=inputs.questions,
        competencies=miti.contract.skills,
        answers=inputs.answers,
        answer_records=inputs.locators,
    )
    apply_evidence_confidence(
        dimensions,
        competency_sources=miti.competency_sources,
        evidence_by_item=evidence_by_item,
    )

    # Read ONCE; both sections derive from the same read, and the employment
    # rows feed both the claim entries and the citable nodes they point at.
    ledger_claims = await _ledger_claims(session, inputs)
    employments = await _declared_employments(session, inputs)
    claims_section = claim_evidence.build(
        claim_records(
            ledger_claims,
            dimensions,
            siddhi_synthesis.evidence_refs_for(dimensions, evidence_by_item),
        )
        + claim_evidence.employment_claims(employments)
    )
    points_section = validation_points.build(
        validation_point_rows(dimensions),
        contradicted_areas=contradicted_areas(ledger_claims),
    )
    validation = dict(inputs.validation)

    section = await gap_analysis.build_gap_groups(
        session, dimensions, evidence_by_item, provenance=provenance
    )
    composed = await siddhi_report.compose_prism(
        dimensions=dimensions,
        evidence_by_item=evidence_by_item,
        gap_groups=section["groups"],
        focus_summary=section["focus_summary"],
        overall_summary=overall,
        overall_grade=overall_word,
        overall_remark_source=overall_remark.source,
        validation=validation,
        validation_points=points_section,
        claim_evidence=claims_section,
        extra_nodes=tuple(claim_evidence.employment_nodes(employments)),
        passages=_passages_by_skill(miti),
        embed=support.semantic_embedder(),
        # THE SUPPORT CHECK'S THIRD LOOK, THROUGH THE TOOL LAYER. Siddhi never
        # imports the retrieval entry point: it is handed here, bound to this
        # tenant and this application, so the scope is fixed once.
        passage_source=support.statement_passage_source(
            evidence_retrieval.support_passages_for_statement,
            session,
            tenant_id=job.tenant_id,
            link_id=link.id,
        ),
    )
    gap_analysis_json = {**section, "siddhi": composed.siddhi_namespace()}

    submitted = link.validation_json or {}
    gate = quality_gate.evaluate(
        gap_analysis_json=gap_analysis_json,
        dimensions=dimensions,
        overall_summary=overall,
        validation=validation,
        validation_source={key: submitted[key] for key in MANDATORY_KEYS if key in submitted},
        evidence_by_item=evidence_by_item,
        miti_grades=quality_gate.miti_grades_from(miti.skills),
        miti_overall_grade=overall_word,
    )

    snapshot = await ai_score.snapshot_for_report(
        session, link.id, source=yukti_projection.pre_assessment_snapshot
    )

    uncertain, uncertainty_findings = uncertainty
    miti_findings = [
        {
            "severity": verification_base.SEVERITY_MEDIUM,
            "issue": "miti_review",
            "location": "evaluation",
            "recommendation": reason,
        }
        for reason in miti.review_reasons
    ]
    findings = (
        [
            {
                "severity": finding.severity,
                "issue": finding.issue,
                "location": finding.location,
                "recommendation": finding.recommendation,
            }
            for finding in gate.findings
        ]
        + list(uncertainty_findings)
        + miti_findings
        + composed.review_findings()
        + (snapshot.review_findings() if snapshot is not None else [])
        + _status_findings(miti)
    )
    needs_review = (
        not gate.passed
        or uncertain
        or aggregate.needs_human_review
        or bool(miti.unresolved_evidence)
        or composed.needs_human_review
        or bool(snapshot is not None and snapshot.needs_human_review)
        or not miti.complete
        or any(grade.partially_assessed for grade in miti.skills)
        or bool(provenance.templates())
    )
    return ComposedReport(
        fields={
            "grade": inputs.grade,
            "overall_summary": overall,
            "overall_score": overall_score,
            "overall_status": aggregate.overall_status,
            "must_have_failed": miti.must_have_failed,
            "scoring_mode": MODE_MITI if miti.complete else MODE_MITI_PARTIAL,
            "validation_json": validation,
            "gap_analysis_json": gap_analysis_json,
            "validation_points_json": points_section,
            "claim_evidence_json": claims_section,
            "ai_score_json": snapshot.as_json() if snapshot is not None else None,
            "category_grades_json": dict(aggregate.category_grades),
            "contract_version": miti.contract_version,
            "contract_digest": miti.contract_digest,
            "needs_human_review": needs_review,
            "review_findings_json": findings or None,
        },
        dimensions=dimensions,
    )
