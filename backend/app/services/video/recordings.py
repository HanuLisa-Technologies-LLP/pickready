"""The session recording's rows: open, segments, parts, finalize, purge date.

The API layer and the orphan sweep call these; the processing task lives in
`processing.py`. The Video Service owns the recording lifecycle and its
metadata, the Processing Service owns what happens to the bytes afterwards.

HOW THE BYTES ARRIVE (Vivekium release, 2026-09-24)
-----------------------------------------------------
The recording used to be buffered whole in the browser and posted once, into
`await file.read()` of up to a gigabyte in the shared API task. Now it is a
sequence of SEGMENTS, each one S3 multipart upload, each fed one part at a
time:

    open_segment  -> CreateMultipartUpload at seg-{ordinal}.<ext>
    record_part   -> UploadPart, at most `video_part_max_bytes`, at least
                     S3's 5 MiB unless the browser marks it the final part
    complete_segment -> CompleteMultipartUpload from the recorded ETags
    finalize_recording -> every segment closed, `uploaded`, purge date stamped

A new segment starts after every camera or microphone recovery, because a
MediaRecorder that lost its device does not resume the same stream.

`finalize_recording` is also what the hourly sweep calls for a recording whose
tab closed mid-session (`orphaned_recordings`): it completes each open segment
from the STORE's own list of parts, so a part S3 accepted is never lost to a
database write that did not land.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentConversation
from app.models.dual_mode import (
    RECORDING_PROCTORED_SESSION,
    SEGMENT_ABORTED,
    SEGMENT_COMPLETED,
    SEGMENT_OPEN,
    VideoRecording,
    VideoRecordingSegment,
)
from app.services.video import keys, lifecycle, storage

logger = logging.getLogger(__name__)


class RecordingRefused(ValueError):
    """A request the recording cannot accept. The message is the candidate's
    sentence; `status_code` is the HTTP answer the route gives it."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Reads ────────────────────────────────────────────────────────────────────


async def active_recording(
    session: AsyncSession, conversation_id: uuid.UUID
) -> VideoRecording | None:
    """The one recording still accepting segments, if any."""
    return (
        await session.execute(
            select(VideoRecording)
            .where(
                VideoRecording.conversation_id == conversation_id,
                VideoRecording.status == lifecycle.RECORDING,
            )
            .order_by(VideoRecording.created_at.desc())
        )
    ).scalars().first()


async def latest_recording(
    session: AsyncSession, conversation_id: uuid.UUID
) -> VideoRecording | None:
    """The newest recording of this session in any state."""
    return (
        await session.execute(
            select(VideoRecording)
            .where(VideoRecording.conversation_id == conversation_id)
            .order_by(VideoRecording.created_at.desc())
        )
    ).scalars().first()


async def recording_for_link(
    session: AsyncSession, link_id: uuid.UUID
) -> VideoRecording | None:
    """The most recent recording on an application. The accessor the client
    portal's video access layer builds on: it exposes the status, the
    compressed key and the lifecycle facts, and nothing about delivery."""
    return (
        await session.execute(
            select(VideoRecording)
            .where(VideoRecording.job_candidate_link_id == link_id)
            .order_by(VideoRecording.created_at.desc())
        )
    ).scalars().first()


async def segments_for(
    session: AsyncSession, recording_id: uuid.UUID
) -> list[VideoRecordingSegment]:
    """This recording's segments in recording order."""
    return list(
        (
            await session.execute(
                select(VideoRecordingSegment)
                .where(VideoRecordingSegment.recording_id == recording_id)
                .order_by(VideoRecordingSegment.ordinal)
            )
        ).scalars().all()
    )


# ── Open ─────────────────────────────────────────────────────────────────────


