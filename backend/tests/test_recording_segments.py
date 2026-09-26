"""The session recording arrives in SEGMENTS, and no request holds more than a part.

Vivekium release, 2026-09-24 (PLAN-p3 WP5, finding N2). The whole session used
to be buffered in the browser and posted once into `await file.read()` of up
to a gigabyte in the shared API task. Now each segment is one S3 multipart
upload fed one part per request, a finalize closes them and hands the
recording to processing AFTER the commit, and an hourly sweep finishes what a
closed tab left behind from the store's own list of parts.

What is pinned, against the real schema and an in-memory store
(`tests/fake_s3.py`) that refuses what S3 refuses:

  * the recording opens only for a consented, live proctored session, and a
    reload comes back to the same recording;
  * a segment is a multipart upload under `aws:kms` with THIS environment's
    key, at an ids-only key;
  * the size rules run BEFORE bytes leave for the store, and a small part is
    the segment's last whether or not the browser said so, so S3 can never
    refuse a segment at completion, after the session is over;
  * two parts of one segment racing each other cannot lose an ETag;
  * finalize dispatches processing only once its rows are committed, and a
    second finalize dispatches nothing;
  * the orphan sweep completes from the STORE, settles an upload an earlier
    attempt already completed, and keeps one failure to one recording;
  * every deletion enumerator names the segment objects.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from tests import fake_s3
from tests.recording_world import (
    World,
    candidate,
    cleanup,
    factory_or_skip,
    resolve_ownership,
    seed,
)

MIB = 1024 * 1024
FULL = b"v" * (5 * MIB)


def _record_backend(monkeypatch) -> None:
    from app.workers import dispatch as dispatch_mod

    monkeypatch.setenv("TASK_DISPATCH_BACKEND", "record")
    dispatch_mod.clear_recorded()


async def _open(factory, world: World):
    """Open the recording and its first segment, committed."""
    from app.api import assessment_recording as routes
    from app.core.db import superadmin_scope
    from app.schemas.videos import SegmentOpenIn

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                started = await routes.start_session_media(
                    world.link_id, user=candidate(world), session=s
                )
                segment = await routes.open_recording_segment(
                    world.conv_id,
                    SegmentOpenIn(source_format="video/webm;codecs=vp8,opus"),
                    user=candidate(world), session=s,
                )
    return started, segment


async def _part(factory, world: World, segment_id, number: int, data: bytes, *, final=False):
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecordingSegment
    from app.services.video import recordings

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                recording = await recordings.active_recording(s, world.conv_id)
                segment = await s.get(VideoRecordingSegment, segment_id)
                return await recordings.record_part(
                    s, recording, segment, part_number=number, data=data, final=final
                )


# ── Opening ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_recording_opens_only_with_the_assessment_consent(monkeypatch) -> None:
    """The consent the candidate gave names the recording, so without it no
    recording row exists and nothing reaches the store."""
    from app.api import assessment_recording as routes
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecording

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    try:
        await seed(factory, world, consent=False)
        resolve_ownership(monkeypatch, world)
        async with factory() as s:
            async with superadmin_scope(s):
                with pytest.raises(HTTPException) as refused:
                    await routes.start_session_media(
                        world.link_id, user=candidate(world), session=s
                    )
                assert refused.value.status_code == 409
                assert "consent" in str(refused.value.detail).lower()
                count = (
                    await s.execute(
                        select(VideoRecording).where(
                            VideoRecording.conversation_id == world.conv_id
                        )
                    )
                ).scalars().all()
        assert count == []
        assert store.uploads == {}
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_reload_reopens_the_same_recording_and_serves_every_ceiling(
    monkeypatch,
) -> None:
    from app.api import assessment_recording as routes
    from app.core.config import get_settings
    from app.core.db import superadmin_scope

    engine, factory = await factory_or_skip()
    world = World()
    fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        first, _segment = await _open(factory, world)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    again = await routes.start_session_media(
                        world.link_id, user=candidate(world), session=s
                    )
        assert again.recording_id == first.recording_id
        settings = get_settings()
        assert first.part_max_bytes == settings.video_part_max_bytes
        assert first.part_min_bytes == fake_s3.MIN_PART
        assert first.video_bits_per_second == settings.video_recording_video_bps
        assert first.audio_bits_per_second == settings.video_recording_audio_bps
        assert (first.max_width, first.max_height) == (
            settings.video_recording_max_width, settings.video_recording_max_height,
        )
        # No bucket name and no object key crosses the API boundary.
        dumped = first.model_dump_json() + _segment.model_dump_json()
        assert "assessment-raw" not in dumped and "readypick-test" not in dumped
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_segment_is_a_kms_multipart_upload_at_an_ids_only_key(
    monkeypatch,
) -> None:
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecordingSegment

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        started, segment = await _open(factory, world)
        assert segment.ordinal == 0 and segment.status == "open"
        async with factory() as s:
            async with superadmin_scope(s):
                row = await s.get(VideoRecordingSegment, segment.segment_id)
        assert row.s3_key == (
            f"assessment-raw/{world.conv_id}/{started.recording_id}/seg-0000.webm"
        )
        create = next(w for w in store.writes if w["op"] == "CreateMultipartUpload")
        assert create["ServerSideEncryption"] == "aws:kms"
        assert create["SSEKMSKeyId"] == fake_s3.KMS_KEY_ARN
    finally:
        await cleanup(factory, world)
        await engine.dispose()


def test_media_writes_refuse_without_this_environments_key(monkeypatch) -> None:
    """`aws:kms` with no key id is the AWS-managed key, and the bucket policy
    denies it. Refusing here names the missing setting instead of letting the
    store deny every part of every candidate's recording."""
    from app.core.config import get_settings
    from app.services import object_storage
    from app.services.video import storage

    fake_s3.install(monkeypatch)
    monkeypatch.setattr(get_settings(), "s3_kms_key_id", "")
    with pytest.raises(object_storage.ObjectStorageNotConfigured) as refused:
        storage.create_multipart(key="assessment-raw/a/b/seg-0000.webm",
                                 content_type="video/webm")
    assert "S3_KMS_KEY_ID" in str(refused.value)


