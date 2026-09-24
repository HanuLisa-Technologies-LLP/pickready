"""Miti on the live scoring path, against real rows. THE sole grading authority.

    G1   the locked assessment contract   `assessment_contract.load_contract_for_conversation`
    1b   item evaluation                  `items.evaluate_skills`, every per-skill grade
    2/3  claims and tiering               read back from the evidence ledger
    4    five isolated evaluators         `pipeline.build_evaluator_inputs`, concurrent
    5    triangulation                    `evidence.contradictions` -> `triangulation`
    6    aggregation of the skill grades  deterministic, and where the three caps bind
    G4   the human review disposition     the latest `review_dispositions` row

WHAT CHANGED IN THE VIVEKIUM RELEASE (WP5-B)
---------------------------------------------
* **G1 reads the CONTRACT, not a frozen matrix.** `hiring.scorecard` is no
  longer imported. The contract is the snapshot the candidate's conversation is
  bound to, loaded through the one read API, and Miti logs its digest with
  `assessment_contract.log_digest("miti", ...)`: the same line the conversation
  logs at its start with `stage=vaada`, so a single log query proves the two
  readers saw the same contract. A conversation whose stored digest disagrees
  with its snapshot is REFUSED (`ContractIntegrityError`), as is a started
  conversation with no binding (`ContractNotBound`); both surface here as
  `ScorecardUnavailable`, because scoring against a contract nobody can prove
  is the one the candidate answered is not a thing to degrade into.
* **Miti grades every skill.** `items.evaluate_skills` produces the ONE
  per-skill judgement and the aggregation scores categories from it. The
  numeric threshold map that was always empty here is gone (`caps` reads the
  grade), and so is the benign-explanation input nothing ever filled.
* **A model failure is "not assessed".** When a skill cannot be assessed and
  the caller has not allowed an incomplete run, the five evaluators are NOT
  run (they would be paid for and thrown away) and the result says
  `complete=False`. The caller decides what that means for the report.
* **G4 reads the disposition.** The latest human decision recorded on the
  application reaches the gate; before this it was a parameter nobody passed.

THE MODEL IS INJECTED, NEVER IMPORTED BY THE PIPELINE
-------------------------------------------------------
`pipeline.evaluate` takes its `invoke` as a parameter and this module supplies
the real one, which keeps `aggregation.py` structurally unable to reach a
provider through a sibling (`test_miti_pipeline.py` walks the AST).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.services.miti import dimensions, items, pipeline, tiering
from app.services.miti.dimensions import EvidenceView
from app.services.miti.grades import ANSWER_NOT_ASSESSED, SkillGrade

logger = logging.getLogger(__name__)

__all__ = [
    "MitiResult",
    "ScorecardUnavailable",
    "evaluate_application",
    "gather_evidence",
    "latest_disposition",
    "load_contract",
]

#: The task type the five evaluators run under. The reasoning tier,
#: temperature 0.0, and `config/llm_providers.py` is the closed mapping that
#: says so. Named here so no caller can route a grading call somewhere else.
EVALUATION_TASK = "dimension_evaluation"


class ScorecardUnavailable(RuntimeError):
    """Gate G1 could not be met: there is no provable contract to grade against.

    A distinct exception because the caller's response is specific: this is
    not a transient failure to retry, it is a state a human (or a defect fix)
    has to change. Runbook section 14.1 files it under abstention, not under
    error handling.
    """


@dataclass
class MitiResult:
    """Everything Miti concluded about one application.

    `skills` is the per-skill judgement, one per contract skill in contract
    order. `outcome` is the evaluators, triangulation, aggregation and gates,
    and is None exactly when the run stopped after the item stage because a
    skill could not be assessed and the caller did not allow an incomplete
    run.

    `unresolved_evidence` names ledger rows whose text could not be fetched.
    They are EXCLUDED from the evaluators rather than passed as empty strings,
    and named here rather than dropped, because silently handing an evaluator
    an empty excerpt would be a grade written from evidence nobody read.
    """

    contract: Any
    skills: tuple[SkillGrade, ...]
    outcome: pipeline.EvaluationOutcome | None = None
    unresolved_evidence: list[str] = field(default_factory=list)
    evidence_count: int = 0
    #: {skill name: the ledger SOURCE TYPES mapped to it}, from the SAME
    #: `EvidenceView` objects the five evaluators were handed. Carried rather
    #: than recomputed by the report writer, because "what did the evaluators
    #: actually read" is a question only the run that read it can answer.
    competency_sources: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def aggregate(self) -> Any:
        return self.outcome.aggregate if self.outcome is not None else None

    @property
    def complete(self) -> bool:
        """No skill ended `not_assessed`."""
        return all(grade.status != ANSWER_NOT_ASSESSED for grade in self.skills)

    @property
    def not_assessed_skills(self) -> list[str]:
        return [grade.name for grade in self.skills if grade.status == ANSWER_NOT_ASSESSED]

    @property
    def must_have_failed(self) -> bool:
        """A Must-have skill graded Not Matching, answered or not (O5-1).

        The ONE predicate, computed from the skill grades so it holds even when
        the evaluators did not run. A `not_assessed` Must-have is not failed.
        """
        from app.services import rating

        return any(
            grade.bucket == "must_have" and grade.grade == rating.GRADE_NOT
            for grade in self.skills
        )

    @property
    def contract_version(self) -> int:
        return int(self.contract.version)

    @property
    def contract_digest(self) -> str:
        return str(self.contract.digest)

    @property
    def review_reasons(self) -> list[str]:
        reasons = list(self.aggregate.review_reasons) if self.aggregate else []
        if self.unresolved_evidence:
            reasons.append(
                f"{len(self.unresolved_evidence)} piece(s) of recorded evidence "
                f"could not be read back and were excluded from the evaluation"
            )
        return reasons


# ── G1 ───────────────────────────────────────────────────────────────────────


async def load_contract(session: Any, conversation_id: uuid.UUID) -> Any:
    """The contract THIS conversation was assessed against. GATE G1.

    Loads through `assessment_contract.load_contract_for_conversation`, logs
    Miti's digest line, and refuses anything G1 would not pass. Every refusal
    is `ScorecardUnavailable` with the reason, and nothing is substituted: no
    live rows, no default contract, no "the job's latest".
    """
    from app.services import assessment_contract

    try:
        contract = await assessment_contract.load_contract_for_conversation(
            session, conversation_id
        )
    except (
        assessment_contract.ContractIntegrityError,
        assessment_contract.ContractNotBound,
    ) as exc:
        logger.error(
            "miti.contract_refused conversation_id=%s reason=%s",
            conversation_id, type(exc).__name__,
        )
        raise ScorecardUnavailable(
            "the contract this candidate was assessed against cannot be proven "
            f"to be the contract being graded: {exc}"
        ) from exc
    assessment_contract.log_digest(assessment_contract.STAGE_MITI, conversation_id, contract)
    gate = pipeline.contract_gate(contract)
    if not gate.passed:
        raise ScorecardUnavailable("; ".join(gate.reasons))
    return contract


# ── G4 ───────────────────────────────────────────────────────────────────────


async def latest_disposition(session: Any, link_id: uuid.UUID) -> tuple[str | None, Any]:
    """The most recent human review decision on this application.

    (disposition, decided_by), or (None, None) on a first pass, which is
    correct: a disposition cannot exist before the flags that need one. Read by
    the LINK, which `review_dispositions` copies since 0062, so a decision
    survives the evaluation row it was first recorded against.
    """
    from sqlalchemy import select

    from app.models.hiring import ReviewDisposition

    row = (
        await session.execute(
            select(ReviewDisposition.disposition, ReviewDisposition.decided_by)
            .where(ReviewDisposition.link_id == link_id)
            .order_by(ReviewDisposition.created_at.desc(), ReviewDisposition.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    return str(row[0]), row[1]


# ── Stages 2 and 3 ───────────────────────────────────────────────────────────


async def gather_evidence(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    subject_names: Sequence[str] = (),
) -> tuple[list[EvidenceView], dict[str, list[str]], list[str]]:
    """Stages 2 and 3, read back off the ledger. Returns (views, mapping, lost).

    The ledger is written per answer (during the conversation, and again as an
    idempotent backfill at scoring), keyed to the skill the question probed.

    The text is fetched through `ledger.resolve_text`, which goes back to the
    source table under the caller's own tenant scope. The ledger stores a
    LOCATOR and never the sentence, so a copy of a candidate's words does not
    sit in a table readable by anyone who can reach the database.

    `subject_names` is the CANDIDATE's own name parts, and every excerpt is
    scrubbed of them before an evaluator sees it. The structural guarantee is
    that `EvaluatorInput` has no name field; this is defence in depth on top.
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


