"""The in-memory hand-off of an audio chunk (sections 2, 3.4 and 10).

THE ONLY MEDIA THAT LEAVES THE BROWSER, AND WHAT HAPPENS TO IT
--------------------------------------------------------------
A chunk arrives as bytes already read into memory by the route. It is posted
to the analysis service's `/diarize`, the speaker count is read back, and the
reference is deleted. There is no write to disk, to object storage or to a
temporary file anywhere on this path, and `tests/test_proctoring_no_media.py`
fails the build if one appears. The analysis service makes the same promise
on its side.

WHEN THERE IS NO SERVICE
------------------------
`config.audio_analysis_available` is false and the chunk is never accepted:
the route answers `unavailable`, one `SESSION_QUALITY_DEGRADED` event per
session records that audio monitoring did not run, and the report says so in
the Audio Monitoring findings. A deployment without diarization reports
"not available", never "no issues detected".

WHEN THE SERVICE FAILS
----------------------
A timeout or a bad answer is logged and recorded once per session under the
same degraded-quality event with a different note. It is not retried from the
request path: the chunk is gone by then, and the next chunk is a fresh sample.

THE RULE: `speaker_count >= 2` in `second_voice_consecutive_chunks` chunks in
a row is a second voice, and it goes through `ingestion.apply_server_event`
so it takes a warning from the same counter every other Path B event uses.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proctoring import ProctoringEvent, ProctoringSession
from app.schemas.proctoring import AudioChunkOut
from app.services.proctoring import catalog, ingestion, phrasing, state
from app.services.proctoring.config import ProctoringConfig, get_config

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_ANALYSED",
    "STATUS_UNAVAILABLE",
    "STATUS_FAILED",
    "STATUS_ENDED",
    "SECOND_VOICE_RUN",
    "post_chunk",
    "note_degraded_once",
    "analyse_chunk",
]

STATUS_ANALYSED = "analysed"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "analysis_failed"
STATUS_ENDED = "ended"
SECOND_VOICE_RUN = "second_voice"
_SECOND_VOICE_SPEAKERS = 2
_MS_PER_SECOND = 1000

Poster = Callable[[bytes, str, ProctoringConfig], Awaitable[int]]


async def post_chunk(chunk: bytes, content_type: str, config: ProctoringConfig) -> int:
    """POST the bytes to the analysis service and return the speaker count.

    The bytes go straight from memory into the request body. `httpx` streams
    the multipart from the object it is given and never spools it to disk.
    """
    url = config.analysis_service_url.rstrip("/") + "/diarize"
    async with httpx.AsyncClient(timeout=config.analysis_timeout_seconds) as client:
        response = await client.post(
            url, files={"chunk": ("chunk", chunk, content_type)}
        )
    response.raise_for_status()
    body: Any = response.json()
    count = body.get("speaker_count") if isinstance(body, dict) else None
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("analysis service answered without an integer speaker_count")
    return count


async def note_degraded_once(
    session: AsyncSession, ps: ProctoringSession, note: str, now: datetime
) -> bool:
    """Record ONE `SESSION_QUALITY_DEGRADED` event per session per note, so
    the report states the gap once rather than once per chunk."""
    if not await state.claim_once(ps.id, f"quality_note:{note}"):
        return False
    session.add(
        ProctoringEvent(
            tenant_id=ps.tenant_id,
            proctoring_session_id=ps.id,
            event_type="SESSION_QUALITY_DEGRADED",
            occurred_at=now,
            duration_ms=None,
            path=catalog.PATH_C,
            metadata_json={"note": note},
        )
    )
    await session.flush()
    return True


async def analyse_chunk(
    session: AsyncSession,
    ps: ProctoringSession,
    policy: str,
    chunk: bytes,
    content_type: str,
    *,
    now: datetime,
    post: Poster = post_chunk,
    enqueue: Callable[[str], None] = ingestion.enqueue_assessment,
) -> AudioChunkOut:
    """Analyse one chunk in memory and apply the second-voice rule."""
    config = get_config()
    if ps.outcome != "active":
        del chunk
        return AudioChunkOut(
            analysed=False, status=STATUS_ENDED, warnings_used=ps.warnings_used,
            termination=ingestion.termination_out(ps),
        )
    if not config.audio_analysis_available:
        del chunk
        await note_degraded_once(session, ps, phrasing.AUDIO_UNAVAILABLE_NOTE, now)
        return AudioChunkOut(
            analysed=False, status=STATUS_UNAVAILABLE, warnings_used=ps.warnings_used
        )
    try:
        speakers = await post(chunk, content_type, config)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "proctoring.audio.analysis_failed session_id=%s err=%s", ps.id, type(exc).__name__
        )
        await note_degraded_once(session, ps, phrasing.AUDIO_FAILED_NOTE, now)
        return AudioChunkOut(
            analysed=False, status=STATUS_FAILED, warnings_used=ps.warnings_used
        )
    finally:
        # The buffer is destroyed here whatever the service answered. Nothing
        # below this line can see the audio.
        del chunk

    if speakers < _SECOND_VOICE_SPEAKERS:
        await state.reset_consecutive(ps.id, SECOND_VOICE_RUN)
        return AudioChunkOut(analysed=True, status=STATUS_ANALYSED, warnings_used=ps.warnings_used)
    consecutive = await state.bump_consecutive(ps.id, SECOND_VOICE_RUN)
    if consecutive < config.second_voice_consecutive_chunks:
        return AudioChunkOut(analysed=True, status=STATUS_ANALYSED, warnings_used=ps.warnings_used)
    await state.reset_consecutive(ps.id, SECOND_VOICE_RUN)
    result = await ingestion.apply_server_event(
        session, ps, policy, "SECOND_VOICE_DETECTED",
        now=now,
        duration_ms=int(config.audio_chunk_seconds * consecutive) * _MS_PER_SECOND,
        metadata={"consecutive_chunks": consecutive, "speakers": speakers},
        enqueue=enqueue,
    )
    return AudioChunkOut(
        analysed=True,
        status=STATUS_ANALYSED,
        warnings_used=result.warnings_used,
        warning=result.warning,
        termination=result.termination,
    )