async def create_recording(
    session: AsyncSession,
    conversation: AssessmentConversation,
    *,
    candidate_id: uuid.UUID,
) -> VideoRecording:
    """Open (or reopen) the session's recording.

    Reuses the open recording rather than minting a sibling: a page reload
    mid-session must come back to the same recording, whose next segment is
    simply the next ordinal. One session records once.
    """
    existing = await active_recording(session, conversation.id)
    if existing is not None:
        return existing
    recording = VideoRecording(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        candidate_id=candidate_id,
        job_candidate_link_id=conversation.job_candidate_link_id,
        status=lifecycle.RECORDING,
        kind=RECORDING_PROCTORED_SESSION,
        started_at=datetime.now(timezone.utc),
    )
    session.add(recording)
    await session.flush()
    recording.s3_compressed_key = keys.compressed_key(conversation.id, recording.id)
    await session.flush()
    return recording


def _source_subtype(content_type: str | None) -> str | None:
    subtype = (content_type or "").split(";")[0].strip().lower()
    if not subtype:
        return None
    if not subtype.startswith("video/"):
        raise RecordingRefused(
            "The recording must be a video stream.", status_code=422
        )
    return subtype


async def open_segment(
    session: AsyncSession,
    recording: VideoRecording,
    *,
    source_format: str | None,
) -> VideoRecordingSegment:
    """Start the next segment: a fresh multipart upload at the next ordinal.

    The ordinal is `max + 1` under the row lock on the recording, so two tabs
    opening at once cannot both claim the same number (the UNIQUE on
    (recording, ordinal) would refuse the second, and the lock turns that
    refusal into an ordering instead of a 500).
    """
    if recording.status != lifecycle.RECORDING:
        raise RecordingRefused("This recording is no longer accepting video.")
    await session.execute(
        select(VideoRecording.id)
        .where(VideoRecording.id == recording.id)
        .with_for_update()
    )
    count, highest = (
        await session.execute(
            select(
                func.count(VideoRecordingSegment.id),
                func.max(VideoRecordingSegment.ordinal),
            ).where(VideoRecordingSegment.recording_id == recording.id)
        )
    ).one()
    if int(count or 0) >= get_settings().video_max_segments:
        raise RecordingRefused(
            "This recording has reached the number of segments one session "
            "may hold."
        )
    ordinal = 0 if highest is None else int(highest) + 1
    subtype = _source_subtype(source_format)
    key = keys.segment_key(recording.conversation_id, recording.id, ordinal, subtype)
    upload_id = await run_in_threadpool(
        storage.create_multipart, key=key, content_type=subtype or "video/webm"
    )
    segment = VideoRecordingSegment(
        tenant_id=recording.tenant_id,
        recording_id=recording.id,
        ordinal=ordinal,
        s3_key=key,
        source_format=subtype,
        multipart_upload_id=upload_id,
        parts_json={},
        bytes=0,
        status=SEGMENT_OPEN,
        started_at=datetime.now(timezone.utc),
    )
    session.add(segment)
    if subtype:
        recording.source_format = subtype
    await session.flush()
    return segment


# ── Parts ────────────────────────────────────────────────────────────────────


async def _locked(
    session: AsyncSession, segment: VideoRecordingSegment
) -> VideoRecordingSegment:
    """Re-read `segment` under a row lock, fresh from the database.

    Two requests for one segment (a retried part racing the original, a
    complete arriving beside the last part) would otherwise each read
    `parts_json`, add their own entry and write it back, and the second write
    would silently drop the first part's ETag. That part would then be missing
    from `CompleteMultipartUpload`, so S3 would join the segment without it
    and the recording would lose those seconds with nothing reporting it.
    `populate_existing` makes the lock's read the value every rule below
    checks, not the copy loaded before the lock was taken.
    """
    return (
        await session.execute(
            select(VideoRecordingSegment)
            .where(VideoRecordingSegment.id == segment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _recording_bytes(session: AsyncSession, recording_id: uuid.UUID) -> int:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(VideoRecordingSegment.bytes), 0)).where(
                VideoRecordingSegment.recording_id == recording_id
            )
        )
    ).scalar_one()
    return int(total or 0)


