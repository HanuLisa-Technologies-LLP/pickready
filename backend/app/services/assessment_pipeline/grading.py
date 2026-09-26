"""Stage 2 of the assessment pipeline: Miti grades, and nothing else does.

    evidence (1)  ->  GRADING (2)  ->  composition (3)  ->  persistence (4)

Miti is the sole grading authority for every per-skill grade and the overall
(CONTRACT v1 "Grading"). This stage hands it stage 1's inputs, the Evidence RAG
reader for related transcript passages (through the typed tool layer, injected
so Miti never imports retrieval) and Phase 4's coding evidence, and records the
model calls that returned a usable value on the run's provenance.

It also reads the ledger's own verdict: whether the evidence under the grades
disagrees with itself. That is a review signal, never a grade change.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.assessment_pipeline.types import AssessmentInputs, ProvenanceRecorder

logger = logging.getLogger(__name__)

__all__ = ["evidence_uncertainty", "grade"]


async def grade(
    session: AsyncSession,
    inputs: AssessmentInputs,
    *,
    allow_incomplete: bool,
    provenance: ProvenanceRecorder,
) -> Any:
    """Miti's result for one application (`miti.live.MitiResult`).

    RAISES `miti.live.ScorecardUnavailable` when gate G1 cannot be met; nothing
    has been called or written at that point. `allow_incomplete` is True only
    on the FINAL attempt (PLAN-p5 P5-D4): earlier attempts stop after the item
    stage when a skill could not be assessed, so the evaluators are not paid
    for a run whose report will not be written.
    """
    from app.prompts import registry
    from app.services import evidence_retrieval
    from app.services.miti import live as miti_live

    result = await miti_live.evaluate_application(
        session,
        job=inputs.job,
        link=inputs.link,
        conversation_id=inputs.conversation.id,
        questions=inputs.questions,
        answers=inputs.answers,
        locators=inputs.locators,
        structured=inputs.structured,
        coding=inputs.coding,
        subject_names=inputs.subject_names,
        allow_incomplete=allow_incomplete,
        passages=evidence_retrieval.transcript_passages_for_skill,
    )
    registered = set(registry.names())
    for task_type, prompt in result.model_calls():
        # The five evaluators' prompt is inline and versioned by the image, so
        # the task is recorded and no registry label is claimed for it.
        provenance.model_call(task_type, prompt if prompt in registered else None)
    return result


async def evidence_uncertainty(
    session: AsyncSession, inputs: AssessmentInputs
) -> tuple[bool, list[dict[str, Any]]]:
    """Whether the ledger holds a contradiction that must not be averaged away.

    Spec section 14: a MATERIAL or CRITICAL contradiction obliges work, and in
    a scoring pass (the conversation is over) the obliged action is to preserve
    the uncertainty: leave every grade exactly as Miti produced it and hand the
    report to a person. An empty ledger is not a contradiction.

    AN UNREADABLE LEDGER RAISES. Miti read this same ledger twice in this
    transaction a moment earlier, so a failure here is a defect, and a failed
    statement has already aborted the transaction the report would be written
    in. The findings are mapped onto the verifier's severity scale so
    `review_findings_json` holds one vocabulary.
    """
    from app.services.evidence import contradictions, ledger

    job, link = inputs.job, inputs.link
    claims = await ledger.load_claims(
        session, tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
    )
    if not claims:
        return False, []
    report = contradictions.detect(
        claims=claims, phase=contradictions.PHASE_POST_CONVERSATION
    )
    if not contradictions.at_least(report.severity, contradictions.MATERIAL):
        return False, []
    logger.warning(
        "functional_assessment.evidence_contradiction link_id=%s severity=%s actions=%s",
        link.id, report.severity, list(report.actions),
    )
    findings: list[dict[str, Any]] = []
    for item in report.contradictions:
        if not contradictions.at_least(item.severity, contradictions.MATERIAL):
            continue
        finding = item.as_finding()
        findings.append(
            {
                "severity": finding.severity,
                "issue": finding.issue,
                "location": finding.location,
                "recommendation": finding.recommendation,
            }
        )
    return True, findings