def _role_context(job: Any, grade: str) -> str:
    """One sentence about the ROLE, and nothing about the person.

    The seniority band is the CONTRACT's grade, locked with the skills, never
    the job row's current value: a grade changed after the lock must not move
    the context a locked assessment is evaluated in.
    """
    parts = [str(getattr(job, "title", "") or "").strip()]
    if grade:
        parts.append(f"seniority band {grade.replace('_', ' ')}")
    return ", ".join(part for part in parts if part)


async def _contradictions(session: Any, *, job: Any, link: Any) -> Any:
    """The contradiction report stage 5 triangulates, from the same ledger.

    An empty ledger yields an empty report, which is correct: nothing recorded
    is not the same as nothing disagreeing, and the difference is paid for in
    coverage and confidence rather than manufactured into a finding here.
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


def _sources_by_competency(
    views: Sequence[EvidenceView], mapping: Mapping[str, Sequence[str]]
) -> dict[str, tuple[str, ...]]:
    """Which KINDS of source stand behind each skill. Kinds, never counts."""
    by_ref = {view.ref: view.source_kind for view in views}
    out: dict[str, set[str]] = {}
    for ref, competencies in mapping.items():
        kind = by_ref.get(ref)
        if not kind:
            continue
        for name in competencies:
            out.setdefault(str(name), set()).add(str(kind))
    return {name: tuple(sorted(kinds)) for name, kinds in out.items()}


# ── The entry point ──────────────────────────────────────────────────────────


async def evaluate_application(
    session: Any,
    *,
    job: Any,
    link: Any,
    conversation_id: uuid.UUID,
    questions: Sequence[Any],
    answers: Mapping[str, Sequence[str]],
    locators: Mapping[str, Sequence[Any]],
    structured: Mapping[str, Any],
    subject_names: Sequence[str] = (),
    allow_incomplete: bool = False,
    invoke: Any = None,
    item_invoke: Any = None,
) -> MitiResult:
    """Grade one application. RAISES `ScorecardUnavailable` when G1 cannot be met.

    `questions` are this candidate's own `candidate_questions` rows (each with
    the rubric written with it), `answers` the transcript grouped by question
    key, `locators` where each answer lives, and `structured` the
    `assessment_answers` rows keyed by question id.

    `allow_incomplete=False` (the default) stops after the item stage when any
    skill is not assessed, returning `outcome=None`: the evaluators would be
    paid for and discarded. A caller writing a final report with skills shown
    "Not assessed" passes True and gets the full aggregate, with the overall
    withheld when a Must-have is among them.
    """
    contract = await load_contract(session, conversation_id)

    context = items.ItemContext(
        tenant_id=job.tenant_id,
        job_id=job.id,
        link_id=link.id,
        candidate_id=link.candidate_id,
    )
    skill_grades = await items.evaluate_skills(
        session,
        context=context,
        skills=contract.skills,
        questions=questions,
        answers=answers,
        locators=locators,
        structured=structured,
        invoke=item_invoke,
    )
    result = MitiResult(contract=contract, skills=skill_grades)
    if not result.complete and not allow_incomplete:
        logger.warning(
            "miti.not_assessed link_id=%s conversation_id=%s skills=%d",
            link.id, conversation_id, len(result.not_assessed_skills),
        )
        return result

    views, mapping, lost = await gather_evidence(
        session,
        tenant_id=job.tenant_id,
        job_id=job.id,
        link_id=link.id,
        subject_names=subject_names,
    )
    disposition, decided_by = await latest_disposition(session, link.id)
    inputs = pipeline.EvaluationInputs(
        contract=contract,
        skill_buckets={skill.name: skill.bucket for skill in contract.skills},
        skill_grades=skill_grades,
        evidence=views,
        evidence_competencies=mapping,
        role_context=_role_context(job, contract.grade),
        contradiction_report=await _contradictions(session, job=job, link=link),
        review_disposition=disposition,
        review_decided_by=decided_by,
    )
    result.outcome = await pipeline.evaluate(inputs, invoke=invoke or _invoke)
    if lost:
        logger.warning(
            "miti.live.evidence_unresolved link_id=%s count=%s", link.id, len(lost),
        )
    result.unresolved_evidence = lost
    result.evidence_count = len(views)
    result.competency_sources = _sources_by_competency(views, mapping)
    return result


async def _invoke(task: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
    """The real model call for the five evaluators, injected into the pipeline.

    Imported inside the function so `llm_router` stays out of this module's
    import graph, which is what lets the AST test over `aggregation.py` and
    `pipeline.py` mean something.
    """
    from app.services import llm_router

    return await llm_router.chat_completion(
        task, messages, response_format_json=bool(kwargs.get("response_format_json"))
    )
