"""The spoken-answer transcription task.

`pickready.transcribe_voice_answer` turns one uploaded recording into the
candidate's answer text. Route.LAMBDA: a three-minute answer is a ten to sixty
second Transcribe job, bounded by `assessment_voice_transcribe_timeout_seconds`
well inside the task worker's timeout, and it must not wait behind a Fargate
cold start while the candidate's clock is paused.

WHAT IT PROMISES
----------------
- The transcript is FINAL and becomes the answer (Appendix B). Nothing edits it.
- A failure is a STATE, never a fake transcript: `status = failed` with an
  operator reason, and the candidate's clock stays paused for
  `assessment_voice_failure_pause_seconds` (or until they acknowledge) so they
  can read what happened and type instead.
- The AUDIO is deleted, HEAD-confirmed, whatever happened. A delete that
  cannot be confirmed is COUNTED on the row (`audio_delete_failures`) and the
  hourly media sweep retries it; there is no terminal failure state.
- Idempotent: a redelivered message for a row that is no longer waiting does
  nothing, and each attempt uses its own Transcribe job name.

It runs in the worker's bypass-RLS session (a background task operating on one
named row), and every query names that row.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.workers.registry import Route, task
from app.workers.runtime import (
    worker_session as _worker_session,
    _run,
)

logger = logging.getLogger(__name__)


@task(
    name="pickready.transcribe_voice_answer",
    route=Route.LAMBDA,
    max_attempts=1,
    summary="Transcribe one spoken assessment answer and delete its audio.",
)
def transcribe_voice_answer(voice_id: str):
    """Transcribe the recording behind `voice_answers.id = voice_id`."""
    import asyncio

    from botocore.exceptions import BotoCoreError, ClientError

    from app.models.voice import (
        PAUSE_TRANSCRIPTION,
        VOICE_FAILED,
        VOICE_TRANSCRIBED,
        VOICE_TRANSCRIBING,
        VOICE_UPLOADED,
        VoiceAnswer,
    )
    from app.services import object_storage
    from app.services.assessment_conversation import pauses
    from app.services.video import transcribe, voice

    async def _task() -> None:
        settings = get_settings()
        async with _worker_session() as session:
            row = await session.get(VoiceAnswer, uuid.UUID(str(voice_id)))
            if row is None:
                raise ValueError(f"voice answer {voice_id} not found")
            if row.status not in (VOICE_UPLOADED, VOICE_TRANSCRIBING) or not row.s3_key:
                logger.info(
                    "voice_transcribe.skipped voice_id=%s status=%s", voice_id, row.status
                )
                return
            row.status = VOICE_TRANSCRIBING
            row.transcribe_job_name = f"voice-{row.id.hex}-{uuid.uuid4().hex[:8]}"
            await session.commit()

            audio_key = row.s3_key
            output_key = voice.transcript_key(row.conversation_id, row.id)
            media_format = audio_key.rsplit(".", 1)[-1]
            text = ""
            failure: str | None = None
            try:
                payload = await asyncio.to_thread(
                    transcribe.run_transcription,
                    audio_key=audio_key,
                    output_key=output_key,
                    job_name=row.transcribe_job_name,
                    media_format=media_format,
                    timeout_seconds=settings.assessment_voice_transcribe_timeout_seconds,
                    poll_seconds=settings.assessment_voice_transcribe_poll_seconds,
                )
                text = transcribe.transcript_text(payload)
                if not text:
                    failure = "Amazon Transcribe heard no speech in the recording."
            except transcribe.TranscriptionError as exc:
                failure = str(exc)
            except (BotoCoreError, ClientError) as exc:
                # An AWS refusal (credentials, a missing bucket grant, a
                # throttle) is a failed TRANSCRIPTION, not a crashed task: the
                # candidate is waiting on this row with their clock paused.
                failure = f"Amazon Transcribe could not be called: {type(exc).__name__}"

            now = datetime.now(timezone.utc)
            if failure is None:
                row.status = VOICE_TRANSCRIBED
                row.transcript_text = text
                row.transcribed_at = now
                await pauses.close_pause(
                    session, row.conversation_id, PAUSE_TRANSCRIPTION, at=now, ref_id=row.id
                )
            else:
                row.status = VOICE_FAILED
                row.failure_reason = failure
                row.failed_at = now
                # The clock stays stopped for a short, SCHEDULED window so the
                # candidate can read the failure and switch to typing; it ends
                # on its own and needs no sweep. Acknowledging ends it sooner.
                await pauses.close_pause(
                    session,
                    row.conversation_id,
                    PAUSE_TRANSCRIPTION,
                    at=now + timedelta(seconds=settings.assessment_voice_failure_pause_seconds),
                    ref_id=row.id,
                )
                logger.warning(
                    "voice_transcribe.failed voice_id=%s conversation_id=%s reason=%s",
                    row.id, row.conversation_id, failure,
                )

            # The audio goes whatever happened. Confirmed by a HEAD, counted
            # when it cannot be, and retried by the hourly media sweep.
            gone = True
            for key in (audio_key, output_key):
                try:
                    confirmed = await asyncio.to_thread(voice.delete_verified, key)
                except object_storage.ObjectStorageError:
                    # The state above must still commit: a transcript the
                    # candidate is waiting on is not lost over a cleanup step.
                    logger.warning(
                        "voice_transcribe.audio_delete_error voice_id=%s", row.id
                    )
                    confirmed = False
                if not confirmed:
                    gone = False
            if gone:
                row.audio_deleted_at = now
            else:
                row.audio_delete_failures = int(row.audio_delete_failures or 0) + 1
                logger.warning(
                    "voice_transcribe.audio_delete_unconfirmed voice_id=%s failures=%d",
                    row.id, row.audio_delete_failures,
                )
            await session.commit()
            logger.info(
                "voice_transcribe.done voice_id=%s status=%s", row.id, row.status
            )

    _run(_task())
