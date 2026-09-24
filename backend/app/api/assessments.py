"""The PPI Assessment Report and the recruiter's transcript view."""
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_tenant_db,
    require_capability,
)
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.assessments import (
    ClaimEvidenceOut,
    DimensionOut,
    FunctionalReportOut,
    GapAnalysisOut,
    RadarChartOut,
    TranscriptAnswerDetailOut,
    TranscriptExchangeOut,
    TranscriptOut,
    ValidationPointsOut,
)
from app.services import capabilities as caps
from app.services import (
    evidence_confidence,
    job_assessment_retention,
    ppi,
    ppi_interview,
    reference_code,
    retention_consent,
    skills,
)
# DUAL-MODE ASSESSMENT (2026-09-05 spec). The video package is reached ONLY by
# these routes and the processing task, never by a scorer; the models carry the
# consent audit and the recording lifecycle.
from app.services.assessment_formats import rendering as format_rendering
from app.services.assessment_formats import scoring as format_scoring
from app.services.assessment_formats import types as question_types
# PROCTORING IS MANDATORY (proctoring-spec-doc.md, principle P4). The gate is
# this module's only import-time dependency on the proctoring package; the
# behaviour recorder and the report loader are reached inside the handlers
# that need them, so the scoring isolation the package promises stays
# visible in the import graph as gate-plus-report-join and nothing else.
from app.services.functional_assessment import (
    CATEGORY_MATCHING,
    CATEGORY_TECHNICAL,
    RADAR_BANDS,
    RADAR_SERIES,
    build_radar_charts,
    rating_label,
)
from app.services.rating import GRADES, grade_for_percent

logger = logging.getLogger(__name__)

router = APIRouter()

READY_FOR_CANDIDATES = skills.READY_FOR_CANDIDATES
PENDING_REVIEW = skills.PENDING_REVIEW


# ── Job setup lives in `api/job_setup.py` (Vivekium release) ────────────────
#
# The setup checklist, the Skills step and the four Job SWOT routes moved to
# `api/job_setup.py` under the same prefix, so every URL is unchanged. The
# Tatva matrix editor (`/framework` and its siblings) that lived here is
# DELETED; `READY_FOR_CANDIDATES` above stays re-exported for the assessment
# conversation until the assessment phase imports it from `services/skills`.


# ── The PPI Assessment Report (spec §9) ──────────────────────────────────────


async def _require_assessment_records_readable(
    session: AsyncSession, user: CurrentUser, link: JobCandidateLink
) -> None:
    """THE JOB-CLOSURE ACCESS GATE (change request 22, 2026-09-22).

    Closing a job no longer deletes its assessment data on the spot; it
    WITHHOLDS it for thirty days and then a sweep deletes it. That makes
    inaccessibility something the read path has to enforce rather than
    something the absence of a row enforced for free, so every staff-facing
    read of a report, a PDF or a transcript asks here.

    Called AFTER the tenant check and BEFORE the artifact is loaded, the
    ordering `services/tools` states: a refusal that ran the handler first has
    already read the row it was refusing to show.

    The CANDIDATE's own routes deliberately do not call this. The employer
    closing a requisition withholds the record from the employer; it was never
    a reason to take a person's own assessment away from them.
    """
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_assessment_retention.require_readable(session, user, job)