async def record_part(
    session: AsyncSession,
    recording: VideoRecording,
    segment: VideoRecordingSegment,
    *,
    part_number: int,
    data: bytes,
    final: bool,
) -> VideoRecordingSegment:
    """Forward one part to the store and record its ETag.

    Every size rule is checked BEFORE the bytes leave for S3:

      * empty is refused; above `video_part_max_bytes` is refused (413);
      * a part below S3's 5 MiB floor IS the segment's final part, whether or
        not `final` says so, because only the last part of a multipart upload
        may be small and S3 would otherwise refuse the whole segment at
        completion, after the session is over and the browser can no longer
        re-send anything. `final` exists to close a segment on a FULL part;
      * nothing may arrive after the final part: a browser that has more to
        send opens a new segment, which is what it does after a device
        recovery anyway;
      * the recording's total across every segment stays under
        `video_max_upload_bytes`.

    Re-sending a part number REPLACES it (a retry after a lost response), and
    the byte count is adjusted rather than double counted. The segment row is
    LOCKED for the whole of this (`_locked`), so concurrent parts of one
    segment are recorded one after the other rather than overwriting each
    other's ETags.
    """
    settings = get_settings()
    segment = await _locked(session, segment)
    if recording.status != lifecycle.RECORDING or segment.status != SEGMENT_OPEN:
        raise RecordingRefused("This part of the recording is already closed.")
    if not 1 <= part_number <= storage.MAX_PART_NUMBER:
        raise RecordingRefused(
            "That part number is outside what one segment accepts.",
            status_code=422,
        )
    size = len(data)
    if size == 0:
        raise RecordingRefused("The recording part is empty.", status_code=422)
    if size > settings.video_part_max_bytes:
        raise RecordingRefused(
            "The recording part is larger than one upload accepts.",
            status_code=413,
        )
    parts = dict(segment.parts_json or {})
    if (
        segment.final_part_number is not None
        and part_number > segment.final_part_number
    ):
        raise RecordingRefused(
            "This segment's final part has already been received."
        )
    final = final or size < storage.MIN_PART_BYTES
    if final and any(int(number) > part_number for number in parts):
        raise RecordingRefused(
            "A later part of this segment already exists, so this one cannot "
            "be its final part. Only the last part of a segment may be "
            "smaller than 5 MiB.",
            status_code=422,
        )
    previous = int((parts.get(str(part_number)) or {}).get("bytes") or 0)
    total = await _recording_bytes(session, recording.id) - previous + size
    if total > settings.video_max_upload_bytes:
        raise RecordingRefused(
            "The recording is larger than this assessment accepts.",
            status_code=413,
        )
    etag = await run_in_threadpool(
        storage.upload_part,
        key=segment.s3_key,
        upload_id=segment.multipart_upload_id,
        part_number=part_number,
        data=data,
    )
    parts[str(part_number)] = {"etag": etag, "bytes": size}
    segment.parts_json = parts
    segment.bytes = sum(int(entry.get("bytes") or 0) for entry in parts.values())
    segment.last_part_at = datetime.now(timezone.utc)
    if final:
        segment.final_part_number = part_number
    await session.flush()
    return segment


# ── Close ────────────────────────────────────────────────────────────────────


def _parts_from_row(segment: VideoRecordingSegment) -> list[tuple[int, str]]:
    return sorted(
        (int(number), str(entry["etag"]))
        for number, entry in (segment.parts_json or {}).items()
    )


