"""Proctoring routes (proctoring-spec-doc.md sections 5, 8 and 9).

Candidate routes: the session, its events, its heartbeat and its audio. Each
one proves the caller is the candidate the application belongs to before it
reads a row, exactly as `api/assessments` does for the conversation. The one
staff route serves the report to a recruiter who may view the review screen,
and answers 404 for a link outside the tenant rather than 403, because a
cross-tenant read must not confirm the row exists.

WHAT THIS MODULE DOES NOT DO. It decides nothing. Every rule (which path an
event takes, whether a warning is issued, whether the session ends) lives in
`services/proctoring/ingestion.py`; every threshold in `config.py`. A route
here loads rows, checks ownership, hands off and maps three exceptions to
status codes: an ended session is 409, an over-limit browser is 429, an
unreachable Redis is 503. The last one is deliberate: a proctoring decision
that could not be made must never read as "nothing happened".
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_tenant_db,
    require_capability,
)
from app.models.assessment import AssessmentConversation
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.models.proctoring import OUTCOME_ACTIVE, ProctoringEvent, ProctoringSession
from app.schemas.proctoring import (
    AudioChunkOut,
    EventBatchIn,
    HeartbeatIn,
    HeartbeatOut,
    IngestOut,
    ProctoringConfigOut,
    ProctoringReportOut,
    SessionCreateIn,
    SessionOut,
)
from app.services import capabilities as caps
from app.services.proctoring import audio as proctoring_audio
from app.services.proctoring import catalog
from app.services.proctoring import gate as proctoring_gate
from app.services.proctoring import identity as proctoring_identity
from app.services.proctoring import ingestion
from app.services.proctoring import report as proctoring_report
from app.services.proctoring import state as proctoring_state
from app.services.proctoring.config import client_config, get_config

logger = logging.getLogger(__name__)

router = APIRouter()

#: The chunk types the browser's MediaRecorder produces and the analysis
#: service decodes in memory.
AUDIO_CONTENT_TYPES: frozenset[str] = frozenset({"audio/webm", "audio/wav"})

STATE_UNAVAILABLE_DETAIL = (
    "Monitoring could not be recorded just now. Your answers are safe; the "
    "page will retry."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _candidate_link(
    session: AsyncSession, user: CurrentUser, link_id: uuid.UUID
) -> tuple[JobCandidateLink, Job]:
    """The application, only if it belongs to the signed-in candidate."""
    candidate = (
        await session.execute(select(Candidate).where(Candidate.user_id == user.user_id))
    ).scalars().first()
    link = await session.get(JobCandidateLink, link_id)
    if candidate is None or link is None or link.candidate_id != candidate.id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return link, job


async def _candidate_session(
    session: AsyncSession, user: CurrentUser, session_id: uuid.UUID
) -> tuple[ProctoringSession, Job]:
    ps = await session.get(ProctoringSession, session_id)
    if ps is None:
        raise HTTPException(status_code=404, detail="Monitoring session not found")
    _link, job = await _candidate_link(session, user, ps.job_candidate_link_id)
    return ps, job


def _state_unavailable(exc: proctoring_state.StateUnavailable) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=STATE_UNAVAILABLE_DETAIL,
        headers={"Retry-After": "5"},
    )


def _session_out(ps: ProctoringSession, job: Job) -> SessionOut:
    config = get_config()
    return SessionOut(
        session_id=ps.id,
        conversation_id=ps.conversation_id,
        status=ps.outcome,
        warnings_used=ps.warnings_used,
        max_warnings=config.max_warnings,
        warning_policy=job.proctoring_warning_policy,
        consented_at=ps.consented_at,
        config=client_config(),
        audio_analysis_available=config.audio_analysis_available,
    )


@router.get("/config", response_model=ProctoringConfigOut)
async def get_client_config(
    _user: CurrentUser = Depends(get_current_candidate),
) -> ProctoringConfigOut:
    """The browser-side thresholds, from the same object the server reads."""
    config = get_config()
    return ProctoringConfigOut(
        config=client_config(),
        max_warnings=config.max_warnings,
        audio_analysis_available=config.audio_analysis_available,
    )


@router.post("/links/{link_id}/session", response_model=SessionOut)
async def create_session(
    link_id: uuid.UUID,
    body: SessionCreateIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> SessionOut:
    """Record consent and open the proctoring session (sections 8.1, 8.2).

    Idempotent: an existing active session for this conversation is returned
    with its descriptor and context refreshed, so a reload of the consent
    page does not open a second session or reset the warning count.
    """
    link, job = await _candidate_link(session, user, link_id)
    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()
    if conversation is None or conversation.invitation_sent_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This assessment is not open to you yet. The hiring team invites "
                "candidates individually, and you will be emailed if they invite you."
            ),
        )
    if not body.system_check.all_passed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Every system check has to pass before the assessment can begin. "
                "Fix the checks that failed and try again."
            ),
        )
    now = _now()
    config = get_config()
    quality = ingestion.session_quality_for(body.system_check.measured_fps, config)
    existing = await proctoring_gate.load_for_conversation(session, conversation.id)
    if existing is not None:
        if existing.outcome != OUTCOME_ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=proctoring_gate.SESSION_ENDED_DETAIL,
            )
        # THE BASELINE IS NOT OVERWRITTEN, and that is the whole point of it.
        #
        # This branch is reached when the consent page is reopened on a session
        # that is already running. Writing the incoming descriptor over the
        # stored one would let a different person install their own face as the
        # baseline and pass every later identity check, which defeats section
        # 3.3 completely and leaves no trace. So the first descriptor captured
        # for a session is the identity that session is anchored to, and a
        # later one is COMPARED against it, which is what the row is stored
        # for (`models/proctoring.py`). A mismatch is recorded on the ordinary
        # Path C event, so it feeds the same consecutive-mismatch rule a
        # mid-assessment check does rather than inventing a second rule.
        if existing.face_descriptor_baseline:
            distance = proctoring_identity.descriptor_distance(
                body.face_descriptor, existing.face_descriptor_baseline
            )
            if proctoring_identity.is_mismatch(distance, config):
                session.add(
                    ProctoringEvent(
                        tenant_id=existing.tenant_id,
                        proctoring_session_id=existing.id,
                        event_type="IDENTITY_CHECK_MISMATCH",
                        occurred_at=now,
                        path=catalog.PATH_C,
                        metadata_json={
                            proctoring_identity.MISMATCH_DISTANCE_KEY: round(distance, 4),
                            "source": "session_reopened",
                        },
                    )
                )
                logger.info(
                    "proctoring.reopened_with_a_different_face session_id=%s", existing.id
                )
        else:
            existing.face_descriptor_baseline = list(body.face_descriptor)
        existing.device_context = body.device_context.model_dump()
        existing.system_check = body.system_check.model_dump()
        existing.session_quality = quality
        existing.updated_at = now
        await session.flush()
        return _session_out(existing, job)
    ps = ProctoringSession(
        tenant_id=link.tenant_id,
        conversation_id=conversation.id,
        job_candidate_link_id=link.id,
        candidate_id=link.candidate_id,
        job_id=job.id,
        consented_at=now,
        started_at=now,
        outcome=OUTCOME_ACTIVE,
        warnings_used=0,
        face_descriptor_baseline=list(body.face_descriptor),
        device_context=body.device_context.model_dump(),
        system_check=body.system_check.model_dump(),
        session_quality=quality,
        last_heartbeat_at=now,
        updated_at=now,
    )
    session.add(ps)
    await session.flush()
    logger.info(
        "proctoring.session_created session_id=%s link_id=%s quality=%s",
        ps.id, link.id, quality,
    )
    return _session_out(ps, job)


@router.post("/sessions/{session_id}/events", response_model=IngestOut)
async def post_events(
    session_id: uuid.UUID,
    body: EventBatchIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> IngestOut:
    ps, job = await _candidate_session(session, user, session_id)
    try:
        return await ingestion.ingest(
            session, ps, job.proctoring_warning_policy, body, now=_now()
        )
    except ingestion.SessionEnded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=proctoring_gate.SESSION_ENDED_DETAIL
        )
    except ingestion.RateLimited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many monitoring events were sent in one minute.",
            headers={"Retry-After": "60"},
        )
    except proctoring_state.StateUnavailable as exc:
        raise _state_unavailable(exc) from exc


@router.post("/sessions/{session_id}/heartbeat", response_model=HeartbeatOut)
async def post_heartbeat(
    session_id: uuid.UUID,
    body: HeartbeatIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> HeartbeatOut:
    ps, _job = await _candidate_session(session, user, session_id)
    try:
        return await ingestion.heartbeat(
            session, ps,
            identity_matched=body.identity_matched,
            monitoring=body.monitoring.model_dump(),
            now=_now(),
        )
    except proctoring_state.StateUnavailable as exc:
        raise _state_unavailable(exc) from exc


@router.post("/sessions/{session_id}/audio", response_model=AudioChunkOut)
async def post_audio_chunk(
    session_id: uuid.UUID,
    chunk: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> AudioChunkOut:
    """One audio chunk, read into memory, analysed, destroyed. Never written
    anywhere (section 10)."""
    ps, job = await _candidate_session(session, user, session_id)
    config = get_config()
    content_type = (chunk.content_type or "").split(";")[0].strip().lower()
    if content_type not in AUDIO_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Audio chunks are accepted as audio/webm or audio/wav.",
        )
    data = await chunk.read()
    if len(data) > config.audio_max_chunk_bytes:
        del data
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="This audio chunk is larger than the assessment accepts.",
        )
    try:
        result = await proctoring_audio.analyse_chunk(
            session, ps, job.proctoring_warning_policy, data, content_type, now=_now()
        )
    except proctoring_state.StateUnavailable as exc:
        raise _state_unavailable(exc) from exc
    finally:
        del data
    return result


@router.get("/links/{link_id}/report", response_model=ProctoringReportOut)
async def get_proctoring_report(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ProctoringReportOut:
    """The report on its own. It is also joined onto the PRISM Report by
    `api/assessments.get_report`; this route exists for the review screen to
    show it before, or without, the PRISM Report."""
    link = await session.get(JobCandidateLink, link_id)
    if link is None or link.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Report not found")
    report = await proctoring_report.load_report_out(session, link.id)
    if report is None:
        raise HTTPException(status_code=404, detail="The proctoring report is not ready")
    return report
