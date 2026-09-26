"""The PRISM Report's read routes: the report, its PDF, its citations and the
recruiter's transcript (PLAN-p5 WP5-F, the Vivekium release).

Moved here from `api/assessments.py` with every URL byte-identical: this
router is mounted under the same `/api/v2/assessments` prefix, so a report link
already sitting in somebody's inbox still resolves. The payload is built by
ONE serializer, `services/prism_view.report_out`, which the PDF route shares,
so the screen and the downloaded document cannot state different grades.

THREE READS ARE AUDITED, each in the ONE insert `record_action` makes inside
the request's transaction: the PDF download (the copy that leaves the
product), the transcript (the candidate's raw answers) and the citation view
(which resolves report statements back to those answers). The on-screen
report is not audited per open; it is the reading surface a reviewer works
from.

The route paths still say `assessments` and `reports`; the user-visible names
are Tatva Assessment and PRISM Report, and a route is quoted in already-issued
links, so the path is not renamed.
"""
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
)
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.assessments import (
    FunctionalReportOut,
    ReportCitationsOut,
    TranscriptAnswerDetailOut,
    TranscriptExchangeOut,
    TranscriptOut,
)
from app.services import audit
from app.services import capabilities as caps
from app.services import (
    job_assessment_retention,
    ppi_interview,
    prism_view,
    retention_consent,
)
from app.services.assessment_formats import rendering as format_rendering
from app.services.assessment_formats import scoring as format_scoring
from app.services.assessment_formats import types as question_types
from app.services.coding_assessment import payload as coding_payload
from app.services.coding_assessment import transcript as coding_transcript

logger = logging.getLogger(__name__)

router = APIRouter()


# ── The job-closure gate ─────────────────────────────────────────────────────


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


async def _application_in_tenant(
    session: AsyncSession, user: CurrentUser, link_id: uuid.UUID, *, not_found: str
) -> JobCandidateLink:
    """The application, or 404, then the closure gate (410). Cross-tenant reads
    answer 404, never 403."""
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail=not_found)
    await _require_assessment_records_readable(session, user, link)
    return link


async def _report_for(
    session: AsyncSession, link: JobCandidateLink
) -> FunctionalSkillsReport:
    report = await prism_view.load_report(session, link.id)
    if report is None:
        raise HTTPException(status_code=404, detail=prism_view.REPORT_NOT_READY)
    return report


def _actor_role(user: CurrentUser) -> str | None:
    return getattr(user.role, "value", user.role)


# ── The PRISM Report ─────────────────────────────────────────────────────────


@router.get("/reports/links/{link_id}", response_model=FunctionalReportOut)
async def get_report(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> FunctionalReportOut:
    link = await _application_in_tenant(session, user, link_id, not_found="Report not found")
    report = await _report_for(session, link)
    return await prism_view.report_out(session, link, report)


@router.get("/reports/links/{link_id}/pdf")
async def download_report_pdf(
    link_id: uuid.UUID,
    request: Request,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """Download the immutable report: consent, then G4, then the number ban.

    GATE G4, THEN THE PDF (P5-D7). A report routed to a person is not
    downloaded until somebody has recorded a decision on it. The on-screen
    report above is deliberately NOT gated: it is where that person reads the
    report. `prism_pdf` takes the clearance G4 mints and runs the number ban on
    its own input, so neither can be skipped from here.
    """
    from app.services.siddhi import delivery  # noqa: PLC0415

    link = await _application_in_tenant(session, user, link_id, not_found="Report not found")
    report = await _report_for(session, link)
    candidate = await session.get(Candidate, link.candidate_id)
    # The candidate decides whether their assessment is retained beyond this
    # job (Consent & Privacy spec, 2026-09-05): Download enabled only on an
    # explicit Yes; No and never-asked both stay view-only.
    if not retention_consent.assessment_download_allowed(candidate):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The candidate consented to view-only access for this assessment.",
        )
    try:
        clearance = await delivery.gate_delivery(session, report)
    except delivery.DeliveryBlocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=delivery.PDF_BLOCKED_REASON,
        ) from None
    report_out = await prism_view.report_out(session, link, report)
    job = await session.get(Job, link.job_id)
    tenant = await session.get(Tenant, link.tenant_id)
    candidate_name = (candidate.full_name if candidate else None) or "Candidate"
    payload = delivery.prism_pdf(
        clearance,
        report_out,
        candidate_name=candidate_name,
        job_title=(job.title if job else None) or "Role",
        tenant_name=(tenant.name if tenant else None) or "Vivekium customer",
        generated_at=report_out.synthesized_at,
    )
    # Written only once the bytes exist: a refused or failed render downloaded
    # nothing, and a row saying otherwise would be a false record.
    await audit.record_action(
        session,
        action=audit.PRISM_PDF_DOWNLOADED,
        actor_user_id=user.user_id,
        actor_role=_actor_role(user),
        tenant_id=user.tenant_id,
        resource_type="functional_skills_report",
        resource_id=report.id,
        job_id=link.job_id,
        application_id=link.id,
        candidate_id=link.candidate_id,
        request_method=request.method,
        request_path=request.url.path,
        metadata={"gate": clearance.as_dict()},
    )
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", candidate_name).strip("-") or "candidate"
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            # USER-VISIBLE, so it follows the copy name, not the code name.
            "Content-Disposition": f'attachment; filename="prism-report-{safe_name}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/reports/links/{link_id}/citations", response_model=ReportCitationsOut)