async def complete_segment(
    session: AsyncSession,
    segment: VideoRecordingSegment,
    *,
    from_store: bool = False,
) -> VideoRecordingSegment:
    """Close one segment: complete it into one object, or abort it when no
    part ever arrived. Idempotent on a segment that is already closed.

    `from_store` completes from the store's own list of parts (the sweep's
    path, see the module docstring); the browser's path uses the ETags the
    row recorded as each part landed. Locked like `record_part`, so a part
    still being recorded is either in the completion or refused after it.
    """
    segment = await _locked(session, segment)
    if segment.status != SEGMENT_OPEN:
        return segment
    try:
        if from_store:
            stored = await run_in_threadpool(
                storage.list_parts,
                key=segment.s3_key,
                upload_id=segment.multipart_upload_id,
            )
            parts = [(part.part_number, part.etag) for part in stored]
            segment.parts_json = {
                str(part.part_number): {"etag": part.etag, "bytes": part.size}
                for part in stored
            }
            segment.bytes = sum(part.size for part in stored)
        else:
            parts = _parts_from_row(segment)
        if not parts:
            await run_in_threadpool(
                storage.abort_multipart,
                key=segment.s3_key,
                upload_id=segment.multipart_upload_id,
            )
            return await _settle(session, segment, SEGMENT_ABORTED)
        await run_in_threadpool(
            storage.complete_multipart,
            key=segment.s3_key,
            upload_id=segment.multipart_upload_id,
            parts=parts,
        )
    except storage.UploadNotFound:
        return await _settle_from_store(session, segment)
    return await _settle(session, segment, SEGMENT_COMPLETED)


async def _settle(
    session: AsyncSession, segment: VideoRecordingSegment, status: str
) -> VideoRecordingSegment:
    segment.status = status
    segment.completed_at = datetime.now(timezone.utc)
    await session.flush()
    return segment


async def _settle_from_store(
    session: AsyncSession, segment: VideoRecordingSegment
) -> VideoRecordingSegment:
    """The store no longer holds this upload, so the OBJECT is the answer.

    That happens when an earlier attempt completed (or aborted) the upload
    and its database write did not land: a sweep whose transaction rolled
    back after S3 had already joined one segment, or a complete whose
    response was lost. Asking the upload again would fail the same way every
    hour for ever, so the HEAD decides: an object at the key is a completed
    segment of that size, and no object is an aborted one with nothing to
    process.
    """
    size = await run_in_threadpool(storage.head_size, segment.s3_key)
    if size is None:
        return await _settle(session, segment, SEGMENT_ABORTED)
    segment.bytes = size
    return await _settle(session, segment, SEGMENT_COMPLETED)


def purge_due_at(
    *,
    session_ended_at: datetime,
    retention_days: int,
    job_purge_due_at: datetime | None,
) -> datetime:
    """Owner decision D4: whichever comes first of the session end plus the
    retention window and the job-closure purge. Pure, so the rule is
    testable without a database."""
    session_due = session_ended_at + timedelta(days=retention_days)
    if job_purge_due_at is not None and job_purge_due_at < session_due:
        return job_purge_due_at
    return session_due


async def stamp_purge_due(session: AsyncSession, recording: VideoRecording) -> None:
    """Store the recording's purge date, ONCE. A date already stamped is
    never moved, so a later settings change cannot shorten or extend a
    deadline a candidate was already given (the closure half still wins at
    read time; see `assessment_media_retention.due_recordings`)."""
    if recording.media_purge_due_at is not None:
        return
    from app.models.candidate import JobCandidateLink  # noqa: PLC0415
    from app.models.job import Job  # noqa: PLC0415

    job_due = (
        await session.execute(
            select(Job.assessment_purge_due_at)
            .join(JobCandidateLink, JobCandidateLink.job_id == Job.id)
            .where(JobCandidateLink.id == recording.job_candidate_link_id)
        )
    ).scalar_one_or_none()
    ended = recording.ended_at or recording.stored_at or datetime.now(timezone.utc)
    recording.media_purge_due_at = purge_due_at(
        session_ended_at=ended,
        retention_days=get_settings().assessment_media_retention_days,
        job_purge_due_at=job_due,
    )
    await session.flush()


@dataclass(frozen=True)
class FinalizeOutcome:
    """What finalizing achieved. `dispatch` is True when there is something
    for the processing pipeline to do."""

    status: str
    dispatch: bool