# ── Parts ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_size_rules_run_before_the_bytes_leave(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.video.recordings import RecordingRefused

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        _started, segment = await _open(factory, world)
        with pytest.raises(RecordingRefused) as empty:
            await _part(factory, world, segment.segment_id, 1, b"")
        assert empty.value.status_code == 422
        too_big = b"x" * (get_settings().video_part_max_bytes + 1)
        with pytest.raises(RecordingRefused) as large:
            await _part(factory, world, segment.segment_id, 1, too_big)
        assert large.value.status_code == 413
        upload = next(iter(store.uploads.values()))
        assert upload["parts"] == {}, "a refused part reached the store"
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_small_part_is_the_last_part_whether_or_not_it_says_so(
    monkeypatch,
) -> None:
    """S3 refuses a multipart upload whose non-last part is under 5 MiB, and it
    refuses at COMPLETION, after the session is over. So a small part closes
    the segment the moment it arrives, and anything after it is refused while
    the browser can still open a new segment."""
    from app.services.video.recordings import RecordingRefused

    engine, factory = await factory_or_skip()
    world = World()
    fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        _started, segment = await _open(factory, world)
        await _part(factory, world, segment.segment_id, 1, FULL)
        row = await _part(factory, world, segment.segment_id, 2, b"tail")
        assert row.final_part_number == 2
        with pytest.raises(RecordingRefused) as after:
            await _part(factory, world, segment.segment_id, 3, FULL)
        assert after.value.status_code == 409
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_retried_part_replaces_itself_and_is_not_double_counted(
    monkeypatch,
) -> None:
    engine, factory = await factory_or_skip()
    world = World()
    fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        _started, segment = await _open(factory, world)
        await _part(factory, world, segment.segment_id, 1, FULL)
        row = await _part(factory, world, segment.segment_id, 1, FULL)
        assert row.bytes == len(FULL)
        assert list(row.parts_json) == ["1"]
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_parts_racing_on_one_segment_both_survive(monkeypatch) -> None:
    """Without the row lock each request read `parts_json`, added its own part
    and wrote the dict back, so the slower write dropped the faster part's
    ETag and the completed segment silently lacked those seconds.
    Mutation-checked: removing `_locked` from `record_part` loses a part."""
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecordingSegment

    engine, factory = await factory_or_skip()
    world = World()
    fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        _started, segment = await _open(factory, world)
        await asyncio.gather(
            _part(factory, world, segment.segment_id, 1, FULL),
            _part(factory, world, segment.segment_id, 2, FULL),
            _part(factory, world, segment.segment_id, 3, FULL),
        )
        async with factory() as s:
            async with superadmin_scope(s):
                row = await s.get(VideoRecordingSegment, segment.segment_id)
        assert sorted(row.parts_json) == ["1", "2", "3"]
        assert row.bytes == 3 * len(FULL)
    finally:
        await cleanup(factory, world)
        await engine.dispose()


