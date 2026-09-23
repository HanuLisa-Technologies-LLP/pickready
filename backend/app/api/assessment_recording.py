"""The assessment's recording routes: the video interview and the proctored session's media.

Carved out of `api/assessments.py` on 2026-09-24 (PLAN-p3 WP0) as a PURE MOVE:
identical URLs under the same `/api/v2/assessments` prefix, identical
dependencies, identical behaviour. The candidate-side helpers these routes
share with the conversation (`_candidate_conversation`, `_mode_frozen`, the
consent and mode state) live in `api/assessment_conversation.py`.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_tenant_db,
    require_capability,
)
from app.models.assessment import (
    AssessmentConversation,
    CandidateQuestion,
)
from app.schemas.assessments import (
    VideoMarkIn,
    VideoQuestionOut,
    VideoRecordingStatusOut,
    VideoStartOut,
)
# The proctored session's recording start. It lives beside the client-portal
# video schemas rather than with the assessment ones because it is the
# candidate-side half of the SAME artifact those schemas deliver, and both
# halves are bound by the same rule: no bucket name and no object key.
from app.schemas.videos import SessionMediaStartOut
from app.services import capabilities as caps
from app.services import (
    assessment_consent,
    hiring_pipeline,
)
# DUAL-MODE ASSESSMENT (2026-09-05 spec). The video package is reached ONLY by
# these routes and the processing task, never by a scorer; the models carry the
# consent audit and the recording lifecycle.
from app.models.dual_mode import (
    MODE_VIDEO_INTERVIEW,
    RECORDING_PROCTORED_SESSION,
    RECORDING_VIDEO_INTERVIEW,
    VideoRecording,
)
from app.services.video import lifecycle as video_lifecycle
from app.services.video import recordings as video_recordings
from app.services.video import storage as video_storage
from app.services.audit import audit
# PROCTORING IS MANDATORY (proctoring-spec-doc.md, principle P4). The gate is
# this module's only import-time dependency on the proctoring package; the
# behaviour recorder and the report loader are reached inside the handlers
# that need them, so the scoring isolation the package promises stays
# visible in the import graph as gate-plus-report-join and nothing else.
from app.services.proctoring import gate as proctoring_gate
from app.workers.dispatch import dispatch

from app.api.assessment_conversation import (
    _candidate_conversation,
    _candidate_link,
    _conversation_prompts,
    _ensure_conversation_ready,
    _question_out,
)
from app.core.config import get_settings as _get_settings
from app.services.video import keys as video_keys

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/conversations/links/{link_id}/video/start", response_model=VideoStartOut)
async def start_video_interview(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VideoStartOut:
    """Open the video interview: the mode's analog of the conversational
    start, gate for gate (invitation, proctoring, mode, consent), plus the
    recording session the browser uploads into.

    The questions are returned as ONE served list rather than one per turn:
    a video interview shows each question while the camera runs and the
    candidate answers in speech, so there is no per-turn request for a
    rewritten prompt. The stored prompts were generated per candidate by
    `assessment_questions.generate.generate_candidate_questions` from this candidate's own resume, so
    every question is already theirs. The answer key still never crosses:
    `_question_out` serves the candidate view only.
    """
    link, job, conversation = await _candidate_conversation(session, user, link_id)
    await proctoring_gate.require_active(session, conversation)
    if conversation.mode != MODE_VIDEO_INTERVIEW:
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment is set to the conversational mode. Choose "
                "the video interview first if you want to answer on camera."
            ),
        )
    await assessment_consent.require_consent(session, conversation)
    await _ensure_conversation_ready(session, job, link)
    if conversation.started_at is None:
        conversation.started_at = datetime.now(timezone.utc)
        await session.flush()
        if hiring_pipeline.can_transition(
            link.status, hiring_pipeline.ASSESSMENT_IN_PROGRESS
        ):
            await hiring_pipeline.apply_transition(
                session,
                link_id=link.id,
                tenant_id=link.tenant_id,
                target=hiring_pipeline.ASSESSMENT_IN_PROGRESS,
            )
    recording = await video_recordings.create_recording(
        session,
        conversation,
        candidate_id=link.candidate_id,
        source_format=None,
        kind=RECORDING_VIDEO_INTERVIEW,
    )
    prompts = await _conversation_prompts(session, job, link)
    settings = _get_settings()
    return VideoStartOut(
        conversation_id=conversation.id,
        recording_id=recording.id,
        status=recording.status,
        questions=[
            VideoQuestionOut(
                ordinal=index, prompt=row.prompt, question=_question_out(row)
            )
            for index, (_aspect, _key, _stored, row) in enumerate(prompts)
        ],
        max_upload_bytes=settings.video_max_upload_bytes,
        max_duration_seconds=settings.video_max_duration_seconds,
    )


@router.post(
    "/conversations/links/{link_id}/session-media/start",
    response_model=SessionMediaStartOut,
)
async def start_session_media(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> SessionMediaStartOut:
    """Open the proctored session's recording (owner ruling, 2026-09-22).

    THE GATE IS THE PROCTORING GATE, and that is the whole authorization
    story: media is captured during a proctored assessment and at no other
    time, so a session that may not proceed may not be recorded either. There
    is no enable flag here, because there is no enable flag for proctoring
    (principle P4, re-affirmed by the same ruling).

    A VIDEO INTERVIEW IS REFUSED, not silently accepted. In that mode the
    interview recording already captures the same camera for the same
    minutes and is already stored, compressed and served through the same
    routes; opening a second recording would store the candidate twice and
    give the hiring team two artifacts to choose between.

    The browser then uses the EXISTING upload, finalize and status routes.
    They are not duplicated for this kind: one recording lifecycle, one
    upload path, one processing task, and the row's `kind` is what decides
    which half of the pipeline the bytes take.
    """
    link, job, conversation = await _candidate_conversation(session, user, link_id)
    await proctoring_gate.require_active(session, conversation)
    if conversation.mode == MODE_VIDEO_INTERVIEW:
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment is a video interview, and that recording is "
                "already the stored record of the session."
            ),
        )
    await assessment_consent.require_consent(session, conversation)
    await _ensure_conversation_ready(session, job, link)
    recording = await video_recordings.create_recording(
        session,
        conversation,
        candidate_id=link.candidate_id,
        source_format=None,
        kind=RECORDING_PROCTORED_SESSION,
    )
    settings = _get_settings()
    return SessionMediaStartOut(
        conversation_id=conversation.id,
        recording_id=recording.id,
        status=recording.status,
        max_upload_bytes=settings.video_max_upload_bytes,
        max_duration_seconds=settings.video_max_duration_seconds,
    )


async def _candidate_recording(
    session: AsyncSession, user: CurrentUser, conversation_id: uuid.UUID
) -> tuple[AssessmentConversation, VideoRecording]:
    """This candidate's own conversation and its open (or latest) recording."""
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _candidate_link(session, user, conversation.job_candidate_link_id)
    recording = await video_recordings.active_recording(session, conversation.id)
    if recording is None:
        recording = await video_recordings.recording_for_link(
            session, conversation.job_candidate_link_id
        )
    if recording is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No recording session is open for this assessment. Start "
                "the video interview first."
            ),
        )
    return conversation, recording


