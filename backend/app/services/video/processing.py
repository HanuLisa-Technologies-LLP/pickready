"""The video processing pipeline (dual-mode spec section 6, video spec 8-10).

One recording in, one ready recording out, in five verified steps:

    uploaded -> processing    download the raw object, extract the audio with
                              ffmpeg, measure the real duration with ffprobe
              (transcribe)    Amazon Transcribe over the extracted audio,
                              bounded by explicit timeouts; DISABLED unless
                              `transcribe_enabled` is set, and disabled means
                              `transcription_failed` with an honest retryable
                              message, NEVER a fake transcript
              (structure)     segment the transcript by the SERVER-stamped
                              question marks and write the SAME transcript and
                              answer records the conversational mode writes,
                              then run the same completion: charge, scoring
                              dispatch (spec 21: one intelligence pipeline)
    -> compressing            ffmpeg transcode to a browser-playable H.264/AAC
                              mp4, every knob a `video_*` setting
    -> storing                upload the compressed object and VERIFY it
                              (HEAD, non-zero size, ffprobe duration within
                              tolerance) before the raw is touched
    -> ready                  delete the raw artifacts with HEAD-confirmed
                              deletion (the project-intake contract); a failed
                              deletion increments `raw_delete_failures` and
                              never blocks `ready`

Every failure advances the row to the failure state naming the step, commits
it, and re-raises so the task's error metric moves -- except the
configuration-off transcription case, which returns without raising because no
retry supplies an AWS account.

NOTHING HERE IS IMPORTED BY A SCORER. The pipeline's whole output is the same
`assessment_messages` / `assessment_answers` rows the conversational mode
writes; the scorers read those without knowing which mode produced them.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    JobCompetency,
)
from app.models.candidate import JobCandidateLink
from app.models.dual_mode import VideoRecording
from app.models.job import Job
from app.services.video import keys, lifecycle, storage
from app.workers.dispatch import dispatch

logger = logging.getLogger(__name__)

#: What the candidate transcript records when a question's segment carried no
#: recognisable speech. An honest empty answer, labelled as the classifier
#: labels one, so the scorers treat it exactly as a typed empty answer.
NO_SPOKEN_ANSWER = "(No spoken answer was captured for this question.)"

TRANSCRIBE_DISABLED_DETAIL = (
    "Speech to text is not configured in this environment "
    "(transcribe_enabled is off). The recording is stored and this step can "
    "be retried once Amazon Transcribe is enabled for the deployment."
)


class FfmpegUnavailable(RuntimeError):
    """ffmpeg or ffprobe is not installed where this pipeline runs."""


class MediaProcessingError(RuntimeError):
    """A media step ran and failed; the message names the step."""


class TranscriptionError(RuntimeError):
    """Amazon Transcribe failed or timed out; the message says how."""


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


def extract_audio(src: Path, dst: Path, *, timeout_seconds: int) -> None:
    """Mono 16kHz WAV, which is what Transcribe consumes most reliably."""
    _run_tool(
        [
            "ffmpeg", "-y", "-i", str(src), "-vn",
            "-ac", "1", "-ar", "16000", "-f", "wav", str(dst),
        ],
        timeout_seconds=timeout_seconds,
        step="audio extraction",
    )


def compress(src: Path, dst: Path) -> None:
    """Transcode to the long-term browser-playable mp4 (H.264 + AAC).

    Every knob is a `video_compression_*` setting; storage optimisation, not
    destructive compression (video spec section 8)."""
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


# ── Amazon Transcribe ────────────────────────────────────────────────────────


def _transcribe_client() -> Any:
    """A Transcribe client with explicit timeouts and bounded retries, the
    same lesson every boto3 client here carries: an unreachable endpoint that
    HANGS defeats every try/except around the call."""
    import boto3  # noqa: PLC0415 -- optional at import time
    from botocore.config import Config  # noqa: PLC0415

    settings = get_settings()
    return boto3.client(
        "transcribe",
        region_name=settings.aws_region or None,
        config=Config(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def run_transcription(
    *, audio_key: str, output_key: str, job_name: str
) -> list[dict[str, Any]]:
    """Run one Transcribe job over `audio_key` and return its word items.

    Blocks, polling at `video_transcribe_poll_seconds`, for at most
    `video_transcribe_timeout_seconds`. Output lands in our own bucket at
    `output_key` so the result is fetched over the S3 transport this codebase
    already trusts, never a vendor-hosted presigned URI.
    """
    settings = get_settings()
    if not settings.transcribe_enabled:
        raise TranscriptionError(TRANSCRIBE_DISABLED_DETAIL)
    bucket = (settings.s3_bucket or "").strip()
    client = _transcribe_client()
    client.start_transcription_job(
        TranscriptionJobName=job_name,
        LanguageCode=settings.transcribe_language_code,
        Media={"MediaFileUri": f"s3://{bucket}/{audio_key}"},
        MediaFormat="wav",
        OutputBucketName=bucket,
        OutputKey=output_key,
    )
    deadline = time.monotonic() + settings.video_transcribe_timeout_seconds
    while True:
        job = client.get_transcription_job(TranscriptionJobName=job_name)[
            "TranscriptionJob"
        ]
        job_status = job["TranscriptionJobStatus"]
        if job_status == "COMPLETED":
            break
        if job_status == "FAILED":
            raise TranscriptionError(
                "Amazon Transcribe reported the job failed: "
                + str(job.get("FailureReason") or "no reason given")
            )
        if time.monotonic() >= deadline:
            raise TranscriptionError(
                "Amazon Transcribe did not finish within "
                f"video_transcribe_timeout_seconds "
                f"({settings.video_transcribe_timeout_seconds}s)."
            )
        time.sleep(settings.video_transcribe_poll_seconds)
    payload = json.loads(storage.get_bytes(output_key).decode("utf-8"))
    items = payload.get("results", {}).get("items", [])
    if not isinstance(items, list):
        raise TranscriptionError(
            "Amazon Transcribe returned a transcript with no item list."
        )
    return items


# ── Transcript structuring (spec section 7) ──────────────────────────────────


@dataclass(frozen=True)
class Segment:
    """One question's spoken answer, cut from the transcript by the marks."""

    question_id: str
    text: str
    start_time: float
    end_time: float


