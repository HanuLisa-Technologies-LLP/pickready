"""The session recording pipeline, run end to end over the real schema.

`services/video/processing.process_recording` is the only thing that turns a
finalized recording's raw segments into the stored mp4 the hiring team
watches, and it is the only thing allowed to delete those segments. Three
promises are pinned here against real rows and the in-memory store:

  * THE SEGMENTS ARE JOINED IN RECORDING ORDER, the compressed object is
    written under this environment's KMS key, verified, and only THEN is
    every raw segment deleted, HEAD-confirmed and stamped;
  * A VERIFICATION THAT FAILS DELETES NOTHING: the row says which step
    failed, the raw segments are all still in the store, and the failure is
    raised so the task's error metric moves;
  * A RUN WHOSE TASK WAS KILLED is found by the hourly repair from
    `processing_started_at` and given the failure state of its step, so it
    is retryable instead of sitting in `compressing` while the bucket's raw
    rule deletes the only copy. A run that is merely slow is left alone.

ffmpeg itself is not exercised (the worker image carries it; the test
environment may not): `assemble`, `compress` and `probe_duration` are
replaced at the module attribute, and what is asserted is everything the
pipeline decides around them.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests import fake_s3
from tests.recording_world import (
    World,
    candidate,
    cleanup,
    factory_or_skip,
    resolve_ownership,
    seed,
)

FIRST = b"a" * (5 * 1024 * 1024)
SECOND = b"b" * 1024


async def _finalized_two_segments(factory, world: World, monkeypatch) -> uuid.UUID:
    """A recording of two segments (a device recovery between them),
    finalized through the candidate's own routes: `uploaded`."""
    from app.api import assessment_recording as routes
    from app.core.db import superadmin_scope
    from app.models.dual_mode import VideoRecordingSegment
    from app.schemas.videos import SegmentOpenIn
    from app.services.video import recordings

    resolve_ownership(monkeypatch, world)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                started = await routes.start_session_media(
                    world.link_id, user=candidate(world), session=s
                )
                for body in (FIRST, SECOND):
                    opened = await routes.open_recording_segment(
                        world.conv_id, SegmentOpenIn(source_format="video/webm"),
                        user=candidate(world), session=s,
                    )
                    recording = await recordings.active_recording(s, world.conv_id)
                    segment = await s.get(VideoRecordingSegment, opened.segment_id)
                    await recordings.record_part(
                        s, recording, segment, part_number=1, data=body, final=True
                    )
                await routes.finalize_recording(
                    world.conv_id, user=candidate(world), session=s
                )
    return started.recording_id


async def _read(factory, sql: str, **params):
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with superadmin_scope(s):
            return (await s.execute(text(sql), params)).mappings().all()


def _stub_media_tools(monkeypatch, *, compressed_duration: float = 60.0) -> list[list[bytes]]:
    """Replace the three ffmpeg calls. Returns the list every assembly's
    inputs are appended to, in the order they were handed over."""
    from app.services.video import processing

    assembled: list[list[bytes]] = []

    def _assemble(sources, dst, *, workdir):
        bodies = [path.read_bytes() for path in sources]
        assembled.append(bodies)
        dst.write_bytes(b"".join(bodies))

    def _compress(src, dst):
        dst.write_bytes(b"mp4:" + src.read_bytes()[:16])

    def _probe(path, *, timeout_seconds):
        return compressed_duration if path.suffix == ".mp4" else 60.0

    monkeypatch.setattr(processing, "assemble", _assemble)
    monkeypatch.setattr(processing, "compress", _compress)
    monkeypatch.setattr(processing, "probe_duration", _probe)
    return assembled