async def finalize_recording(
    session: AsyncSession,
    recording: VideoRecording,
    *,
    from_store: bool = False,
    ended_at: datetime | None = None,
) -> FinalizeOutcome:
    """Close every open segment and move the recording out of `recording`.

    Idempotent: a recording already past `recording` answers its status and
    asks for no dispatch, so a double click or a sweep racing the browser
    does nothing twice. At least one completed, non-empty segment makes it
    `uploaded`; none makes it `upload_failed`, honestly, because no byte of
    video arrived. Both stamp `ended_at` and the stored purge date.

    `ended_at` is when the SESSION stopped being recorded, because D4's clock
    runs from the session. The browser passes the moment it finalized; the
    sweep passes nothing, and then the last part to arrive is the truthful
    answer (the recording's own start when none did). The sweep's clock would
    be the grace period late, and every orphaned recording would be kept
    that much longer than the candidate was told.
    """
    if recording.status != lifecycle.RECORDING:
        return FinalizeOutcome(status=recording.status, dispatch=False)
    segments = await segments_for(session, recording.id)
    for index, segment in enumerate(segments):
        segments[index] = await complete_segment(
            session, segment, from_store=from_store
        )
    completed = [
        segment for segment in segments
        if segment.status == SEGMENT_COMPLETED and segment.bytes > 0
    ]
    last_part = max(
        (segment.last_part_at for segment in segments if segment.last_part_at),
        default=None,
    )
    recording.ended_at = (
        recording.ended_at
        or ended_at
        or last_part
        or recording.started_at
        or datetime.now(timezone.utc)
    )
    recording.file_size_bytes = sum(segment.bytes for segment in completed)
    if completed:
        lifecycle.advance(recording, lifecycle.UPLOADED)
    else:
        lifecycle.advance(
            recording,
            lifecycle.UPLOAD_FAILED,
            error_detail="No part of the recording reached the object store.",
        )
    await session.flush()
    await stamp_purge_due(session, recording)
    return FinalizeOutcome(
        status=recording.status, dispatch=recording.status == lifecycle.UPLOADED
    )


async def orphaned_recordings(
    session: AsyncSession, *, now: datetime | None = None
) -> list[VideoRecording]:
    """Recordings still in `recording` whose browser is not coming back.

    A recording is orphaned when nothing has arrived for
    `video_orphan_grace_minutes` AND either its assessment session is over
    (the conversation completed or ended) or it has outlived
    `video_max_duration_seconds`, which no live session can. The grace is
    measured from the latest part (or the recording's own start when no part
    ever arrived), so a slow final flush is never cut off.
    """
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    grace = timedelta(minutes=settings.video_orphan_grace_minutes)
    last_part = (
        select(func.max(VideoRecordingSegment.last_part_at))
        .where(VideoRecordingSegment.recording_id == VideoRecording.id)
        .correlate(VideoRecording)
        .scalar_subquery()
    )
    last_activity = func.coalesce(last_part, VideoRecording.started_at, VideoRecording.created_at)
    session_over = (AssessmentConversation.completed_at.is_not(None)) | (
        AssessmentConversation.status.in_(("completed", "terminated"))
    )
    too_old = VideoRecording.created_at < moment - timedelta(
        seconds=settings.video_max_duration_seconds
    ) - grace
    return list(
        (
            await session.execute(
                select(VideoRecording)
                .join(
                    AssessmentConversation,
                    AssessmentConversation.id == VideoRecording.conversation_id,
                )
                .where(
                    VideoRecording.status == lifecycle.RECORDING,
                    # A recording whose media a closure or erasure already
                    # purged has nothing left to complete: its uploads were
                    # aborted by that deletion.
                    VideoRecording.media_deleted_at.is_(None),
                    last_activity < moment - grace,
                    session_over | too_old,
                )
                .order_by(VideoRecording.created_at)
            )
        ).scalars().all()
    )
