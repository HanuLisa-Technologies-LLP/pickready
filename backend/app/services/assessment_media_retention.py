"""Deleting stored assessment media: erasure, job closure, retention.

Provenance: the 2026-09-22 owner ruling that assessment media is stored, which
says in the same sentence that the retention and deletion lifecycle is
preserved. Storing media without a way to get rid of it is the half of this
feature that is easy to skip and impossible to add back after the fact, so it
is one module with one enumeration and one deletion, reached from three
callers that ask for different ROWS and then do the identical thing to the
objects those rows name.

WHY THE ENUMERATION IS SEPARATE FROM THE DELETION
---------------------------------------------------
`object_keys_for_candidate` reads the table and returns keys; `delete_objects`
takes keys and deletes them. They are split because candidate erasure needs
exactly that split: `candidate_deletion_requests.object_keys_json` is captured
BEFORE the rows are erased, for the reason migration 0108 states in place --
after the cascade nothing in the database names a single one of those objects,
so an enumeration performed afterwards returns an empty list and the sweep
reports a complete deletion of nothing.

The key entries are `{"key": ..., "kind": ...}` dicts, which is the shape
`candidate_deletion_requests.object_keys_json` already stores, so the erasure
path can concatenate this module's answer with its own without a translation
step that could drop a field.

"THE DELETE CALL RETURNED" IS NOT EVIDENCE
--------------------------------------------
Every deletion here goes through `video.storage.delete_verified`, which HEADs
the key afterwards and answers False when the object is still there. A key
that did not confirm is RETURNED as remaining and the row's
`media_delete_failures` is incremented, so the next sweep has a shorter list
rather than a clean-looking log. That is the project-intake contract and the
same one `services/deletion_requests` applies to resumes.

SEGMENTS ARE OBJECTS TOO (migration 0126)
-------------------------------------------
A recording now arrives as segments, one S3 object each, named only by
`video_recording_segments` rows that CASCADE with the recording. Every
enumerator here therefore reads the segment rows for the recordings it
returns, so the objects-before-rows order holds for the new table exactly as
it holds for `video_recordings`. A segment whose multipart upload is still
open carries its upload id, and the deletion aborts the upload before it
HEADs the key, so no billed part outlives a purge.

RETENTION IS A STORED DATE, NOT A WINDOW OVER A TIMESTAMP (owner decision D4)
------------------------------------------------------------------------------
`due_recordings` asks for recordings whose EARLIER of two stored dates has
passed: the recording's own `media_purge_due_at` (the session end plus the
retention setting, stamped once at finalize) and its job's
`assessment_purge_due_at` (the closure purge). Nothing is computed from a
module constant at sweep time, so neither a settings change nor a late sweep
can move a deadline a candidate was promised.

THIS MODULE SCORES NOTHING AND IMPORTS NO SCORER. It also imports nothing from
`services/proctoring`: a proctored-session recording is deleted because of its
age, its candidate or its job, never because of anything proctoring observed.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dual_mode import (
    SEGMENT_ABORTED,
    SEGMENT_OPEN,
    VideoRecording,
    VideoRecordingSegment,
)
from app.services import object_storage
from app.services.video import storage as video_storage

logger = logging.getLogger(__name__)

#: The `kind` field of an object-key entry, so a mixed list from several
#: enumerators still says what each key is. Distinct from the RECORDING kind
#: on purpose: this names the ROLE of the object, not the artifact it belongs
#: to, and both objects below can belong to either recording kind.
#:
#: THE VALUES MATCH `services/erasure.KIND_VIDEO_*` EXACTLY, and that is
#: deliberate rather than coincidental: the erasure path already stores these
#: strings in `candidate_deletion_requests.object_keys_json`, so pointing it
#: at this module is a one-line change that leaves every record already
#: written still readable.
OBJECT_KIND_COMPRESSED = "assessment_video_compressed"
OBJECT_KIND_RAW = "assessment_video_raw"


@dataclass(frozen=True)
class MediaDeletion:
    """What one pass over a set of media objects achieved.

    `remaining` is the keys still present after the pass, so a caller sweeping
    this again has a shorter list rather than re-deleting what is already
    gone. `failure` is a CLASS NAME or None: these records outlive the rows
    they were derived from, and nothing that could quote a row goes into one.
    """

    deleted: int
    remaining: tuple[dict[str, str], ...]
    failure: str | None = None

    @property
    def finished(self) -> bool:
        return not self.remaining


def object_keys_for_recording(
    recording: VideoRecording,
    segments: tuple[VideoRecordingSegment, ...] | list[VideoRecordingSegment] = (),
) -> list[dict[str, str]]:
    """Every object key this recording owns, compressed first, then the
    single raw object of a pre-0126 row, then each segment in order.

    Raw keys are included even when `raw_deleted` (or a segment's
    `raw_deleted_at`) is set, and that is deliberate: those stamps record
    that a HEAD confirmed the object gone once, and a deletion pass that
    re-HEADs a key already absent costs one call and answers the same thing.
    Trusting the stamp instead would mean an object left behind is never
    looked at again by the very sweep that exists to finish it.

    An OPEN segment's entry carries its `upload_id`, so `delete_objects`
    aborts the multipart upload (and its billed parts) before the HEAD.
    """
    entries: list[dict[str, str]] = []
    if recording.s3_compressed_key:
        entries.append(
            {"key": recording.s3_compressed_key, "kind": OBJECT_KIND_COMPRESSED}
        )
    if recording.s3_raw_key:
        entries.append({"key": recording.s3_raw_key, "kind": OBJECT_KIND_RAW})
    for segment in sorted(segments, key=lambda row: row.ordinal):
        entry = {"key": segment.s3_key, "kind": OBJECT_KIND_RAW}
        if segment.status == SEGMENT_OPEN and segment.multipart_upload_id:
            entry["upload_id"] = segment.multipart_upload_id
        entries.append(entry)
    return entries


async def segments_by_recording(
    session: AsyncSession, recording_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[VideoRecordingSegment]]:
    """The segment rows of several recordings in one query."""
    grouped: dict[uuid.UUID, list[VideoRecordingSegment]] = {
        recording_id: [] for recording_id in recording_ids
    }
    if not recording_ids:
        return grouped
    rows = (
        await session.execute(
            select(VideoRecordingSegment)
            .where(VideoRecordingSegment.recording_id.in_(recording_ids))
            .order_by(VideoRecordingSegment.recording_id, VideoRecordingSegment.ordinal)
        )
    ).scalars().all()
    for row in rows:
        grouped[row.recording_id].append(row)
    return grouped


async def _keys_for(
    session: AsyncSession, recordings: list[VideoRecording]
) -> list[dict[str, str]]:
    segments = await segments_by_recording(session, [row.id for row in recordings])
    entries: list[dict[str, str]] = []
    for recording in recordings:
        entries.extend(object_keys_for_recording(recording, segments[recording.id]))
    return entries


async def object_keys_for_candidate(
    session: AsyncSession, candidate_id: uuid.UUID
) -> list[dict[str, str]]:
    """Every stored media object belonging to one candidate, across tenants.

    THE ERASURE HOOK. Call it BEFORE the candidate's rows are erased; after
    the cascade `video_recordings` has no row for them and this returns an
    empty list, which would read as "there was no media" rather than as "the
    media is now unreachable".

    Cross-tenant by construction: a candidate applies to several tenants and
    an erasure is about the PERSON, so this must run in a session that can see
    all of their recordings (the audited bypass scope or the candidate's own).
    """
    rows = (
        await session.execute(
            select(VideoRecording).where(VideoRecording.candidate_id == candidate_id)
        )
    ).scalars().all()
    return await _keys_for(session, list(rows))


async def object_keys_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[dict[str, str]]:
    """Every stored media object recorded against one job's applications.

    The job-closure hook. Reached through `job_candidate_links` rather than a
    `job_id` column on the recording, because the recording's link to a job is
    the application and denormalising it would give two answers the day an
    application moves.
    """
    from app.models.candidate import JobCandidateLink  # noqa: PLC0415

    rows = (
        await session.execute(
            select(VideoRecording)
            .join(
                JobCandidateLink,
                JobCandidateLink.id == VideoRecording.job_candidate_link_id,
            )
            .where(JobCandidateLink.job_id == job_id)
        )
    ).scalars().all()
    return await _keys_for(session, list(rows))


def delete_objects(entries: list[dict[str, str]]) -> MediaDeletion:
    """Delete each key and HEAD-confirm it. Synchronous, like every other
    object-storage call in this codebase; API callers wrap it in
    `run_in_threadpool` and worker callers run it directly.

    An unconfigured object store is reported as a FAILURE with every key
    remaining, never as a successful deletion of nothing: a deployment with no
    bucket has not deleted a candidate's video, it has failed to look.
    """
    deleted = 0
    remaining: list[dict[str, str]] = []
    failure: str | None = None
    for entry in entries:
        key = entry.get("key") or ""
        if not key:
            continue
        try:
            upload_id = entry.get("upload_id")
            if upload_id:
                video_storage.abort_multipart(key=key, upload_id=upload_id)
            if video_storage.delete_verified(key):
                deleted += 1
            else:
                remaining.append(entry)
        except object_storage.ObjectStorageError as exc:
            failure = type(exc).__name__
            remaining.append(entry)
    return MediaDeletion(
        deleted=deleted, remaining=tuple(remaining), failure=failure
    )


async def delete_media_for_recording(
    session: AsyncSession, recording: VideoRecording
) -> MediaDeletion:
    """Delete one recording's objects and record the outcome on the row.

    The ROW SURVIVES. `media_deleted_at` is stamped and the keys are left in
    place, so the record still says what was recorded, when, and that its
    media has gone. Blanking the keys would make a recording whose media was
    deliberately deleted indistinguishable from one that never had any, which
    is the difference an auditor is asking about.
    """
    from fastapi.concurrency import run_in_threadpool  # noqa: PLC0415

    segments = (await segments_by_recording(session, [recording.id]))[recording.id]
    outcome = await run_in_threadpool(
        delete_objects, object_keys_for_recording(recording, segments)
    )
    if outcome.finished and outcome.failure is None:
        now = datetime.now(timezone.utc)
        recording.media_deleted_at = now
        recording.raw_deleted = True
        for segment in segments:
            segment.raw_deleted_at = segment.raw_deleted_at or now
            # The deletion aborted this segment's multipart upload, so the
            # row must stop saying it is open: an `open` segment is one the
            # repair sweep would try to complete from parts that are gone.
            if segment.status == SEGMENT_OPEN:
                segment.status = SEGMENT_ABORTED
                segment.completed_at = now
    else:
        recording.media_delete_failures += 1
        logger.warning(
            "assessment_media.delete_incomplete recording_id=%s remaining=%d "
            "failures=%d",
            recording.id,
            len(outcome.remaining),
            recording.media_delete_failures,
        )
    await session.flush()
    return outcome


async def due_recordings(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 200
) -> list[VideoRecording]:
    """Recordings whose media is due for deletion under owner decision D4.

    Due means the EARLIER of the recording's stored `media_purge_due_at` and
    its job's stored `assessment_purge_due_at` has passed. `LEAST` ignores a
    NULL, so an open job contributes nothing and a recording not yet
    finalized is due only if its job's closure purge is. Reads the TABLE,
    never a module constant, and `media_deleted_at IS NULL` is what makes the
    sweep converge: a recording whose objects are gone is never enumerated
    again. Bounded per pass so one hour's backlog cannot hold a worker past
    its timeout; the next hour takes the rest.
    """
    from app.models.candidate import JobCandidateLink  # noqa: PLC0415
    from app.models.job import Job  # noqa: PLC0415

    moment = now or datetime.now(timezone.utc)
    effective_due = func.least(
        VideoRecording.media_purge_due_at, Job.assessment_purge_due_at
    )
    return list(
        (
            await session.execute(
                select(VideoRecording)
                .join(
                    JobCandidateLink,
                    JobCandidateLink.id == VideoRecording.job_candidate_link_id,
                )
                .join(Job, Job.id == JobCandidateLink.job_id)
                .where(
                    VideoRecording.media_deleted_at.is_(None),
                    effective_due.is_not(None),
                    effective_due <= moment,
                )
                .order_by(effective_due)
                .limit(limit)
            )
        ).scalars().all()
    )


async def raw_retry_recordings(
    session: AsyncSession, *, limit: int = 200
) -> list[VideoRecording]:
    """Stored recordings whose raw objects did not all confirm deleted.

    `ready` means the compressed object was verified present, which is the
    only state in which deleting the raw is allowed; a failed recording keeps
    its raw bytes for the staff retry, and the bucket's seven-day raw rule is
    what bounds that. Media already purged is excluded: that pass deleted
    every key the recording owns.
    """
    from app.services.video import lifecycle  # noqa: PLC0415

    return list(
        (
            await session.execute(
                select(VideoRecording)
                .where(
                    VideoRecording.status == lifecycle.READY,
                    VideoRecording.raw_deleted.is_(False),
                    VideoRecording.media_deleted_at.is_(None),
                )
                .order_by(VideoRecording.created_at)
                .limit(limit)
            )
        ).scalars().all()
    )


async def stuck_uploaded_recordings(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 200
) -> list[VideoRecording]:
    """Finalized recordings that no processing run has picked up.

    A finalize hands the processing task off after its commit, and a lost
    invoke is logged rather than raised; this is the repair. The grace
    (`video_orphan_grace_minutes`) is longer than any cold start, so a run
    that is merely slow to begin is not dispatched twice, and
    `process_recording` locks the row before it moves, so a second run that
    does arrive finds the recording already past `uploaded` and stops.
    """
    from app.core.config import get_settings  # noqa: PLC0415
    from app.services.video import lifecycle  # noqa: PLC0415

    moment = now or datetime.now(timezone.utc)
    grace = timedelta(minutes=get_settings().video_orphan_grace_minutes)
    return list(
        (
            await session.execute(
                select(VideoRecording)
                .where(
                    VideoRecording.status == lifecycle.UPLOADED,
                    func.coalesce(VideoRecording.ended_at, VideoRecording.created_at)
                    < moment - grace,
                )
                .order_by(VideoRecording.created_at)
                .limit(limit)
            )
        ).scalars().all()
    )