def segment_transcript(
    items: list[dict[str, Any]],
    marks: list[dict[str, Any]],
    total_duration: float,
) -> list[Segment]:
    """Cut the word stream into per-question answers by the question marks.

    The marks are the SERVER-stamped display times
    (`video_recordings.question_marks_json`); a word belongs to the question
    that was on screen when it was spoken. Punctuation items carry no
    timestamps in Transcribe output and attach to the preceding word. Pure and
    deterministic, so the alignment is testable without AWS.
    """
    boundaries: list[tuple[str, float, float]] = []
    for index, mark in enumerate(marks):
        start = float(mark.get("offset_seconds") or 0.0)
        end = (
            float(marks[index + 1].get("offset_seconds") or total_duration)
            if index + 1 < len(marks)
            else max(total_duration, start)
        )
        boundaries.append((str(mark.get("question_id")), start, end))

    segments: list[Segment] = []
    for question_id, start, end in boundaries:
        words: list[str] = []
        first: float | None = None
        last: float | None = None
        in_window = False
        for item in items:
            content = (item.get("alternatives") or [{}])[0].get("content", "")
            if item.get("type") == "punctuation":
                if in_window and words:
                    words[-1] = words[-1] + content
                continue
            raw_start = item.get("start_time")
            if raw_start is None:
                continue
            at = float(raw_start)
            in_window = start <= at < end
            if not in_window:
                continue
            words.append(content)
            if first is None:
                first = at
            last = float(item.get("end_time") or at)
        segments.append(
            Segment(
                question_id=question_id,
                text=" ".join(words).strip(),
                start_time=first if first is not None else start,
                end_time=last if last is not None else start,
            )
        )
    return segments