@router.post(
    "/conversations/{conversation_id}/video/mark",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def mark_video_question(
    conversation_id: uuid.UUID,
    body: VideoMarkIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> Response:
    """The next-question control: stamp, with the SERVER's clock, that this
    question just reached the screen. The transcript is segmented by these
    marks; a client-reported timeline would be a timeline the client chose."""
    conversation, recording = await _candidate_recording(session, user, conversation_id)
    question = await session.get(CandidateQuestion, body.question_id)
    if (
        question is None
        or question.job_candidate_link_id != conversation.job_candidate_link_id
    ):
        raise HTTPException(status_code=404, detail="Question not found")
    if recording.status != video_lifecycle.RECORDING:
        raise HTTPException(
            status_code=409,
            detail="This recording is no longer accepting question marks.",
        )
    await video_recordings.mark_question_shown(
        session, recording,
        question_id=question.id, question_type=question.question_type,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _recording_status_out(recording: VideoRecording) -> VideoRecordingStatusOut:
    """The candidate's honest processing status, in words (spec 16). Each
    failure message says what actually happened; none pretends progress."""
    messages = {
        video_lifecycle.RECORDING: "Your interview is being recorded.",
        video_lifecycle.UPLOADING: "Your recording is uploading.",
        video_lifecycle.UPLOADED: (
            "Your recording was received and is waiting to be processed."
        ),
        video_lifecycle.PROCESSING: (
            "Your recording is being processed and your answers are being "
            "transcribed."
        ),
        video_lifecycle.COMPRESSING: (
            "Your answers were transcribed. The recording is being prepared "
            "for storage."
        ),
        video_lifecycle.STORING: "Your recording is being stored.",
        video_lifecycle.READY: (
            "Your interview was processed successfully. The hiring team will "
            "review your assessment."
        ),
        video_lifecycle.UPLOAD_FAILED: (
            "The upload did not complete. Please retry the upload from this "
            "browser."
        ),
        video_lifecycle.PROCESSING_FAILED: (
            "Processing did not complete. Your recording is stored safely and "
            "the team has been notified; no action is needed from you."
        ),
        video_lifecycle.TRANSCRIPTION_FAILED: (
            "Your recording is stored safely, but transcription could not run "
            "yet. The team has been notified; no action is needed from you."
        ),
        video_lifecycle.COMPRESSION_FAILED: (
            "Your answers were captured. Final storage preparation did not "
            "complete; the team has been notified."
        ),
        video_lifecycle.STORAGE_FAILED: (
            "Your answers were captured. Final storage did not complete; the "
            "team has been notified."
        ),
    }
    return VideoRecordingStatusOut(
        recording_id=recording.id,
        status=recording.status,
        message=messages.get(recording.status, "Your recording is being handled."),
        can_retry_upload=recording.status == video_lifecycle.UPLOAD_FAILED,
    )


@router.post(
    "/conversations/{conversation_id}/video/upload",
    response_model=VideoRecordingStatusOut,
)
async def upload_video_recording(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VideoRecordingStatusOut:
    """Receive the finished recording and store it at the recording's raw key.

    A failed store is RECORDED (`upload_failed`) and returned as a status the
    candidate can retry from, rather than raised: an exception here would roll
    back the very row that tells anyone the upload failed.
    """
    conversation, recording = await _candidate_recording(session, user, conversation_id)
    if recording.status not in (
        video_lifecycle.RECORDING,
        video_lifecycle.UPLOADING,
        video_lifecycle.UPLOAD_FAILED,
    ):
        raise HTTPException(
            status_code=409,
            detail="This recording has already been uploaded.",
        )
    data = await file.read()
    try:
        video_recordings.validate_upload(data, file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The MediaRecorder's real MIME type arrives with the bytes; the raw key's
    # extension is derived from it through the fixed table in `video/keys.py`,
    # never from the client string itself.
    recording.source_format = (
        (file.content_type or "").split(";")[0] or recording.source_format
    )
    recording.s3_raw_key = video_keys.raw_key(
        conversation.id, recording.id, recording.source_format
    )
    if recording.status != video_lifecycle.UPLOADING:
        video_lifecycle.advance(recording, video_lifecycle.UPLOADING)
    await session.flush()
    try:
        await run_in_threadpool(
            video_storage.put,
            key=recording.s3_raw_key,
            data=data,
            content_type=recording.source_format or "video/webm",
        )
    except Exception as exc:  # noqa: BLE001 -- recorded as the honest status
        logger.warning(
            "video_upload.failed recording_id=%s error=%s",
            recording.id, type(exc).__name__,
        )
        video_lifecycle.advance(
            recording, video_lifecycle.UPLOAD_FAILED,
            error_detail="The object store refused or dropped the upload.",
        )
        await session.flush()
        return _recording_status_out(recording)
    video_lifecycle.advance(recording, video_lifecycle.UPLOADED)
    recording.file_size_bytes = len(data)
    recording.ended_at = datetime.now(timezone.utc)
    await session.flush()
    return _recording_status_out(recording)


@router.post(
    "/conversations/{conversation_id}/video/finalize",
    response_model=VideoRecordingStatusOut,
)
async def finalize_video_interview(
    conversation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VideoRecordingStatusOut:
    """The candidate is done: hand the uploaded recording to the processing
    pipeline (spec section 6). Asynchronous by rule 4; the response is the
    honest status the candidate polls afterwards. Idempotent past `uploaded`:
    a second finalize re-dispatches nothing the pipeline has moved on from."""
    _conversation, recording = await _candidate_recording(session, user, conversation_id)
    if recording.status in (
        video_lifecycle.RECORDING,
        video_lifecycle.UPLOADING,
        video_lifecycle.UPLOAD_FAILED,
    ):
        raise HTTPException(
            status_code=409,
            detail="The recording has not finished uploading yet.",
        )
    if recording.status == video_lifecycle.UPLOADED:
        dispatch("pickready.process_assessment_video", args=[str(recording.id)])
    return _recording_status_out(recording)


@router.get(
    "/conversations/{conversation_id}/video/status",
    response_model=VideoRecordingStatusOut,
)
async def video_recording_status(
    conversation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VideoRecordingStatusOut:
    """What is happening to the candidate's recording, honestly (spec 16)."""
    _conversation, recording = await _candidate_recording(session, user, conversation_id)
    return _recording_status_out(recording)


@router.post(
    "/videos/recordings/{recording_id}/retry",
    response_model=VideoRecordingStatusOut,
)
async def retry_video_processing(
    recording_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> VideoRecordingStatusOut:
    """Re-run the processing pipeline for a failed recording (spec 16: failed
    asynchronous jobs are retryable).

    Gated on `view_review_screen`, the capability that already opens this
    candidate's transcript and report: retrying a stalled pipeline is an act
    of reviewing the assessment, not of deciding it. Only the server-side
    failure states are retryable; `upload_failed` has no bytes to retry with
    and stays with the candidate.
    """
    recording = await session.get(VideoRecording, recording_id)
    if recording is None or recording.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Recording not found")
    if recording.status not in video_lifecycle.RETRYABLE_FAILURES:
        raise HTTPException(
            status_code=409,
            detail=(
                "This recording is not in a retryable state. Only a failed "
                "processing, transcription, compression or storage step can "
                "be retried."
            ),
        )
    dispatch("pickready.process_assessment_video", args=[str(recording.id)])
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="video_recording.retry",
        target_type="video_recording",
        target_id=recording.id,
        metadata={"status_at_retry": recording.status},
    )
    return _recording_status_out(recording)