async def get_report_citations(
    link_id: uuid.UUID,
    request: Request,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ReportCitationsOut:
    """What each statement of the PRISM Report rests on: the question asked,
    the answer given, the passage read, resolved at READ time from the stored
    trail's locators and scoped to this application alone.

    The same capability as the report and the transcript: someone who may
    read the grade may read the evidence for it. Words and the candidate's own
    text only; no id, no locator and no position crosses this boundary. A
    report written before the trail existed answers `trail_available: false`.
    """
    from app.services.siddhi import trail as siddhi_trail  # noqa: PLC0415

    link = await _application_in_tenant(session, user, link_id, not_found="Report not found")
    report = await _report_for(session, link)
    # A cited passage may come from this application's own transcript chunks
    # or from the resume it was sent with, and from nowhere else.
    sources = {
        source
        for source in (link.id, link.profile_id, link.yukti_profile_id)
        if source is not None
    }
    view = await siddhi_trail.citation_view(
        session,
        report.gap_analysis_json,
        link_id=link.id,
        chunk_source_ids=sources,
    )
    await audit.record_action(
        session,
        action=audit.PRISM_CITATIONS_VIEWED,
        actor_user_id=user.user_id,
        actor_role=_actor_role(user),
        tenant_id=user.tenant_id,
        resource_type="functional_skills_report",
        resource_id=report.id,
        job_id=link.job_id,
        application_id=link.id,
        candidate_id=link.candidate_id,
        request_method=request.method,
        request_path=request.url.path,
    )
    return ReportCitationsOut.model_validate(view)


# ── Report immutability ──────────────────────────────────────────────────────
# A generated report is a permanent record: it is what a hiring decision was
# made from. There is no edit or delete affordance in the UI, the database
# refuses an UPDATE (migration 0130), and these handlers make the API say so.
#
# Declaring them is deliberate. Without a registered handler, FastAPI answers a
# DELETE on this path with 405 Method Not Allowed, which reads as "the route
# does not do that yet" rather than "this is forbidden by design".

_IMMUTABLE_DETAIL = (
    "PRISM Reports are immutable. A report records the assessment a hiring "
    "decision was made from, so it is never edited or deleted."
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
    option ids, the accepted answers per blank, the approach a LEGACY coding
    answer was read against. Prose and identifiers; never a score.

    An executed (v2) coding question has NO key here, deliberately: its hidden
    tests, reference solution and approach notes live in the answer-key table
    that no response reads, and what the recruiter reads instead is the
    outcome sentence and the code review (`coding_transcript`, Phase 4 WP-4C).
    """
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
        if coding_payload.is_v2(payload):
            return {}
        return {"expected_approach": payload.get("expected_approach")}
    return {}


def _answer_detail(
    row: CandidateQuestion,
    record: AssessmentAnswer | None,
    coding: coding_transcript.RecruiterView | None = None,
) -> TranscriptAnswerDetailOut | None:
    """The per-format view of one answer (assessment-spec-doc 7).

    Correctness is a WORD, time spent is a PHRASE, and the AI evaluation is
    its reasoning and citations. `auto_score`, the per-criterion numbers and
    the seconds all stay on the row. None for a pre-format short-answer row
    with no structured record, which is every exchange written before 0076.

    `coding` is the executed coding answer's recruiter view: the outcome
    SENTENCE (counts spelled out), the compiler's message when it did not
    compile, and the code review's reasoning and citations. None for every
    other answer, including a legacy coding answer that was read, not run,
    whose `not_executed_note` still comes from its stored evaluation.
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
    if coding is not None:
        detail.coding_outcome = coding.outcome
        detail.compile_error = coding.compile_error
        detail.review_reasoning = coding.review_reasoning
        detail.review_citations = list(coding.review_citations)
    return detail


@router.get("/transcripts/links/{link_id}", response_model=TranscriptOut)
async def get_transcript(
    link_id: uuid.UUID,
    request: Request,
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

    link = await _application_in_tenant(
        session, user, link_id, not_found="Application not found"
    )
    # AUDITED after the gates and before anything is read: a refused request
    # read nothing, and every request that reaches here reads the answers.
    await audit.record_action(
        session,
        action=audit.ASSESSMENT_TRANSCRIPT_VIEWED,
        actor_user_id=user.user_id,
        actor_role=_actor_role(user),
        tenant_id=user.tenant_id,
        resource_type="job_candidate_link",
        resource_id=link.id,
        job_id=link.job_id,
        application_id=link.id,
        candidate_id=link.candidate_id,
        request_method=request.method,
        request_path=request.url.path,
        metadata={"offset": offset, "limit": limit},
    )
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
    coding_views = await coding_transcript.recruiter_views(session, conversation.id)
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
                    _answer_detail(row, records.get(key), coding_views.get(row.id))
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
