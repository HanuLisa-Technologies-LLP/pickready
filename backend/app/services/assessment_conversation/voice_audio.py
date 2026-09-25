"""A spoken answer's AUDIO: deleted once it is transcribed, and never forgotten.

Appendix B section 3: "Store transcript text only." The recording of a spoken
answer exists for the minutes between upload and transcript, and then it goes,
HEAD-confirmed, the project-intake deletion contract. This module is the one
implementation of that deletion, used by the transcription task the moment it
finishes and by the hourly repair pass when that first attempt could not be
confirmed.

THERE IS NO TERMINAL FAILURE STATE. A delete that cannot be confirmed is
COUNTED on the row (`audio_delete_failures`) and tried again next hour, and past
`deletion_requests.ATTEMPTS_BEFORE_ALARM` the log line becomes an error, the
rule `services/deletion_requests` already states. Giving up would leave a
person's voice in a bucket with nothing in the database still pointing at it.

A TRANSCRIPTION THAT NEVER REPORTED BACK IS REPAIRED TOO. The task can be lost
(an invoke that failed after the commit, a worker killed mid-job), and the
candidate's turn has long moved on by the time anybody notices. Such a row is
marked failed through `turns.fail_stale_voice`, the same rule the routes apply
on read, and its audio is then deleted like any other.

OBJECTS BEFORE ROWS. `voice_answers.conversation_id` is ON DELETE CASCADE, so a
job closure purge or a candidate erasure that deletes the conversation takes the
row, and the row is the only thing that names the object.
`undeleted_audio_keys` is what those purges ask BEFORE they delete a row.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice import VOICE_IN_FLIGHT, VoiceAnswer
from app.services import deletion_requests, object_storage
from app.services.assessment_conversation import turns
from app.services.video import voice

logger = logging.getLogger(__name__)

#: How many recordings one repair pass touches. A backlog larger than this is
#: finished by the next hourly pass rather than by one long transaction.
REPAIR_BATCH = 200


@dataclass(frozen=True)
class RepairResult:
    """What one repair pass did, for the sweep's log line."""

    examined: int
    deleted: int
    unconfirmed: int
    failed_stale: int


def object_keys(row: VoiceAnswer) -> tuple[str, ...]:
    """Every object one spoken answer can have left behind: the recording and
    the transcript file Transcribe wrote."""
    if not row.s3_key:
        return ()
    return (row.s3_key, voice.transcript_key(row.conversation_id, row.id))


async def delete_audio(row: VoiceAnswer, *, now: datetime) -> bool:
    """Delete this answer's objects and record the outcome on the row.

    Returns True only when every object is verifiably absent. A delete that
    raised is logged by class name and counted exactly like one the HEAD
    could not confirm; the caller commits either way, because a transcript a
    candidate is waiting on must never be lost over a cleanup step.
    """
    gone = True
    for key in object_keys(row):
        try:
            confirmed = await asyncio.to_thread(voice.delete_verified, key)
        except object_storage.ObjectStorageError as exc:
            logger.warning(
                "voice_audio.delete_error voice_id=%s error=%s", row.id, type(exc).__name__
            )
            confirmed = False
        if not confirmed:
            gone = False
    if gone:
        row.audio_deleted_at = now
        return True
    row.audio_delete_failures = int(row.audio_delete_failures or 0) + 1
    log = (
        logger.error
        if row.audio_delete_failures >= deletion_requests.ATTEMPTS_BEFORE_ALARM
        else logger.warning
    )
    log(
        "voice_audio.delete_unconfirmed voice_id=%s failures=%d",
        row.id, row.audio_delete_failures,
    )
    return False


async def repair_pending_audio(
    session: AsyncSession, *, now: datetime, limit: int = REPAIR_BATCH
) -> RepairResult:
    """One pass over spoken answers whose audio still exists.

    A transcription still inside its budget is left alone (the task may be
    running this minute). One past it is failed first, then deleted. The
    caller commits; the rows are mutated in `session`.
    """
    rows = (
        await session.execute(
            select(VoiceAnswer)
            .where(VoiceAnswer.s3_key.is_not(None), VoiceAnswer.audio_deleted_at.is_(None))
            .order_by(VoiceAnswer.created_at)
            .limit(limit)
        )
    ).scalars().all()
    deleted = unconfirmed = failed_stale = 0
    for row in rows:
        if row.status in VOICE_IN_FLIGHT:
            if not await turns.fail_stale_voice(session, row, now=now):
                continue
            failed_stale += 1
        if await delete_audio(row, now=now):
            deleted += 1
        else:
            unconfirmed += 1
    await session.flush()
    return RepairResult(
        examined=len(rows), deleted=deleted, unconfirmed=unconfirmed, failed_stale=failed_stale
    )


async def undeleted_audio_keys(
    session: AsyncSession, conversation_ids: Iterable[uuid.UUID]
) -> list[str]:
    """Every spoken-answer object still in the store for these conversations.

    Asked by a purge BEFORE it deletes the conversations, because the cascade
    takes these rows and with them the only record of the keys.
    """
    ids = list(conversation_ids)
    if not ids:
        return []
    rows = (
        await session.execute(
            select(VoiceAnswer).where(
                VoiceAnswer.conversation_id.in_(ids),
                VoiceAnswer.s3_key.is_not(None),
                VoiceAnswer.audio_deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return [key for row in rows for key in object_keys(row)]
