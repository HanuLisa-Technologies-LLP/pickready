"""The proctored session's recording routes.

Carved out of `api/assessments.py` on 2026-09-24 (PLAN-p3 WP0), then rebuilt
in the same release (PLAN-p3 WP5):

  * THE VIDEO INTERVIEW MODE IS DELETED. Its start, question-mark, whole-file
    upload, finalize and status routes are gone with the answer-transcription
    pipeline behind them; `tests/test_video_interview_mode_removed.py` keeps
    them gone. What remains is the recording every proctored assessment keeps.
  * THE RECORDING ARRIVES IN SEGMENTS. The deleted whole-file upload route
    read the session, up to a gigabyte, into the shared API task with one
    `await file.read()`. Now the browser opens a segment (one S3 multipart
    upload), streams parts of at most `video_part_max_bytes` into it, closes
    it, and opens a new one after every device recovery. No request holds more
    than one part.
  * FINALIZE DISPATCHES AFTER COMMIT. Processing reads the rows finalize
    writes, so the task is handed off only once they are durable
    (`dispatch_after_commit`); a rolled-back request dispatches nothing.

Candidate routes run in the candidate's bypass-scoped session and check
ownership through the application, exactly as the conversation routes do.
The one staff route, retry, is the hiring team's: `view_review_screen` through
`rbac.authorize` over the job, so a scoped Recruiter, Hiring Manager or
Interview Manager reaches only the jobs they are assigned to.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assessment_conversation import _candidate_conversation, _candidate_link
from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_tenant_db,
    require_capability,
)
from app.core.config import get_settings
from app.models.assessment import AssessmentConversation
from app.models.dual_mode import VideoRecording, VideoRecordingSegment
from app.models.job import Job
from app.models.candidate import JobCandidateLink
from app.schemas.videos import (
    RecordingStatusOut,
    SegmentOpenIn,
    SegmentOut,
    SessionMediaStartOut,
)
from app.services import assessment_consent, job_assessment_retention
from app.services import assessment_video_access as video_access
from app.services import capabilities as caps
from app.services.audit import audit
# PROCTORING IS MANDATORY (proctoring-spec-doc.md, principle P4). The gate is
# this module's only dependency on the proctoring package, so the scoring
# isolation the package promises stays visible in the import graph.
from app.services.proctoring import gate as proctoring_gate
from app.services.video import lifecycle as video_lifecycle
from app.services.video import recordings as video_recordings
from app.services.video import storage as video_storage
from app.workers.dispatch import dispatch_after_commit

logger = logging.getLogger(__name__)

router = APIRouter()

#: The candidate's plain-language account of each status. Every failure says
#: what actually happened; none pretends progress.
_STATUS_MESSAGES: dict[str, str] = {
    video_lifecycle.RECORDING: "Your assessment is being recorded.",
    video_lifecycle.UPLOADED: (
        "Your recording was received and is waiting to be processed."
    ),
    video_lifecycle.COMPRESSING: "Your recording is being prepared for storage.",
    video_lifecycle.STORING: "Your recording is being stored.",
    video_lifecycle.READY: (
        "Your recording was stored successfully. The hiring team will review "
        "your assessment."
    ),
    video_lifecycle.UPLOAD_FAILED: (
        "No part of your recording reached us. Your answers are unaffected, "
        "and the hiring team can see that the recording is missing."
    ),
    video_lifecycle.COMPRESSION_FAILED: (
        "Your recording was received. Preparing it for storage did not "
        "complete; the team has been notified and no action is needed from you."
    ),
    video_lifecycle.STORAGE_FAILED: (
        "Your recording was received. Final storage did not complete; the "
        "team has been notified and no action is needed from you."
    ),
}


def _status_out(recording: VideoRecording) -> RecordingStatusOut:
    return RecordingStatusOut(
        recording_id=recording.id,
        status=recording.status,
        message=_STATUS_MESSAGES.get(
            recording.status, "Your recording is being handled."
        ),
    )


def _segment_out(segment: VideoRecordingSegment) -> SegmentOut:
    return SegmentOut(
        segment_id=segment.id,
        recording_id=segment.recording_id,
        ordinal=segment.ordinal,
        status=segment.status,
        parts_received=sorted(int(number) for number in (segment.parts_json or {})),
        bytes_received=int(segment.bytes or 0),
        final_part_number=segment.final_part_number,
    )


def _refusal(exc: video_recordings.RecordingRefused) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


async def _owned_conversation(
    session: AsyncSession, user: CurrentUser, conversation_id: uuid.UUID
) -> AssessmentConversation:
    """This candidate's own conversation, or 404."""
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _candidate_link(session, user, conversation.job_candidate_link_id)
    return conversation