def test_the_segments_are_joined_in_order_stored_under_kms_then_deleted(
    monkeypatch,
) -> None:
    from app.workers import tasks_media

    store = fake_s3.install(monkeypatch)
    assembled = _stub_media_tools(monkeypatch)
    world = World()

    async def _prepare():
        engine, factory = await factory_or_skip()
        await seed(factory, world)
        recording_id = await _finalized_two_segments(factory, world, monkeypatch)
        segments = await _read(
            factory,
            "SELECT s3_key FROM video_recording_segments WHERE recording_id = :r "
            "ORDER BY ordinal",
            r=str(recording_id),
        )
        before = (await _read(
            factory,
            "SELECT media_purge_due_at FROM video_recordings WHERE id = :r",
            r=str(recording_id),
        ))[0]["media_purge_due_at"]
        await engine.dispose()
        return recording_id, [row["s3_key"] for row in segments], before

    async def _after(recording_id):
        engine, factory = await factory_or_skip()
        row = (await _read(
            factory,
            "SELECT status, stored_at, processing_started_at, processed_at, "
            "raw_deleted, s3_compressed_key, compressed_size_bytes, "
            "duration_seconds, media_purge_due_at, error_detail "
            "FROM video_recordings WHERE id = :r",
            r=str(recording_id),
        ))[0]
        stamps = await _read(
            factory,
            "SELECT raw_deleted_at FROM video_recording_segments "
            "WHERE recording_id = :r",
            r=str(recording_id),
        )
        await cleanup(factory, world)
        await engine.dispose()
        return row, stamps

    recording_id, segment_keys, due_before = asyncio.run(_prepare())
    assert all(key in store.objects for key in segment_keys)
    try:
        tasks_media.process_assessment_video(str(recording_id))
    finally:
        row, stamps = asyncio.run(_after(recording_id))

    # Joined in recording order, the device-recovery segment second.
    assert assembled == [[FIRST, SECOND]]
    assert row["status"] == "ready" and row["error_detail"] is None
    assert row["stored_at"] is not None and row["processed_at"] is not None
    assert row["processing_started_at"] is not None
    assert row["duration_seconds"] == 60.0
    # Stored, and stored under this environment's key: the bucket policy
    # refuses any other encryption header.
    compressed = row["s3_compressed_key"]
    assert compressed in store.objects
    assert row["compressed_size_bytes"] == len(store.objects[compressed])
    upload = [w for w in store.writes if w["op"] == "upload_file"]
    assert upload and upload[0]["ServerSideEncryption"] == "aws:kms"
    assert upload[0]["SSEKMSKeyId"] == fake_s3.KMS_KEY_ARN
    # Every raw segment is gone from the STORE and stamped per segment.
    assert not any(key in store.objects for key in segment_keys)
    assert row["raw_deleted"] is True
    assert stamps and all(stamp["raw_deleted_at"] is not None for stamp in stamps)
    # Storing never moves the purge date finalize stamped (D4).
    assert row["media_purge_due_at"] == due_before


def test_a_failed_verification_deletes_no_raw_segment(monkeypatch) -> None:
    """A compressed file whose duration disagrees with the raw recording is
    not a copy of it. The raw segments are the only copy, so nothing is
    deleted, the row names the step, and the failure is raised."""
    from app.services.video import processing
    from app.workers import tasks_media

    store = fake_s3.install(monkeypatch)
    _stub_media_tools(monkeypatch, compressed_duration=10.0)
    world = World()

    async def _prepare():
        engine, factory = await factory_or_skip()
        await seed(factory, world)
        recording_id = await _finalized_two_segments(factory, world, monkeypatch)
        keys = await _read(
            factory,
            "SELECT s3_key FROM video_recording_segments WHERE recording_id = :r",
            r=str(recording_id),
        )
        await engine.dispose()
        return recording_id, [row["s3_key"] for row in keys]

    async def _after(recording_id):
        engine, factory = await factory_or_skip()
        row = (await _read(
            factory,
            "SELECT status, raw_deleted, stored_at, error_detail "
            "FROM video_recordings WHERE id = :r",
            r=str(recording_id),
        ))[0]
        await cleanup(factory, world)
        await engine.dispose()
        return row

    recording_id, segment_keys = asyncio.run(_prepare())
    try:
        with pytest.raises(processing.MediaProcessingError, match="duration"):
            tasks_media.process_assessment_video(str(recording_id))
    finally:
        row = asyncio.run(_after(recording_id))

    assert row["status"] == "storage_failed"
    assert "refusing to delete the raw recording" in row["error_detail"]
    assert row["raw_deleted"] is False and row["stored_at"] is None
    assert all(key in store.objects for key in segment_keys)


