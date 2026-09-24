"""The session recording pipeline: assemble, compress, store, delete raw.

ONE KIND, ONE PATH. The video-interview mode, whose recording WAS the answer
and was transcribed, segmented by question marks and written into the scoring
records, is deleted (Vivekium release, 2026-09-24), and so is every step that
served it: audio extraction, Amazon Transcribe over the recording, transcript
segmentation, the transcript writer and the completion it triggered. What is
left is the proctored session recording, and the steps it takes are the steps
that reach NO scorer, so principle P3 holds by construction rather than by a
branch somebody has to remember to take.

    uploaded -> compressing   download every completed segment to DISK, join
                              them in recording order with ffmpeg's concat
                              demuxer (a stream copy, so it is fast and exact),
                              measure the joined duration, transcode to the
                              browser-playable H.264/AAC mp4
             -> storing       upload the compressed file FROM DISK and VERIFY
                              it (HEAD size equals the file, ffprobe duration
                              within tolerance of the joined recording)
             -> ready         delete every raw segment with HEAD-confirmed
                              deletion (the project-intake contract); a failed
                              deletion is counted, never blocks `ready`, and is
                              retried hourly by
                              `pickready.reconcile_assessment_recordings`

WHY THE SEGMENTS ARE JOINED BY A REMUX FIRST. A browser MediaRecorder writes
WebM without a duration in its header, so ffprobe on a raw segment answers
"N/A". The concat step writes a Matroska file whose header carries the real
duration, which is what the verify-before-delete comparison needs, and it is
the same step whether there is one segment or five.

Every failure advances the row to the failure state naming the step, commits
it, and re-raises so the task's error metric moves.

NOTHING HERE IS IMPORTED BY A SCORER, and nothing here writes a transcript,
an answer or a completion.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.dual_mode import SEGMENT_COMPLETED, VideoRecording
from app.services.video import keys, lifecycle, recordings, storage

logger = logging.getLogger(__name__)


class FfmpegUnavailable(RuntimeError):
    """ffmpeg or ffprobe is not installed where this pipeline runs."""


class MediaProcessingError(RuntimeError):
    """A media step ran and failed; the message names the step."""


# ── ffmpeg / ffprobe ─────────────────────────────────────────────────────────


def _run_tool(argv: list[str], *, timeout_seconds: int, step: str) -> str:
    """Run one media tool to completion. Raises honestly in each direction:
    a missing binary is `FfmpegUnavailable` (an environment defect, not a bad
    recording), a non-zero exit is `MediaProcessingError` carrying the tool's
    own last words, and an overrun is a timeout naming the ceiling setting."""
    if shutil.which(argv[0]) is None:
        raise FfmpegUnavailable(
            f"{argv[0]} is not installed in this environment; the {step} step "
            "cannot run. Install ffmpeg in the worker image."
        )
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed binary, built argv
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError(
            f"The {step} step exceeded video_ffmpeg_timeout_seconds "
            f"({timeout_seconds}s) and was stopped."
        ) from exc
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-3:]
        raise MediaProcessingError(
            f"The {step} step failed (exit {completed.returncode}): "
            + " | ".join(tail)
        )
    return completed.stdout


def probe_duration(path: Path, *, timeout_seconds: int) -> float:
    """The container's real duration in seconds, from ffprobe."""
    out = _run_tool(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        timeout_seconds=timeout_seconds,
        step="duration probe",
    )
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaProcessingError(
            "ffprobe returned no duration for the recording; the file may be "
            "truncated or not a media container."
        ) from exc


def concat_list(paths: list[Path]) -> str:
    """The concat demuxer's input list, one quoted path per line in order.

    Pure, so the quoting is testable: a path is written between single
    quotes and any single quote inside it is escaped the way the demuxer
    reads it, so a working-directory name can never split an entry.
    """
    lines = []
    for path in paths:
        escaped = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def assemble(sources: list[Path], dst: Path, *, workdir: Path) -> None:
    """Join the segments, in order, into one Matroska file with a real
    duration header. A stream copy: nothing is re-encoded here."""
    listing = workdir / "segments.txt"
    listing.write_text(concat_list(sources), encoding="utf-8")
    _run_tool(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", str(dst),
        ],
        timeout_seconds=get_settings().video_ffmpeg_timeout_seconds,
        step="segment assembly",
    )


