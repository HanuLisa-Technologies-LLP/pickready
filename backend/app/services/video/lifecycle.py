"""The session recording's status machine.

Explicit states, explicit transitions, and every failure state names the STEP
that failed. `advance` is the only way a status moves, so an illegal jump
raises instead of persisting, the same posture as `hiring_pipeline` on a
smaller machine.

    recording -> uploaded -> compressing -> storing -> ready

`recording` covers the whole session: the browser opens segments and streams
their parts while the candidate answers. `finalize` (the candidate's last call,
or the orphan sweep when the tab closed) moves the row to `uploaded` once every
segment is one completed object, and the processing task takes it from there.

THERE IS NO TRANSCRIPTION STATE ANY MORE, and the absence is the enforcement.
The deleted video-interview mode had a `processing` state where audio was
extracted, transcribed and written into the records the scorers read; a
proctored session recording never had a door into it, and now there is no
such room at all. The values `uploading`, `processing`, `processing_failed`
and `transcription_failed` stay legal in the database CHECK (migration 0081)
so a row written before the removal still loads, and the delivery layer
reads any status it does not know as Processing rather than failing.

Failure states and where they lead back to on retry:

    upload_failed          terminal: no bytes ever arrived, so there is
                           nothing server-side to retry with
    compression_failed     -> uploaded (re-dispatch reruns the pipeline)
    storage_failed         -> uploaded

Re-running the pipeline after a late failure is safe because every step is
idempotent: assembly and compression overwrite their own working files, the
compressed object overwrites its own key, and raw deletion is a verified no-op
once an object is gone.
"""
from __future__ import annotations

RECORDING = "recording"
UPLOADED = "uploaded"
COMPRESSING = "compressing"
STORING = "storing"
READY = "ready"

UPLOAD_FAILED = "upload_failed"
COMPRESSION_FAILED = "compression_failed"
STORAGE_FAILED = "storage_failed"

WORKING_STATUSES = (RECORDING, UPLOADED, COMPRESSING, STORING, READY)
FAILURE_STATUSES = (UPLOAD_FAILED, COMPRESSION_FAILED, STORAGE_FAILED)
STATUSES = WORKING_STATUSES + FAILURE_STATUSES

#: Failure states the staff retry endpoint may reset to `uploaded` for a
#: re-dispatch. `upload_failed` is absent deliberately: the bytes never
#: arrived, so there is nothing server-side to retry with.
RETRYABLE_FAILURES = frozenset({COMPRESSION_FAILED, STORAGE_FAILED})

_ALLOWED: dict[str, frozenset[str]] = {
    RECORDING: frozenset({UPLOADED, UPLOAD_FAILED}),
    UPLOAD_FAILED: frozenset(),
    UPLOADED: frozenset({COMPRESSING}),
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
