"""The session recording tasks: processing, repair, and the retention purge.

Carved out of `workers/tasks.py` on 2026-09-24 (PLAN-p3 WP0) and rebuilt in
the same release (PLAN-p3 WP5). `workers/tasks.py` imports this module so
importing the registry still registers every task.

    pickready.process_assessment_video        Route.ECS, one recording
    pickready.reconcile_assessment_recordings Route.LAMBDA, hourly repair
    pickready.purge_assessment_media          Route.LAMBDA, hourly retention

The processing task keeps its name for queued-message compatibility although
the video-interview half it used to carry is deleted.
"""
from __future__ import annotations

import logging
import uuid

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
    """Hourly retention sweep: owner decision D4.

    A recording is purged at whichever comes first of its stored
    `media_purge_due_at` (the session end plus the retention setting) and its
    job's stored closure purge date. It asks the TABLE for both
    (`media_retention.due_recordings`), never a constant, and every deletion
    is HEAD-confirmed. The ROW survives with `media_deleted_at` stamped, so
    the record still says a recording existed and that its media is gone.

    One recording at a time, committing as it goes: a single transaction over
    the whole backlog would roll back deletions the object store has already
    performed and leave the counters disagreeing with the store. A deletion
    that does not confirm is counted and comes back next hour; there is no
    terminal failure state.
    """
    from app.services import assessment_media_retention as media_retention
    from app.services import deletion_requests

    async def _task():
        async with _worker_session() as session:
            due = await media_retention.due_recordings(session)
            purged = 0
            incomplete = 0
            for recording in due:
                outcome = await media_retention.delete_media_for_recording(
                    session, recording
                )
                await session.commit()
                if outcome.finished and outcome.failure is None:
                    purged += 1
                    continue
                incomplete += 1
                if recording.media_delete_failures >= deletion_requests.ATTEMPTS_BEFORE_ALARM:
                    logger.error(
                        "assessment_media.purge_stuck recording_id=%s failures=%d "
                        "failure=%s",
                        recording.id,
                        recording.media_delete_failures,
                        outcome.failure or "ObjectStillPresent",
                    )
            logger.info(
                "assessment_media.purged recordings=%d incomplete=%d",
                purged, incomplete,
            )
    _run(_task())


@task(
    name="pickready.reconcile_assessment_recordings",
    route=Route.LAMBDA,
)
def reconcile_assessment_recordings():
    """Hourly repair of the recording pipeline. Three passes, each asking the
    TABLE rather than trusting that an earlier step happened:

    1. RAW DELETIONS THAT DID NOT CONFIRM. A stored (`ready`) recording whose
       raw segment objects were not all HEAD-confirmed gone. Before this
       sweep a failed deletion incremented `raw_delete_failures` and nothing
       ever looked at the row again, so the raw bytes of a candidate's session
       sat in the bucket until the lifecycle rule found them. Past
       `ATTEMPTS_BEFORE_ALARM` the log line becomes an error.
    2. ORPHANED RECORDINGS. A tab closed mid-session never calls finalize.
       Once the session is over and nothing has arrived for the grace, the
       recording is finalized from the STORE's own list of parts and handed
       to processing, so a session is stored even when its browser vanished.
    3. FINALIZED RECORDINGS NO RUN PICKED UP. A finalize dispatches after its
       commit and a lost invoke is logged rather than raised; this re-hands
       a recording still `uploaded` after the grace.

    Dispatches happen AFTER the commit that makes the rows they read durable.
    """
    from app.services import assessment_media_retention as media_retention
    from app.services import deletion_requests
    from app.services.video import processing
    from app.services.video import recordings as video_recordings
    from app.workers.dispatch import dispatch

    async def _task():
        to_process: list[str] = []
        async with _worker_session() as session:
            retried = cleared = 0
            for recording in await media_retention.raw_retry_recordings(session):
                retried += 1
                if await processing.delete_raw_objects(session, recording):
                    cleared += 1
                elif recording.raw_delete_failures >= deletion_requests.ATTEMPTS_BEFORE_ALARM:
                    logger.error(
                        "assessment_media.raw_delete_stuck recording_id=%s failures=%d",
                        recording.id, recording.raw_delete_failures,
                    )
                await session.commit()

            finalized = 0
            for recording in await video_recordings.orphaned_recordings(session):
                outcome = await video_recordings.finalize_recording(
                    session, recording, from_store=True
                )
                await session.commit()
                finalized += 1
                if outcome.dispatch:
                    to_process.append(str(recording.id))

            for recording in await media_retention.stuck_uploaded_recordings(session):
                to_process.append(str(recording.id))
            await session.commit()
        for recording_id in dict.fromkeys(to_process):
            dispatch("pickready.process_assessment_video", args=[recording_id])
        logger.info(
            "assessment_media.reconciled raw_retried=%d raw_cleared=%d "
            "orphans_finalized=%d dispatched=%d",
            retried, cleared, finalized, len(set(to_process)),
        )
    _run(_task())


@task(
    name="pickready.process_assessment_video",
    route=Route.ECS,
    max_attempts=2,
    backoff_seconds=10.0,
)
def process_assessment_video(recording_id: str):
    """The session recording pipeline: assemble, compress, store, delete raw.

    Route.ECS because this is work measured in minutes: downloading a two-hour
    recording's segments and one ffmpeg transcode legitimately outrun Lambda's
    ceiling.

    The whole body lives in `services/video/processing.process_recording`,
    which owns the recording's state machine: every failure lands the row in
    the failure state naming the step (committed before the re-raise, so a
    retry or the staff retry endpoint knows what to retry), and the row is
    locked while it leaves `uploaded`, so this task redelivered is safe.
    """
    from app.services.video import processing

    async def _task():
        async with _worker_session() as session:
            await processing.process_recording(session, uuid.UUID(str(recording_id)))

    _run(_task())