def test_a_run_killed_mid_transcode_is_found_and_made_retryable(monkeypatch) -> None:
    """A task killed from outside commits nothing more. Past every bound the
    pipeline runs under, the repair gives the row its step's failure state;
    a run inside the bound is left to finish."""
    from app.core.db import superadmin_scope
    from app.services import assessment_media_retention as media_retention
    from app.services.video import processing
    from app.workers import tasks_media

    fake_s3.install(monkeypatch)
    world = World()
    stall = media_retention.processing_stall_after()

    async def _prepare():
        engine, factory = await factory_or_skip()
        await seed(factory, world)
        now = datetime.now(timezone.utc)
        rows = {
            "dead_compressing": ("compressing", now - stall - timedelta(minutes=5)),
            "dead_storing": ("storing", now - stall - timedelta(minutes=5)),
            "slow_compressing": ("compressing", now - stall + timedelta(minutes=30)),
        }
        ids = {}
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    for name, (status, started) in rows.items():
                        recording_id = uuid.uuid4()
                        ids[name] = recording_id
                        await s.execute(
                            text(
                                "INSERT INTO video_recordings (id, tenant_id, "
                                "conversation_id, candidate_id, job_candidate_link_id, "
                                "status, kind, started_at, ended_at, "
                                "processing_started_at) VALUES (:id, :t, :c, :cand, "
                                ":l, :st, 'proctored_session', :s, :s, :p)"
                            ),
                            {"id": str(recording_id), "t": str(world.tenant_id),
                             "c": str(world.conv_id), "cand": str(world.cand_id),
                             "l": str(world.link_id), "st": status,
                             "s": started, "p": started},
                        )
        await engine.dispose()
        return ids

    async def _after(ids):
        engine, factory = await factory_or_skip()
        rows = {}
        for name, recording_id in ids.items():
            rows[name] = (await _read(
                factory,
                "SELECT status, error_detail FROM video_recordings WHERE id = :r",
                r=str(recording_id),
            ))[0]
        await cleanup(factory, world)
        await engine.dispose()
        return rows

    ids = asyncio.run(_prepare())
    try:
        tasks_media.reconcile_assessment_recordings()
    finally:
        rows = asyncio.run(_after(ids))

    assert rows["dead_compressing"]["status"] == "compression_failed"
    assert rows["dead_storing"]["status"] == "storage_failed"
    for name in ("dead_compressing", "dead_storing"):
        assert rows[name]["error_detail"] == processing.STALLED_DETAIL
    # Slow is not dead: inside the bound the run is left to finish.
    assert rows["slow_compressing"]["status"] == "compressing"
    assert rows["slow_compressing"]["error_detail"] is None


def test_the_stall_bound_is_derived_from_the_pipelines_own_ceilings(monkeypatch) -> None:
    """No new knob: four media tool calls at the ffmpeg ceiling plus the
    orphan grace. Moving either ceiling moves the bound with it."""
    from app.core.config import get_settings
    from app.services import assessment_media_retention as media_retention

    settings = get_settings()
    monkeypatch.setattr(settings, "video_ffmpeg_timeout_seconds", 100)
    monkeypatch.setattr(settings, "video_orphan_grace_minutes", 7)
    assert media_retention.processing_stall_after() == timedelta(seconds=400, minutes=7)