async def _open_recording(
    session: AsyncSession, user: CurrentUser, conversation_id: uuid.UUID
) -> tuple[AssessmentConversation, VideoRecording]:
    conversation = await _owned_conversation(session, user, conversation_id)
    recording = await video_recordings.active_recording(session, conversation.id)
    if recording is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No recording is open for this assessment. Start the "
                "session's recording first."
            ),
        )
    return conversation, recording


async def _owned_segment(
    session: AsyncSession, recording: VideoRecording, segment_id: uuid.UUID
) -> VideoRecordingSegment:
    segment = await session.get(VideoRecordingSegment, segment_id)
    if segment is None or segment.recording_id != recording.id:
        raise HTTPException(status_code=404, detail="Recording segment not found")
    return segment


# ── Open the recording ───────────────────────────────────────────────────────


@router.post(
    "/conversations/links/{link_id}/session-media/start",
    response_model=SessionMediaStartOut,
)
async def start_session_media(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> SessionMediaStartOut:
    """Open (or reopen, after a reload) the proctored session's recording.

    THE GATE IS THE PROCTORING GATE: media is captured during a proctored
    assessment and at no other time, so a session that may not proceed may
    not be recorded either. There is no enable flag here because there is no
    enable flag for proctoring (principle P4). The assessment consent the
    candidate gave names the recording, so it is required too.
    """
    link, _job, conversation = await _candidate_conversation(session, user, link_id)
    await proctoring_gate.require_active(session, conversation)
    await assessment_consent.require_consent(session, conversation)
    recording = await video_recordings.create_recording(
        session, conversation, candidate_id=link.candidate_id
    )
    settings = get_settings()
    return SessionMediaStartOut(
        conversation_id=conversation.id,
        recording_id=recording.id,
        status=recording.status,
        max_upload_bytes=settings.video_max_upload_bytes,
        max_duration_seconds=settings.video_max_duration_seconds,
        part_max_bytes=settings.video_part_max_bytes,
        part_min_bytes=video_storage.MIN_PART_BYTES,
        video_bits_per_second=settings.video_recording_video_bps,
        audio_bits_per_second=settings.video_recording_audio_bps,
        max_width=settings.video_recording_max_width,
        max_height=settings.video_recording_max_height,
    )


# ── Segments and parts ───────────────────────────────────────────────────────


@router.post(
    "/conversations/{conversation_id}/recording/segments",
    response_model=SegmentOut,
)
async def open_recording_segment(
    conversation_id: uuid.UUID,
    body: SegmentOpenIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> SegmentOut:
    """Start the next segment: at the session's start, after a device
    recovery, or after a reload. Only while the proctored session is live."""
    conversation, recording = await _open_recording(session, user, conversation_id)
    await proctoring_gate.require_active(session, conversation)
    try:
        segment = await video_recordings.open_segment(
            session, recording, source_format=body.source_format
        )
    except video_recordings.RecordingRefused as exc:
        raise _refusal(exc) from exc
    return _segment_out(segment)


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused the moment it passes `limit`. A body is read
    in chunks and never beyond one byte over the ceiling, so an oversized
    upload costs this process a part's worth of memory and no more."""
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise HTTPException(
                status_code=413,
                detail="The recording part is larger than one upload accepts.",
            )
    return bytes(received)


@router.put(
    "/conversations/{conversation_id}/recording/segments/{segment_id}/parts/{part_number}",
    response_model=SegmentOut,
)
async def upload_recording_part(
    conversation_id: uuid.UUID,
    segment_id: uuid.UUID,
    part_number: int,
    request: Request,
    final: bool = Query(default=False),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> SegmentOut:
    """Receive one part (the raw request body) and forward it to the store.

    NOT gated on the proctoring session being live: a session that has just
    ended, including one ended by a termination, still owes the store its
    last parts, and that recording is exactly the one the hiring team will
    want. The recording itself must still be open.
    """
    _conversation, recording = await _open_recording(session, user, conversation_id)
    segment = await _owned_segment(session, recording, segment_id)
    data = await _read_capped(request, get_settings().video_part_max_bytes)
    try:
        segment = await video_recordings.record_part(
            session, recording, segment,
            part_number=part_number, data=data, final=final,
        )
    except video_recordings.RecordingRefused as exc:
        raise _refusal(exc) from exc
    return _segment_out(segment)


@router.post(
    "/conversations/{conversation_id}/recording/segments/{segment_id}/complete",
    response_model=SegmentOut,
)
async def complete_recording_segment(
    conversation_id: uuid.UUID,
    segment_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> SegmentOut:
    """Close one segment into one object. Idempotent."""
    _conversation, recording = await _open_recording(session, user, conversation_id)
    segment = await _owned_segment(session, recording, segment_id)
    segment = await video_recordings.complete_segment(session, segment)
    return _segment_out(segment)


# ── Finalize and status ──────────────────────────────────────────────────────


@router.post(
    "/conversations/{conversation_id}/recording/finalize",
    response_model=RecordingStatusOut,
)
async def finalize_recording(
    conversation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> RecordingStatusOut:
    """The candidate's session is over: close every segment, stamp the purge
    date, and hand the recording to processing once this commits.

    Idempotent: a recording already past `recording` answers its status and
    dispatches nothing. A tab closed before this call is finalized by the
    hourly sweep instead, from the store's own list of parts.
    """
    conversation = await _owned_conversation(session, user, conversation_id)
    recording = await video_recordings.latest_recording(session, conversation.id)
    if recording is None:
        raise HTTPException(
            status_code=409, detail="No recording exists for this assessment."
        )
    outcome = await video_recordings.finalize_recording(
        session, recording, ended_at=datetime.now(timezone.utc)
    )
    if outcome.dispatch:
        # Lost after commit? `pickready.reconcile_assessment_recordings`
        # re-dispatches a recording left in `uploaded` past the grace.
        dispatch_after_commit(
            session, "pickready.process_assessment_video", args=[str(recording.id)]
        )
    return _status_out(recording)


@router.get(
    "/conversations/{conversation_id}/recording/status",
    response_model=RecordingStatusOut,
)
async def recording_status(
    conversation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> RecordingStatusOut:
    """What is happening to the candidate's recording, honestly."""
    conversation = await _owned_conversation(session, user, conversation_id)
    recording = await video_recordings.latest_recording(session, conversation.id)
    if recording is None:
        raise HTTPException(
            status_code=404, detail="No recording exists for this assessment."
        )
    return _status_out(recording)


# ── Staff retry ──────────────────────────────────────────────────────────────


@router.post(
    "/videos/recordings/{recording_id}/retry",
    response_model=RecordingStatusOut,
)
async def retry_video_processing(
    recording_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> RecordingStatusOut:
    """Re-run processing for a recording whose compression or storage failed.

    THE HIRING TEAM'S ACT, and only theirs: `view_review_screen` resolved by
    `rbac.authorize` over THIS job, so tenant, grant and per-job assignment
    scope all run, and a cross-tenant id answers 404. The job-closure gate
    follows, because a closed job's recordings are withheld from everybody on
    the employer's side. `upload_failed` is not retryable: no bytes arrived.
    """
    recording = await session.get(VideoRecording, recording_id)
    if recording is None or recording.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Recording not found")
    link = await session.get(JobCandidateLink, recording.job_candidate_link_id)
    job = await session.get(Job, link.job_id) if link is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    await video_access.require_hiring_team(session, user, job.id)
    await job_assessment_retention.require_readable(session, user, job)
    if recording.status not in video_lifecycle.RETRYABLE_FAILURES:
        raise HTTPException(
            status_code=409,
            detail=(
                "This recording is not in a retryable state. Only a failed "
                "compression or storage step can be retried."
            ),
        )
    dispatch_after_commit(
        session, "pickready.process_assessment_video", args=[str(recording.id)]
    )
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="video_recording.retry",
        target_type="video_recording",
        target_id=recording.id,
        metadata={"status_at_retry": recording.status},
    )
    return _status_out(recording)

