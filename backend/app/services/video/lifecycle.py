"""The recording status machine (dual-mode spec 16, video spec 8).

Explicit states, explicit transitions, and every failure state names the STEP
that failed. `advance` is the only way a status moves, so an illegal jump
raises instead of persisting -- the same posture as `hiring_pipeline`, on a
smaller machine.

    recording -> uploading -> uploaded -> processing -> compressing
                                                     -> storing -> ready

Failure states and where they lead back to on retry:

    upload_failed          the candidate re-uploads (client-side retry only;
                           there are no server-side bytes to retry with)
    processing_failed      -> uploaded (re-dispatch reruns the pipeline)
    transcription_failed   -> uploaded
    compression_failed     -> uploaded
    storage_failed         -> uploaded

Re-running the whole pipeline after a late failure is safe because every step
is idempotent: transcript structuring skips questions that already carry a
spoken answer, compression overwrites its own key, and raw deletion is a
verified no-op once the object is gone.
"""
from __future__ import annotations

RECORDING = "recording"
UPLOADING = "uploading"
UPLOADED = "uploaded"
PROCESSING = "processing"
COMPRESSING = "compressing"
STORING = "storing"
READY = "ready"

UPLOAD_FAILED = "upload_failed"
PROCESSING_FAILED = "processing_failed"
TRANSCRIPTION_FAILED = "transcription_failed"
COMPRESSION_FAILED = "compression_failed"
STORAGE_FAILED = "storage_failed"

WORKING_STATUSES = (
    RECORDING, UPLOADING, UPLOADED, PROCESSING, COMPRESSING, STORING, READY,
)
FAILURE_STATUSES = (
    UPLOAD_FAILED, PROCESSING_FAILED, TRANSCRIPTION_FAILED,
    COMPRESSION_FAILED, STORAGE_FAILED,
)
STATUSES = WORKING_STATUSES + FAILURE_STATUSES

#: Failure states the staff retry endpoint may reset to `uploaded` for a
#: re-dispatch. `upload_failed` is absent deliberately: the raw bytes never
#: arrived, so there is nothing server-side to retry with.
RETRYABLE_FAILURES = frozenset(
    {PROCESSING_FAILED, TRANSCRIPTION_FAILED, COMPRESSION_FAILED, STORAGE_FAILED}
)

_ALLOWED: dict[str, frozenset[str]] = {
    RECORDING: frozenset({UPLOADING, UPLOAD_FAILED}),
    UPLOADING: frozenset({UPLOADED, UPLOAD_FAILED}),
    UPLOAD_FAILED: frozenset({UPLOADING}),
    UPLOADED: frozenset({PROCESSING}),
    PROCESSING: frozenset({COMPRESSING, PROCESSING_FAILED, TRANSCRIPTION_FAILED}),
    PROCESSING_FAILED: frozenset({UPLOADED}),
    TRANSCRIPTION_FAILED: frozenset({UPLOADED}),
    COMPRESSING: frozenset({STORING, COMPRESSION_FAILED}),
    COMPRESSION_FAILED: frozenset({UPLOADED}),
    STORING: frozenset({READY, STORAGE_FAILED}),
    STORAGE_FAILED: frozenset({UPLOADED}),
    READY: frozenset(),
}


class IllegalTransition(ValueError):
    """A status move the machine does not define. Raised, never persisted."""


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED.get(current, frozenset())


def advance(recording, target: str, *, error_detail: str | None = None) -> None:
    """Move `recording.status` to `target`, or raise `IllegalTransition`.

    Mutates the row in memory; the caller owns the flush. `error_detail` is
    stored on a failure state and CLEARED on a working state, so a retried
    recording does not carry a stale failure message that reads as current.
    """
    if not can_transition(recording.status, target):
        raise IllegalTransition(
            f"video recording {recording.id}: {recording.status} -> {target} "
            "is not a defined transition"
        )
    recording.status = target
    if target in FAILURE_STATUSES:
        recording.error_detail = error_detail
    else:
        recording.error_detail = None