def compress(src: Path, dst: Path) -> None:
    """Transcode to the long-term browser-playable mp4 (H.264 + AAC).

    Every knob is a `video_compression_*` setting; storage optimisation, not
    destructive compression."""
    settings = get_settings()
    _run_tool(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "libx264",
            "-preset", settings.video_compression_preset,
            "-crf", str(settings.video_compression_crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", f"{settings.video_compression_audio_bitrate_kbps}k",
            str(dst),
        ],
        timeout_seconds=settings.video_ffmpeg_timeout_seconds,
        step="compression",
    )


# ── Raw objects ──────────────────────────────────────────────────────────────


async def raw_keys(session: AsyncSession, recording: VideoRecording) -> list[str]:
    """Every raw object this recording's processing reads, in order: the
    completed segments, or the single raw object of a pre-0126 row."""
    segments = await recordings.segments_for(session, recording.id)
    keys_in_order = [
        segment.s3_key for segment in segments if segment.status == SEGMENT_COMPLETED
    ]
    if not keys_in_order and recording.s3_raw_key:
        keys_in_order = [recording.s3_raw_key]
    return keys_in_order


async def delete_raw_objects(session: AsyncSession, recording: VideoRecording) -> bool:
    """Delete every raw object still standing, HEAD-confirmed, per segment.

    Shared by the pipeline's tail and the hourly retry sweep, so "is the raw
    really gone" has one answer. Each confirmed segment is stamped
    `raw_deleted_at` so the next pass re-deletes only what is left; a
    confirmed legacy raw key is simply absent next time. Returns True when
    nothing raw remains, and then sets `raw_deleted`.

    A failure is COUNTED on the row and never raised: an undeleted processing
    artifact is a cleanup job, and the recording it belongs to is already
    stored and watchable.
    """
    from fastapi.concurrency import run_in_threadpool  # noqa: PLC0415

    from app.services import object_storage  # noqa: PLC0415

    remaining = False
    #: The store's own error CLASS, never its message: a message can quote a
    #: key, and this line is read wherever the logs are.
    failure = "ObjectStillPresent"
    now = datetime.now(timezone.utc)
    for segment in await recordings.segments_for(session, recording.id):
        if segment.raw_deleted_at is not None:
            continue
        if segment.status != SEGMENT_COMPLETED:
            # An aborted segment has no object; an open one is not a raw
            # artifact of a processed recording and is left to the sweep that
            # completes it.
            continue
        try:
            confirmed = await run_in_threadpool(storage.delete_verified, segment.s3_key)
        except object_storage.ObjectStorageError as exc:
            failure = type(exc).__name__
            confirmed = False
        if confirmed:
            segment.raw_deleted_at = now
        else:
            remaining = True
    if recording.s3_raw_key:
        try:
            confirmed = await run_in_threadpool(
                storage.delete_verified, recording.s3_raw_key
            )
        except object_storage.ObjectStorageError as exc:
            failure = type(exc).__name__
            confirmed = False
        remaining = remaining or not confirmed
    if remaining:
        recording.raw_delete_failures += 1
        logger.warning(
            "video_processing.raw_delete_failed recording_id=%s failures=%d "
            "failure=%s",
            recording.id, recording.raw_delete_failures, failure,
        )
    else:
        recording.raw_deleted = True
    await session.flush()
    return not remaining


# ── The pipeline ─────────────────────────────────────────────────────────────


async def _fail(
    session: AsyncSession,
    recording: VideoRecording,
    target: str,
    detail: str,
) -> None:
    """Record the failure state and COMMIT it, so the honest status survives
    the re-raise that follows."""
    lifecycle.advance(recording, target, error_detail=detail[:2000])
    await session.flush()
    await session.commit()