# ── Finalize ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finalize_joins_the_segment_and_dispatches_only_after_commit(
    monkeypatch,
) -> None:
    from app.api import assessment_recording as routes
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecording, VideoRecordingSegment
    from app.workers import dispatch as dispatch_mod

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    _record_backend(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        started, segment = await _open(factory, world)
        await _part(factory, world, segment.segment_id, 1, FULL)
        await _part(factory, world, segment.segment_id, 2, b"end")

        # A finalize whose transaction rolls back dispatches NOTHING.
        async with factory() as s:
            async with superadmin_scope(s):
                await routes.finalize_recording(
                    world.conv_id, user=candidate(world), session=s
                )
            await s.rollback()
        assert dispatch_mod.recorded_names() == []

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await routes.finalize_recording(
                        world.conv_id, user=candidate(world), session=s
                    )
                    assert dispatch_mod.recorded_names() == [], (
                        "processing was handed off before the commit"
                    )
        assert out.status == "uploaded"
        runs = dispatch_mod.recorded()
        assert [(r.name, r.args) for r in runs] == [
            ("pickready.process_assessment_video", (str(started.recording_id),))
        ]

        # Read back from a SECOND connection: the rows are what processing reads.
        async with factory() as s:
            async with superadmin_scope(s):
                recording = await s.get(VideoRecording, started.recording_id)
                row = await s.get(VideoRecordingSegment, segment.segment_id)
        assert row.status == "completed"
        assert store.objects[row.s3_key] == FULL + b"end"
        assert recording.file_size_bytes == len(FULL) + 3
        assert recording.ended_at is not None
        assert recording.media_purge_due_at == recording.ended_at + timedelta(days=90)

        # Idempotent: a double click dispatches nothing more.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await routes.finalize_recording(
                        world.conv_id, user=candidate(world), session=s
                    )
        assert len(dispatch_mod.recorded()) == 1
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_recording_with_no_bytes_is_an_honest_upload_failure(monkeypatch) -> None:
    from app.api import assessment_recording as routes
    from app.core.db import superadmin_scope
    from app.workers import dispatch as dispatch_mod

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    _record_backend(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        await _open(factory, world)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await routes.finalize_recording(
                        world.conv_id, user=candidate(world), session=s
                    )
        assert out.status == "upload_failed"
        assert "reached us" in out.message
        assert store.uploads == {}, "the empty upload was left open and billed"
        assert dispatch_mod.recorded_names() == []
    finally:
        await cleanup(factory, world)
        await engine.dispose()


# ── The orphan sweep ─────────────────────────────────────────────────────────


async def _age(factory, world: World, *, minutes: int) -> None:
    """Make the session over and its last part `minutes` old."""
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("UPDATE assessment_conversations SET status = 'completed', "
                         "completed_at = now() WHERE id = :c"),
                    {"c": str(world.conv_id)},
                )
                await s.execute(
                    text("UPDATE video_recording_segments SET last_part_at = "
                         "now() - make_interval(mins => :m) WHERE tenant_id = :t"),
                    {"m": minutes, "t": str(world.tenant_id)},
                )


def _run_reconcile() -> None:
    """Run the hourly task body against the test database. The registry
    returns task bodies unwrapped, and this one calls `asyncio.run` itself,
    which is why the test that uses it is synchronous."""
    from app.workers import tasks_media

    tasks_media.reconcile_assessment_recordings()


