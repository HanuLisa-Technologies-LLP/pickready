"""The in-memory hand-off of an audio chunk (sections 2, 3.4 and 10).

THE ONLY MEDIA THAT LEAVES THE BROWSER FOR PROCTORING, AND WHAT HAPPENS TO IT
----------------------------------------------------------------------------
A chunk arrives as bytes already read into memory by the route. It is posted
to the analysis service's `/diarize`, the speaker figures are read back, and
the reference is deleted. There is no write to disk, to object storage or to a
temporary file anywhere on this path, and `tests/test_proctoring_no_media.py`
fails the build if one appears. The analysis service makes the same promise
on its side. (The session RECORDING is a different artifact, written only by
`services/video/`; nothing here touches it.)

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
An answer that lacks the per-speaker figures (an analysis service older than
this backend) is a BAD answer, not "one speaker": the strength rule below
cannot be applied without them, and guessing would either flag the
candidate's own voice or hear nobody.

TWO RULES, BOTH ON THE SERVER (master prompt, Phase 3, Proctoring)
-----------------------------------------------------------------
1. A STRONG SECOND VOICE. The diarizer separates speakers and reports how long
   each one spoke. A chunk counts toward a second voice only when the
   SECOND-longest speaker spoke for at least `second_voice_min_seconds`: the
   candidate's own voice is one speaker and is never counted, and a cough, a
   passing voice in a corridor or the diarizer splitting one voice into two
   short labels is too brief to be anything. `second_voice_consecutive_chunks`
   strong chunks in a row are a second voice, which goes through
   `ingestion.apply_server_event` and takes a warning from the same counter
   every other Path B event uses.
2. SPEAKING DURING A QUESTION THAT DOES NOT TAKE A SPOKEN ANSWER. Speech of at
   least `speech_min_seconds` in a chunk while no spoken answer is being
   captured (`voice_capture_open`) is logged, once per RUN of consecutive
   speaking chunks, as Path C: never a warning and never a termination. The
   event's duration grows with the run, so a minute of reading aloud is one
   occurrence of about a minute, not four of fifteen seconds. The report
   highlights it at `speech_highlight_threshold` occurrences.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
    "SPEECH_RUN",
    "ChunkAnalysis",
    "post_chunk",
    "parse_analysis",
    "strong_second_voice",
    "voice_capture_open",
    "note_degraded_once",
    "analyse_chunk",
]

STATUS_ANALYSED = "analysed"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "analysis_failed"
STATUS_ENDED = "ended"
SECOND_VOICE_RUN = "second_voice"
SPEECH_RUN = "speech_during_non_audio"
_MS_PER_SECOND = 1000


@dataclass(frozen=True)
class ChunkAnalysis:
    """What the analysis service heard in one chunk. Figures, never audio."""

    speaker_count: int
    #: Seconds each separated speaker spoke, longest first.
    speaker_seconds: tuple[float, ...]
    #: Seconds of any speech at all, overlaps counted once.
    speech_seconds: float


Poster = Callable[[bytes, str, ProctoringConfig], Awaitable[ChunkAnalysis]]
#: Answers "is a spoken answer being captured for this conversation right now".
VoiceProbe = Callable[[AsyncSession, uuid.UUID, datetime, ProctoringConfig], Awaitable[bool]]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_analysis(body: Any) -> ChunkAnalysis:
    """Validate the `/diarize` answer. Raises ValueError on anything that is
    not the three figures this module needs, with consistent values."""
    if not isinstance(body, dict):
        raise ValueError("analysis service answered with a non-object body")
    count = body.get("speaker_count")
    speech = body.get("speech_seconds")
    per_speaker = body.get("speaker_seconds")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("analysis service answered without an integer speaker_count")
    if not _number(speech) or speech < 0:
        raise ValueError("analysis service answered without a speech_seconds figure")
    if not isinstance(per_speaker, list) or not all(
        _number(item) and item >= 0 for item in per_speaker
    ):
        raise ValueError("analysis service answered without per-speaker speaker_seconds")
    if len(per_speaker) != count:
        raise ValueError("analysis service speaker_seconds disagrees with speaker_count")
    return ChunkAnalysis(
        speaker_count=count,
        speaker_seconds=tuple(sorted((float(item) for item in per_speaker), reverse=True)),
        speech_seconds=float(speech),
    )


async def post_chunk(chunk: bytes, content_type: str, config: ProctoringConfig) -> ChunkAnalysis:
    """POST the bytes to the analysis service and return what it heard.

    The bytes go straight from memory into the request body. `httpx` streams
    the multipart from the object it is given and never spools it to disk.
    """
    url = config.analysis_service_url.rstrip("/") + "/diarize"
    async with httpx.AsyncClient(timeout=config.analysis_timeout_seconds) as client:
        response = await client.post(
            url, files={"chunk": ("chunk", chunk, content_type)}
        )
    response.raise_for_status()
    return parse_analysis(response.json())


def strong_second_voice(analysis: ChunkAnalysis, config: ProctoringConfig) -> bool:
    """True when a SECOND speaker spoke for long enough to count.

    The longest speaker is taken to be the candidate; only the next one is
    measured. One speaker, however long they spoke, is never a second voice.
    """
    if len(analysis.speaker_seconds) <= 1:
        return False
    return analysis.speaker_seconds[1] >= config.second_voice_min_seconds


# The voice answer table belongs to the conversation engine (PLAN-p3 3.6,
# migration `phase3_conversation`); proctoring reads two facts from it and
# writes nothing, so it is queried by name rather than by importing the
# engine's model into this package.
_VOICE_CAPTURE_SQL = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM voice_answers
         WHERE conversation_id = :conversation_id
           AND (
                (status = 'recording' AND capture_started_at >= :capture_floor)
             OR uploaded_at >= :window_start
           )
    )
    """
)