async def process_recording(session: AsyncSession, recording_id: uuid.UUID) -> None:
    """Drive one uploaded recording to `ready`. The worker task's whole body.

    The session is a WORKER session (RLS bypassed); tenant scoping is by the
    recording's own id. Commits at each state transition so a kill
    mid-pipeline leaves a truthful status rather than a rolled-back lie.
    Recordings of the deleted interview kind take the same path: storing a
    recording has never been the step that reaches a scorer.
    """
    settings = get_settings()
    # LOCKED until the first commit below: a redelivered dispatch or the
    # stuck-upload sweep arriving together with this run waits here, then
    # finds the recording already past `uploaded` and stops, so one
    # recording is never compressed twice at once.
    recording = (
        await session.execute(
            select(VideoRecording)
            .where(VideoRecording.id == recording_id)
            .with_for_update()
        )
    ).scalars().first()
    if recording is None:
        raise ValueError(f"Video recording {recording_id} not found")
    if recording.status in lifecycle.RETRYABLE_FAILURES:
        lifecycle.advance(recording, lifecycle.UPLOADED)
        await session.flush()
    if recording.status != lifecycle.UPLOADED:
        logger.info(
            "video_processing.skipped recording_id=%s status=%s",
            recording_id, recording.status,
        )
        await session.commit()
        return
    sources = await raw_keys(session, recording)

    lifecycle.advance(recording, lifecycle.COMPRESSING)
    await session.flush()
    await session.commit()

    with tempfile.TemporaryDirectory(prefix="readypick-session-media-") as tmp:
        workdir = Path(tmp)
        assembled = workdir / "assembled.mkv"
        compressed = workdir / "assessment.mp4"
        try:
            if not sources:
                raise MediaProcessingError(
                    "No completed segment exists for this recording."
                )
            local: list[Path] = []
            for index, key in enumerate(sources):
                target = workdir / f"part-{index:04d}{Path(key).suffix or '.webm'}"
                storage.download_to(key, target)
                local.append(target)
            assemble(local, assembled, workdir=workdir)
            duration = probe_duration(
                assembled, timeout_seconds=settings.video_ffmpeg_timeout_seconds
            )
            if duration > settings.video_max_duration_seconds:
                raise MediaProcessingError(
                    "The recording is longer than video_max_duration_seconds "
                    f"({settings.video_max_duration_seconds}s) allows."
                )
            recording.duration_seconds = duration
            compress(assembled, compressed)
        except Exception as exc:  # noqa: BLE001 -- recorded then re-raised
            await _fail(session, recording, lifecycle.COMPRESSION_FAILED, str(exc))
            raise

        lifecycle.advance(recording, lifecycle.STORING)
        await session.flush()
        await session.commit()
        try:
            compressed_key = recording.s3_compressed_key or keys.compressed_key(
                recording.conversation_id, recording.id
            )
            size = storage.upload_path(
                key=compressed_key, path=compressed, content_type="video/mp4"
            )
            stored_size = storage.head_size(compressed_key)
            if not stored_size or stored_size != size:
                raise MediaProcessingError(
                    "The compressed object's stored size does not match what "
                    "was uploaded; refusing to delete the raw recording."
                )
            compressed_duration = probe_duration(
                compressed, timeout_seconds=settings.video_ffmpeg_timeout_seconds
            )
            if abs(compressed_duration - duration) > settings.video_duration_tolerance_seconds:
                raise MediaProcessingError(
                    "The compressed recording's duration differs from the raw "
                    "recording by more than video_duration_tolerance_seconds; "
                    "refusing to delete the raw recording."
                )
            recording.s3_compressed_key = compressed_key
            recording.compressed_size_bytes = size
            recording.stored_format = "mp4"
            recording.stored_at = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001 -- recorded then re-raised
            await _fail(session, recording, lifecycle.STORAGE_FAILED, str(exc))
            raise

    # Raw deletion only AFTER the compressed object is verified present: the
    # project-intake order, and it is not negotiable.
    await delete_raw_objects(session, recording)
    # A row finalized before migration 0126 has no stored purge date yet; the
    # store is the last point it can be given one from its own facts.
    await recordings.stamp_purge_due(session, recording)
    lifecycle.advance(recording, lifecycle.READY)
    recording.processed_at = datetime.now(timezone.utc)
    await session.flush()
    await session.commit()
    logger.info("video_processing.ready recording_id=%s", recording.id)