async def write_transcript_records(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    recording: VideoRecording,
    segments: list[Segment],
) -> int:
    """Write the SAME records the conversational mode writes, one pair per
    served question: an agent `assessment_messages` row carrying the prompt,
    a candidate row carrying the transcribed answer under the question's key,
    and one `assessment_answers` row.

    IDEMPOTENT PER QUESTION: a question that already has an answer row is
    skipped, so a pipeline re-run after a late failure never doubles the
    transcript. Returns how many questions were newly written.

    ASSUMPTION (dual-mode spec section 7): a structured question served in
    video mode is answered in speech like any other; its transcribed answer is
    recorded as prose and evaluated from the transcript rather than through
    the deterministic objective scorer, which has no spoken input to parse.
    """
    by_question = {segment.question_id: segment for segment in segments}
    rows = (
        await session.execute(
            select(CandidateQuestion, JobCompetency)
            .join(JobCompetency, JobCompetency.id == CandidateQuestion.competency_id)
            .where(
                CandidateQuestion.job_candidate_link_id
                == conversation.job_candidate_link_id
            )
            .order_by(CandidateQuestion.ordinal)
        )
    ).all()
    answered_ids = set(
        (
            await session.execute(
                select(AssessmentAnswer.question_id).where(
                    AssessmentAnswer.conversation_id == conversation.id
                )
            )
        ).scalars().all()
    )
    ordinal = (
        await session.execute(
            select(func.coalesce(func.max(AssessmentMessage.ordinal), 0)).where(
                AssessmentMessage.conversation_id == conversation.id
            )
        )
    ).scalar_one()
    served_ids = {str(mark.get("question_id")) for mark in (recording.question_marks_json or [])}
    written = 0
    base = recording.started_at or datetime.now(timezone.utc)
    for question, competency in rows:
        if question.id in answered_ids:
            continue
        if str(question.id) not in served_ids:
            # Never shown on screen (the interview ended early), so there is
            # no window of speech to attribute to it. An unserved question is
            # honestly absent, exactly as an unreached conversational one is.
            continue
        segment = by_question.get(str(question.id))
        text = segment.text if segment is not None and segment.text else ""
        content = text or NO_SPOKEN_ANSWER
        label = "substantive" if text else "empty"
        agent_message = AssessmentMessage(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            ordinal=ordinal + 1,
            speaker="agent",
            domain=competency.category,
            question_key=str(question.id),
            content=question.prompt,
        )
        candidate_message = AssessmentMessage(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            ordinal=ordinal + 2,
            speaker="candidate",
            domain=competency.category,
            question_key=str(question.id),
            content=content,
            answer_label=label,
        )
        session.add_all([agent_message, candidate_message])
        await session.flush()
        started = None
        submitted = datetime.now(timezone.utc)
        spent = None
        if segment is not None:
            from datetime import timedelta  # noqa: PLC0415

            started = base + timedelta(seconds=segment.start_time)
            submitted = base + timedelta(seconds=segment.end_time)
            spent = max(0, int(segment.end_time - segment.start_time))
        session.add(
            AssessmentAnswer(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                question_id=question.id,
                message_id=candidate_message.id,
                question_type=question.question_type,
                answer_json={
                    "transcribed_text": text,
                    "start_time": segment.start_time if segment else None,
                    "end_time": segment.end_time if segment else None,
                    "source": "video_interview_transcript",
                },
                started_at=started,
                submitted_at=submitted,
                time_spent_seconds=spent,
            )
        )
        await session.flush()
        written += 1
        ordinal += 2
    return written


