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

THIS MODULE SCORES NOTHING AND IMPORTS NO SCORER. It also imports nothing from
`services/proctoring`: a proctored-session recording is deleted because of its
age, its candidate or its job, never because of anything proctoring observed.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dual_mode import VideoRecording
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


def object_keys_for_recording(recording: VideoRecording) -> list[dict[str, str]]:
    """Every object key this recording owns, compressed first.

    The raw key is included even when `raw_deleted` is set, and that is
    deliberate: `raw_deleted` records that a HEAD confirmed the object gone
    once, and a deletion pass that re-HEADs a key already absent costs one
    call and answers the same thing. Trusting the flag instead would mean a
    raw object left behind by a failed deletion is never looked at again by
    the very sweep that exists to finish it.
    """
    entries: list[dict[str, str]] = []
    if recording.s3_compressed_key:
        entries.append(
            {"key": recording.s3_compressed_key, "kind": OBJECT_KIND_COMPRESSED}
        )
    if recording.s3_raw_key:
        entries.append({"key": recording.s3_raw_key, "kind": OBJECT_KIND_RAW})
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
    entries: list[dict[str, str]] = []
    for recording in rows:
        entries.extend(object_keys_for_recording(recording))
    return entries


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
    entries: list[dict[str, str]] = []
    for recording in rows:
        entries.extend(object_keys_for_recording(recording))
    return entries


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

    outcome = await run_in_threadpool(
        delete_objects, object_keys_for_recording(recording)
    )
    if outcome.finished and outcome.failure is None:
        recording.media_deleted_at = datetime.now(timezone.utc)
        recording.raw_deleted = True
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


async def expired_recordings(
    session: AsyncSession, *, retention_days: int, now: datetime | None = None
) -> list[VideoRecording]:
    """Recordings whose stored media is older than the retention setting.

    Reads the TABLE, never a timestamp on something else (rule 8). A
    retention of zero or less returns nothing at all and the caller says so in
    its log, rather than the sweep silently deleting everything or silently
    deleting nothing: zero means "the platform's cascade policy applies", and
    that is a policy an operator should be able to read off the worker log.

    `media_deleted_at IS NULL` is what makes the sweep converge: a recording
    whose objects are already gone is never enumerated again.
    """
    if retention_days <= 0:
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    return list(
        (
            await session.execute(
                select(VideoRecording).where(
                    VideoRecording.stored_at.is_not(None),
                    VideoRecording.stored_at < cutoff,
                    VideoRecording.media_deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
