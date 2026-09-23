"""The assessment media tasks: the recording pipeline and the retention sweep.

Carved out of `workers/tasks.py` on 2026-09-24 (PLAN-p3 WP0) as a PURE MOVE:
task names, routes and retry policies are unchanged, and `workers/tasks.py`
imports this module so importing the registry still registers every task.
"""
from __future__ import annotations

import logging
import uuid

from app.core.config import get_settings
from app.workers.registry import Route, task
from app.workers.runtime import (
    worker_session as _worker_session,
    _run,
)

logger = logging.getLogger(__name__)


@task(
    name="pickready.purge_assessment_media",
    route=Route.LAMBDA,
)
def purge_assessment_media():
    """Hourly retention sweep over stored assessment recordings.

    `assessment_media_retention_days` is zero by default, and zero means the
    platform's existing candidate-data policy applies: media leaves with the
    candidate erasure, the job closure deletion and the tenant cascade, and
    nothing is deleted early. The task then LOGS that and does nothing, so an
    operator reading the worker log can see the policy in force rather than
    inferring it from silence. The same shape `purge_proctoring_events`
    already has, for the same reason.

    It asks the TABLE (`stored_at` older than the window and
    `media_deleted_at` still null), never a timestamp on something else, and
    every deletion is HEAD-confirmed. One recording at a time, committing as
    it goes: a single transaction over the whole backlog would roll back
    deletions the object store has already performed and leave the counters
    disagreeing with the store.
    """
    from app.services import assessment_media_retention as media_retention

    async def _task():
        days = get_settings().assessment_media_retention_days
        if days <= 0:
            logger.info(
                "assessment_media.purge_noop retention follows the platform "
                "cascade policy"
            )
            return
        async with _worker_session() as session:
            expired = await media_retention.expired_recordings(
                session, retention_days=days
            )
            purged = 0
            incomplete = 0
            for recording in expired:
                outcome = await media_retention.delete_media_for_recording(
                    session, recording
                )
                await session.commit()
                if outcome.finished and outcome.failure is None:
                    purged += 1
                else:
                    incomplete += 1
            logger.info(
                "assessment_media.purged recordings=%d incomplete=%d "
                "older_than_days=%d",
                purged, incomplete, days,
            )
    _run(_task())


@task(
    name="pickready.process_assessment_video",
    route=Route.ECS,
    max_attempts=2,
    backoff_seconds=10.0,
)
def process_assessment_video(recording_id: str):
    """The video-interview processing pipeline (dual-mode spec section 6).

    Route.ECS because this is work measured in minutes: downloading a
    recording, two ffmpeg passes and an Amazon Transcribe job over an
    hour-long interview legitimately outrun Lambda's ceiling.

    The whole body lives in `services/video/processing.process_recording`,
    which owns the recording's state machine: every failure lands the row in
    the failure state naming the step (committed before the re-raise, so a
    retry or the staff retry endpoint knows what to retry), the transcript is
    written into the SAME records the conversational mode writes, and the
    completion it triggers (`charge_completed` + the scoring dispatch) is
    idempotent, so this task redelivered is safe.
    """
    from app.services.video import processing

    async def _task():
        async with _worker_session() as session:
            await processing.process_recording(session, uuid.UUID(str(recording_id)))

    _run(_task())