async def complete_assessment(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    job: Job,
    link: JobCandidateLink,
) -> bool:
    """The SAME completion the conversational `respond` runs when the last
    answer lands: stamp `completed_at`, emit the telemetry event, charge the
    completed-assessment credit, and dispatch the scoring task.

    Idempotent twice over: guarded on `completed_at` here, and
    `charge_completed` is idempotent by ledger key underneath, so a pipeline
    re-run can never double-charge (the study the dual-mode brief asked for:
    the ledger key is derived from the conversation id and the event type, and
    a second consume with the same key is a no-op).
    """
    if conversation.completed_at is not None:
        return False
    from app.services import credit_reconciliation, telemetry_events  # noqa: PLC0415

    conversation.status = "completed"
    conversation.completed_at = datetime.now(timezone.utc)
    await telemetry_events.emit(
        session,
        tenant_id=job.tenant_id,
        event_code=telemetry_events.EV_INT_COMPLETED,
        job_id=job.id,
        candidate_id=link.candidate_id,
        job_candidate_link_id=link.id,
        correlation_id=job.correlation_id,
        payload={"conversation_id": str(conversation.id), "mode": conversation.mode},
    )
    await credit_reconciliation.charge_completed(
        session,
        conversation_id=conversation.id,
        tenant_id=job.tenant_id,
        job_candidate_link_id=link.id,
    )
    dispatch("pickready.run_functional_assessment", args=[str(link.id)])
    return True


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

    The session is a WORKER session (RLS bypassed); tenant scoping is explicit
    per query. Commits at each state transition so a kill mid-pipeline leaves
    a truthful status rather than a rolled-back lie.
    """
    settings = get_settings()
    recording = await session.get(VideoRecording, recording_id)
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
        return
    conversation = await session.get(AssessmentConversation, recording.conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {recording.conversation_id} not found")
    job = await session.get(Job, conversation.job_id)
    link = await session.get(JobCandidateLink, conversation.job_candidate_link_id)
    if job is None or link is None:
        raise ValueError(f"Recording {recording_id} has no job or link")

    lifecycle.advance(recording, lifecycle.PROCESSING)
    await session.flush()
    await session.commit()

    with tempfile.TemporaryDirectory(prefix="readypick-video-") as workdir:
        raw_path = Path(workdir) / Path(recording.s3_raw_key or "raw.webm").name
        audio_path = Path(workdir) / "audio.wav"
        compressed_path = Path(workdir) / "assessment.mp4"
        transcript_key = f"{keys.RAW_PREFIX}/{conversation.id}/{recording.id}/transcript.json"
        audio_object_key = keys.audio_key(conversation.id, recording.id)

        # ── Download + audio extraction + real duration ─────────────────────
        try:
            raw_path.write_bytes(storage.get_bytes(recording.s3_raw_key or ""))
            duration = probe_duration(
                raw_path, timeout_seconds=settings.video_ffmpeg_timeout_seconds
            )
            if duration > settings.video_max_duration_seconds:
                raise MediaProcessingError(
                    "The recording is longer than video_max_duration_seconds "
                    f"({settings.video_max_duration_seconds}s) allows."
                )
            recording.duration_seconds = duration
            extract_audio(
                raw_path, audio_path,
                timeout_seconds=settings.video_ffmpeg_timeout_seconds,
            )
            storage.put(
                key=audio_object_key,
                data=audio_path.read_bytes(),
                content_type="audio/wav",
            )
        except Exception as exc:  # noqa: BLE001 -- recorded then re-raised
            await _fail(session, recording, lifecycle.PROCESSING_FAILED, str(exc))
            raise

        # ── Speech to text ──────────────────────────────────────────────────
        if not settings.transcribe_enabled:
            # Honest and retryable, never a fake transcript. Returns rather
            # than raises: a platform retry cannot supply an AWS account, and
            # the staff retry endpoint re-dispatches once it is configured.
            await _fail(
                session, recording, lifecycle.TRANSCRIPTION_FAILED,
                TRANSCRIBE_DISABLED_DETAIL,
            )
            logger.warning(
                "video_processing.transcription_unconfigured recording_id=%s",
                recording_id,
            )
            return
        try:
            items = run_transcription(
                audio_key=audio_object_key,
                output_key=transcript_key,
                job_name=f"readypick-{recording.id}",
            )
        except TranscriptionError as exc:
            await _fail(session, recording, lifecycle.TRANSCRIPTION_FAILED, str(exc))
            raise

        # ── Structure + the shared completion ───────────────────────────────
        try:
            segments = segment_transcript(
                items, list(recording.question_marks_json or []), duration
            )
            written = await write_transcript_records(
                session,
                conversation=conversation,
                recording=recording,
                segments=segments,
            )
            await complete_assessment(
                session, conversation=conversation, job=job, link=link
            )
            await session.flush()
            await session.commit()
            logger.info(
                "video_processing.structured recording_id=%s questions_written=%d",
                recording_id, written,
            )
        except Exception as exc:  # noqa: BLE001 -- recorded then re-raised
            await session.rollback()
            recording = await session.get(VideoRecording, recording_id)
            if recording is not None:
                await _fail(session, recording, lifecycle.PROCESSING_FAILED, str(exc))
            raise

        # ── Compression ─────────────────────────────────────────────────────
        lifecycle.advance(recording, lifecycle.COMPRESSING)
        await session.flush()
        await session.commit()
        try:
            compress(raw_path, compressed_path)
        except Exception as exc:  # noqa: BLE001 -- recorded then re-raised
            await _fail(session, recording, lifecycle.COMPRESSION_FAILED, str(exc))
            raise

        # ── Store, verify, and only then delete the raw ─────────────────────
        lifecycle.advance(recording, lifecycle.STORING)
        await session.flush()
        await session.commit()
        try:
            compressed_bytes = compressed_path.read_bytes()
            compressed_key = recording.s3_compressed_key or keys.compressed_key(
                conversation.id, recording.id
            )
            storage.put(
                key=compressed_key, data=compressed_bytes, content_type="video/mp4"
            )
            stored_size = storage.head_size(compressed_key)
            if not stored_size or stored_size != len(compressed_bytes):
                raise MediaProcessingError(
                    "The compressed object's stored size does not match what "
                    "was uploaded; refusing to delete the raw recording."
                )
            compressed_duration = probe_duration(
                compressed_path,
                timeout_seconds=settings.video_ffmpeg_timeout_seconds,
            )
            if abs(compressed_duration - duration) > settings.video_duration_tolerance_seconds:
                raise MediaProcessingError(
                    "The compressed recording's duration differs from the raw "
                    "recording by more than video_duration_tolerance_seconds; "
                    "refusing to delete the raw recording."
                )
            recording.s3_compressed_key = compressed_key
            recording.compressed_size_bytes = len(compressed_bytes)
            recording.stored_format = "mp4"
        except Exception as exc:  # noqa: BLE001 -- recorded then re-raised
            await _fail(session, recording, lifecycle.STORAGE_FAILED, str(exc))
            raise

        # ── Raw deletion, HEAD-confirmed (project-intake contract) ──────────
        deleted = True
        for key in (recording.s3_raw_key, audio_object_key, transcript_key):
            if not key:
                continue
            try:
                if not storage.delete_verified(key):
                    deleted = False
            except Exception:  # noqa: BLE001 -- counted, never blocks ready
                deleted = False
        if deleted:
            recording.raw_deleted = True
        else:
            recording.raw_delete_failures += 1
            logger.warning(
                "video_processing.raw_delete_failed recording_id=%s failures=%d",
                recording_id, recording.raw_delete_failures,
            )

        lifecycle.advance(recording, lifecycle.READY)
        recording.processed_at = datetime.now(timezone.utc)
        await session.flush()
        await session.commit()
        logger.info("video_processing.ready recording_id=%s", recording_id)