@router.get("/reports/links/{link_id}", response_model=FunctionalReportOut)
async def get_report(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FunctionalReportOut:
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Report not found")
    await _require_assessment_records_readable(session, user, link)
    report = (
        await session.execute(select(FunctionalSkillsReport).where(FunctionalSkillsReport.job_candidate_link_id == link.id))
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=404, detail="The PPI Assessment Report is not ready")
    rows = (
        await session.execute(
            select(ReportDimension)
            .where(ReportDimension.report_id == report.id)
            .order_by(ReportDimension.ordinal)
        )
    ).scalars().all()

    def _out(row: ReportDimension) -> DimensionOut:
        return DimensionOut(
            name=row.name,
            description=row.description,
            grade=rating_label(row.score) or GRADES[-1],
            required_level=grade_for_percent(row.required_level),
            remark=row.remark,
            # EVIDENCE CONFIDENCE (0107). The stored code becomes a word HERE,
            # server side, for the same reason `score` becomes a grade here: a
            # code that crossed the boundary is a code somebody eventually
            # renders directly. A row written before 0107 carries None and
            # `display_word` returns None for it rather than inventing one.
            evidence_confidence=evidence_confidence.display_word(
                row.evidence_confidence
            ),
            evidence_sources=list(
                evidence_confidence.source_labels(row.evidence_sources)
            ),
        )

    grouped: dict[str, list[DimensionOut]] = {}
    for row in rows:
        grouped.setdefault(row.category, []).append(_out(row))

    # Charts are built from the SAME rows the sections render, so a chart can
    # never disagree with the text beside it.
    charts = build_radar_charts(
        [
            {
                "category": row.category,
                "name": row.name,
                "score": row.score,
                "required_level": row.required_level,
                "ordinal": row.ordinal,
            }
            for row in rows
        ]
    )

    overall = report.overall_score
    if overall is None:
        # Written before migration 0030. Recompute rather than showing nothing.
        assessed = [row.score for row in rows if row.category != CATEGORY_MATCHING]
        overall = round(sum(assessed) / len(assessed)) if assessed else 0

    # The Proctoring Report, appended as the final, informational section.
    # It moves nothing above it: no grade in this payload is read from it.
    from app.services.proctoring import report as proctoring_report  # noqa: PLC0415

    proctoring = await proctoring_report.load_report_out(session, link.id)

    # Data-retention consent (Consent & Privacy spec, 2026-09-05): whether the
    # Download control may be offered at all. Read here so the UI and the PDF
    # route agree, and a refused download is never a dead button.
    report_candidate = await session.get(Candidate, link.candidate_id)

    return FunctionalReportOut(
        id=report.id,
        job_candidate_link_id=link.id,
        # The same COMPANY-JOB-CANDIDATE code the candidate table shows under
        # the name, so a report and a table row can be matched up by eye.
        # Recomputed rather than joined: it is a pure function of three ids that
        # never change, so there is no stored value to disagree with.
        reference_code=reference_code.reference_code(
            link.tenant_id, link.job_id, link.candidate_id
        ),
        grade=report.grade,
        ai_score=grouped.get(CATEGORY_MATCHING, []),
        overall_grade=grade_for_percent(overall) or GRADES[-1],
        overall_summary=report.overall_summary,
        must_have=grouped.get(ppi.CATEGORY_MUST_HAVE, []),
        nice_to_have=grouped.get(ppi.CATEGORY_NICE_TO_HAVE, []),
        behavioural=grouped.get(ppi.CATEGORY_BEHAVIOURAL, []),
        # Empty on every report written from Draft v4 onward. A report written
        # before it still carries rows here and still renders them.
        technical=grouped.get(CATEGORY_TECHNICAL, []),
        validation=report.validation_json,
        proctoring=proctoring,
        gap_analysis=GapAnalysisOut.model_validate(report.gap_analysis_json or {}),
        # An EMPTY model, not None, when the column is NULL. The two sections
        # were added in 0107 and every report written before it has neither;
        # a client walking the section order must find an empty section it can
        # skip rather than a missing key it has to guard.
        claim_evidence=ClaimEvidenceOut.model_validate(
            report.claim_evidence_json or {}
        ),
        validation_points=ValidationPointsOut.model_validate(
            report.validation_points_json or {}
        ),
        # Populated only where Gap Analysis is not: a pre-Draft-v4 report shows
        # what it was actually written with rather than an empty section.
        suggested_interview_questions=(
            list(report.suggested_probes_json or [])
            if not (report.gap_analysis_json or {})
            else []
        ),
        radar_charts=[RadarChartOut(**chart) for chart in charts],
        radar_bands=list(RADAR_BANDS),
        radar_series=list(RADAR_SERIES),
        synthesized_at=report.synthesized_at,
        immutable=True,
        report_download_allowed=retention_consent.assessment_download_allowed(
            report_candidate
        ),
    )


@router.get("/reports/links/{link_id}/pdf")
async def download_report_pdf(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Download the immutable report with tenant-scoped authorization."""
    report_out = await get_report(link_id=link_id, user=user, session=session)
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Report not found")
    # Asked again rather than relied on through `get_report` above, the same
    # defensiveness the tenant check two lines up already applies: a gate that
    # only holds because of the order two calls happen in is a gate the next
    # reordering deletes without a diff that looks like a deletion. The Job is
    # in the session identity map by now, so the second ask is free.
    await _require_assessment_records_readable(session, user, link)
    candidate = await session.get(Candidate, link.candidate_id)
    # The candidate decides whether their assessment is retained beyond this
    # job (Consent & Privacy spec, 2026-09-05): Download enabled only on an
    # explicit Yes; No and never-asked both stay view-only. The on-screen
    # report route above is deliberately not gated.
    if not retention_consent.assessment_download_allowed(candidate):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The candidate consented to view-only access for this assessment.",
        )
    job = await session.get(Job, link.job_id)
    tenant = await session.get(Tenant, link.tenant_id)
    candidate_name = (candidate.full_name if candidate else None) or "Candidate"
    job_title = (job.title if job else None) or "Role"
    tenant_name = (tenant.name if tenant else None) or "Vivekium customer"
    # ReportLab is heavy and PDF downloads are infrequent; keep it off the API
    # startup path so ordinary requests do not pay its import cost.
    from app.services.report_pdf import render_report_pdf

    payload = render_report_pdf(
        report_out,
        candidate_name=candidate_name,
        job_title=job_title,
        tenant_name=tenant_name,
        generated_at=report_out.synthesized_at,
    )
    safe_name = (
        re.sub(r"[^A-Za-z0-9_-]+", "-", candidate_name).strip("-")
        or "candidate"
    )
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                # USER-VISIBLE, so it follows the copy rename rather than the
                # code names. Everything else in this file still says `ppi`
                # deliberately (a route is quoted in links already sitting in
                # inboxes), but a downloaded file lands on somebody's desktop
                # under whatever we call it, and that name should match the
                # header printed inside it.
                f'attachment; filename="prism-report-{safe_name}.pdf"'
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Report immutability ──────────────────────────────────────────────────────
# A generated report is a permanent record: it is what a hiring decision was
# made from, and a retake produces a NEW report ALONGSIDE the old one rather
# than overwriting it. There is no edit or delete affordance in the UI, and
# these handlers make the backend say so explicitly.
#
# Declaring them is deliberate. Without a registered handler, FastAPI answers a
# DELETE on this path with 405 Method Not Allowed -- which reads as "the route
# doesn't do that yet" rather than "this is forbidden by design", and would
# leave a future edit free to add one without anyone noticing the rule.

_IMMUTABLE_DETAIL = (
    "PPI Assessment Reports are immutable. A report records the assessment a "
    "hiring decision was made from, so it is never edited or deleted; a retake "
    "after six months generates a new report alongside this one."
)


# ── The recruiter's view of what was asked and answered ──────────────────────
#
# A report states a grade. This is the evidence behind it, and until 2026-08-06
# the only way to read it was a psql session against `assessment_messages`.
#
# WHY IT IS SHAPED THIS WAY, given it has to hold for a long time:
#
#   * PAIRED SERVER-SIDE. `assessment_messages` stores speakers in sequence,
#     which is the right shape to write and the wrong shape to read. Pairing in
#     the client means every client re-implements the follow-up and re-ask rules
#     (a probe shares its parent's question_key and is NOT a new question), and
#     they will drift. It is done once, here, by the module that owns those rules.
#
#   * PAGINATED FROM DAY ONE. A non-managerial interview is 45 base questions
#     plus up to 15 probes: 120 messages, several of them long. Returning all of
#     it grows without bound as grades get longer and is the kind of endpoint
#     that works fine until the first CXO pipeline with 200 candidates.
#
#   * KEYED ON THE LINK, not on the report. The transcript exists the moment the
#     candidate answers question one; the report does not exist until they
#     finish. A recruiter chasing a stalled assessment needs the former, and
#     hanging this off the report would make exactly that case unreachable.
#
#   * CRITERION RESOLVED, NOT LEAKED. Each exchange carries the skill or
#     competency it was filed under, as a WORD. That is what makes a transcript
#     readable as evidence. No score, no rubric and no required level crosses
#     this boundary -- the rubric is internal scoring machinery, and the
#     no-numbers rule covers this response like every other.

#: Bounded so one request cannot ask for an entire tenant's interview history.
#: 200 comfortably holds the longest single interview the product can produce
#: (28 base questions at non-managerial, plus follow-ups), so the common case is
#: one page. Left at 200 rather than lowered with the question counts: the bound
#: exists to stop an unbounded read, and a historic 45-question interview must
#: still page the same way.
TRANSCRIPT_MAX_LIMIT = 200
TRANSCRIPT_DEFAULT_LIMIT = 100


async def _criterion_labels(
    session: AsyncSession, link: JobCandidateLink, questions: list[CandidateQuestion]
) -> dict[str, str]:
    """{question_key: human label} for every key this candidate could produce.

    The scorer keys on a row id, so the raw transcript is a wall of UUIDs. This
    resolves them in a fixed number of queries rather than one per exchange --
    the N+1 here would be 60 round trips on an ordinary interview.

    TWO key shapes, because a transcript outlives the design that wrote it:

      CandidateQuestion.id  what the unified conversation stamps today
      JobCompetency.id      what PPI questions were keyed on before Draft v4

    Both are resolved rather than only the current one. A recruiter opening a
    report written last month is the exact case this screen exists for, and an
    unresolved key renders as a UUID beside the answer it labels. A third shape,
    the retired per-candidate technical track's row id, is gone with its table
    (migration 0128): it held no row anywhere it was dropped, so no stored
    transcript carries one.
    """
    labels: dict[str, str] = {}
    competencies = (
        await session.execute(
            select(JobCompetency.id, JobCompetency.name).where(
                JobCompetency.job_id == link.job_id
            )
        )
    ).all()
    by_competency = {competency_id: name for competency_id, name in competencies}
    for competency_id, name in by_competency.items():
        labels[str(competency_id)] = name

    for question in questions:
        name = by_competency.get(question.competency_id)
        if name:
            labels[str(question.id)] = name

    return labels


def _answer_key(row: CandidateQuestion) -> dict[str, Any]:
    """What the recruiter sees BESIDE the candidate's choice: the correct
    option ids, the accepted answers per blank, the approach a coding answer
    was read against. Prose and identifiers; never a score."""
    payload = dict(row.payload_json or {})
    if row.question_type == question_types.MCQ_SINGLE:
        return {"correct_option_id": payload.get("correct_option_id")}
    if row.question_type == question_types.MCQ_MULTI:
        return {"correct_option_ids": list(payload.get("correct_option_ids") or [])}
    if row.question_type == question_types.FILL_BLANK:
        return {
            "accepted": [
                list(blank.get("accepted") or [])
                for blank in sorted(payload.get("blanks") or [], key=lambda item: item.get("index", 0))
            ]
        }
    if row.question_type == question_types.CODING:
        return {"expected_approach": payload.get("expected_approach")}
    return {}


def _answer_detail(
    row: CandidateQuestion, record: AssessmentAnswer | None
) -> TranscriptAnswerDetailOut | None:
    """The per-format view of one answer (assessment-spec-doc 7).

    Correctness is a WORD, time spent is a PHRASE, and the AI evaluation is
    its reasoning and citations. `auto_score`, the per-criterion numbers and
    the seconds all stay on the row. None for a pre-format short-answer row
    with no structured record, which is every exchange written before 0076.
    """
    if record is None and row.question_type == question_types.SHORT_ANSWER:
        return None
    evaluation = dict(getattr(record, "ai_evaluation_json", None) or {})
    detail = TranscriptAnswerDetailOut(
        time_spent=format_rendering.time_spent_phrase(
            record.time_spent_seconds if record is not None else None
        ),
    )
    if question_types.is_structured(row.question_type):
        detail.payload = question_types.candidate_view(row.id, row.question_type, row.payload_json)
        detail.answer = dict(record.answer_json or {}) if record is not None else {}
        detail.answer_key = _answer_key(row)
    if row.question_type in question_types.OBJECTIVE_TYPES:
        detail.correctness = format_scoring.correctness_word(
            record.auto_score if record is not None else None
        )
        detail.blank_results = [str(item) for item in evaluation.get("blank_results") or []]
    if row.question_type in (question_types.EVIDENCE_BASED, question_types.CODING):
        detail.evaluation_reasoning = evaluation.get("reasoning") or None
        detail.evaluation_citations = [str(item) for item in evaluation.get("citations") or []]
        detail.not_executed_note = evaluation.get("not_executed_note") or None
    return detail


@router.get("/transcripts/links/{link_id}", response_model=TranscriptOut)
async def get_transcript(
    link_id: uuid.UUID,
    limit: int = TRANSCRIPT_DEFAULT_LIMIT,
    offset: int = 0,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TranscriptOut:
    """Every question this candidate was asked and every answer they gave.

    Gated on `view_review_screen`, the same capability that opens the report:
    someone who may read the grade may read the evidence for it, and someone who
    may not read the grade certainly may not read the candidate's raw answers.
    """
    limit = max(1, min(TRANSCRIPT_MAX_LIMIT, limit))
    offset = max(0, offset)

    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Application not found")
    await _require_assessment_records_readable(session, user, link)
    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()
    candidate_name = (
        await session.execute(select(Candidate.full_name).where(Candidate.id == link.candidate_id))
    ).scalar_one_or_none()
    job_title = (
        await session.execute(select(Job.title).where(Job.id == link.job_id))
    ).scalar_one_or_none()

    if conversation is None:
        # Invited but never opened, or not invited at all. An empty transcript
        # is the correct answer and a 404 is not: the recruiter asked a
        # reasonable question about a real application and "nothing yet" is the
        # true answer to it.
        return TranscriptOut(
            job_candidate_link_id=link.id,
            candidate_name=candidate_name,
            job_title=job_title,
            status="not_started",
            exchanges=[],
            total=0,
            limit=limit,
            offset=offset,
        )

    messages = (
        await session.execute(
            select(AssessmentMessage)
            .where(AssessmentMessage.conversation_id == conversation.id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).scalars().all()
    questions = await ppi_interview.load_for_link(session, link.id)
    labels = await _criterion_labels(session, link, questions)
    rows_by_key = {str(question.id): question for question in questions}
    records = {
        str(record.question_id): record
        for record in (
            await session.execute(
                select(AssessmentAnswer).where(
                    AssessmentAnswer.conversation_id == conversation.id
                )
            )
        ).scalars().all()
    }

    # Pair each agent line with the candidate line that answered it. Walking the
    # ordinals rather than zipping alternate rows: the last question of an
    # abandoned assessment has no answer, and zipping would silently pair it
    # with someone else's.
    exchanges: list[TranscriptExchangeOut] = []
    seen_keys: set[str] = set()
    pending: AssessmentMessage | None = None
    for message in messages:
        if message.speaker == "agent":
            pending = message
            continue
        if pending is None:
            continue
        key = pending.question_key or ""
        # A follow-up or re-ask reuses its parent's key by design, which is
        # exactly how the scorers file it as more evidence for one question. It
        # is therefore also how we recognise one here, with no extra column.
        follow_up = key in seen_keys
        if key:
            seen_keys.add(key)
        row = rows_by_key.get(key)
        exchanges.append(
            TranscriptExchangeOut(
                ordinal=len(exchanges) + 1,
                domain=pending.domain,
                question=pending.content,
                answer=message.content,
                criterion=labels.get(key),
                follow_up=follow_up,
                asked_at=pending.created_at,
                question_type=row.question_type if row is not None else None,
                resume_anchor=row.resume_anchor if row is not None else None,
                # The structured record belongs to the BASE exchange; a
                # follow-up is more evidence for the same question and shows
                # the anchor, never a second copy of the detail.
                detail=(
                    _answer_detail(row, records.get(key))
                    if row is not None and not follow_up
                    else None
                ),
            )
        )
        pending = None

    total = len(exchanges)
    return TranscriptOut(
        job_candidate_link_id=link.id,
        candidate_name=candidate_name,
        job_title=job_title,
        status=conversation.status,
        completed_at=conversation.completed_at,
        exchanges=exchanges[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/reports/links/{link_id}", status_code=status.HTTP_403_FORBIDDEN)
async def patch_report_forbidden(link_id: uuid.UUID) -> None:
    raise HTTPException(status_code=403, detail=_IMMUTABLE_DETAIL)


@router.put("/reports/links/{link_id}", status_code=status.HTTP_403_FORBIDDEN)
async def put_report_forbidden(link_id: uuid.UUID) -> None:
    raise HTTPException(status_code=403, detail=_IMMUTABLE_DETAIL)


@router.delete("/reports/links/{link_id}", status_code=status.HTTP_403_FORBIDDEN)
async def delete_report_forbidden(link_id: uuid.UUID) -> None:
    raise HTTPException(status_code=403, detail=_IMMUTABLE_DETAIL)