@pytest.mark.asyncio
async def test_the_sweep_completes_an_orphan_from_the_store(monkeypatch) -> None:
    """A tab closed mid-session: the row still says `open` and knows only the
    parts whose database write landed. The store knows them all, so the
    sweep completes from the store, and the session's end is its last part,
    not the sweep's own clock."""
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecording
    from app.services.video import recordings

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    _record_backend(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        started, segment = await _open(factory, world)
        await _part(factory, world, segment.segment_id, 1, FULL)
        # A part S3 accepted whose database write never landed.
        upload_id = next(iter(store.uploads))
        store.upload_part(Bucket="b", Key=next(iter(store.uploads.values()))["key"],
                          UploadId=upload_id, PartNumber=2, Body=b"lost-write")
        await _age(factory, world, minutes=45)

        async with factory() as s:
            async with superadmin_scope(s):
                orphans = await recordings.orphaned_recordings(s)
                assert [r.id for r in orphans] == [started.recording_id]
                recording = orphans[0]
                outcome = await recordings.finalize_recording(
                    s, recording, from_store=True
                )
                await s.commit()
        assert outcome.dispatch is True
        async with factory() as s:
            async with superadmin_scope(s):
                recording = await s.get(VideoRecording, started.recording_id)
                last_part = (
                    await s.execute(
                        text("SELECT last_part_at FROM video_recording_segments "
                             "WHERE recording_id = :r"),
                        {"r": str(started.recording_id)},
                    )
                ).scalar_one()
        assert recording.status == "uploaded"
        assert recording.file_size_bytes == len(FULL) + len(b"lost-write")
        assert recording.ended_at == last_part
        assert recording.media_purge_due_at == last_part + timedelta(days=90)
    finally:
        await cleanup(factory, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_upload_already_completed_is_settled_from_the_object(
    monkeypatch,
) -> None:
    """An earlier attempt completed the upload and its database write rolled
    back. Asking the upload again answers NoSuchUpload every hour for ever;
    the object at the key is the answer, and the segment settles from it."""
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecordingSegment
    from app.services.video import recordings

    engine, factory = await factory_or_skip()
    world = World()
    store = fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        _started, segment = await _open(factory, world)
        await _part(factory, world, segment.segment_id, 1, FULL)
        upload_id, upload = next(iter(store.uploads.items()))
        etag = upload["parts"][1][0]
        store.complete_multipart_upload(
            Bucket="b", Key=upload["key"], UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": etag}]},
        )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    row = await s.get(VideoRecordingSegment, segment.segment_id)
                    settled = await recordings.complete_segment(s, row, from_store=True)
                    assert settled.status == "completed"
                    assert settled.bytes == len(FULL)
    finally:
        await cleanup(factory, world)
        await engine.dispose()


def test_a_store_failure_for_one_orphan_does_not_stop_the_sweep(monkeypatch) -> None:
    """One recording's CompleteMultipartUpload fails: that recording stays
    `recording` for the next hour, the other is finalized and dispatched, and
    the failure is logged with its class. Mutation-checked: without the
    per-recording `except ObjectStorageError` the first failure ends the pass
    and the second orphan waits another hour."""
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecording
    from app.workers import dispatch as dispatch_mod

    store = fake_s3.install(monkeypatch)
    _record_backend(monkeypatch)
    worlds = [World(), World()]

    async def _prepare():
        engine, factory = await factory_or_skip()
        opened = []
        for world in worlds:
            await seed(factory, world)
            resolve_ownership(monkeypatch, world)
            started, segment = await _open(factory, world)
            await _part(factory, world, segment.segment_id, 1, FULL)
            await _age(factory, world, minutes=45)
            opened.append(started.recording_id)
        await engine.dispose()
        return opened

    async def _read(ids):
        engine, factory = await factory_or_skip()
        async with factory() as s:
            async with superadmin_scope(s):
                rows = [await s.get(VideoRecording, rid) for rid in ids]
                statuses = [row.status for row in rows]
        for world in worlds:
            await cleanup(factory, world)
        await engine.dispose()
        return statuses

    ids = asyncio.run(_prepare())
    failing_key = next(
        upload["key"] for upload in store.uploads.values()
        if str(ids[0]) in upload["key"]
    )
    store.fail_complete.add(failing_key)
    try:
        _run_reconcile()
    finally:
        statuses = asyncio.run(_read(ids))
    assert statuses == ["recording", "uploaded"]
    assert [r.args for r in dispatch_mod.recorded()
            if r.name == "pickready.process_assessment_video"] == [(str(ids[1]),)]


# ── Deletion enumerators name the segments ───────────────────────────────────


@pytest.mark.asyncio
async def test_every_enumerator_names_the_segment_objects(monkeypatch) -> None:
    """The segment rows CASCADE with the recording and are the only thing
    naming a segment's object, so objects-before-rows holds only if every
    enumerator reads them first."""
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecordingSegment
    from app.services import assessment_media_retention as media_retention

    engine, factory = await factory_or_skip()
    world = World()
    fake_s3.install(monkeypatch)
    try:
        await seed(factory, world)
        resolve_ownership(monkeypatch, world)
        _started, segment = await _open(factory, world)
        async with factory() as s:
            async with superadmin_scope(s):
                row = await s.get(VideoRecordingSegment, segment.segment_id)
                by_job = await media_retention.object_keys_for_job(s, world.job_id)
                by_candidate = await media_retention.object_keys_for_candidate(
                    s, world.cand_id
                )
        for entries in (by_job, by_candidate):
            entry = next(e for e in entries if e["key"] == row.s3_key)
            assert entry["kind"] == media_retention.OBJECT_KIND_RAW
            # Still open: the deletion must abort the upload, not only HEAD.
            assert entry["upload_id"] == row.multipart_upload_id
    finally:
        await cleanup(factory, world)
        await engine.dispose()


def test_deleting_an_open_segment_aborts_its_upload(monkeypatch) -> None:
    from app.services import assessment_media_retention as media_retention

    store = fake_s3.install(monkeypatch)
    created = store.create_multipart_upload(
        Bucket="b", Key="assessment-raw/a/b/seg-0000.webm", ContentType="video/webm"
    )
    outcome = media_retention.delete_objects([
        {"key": "assessment-raw/a/b/seg-0000.webm", "kind": "assessment_video_raw",
         "upload_id": created["UploadId"]},
    ])
    assert outcome.finished and outcome.failure is None
    assert store.uploads == {}


def test_a_denied_head_is_never_a_confirmed_deletion(monkeypatch) -> None:
    """`object_storage.exists` reads a 403 as "not there for us", which is
    right before a conditional PUT and wrong after a DELETE: a worker with no
    grant would have confirmed deleting a candidate's recording it never
    touched. The media store's HEAD raises instead, and the deletion counts
    as a failure to retry. Mutation-checked against `object_storage.exists`."""
    from app.services import assessment_media_retention as media_retention

    key = "assessment-compressed/a/b/assessment.mp4"
    store = fake_s3.install(monkeypatch)
    store.objects[key] = b"mp4"
    # The delete "succeeds" and the object is still there, and the HEAD that
    # would say so is denied.
    store.stubborn.add(key)
    store.deny_head.add(key)
    outcome = media_retention.delete_objects([
        {"key": key, "kind": "assessment_video_compressed"},
    ])
    assert not outcome.finished
    assert outcome.failure == "ObjectStorageError"
    # A refused DELETE is a failure too, never a quiet no-op.
    store.deny_head.clear()
    store.deny.add(key)
    outcome = media_retention.delete_objects([
        {"key": key, "kind": "assessment_video_compressed"},
    ])
    assert not outcome.finished and outcome.failure == "ObjectStorageError"
    # And the one a delete call "succeeded" on but that is still there.
    store.deny.clear()
    outcome = media_retention.delete_objects([
        {"key": key, "kind": "assessment_video_compressed"},
    ])
    assert not outcome.finished and outcome.failure is None


def test_the_orphan_grace_is_measured_from_the_last_part() -> None:
    """A slow final flush is never cut off: the grace runs from the newest
    part, and only once the session is over (or impossibly long)."""
    import inspect

    from app.services.video import recordings

    source = inspect.getsource(recordings.orphaned_recordings)
    assert "func.max(VideoRecordingSegment.last_part_at)" in source
    assert "VideoRecording.media_deleted_at.is_(None)" in source
