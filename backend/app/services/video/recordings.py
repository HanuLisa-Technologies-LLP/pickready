"""Recording rows: creation, marks, upload, finalize (dual-mode spec 4-5).

The API layer calls these; the processing task lives in `processing.py`. The
split mirrors the spec's separation of concerns (section 20): the Video
Service owns the recording lifecycle and metadata, the Processing Service
owns what happens to the bytes afterwards.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentConversation
from app.models.dual_mode import VideoRecording
from app.services.video import keys, lifecycle

logger = logging.getLogger(__name__)


async def active_recording(
    session: AsyncSession, conversation_id: uuid.UUID
) -> VideoRecording | None:
    """The one recording currently accepting marks or bytes, if any."""
    return (
        await session.execute(
            select(VideoRecording)
            .where(
                VideoRecording.conversation_id == conversation_id,
                VideoRecording.status.in_(
                    (lifecycle.RECORDING, lifecycle.UPLOADING, lifecycle.UPLOAD_FAILED)
                ),
            )
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


async def create_recording(
    session: AsyncSession,
    conversation: AssessmentConversation,
    *,
    candidate_id: uuid.UUID,
    source_format: str | None,
) -> VideoRecording:
    """Open a recording session for this conversation.

    Reuses an existing open recording rather than minting a sibling: a page
    reload mid-interview must come back to the same recording, or the marks
    already stamped would belong to a row nobody finishes.
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
        source_format=source_format,
        started_at=datetime.now(timezone.utc),
    )
    session.add(recording)
    await session.flush()
    recording.s3_raw_key = keys.raw_key(conversation.id, recording.id, source_format)
    recording.s3_compressed_key = keys.compressed_key(conversation.id, recording.id)
    await session.flush()
    return recording


async def mark_question_shown(
    session: AsyncSession,
    recording: VideoRecording,
    *,
    question_id: uuid.UUID,
    question_type: str,
) -> None:
    """Stamp the SERVER time a question reached the screen, as an offset into
    the recording. The transcript is segmented by these marks; a client
    timeline would be a timeline the client chose. Repeat displays of the
    question already at the tail (a reload) do not add a second mark."""
    marks = list(recording.question_marks_json or [])
    if marks and str(marks[-1].get("question_id")) == str(question_id):
        return
    started = recording.started_at or datetime.now(timezone.utc)
    offset = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
    marks.append(
        {
            "question_id": str(question_id),
            "question_type": question_type,
            "offset_seconds": round(offset, 2),
        }
    )
    recording.question_marks_json = marks
    await session.flush()


def validate_upload(data: bytes, content_type: str | None) -> None:
    """The ceilings, before a byte is stored. Every bound is a setting."""
    settings = get_settings()
    if not data:
        raise ValueError("The uploaded recording is empty.")
    if len(data) > settings.video_max_upload_bytes:
        raise ValueError(
            "The recording is larger than this assessment accepts. Please "
            "contact the hiring team if your interview ran unusually long."
        )
    subtype = (content_type or "").split(";")[0].strip().lower()
    if subtype and not subtype.startswith("video/"):
        raise ValueError("The uploaded file is not a video recording.")