async def voice_capture_open(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    now: datetime,
    config: ProctoringConfig,
) -> bool:
    """Was a spoken answer being captured at any time this chunk covers?

    A chunk is the last `audio_chunk_seconds` of audio, and it arrives after
    it was recorded, so the window looked at is TWO chunk lengths: speech at
    the tail of a spoken answer that the candidate has just submitted is the
    answer, not speaking out of turn. A capture still marked `recording` is
    believed only for as long as a spoken answer can last
    (`assessment_voice_max_seconds`) plus that window, so an abandoned capture
    cannot excuse speech for the rest of the assessment.
    """
    window = timedelta(seconds=config.audio_chunk_seconds * 2)
    capture_floor = now - timedelta(seconds=get_settings().assessment_voice_max_seconds) - window
    return bool(
        (
            await session.execute(
                _VOICE_CAPTURE_SQL,
                {
                    "conversation_id": conversation_id,
                    "capture_floor": capture_floor,
                    "window_start": now - window,
                },
            )
        ).scalar_one()
    )


async def note_degraded_once(
    session: AsyncSession, ps: ProctoringSession, note: str, now: datetime
) -> bool:
    """Record ONE `SESSION_QUALITY_DEGRADED` event per session per note, so
    the report states the gap once rather than once per chunk."""
    if not await state.claim_once(ps.id, f"quality_note:{note}"):
        return False
    session.add(
        ProctoringEvent(
            id=uuid.uuid4(),
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


async def _speech_during_non_audio(
    session: AsyncSession,
    ps: ProctoringSession,
    policy: str,
    analysis: ChunkAnalysis,
    *,
    now: datetime,
    config: ProctoringConfig,
    probe: VoiceProbe,
    enqueue: ingestion.Enqueue | None,
) -> None:
    """Rule 2. One event per run of speaking chunks; its duration grows."""
    speaking = analysis.speech_seconds >= config.speech_min_seconds
    if speaking and await probe(session, ps.conversation_id, now, config):
        speaking = False
    if not speaking:
        await state.reset_consecutive(ps.id, SPEECH_RUN)
        return
    run_length = await state.bump_consecutive(ps.id, SPEECH_RUN)
    heard_ms = int(analysis.speech_seconds * _MS_PER_SECOND)
    if run_length > 1:
        event_id = await state.recall(ps.id, SPEECH_RUN)
        if event_id is not None:
            await session.execute(
                update(ProctoringEvent)
                .where(
                    ProctoringEvent.id == uuid.UUID(event_id),
                    ProctoringEvent.proctoring_session_id == ps.id,
                )
                .values(duration_ms=ProctoringEvent.duration_ms + heard_ms)
            )
            return
        # The marker expired or was cleared: this chunk starts a new
        # occurrence rather than vanishing.
    event_id = uuid.uuid4()
    await ingestion.apply_server_event(
        session, ps, policy, "SPEECH_DURING_NON_AUDIO_QUESTION",
        now=now, duration_ms=heard_ms,
        metadata={"speech_seconds": round(analysis.speech_seconds, 1)},
        enqueue=enqueue, event_id=event_id,
    )
    await state.remember(ps.id, SPEECH_RUN, str(event_id))


async def analyse_chunk(
    session: AsyncSession,
    ps: ProctoringSession,
    policy: str,
    chunk: bytes,
    content_type: str,
    *,
    now: datetime,
    post: Poster = post_chunk,
    probe: VoiceProbe = voice_capture_open,
    enqueue: ingestion.Enqueue | None = None,
) -> AudioChunkOut:
    """Analyse one chunk in memory and apply both audio rules."""
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
        analysis = await post(chunk, content_type, config)
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

    await _speech_during_non_audio(
        session, ps, policy, analysis, now=now, config=config, probe=probe, enqueue=enqueue
    )
    if ps.outcome != "active":
        # Settling an expired device pause inside the speech rule's batch can
        # end the session; nothing below may act on an ended one.
        return AudioChunkOut(
            analysed=True, status=STATUS_ENDED, warnings_used=ps.warnings_used,
            termination=ingestion.termination_out(ps),
        )

    if not strong_second_voice(analysis, config):
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
        metadata={
            "consecutive_chunks": consecutive,
            "speakers": analysis.speaker_count,
            "second_speaker_seconds": round(analysis.speaker_seconds[1], 1),
        },
        enqueue=enqueue,
    )
    return AudioChunkOut(
        analysed=True,
        status=STATUS_ANALYSED,
        warnings_used=result.warnings_used,
        warning=result.warning,
        termination=result.termination,
    )
