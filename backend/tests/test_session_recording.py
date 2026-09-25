"""The proctored session recording: its lifecycle, its keys, its isolation.

Rewritten from `test_dual_mode_assessment.py` on 2026-09-24 (PLAN-p3 WP5),
when the video-interview mode was deleted. What that file pinned about the
MODE (the mode freeze, the per-mode consent, the canonical shape both modes
shared, the transcript segmentation, Transcribe over a recording) went with
the mode; `tests/test_video_interview_mode_removed.py` keeps it gone, and the
single-mode consent is pinned by the conversation's own tests. What survives
is what the recording every proctored assessment keeps must still promise:

  * the status machine moves only along its edges, every failure names its
    step, and there is NO transcription state for a recording to enter;
  * an S3 key carries IDS ONLY, and a hostile MIME type cannot shape one;
  * the segments are joined in order by a quoted concat list that no path
    can split;
  * NO SCORER IMPORTS `services/video`, and the package imports no scorer and
    nothing from proctoring.

The database-backed behaviour (segments, finalize, the sweeps, retention) is
in `tests/test_recording_segments.py` and `tests/test_media_retention_d4.py`.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


# ── The lifecycle machine (pure) ─────────────────────────────────────────────


def test_the_recording_lifecycle_moves_only_along_defined_edges() -> None:
    from app.services.video import lifecycle

    class Row:
        id = "r"
        status = lifecycle.RECORDING
        error_detail = None

    row = Row()
    lifecycle.advance(row, lifecycle.UPLOADED)
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.advance(row, lifecycle.READY)
    lifecycle.advance(row, lifecycle.COMPRESSING)
    lifecycle.advance(row, lifecycle.COMPRESSION_FAILED, error_detail="ffmpeg exited 1")
    assert row.error_detail == "ffmpeg exited 1"
    # The retry path: a failure goes back to uploaded, and the stale failure
    # message is cleared so it cannot read as current.
    lifecycle.advance(row, lifecycle.UPLOADED)
    assert row.error_detail is None
    lifecycle.advance(row, lifecycle.COMPRESSING)
    lifecycle.advance(row, lifecycle.STORING)
    lifecycle.advance(row, lifecycle.READY)


def test_every_failure_state_names_a_step_and_ready_is_terminal() -> None:
    from app.services.video import lifecycle

    for failure in lifecycle.FAILURE_STATUSES:
        assert failure.endswith("_failed")
    for terminal in (lifecycle.READY, lifecycle.UPLOAD_FAILED):
        assert not any(
            lifecycle.can_transition(terminal, target) for target in lifecycle.STATUSES
        ), terminal
    # `upload_failed` is deliberately NOT server-retryable: there are no bytes.
    assert lifecycle.UPLOAD_FAILED not in lifecycle.RETRYABLE_FAILURES


def test_there_is_no_transcription_state_to_enter() -> None:
    """THE ENFORCEMENT OF P3 IS A MISSING STATE. The deleted interview mode
    had a `processing` state where audio was transcribed into the records the
    scorers read. The machine has no such state and no edge to one, so no
    code path runs from a stored recording to a grade."""
    from app.services.video import lifecycle

    for gone in ("processing", "transcription_failed", "processing_failed", "uploading"):
        assert gone not in lifecycle.STATUSES, gone
    assert lifecycle.can_transition(lifecycle.UPLOADED, lifecycle.COMPRESSING)


# ── S3 keys carry no PII (spec 5) ────────────────────────────────────────────


def test_object_keys_are_ids_only_and_a_hostile_mime_cannot_escape() -> None:
    from app.services.video import keys

    conversation_id = uuid.uuid4()
    recording_id = uuid.uuid4()
    segment = keys.segment_key(conversation_id, recording_id, 3, "video/webm")
    compressed = keys.compressed_key(conversation_id, recording_id)
    assert segment.endswith("/seg-0003.webm")
    for key in (segment, compressed):
        assert str(conversation_id) in key
        assert str(recording_id) in key
        assert "@" not in key
        # Nothing but the two ids, the fixed prefixes and fixed file names.
        for part in key.split("/"):
            assert part in (
                keys.RAW_PREFIX, keys.COMPRESSED_PREFIX,
                str(conversation_id), str(recording_id),
            ) or part.split(".")[0] in ("seg-0003", "assessment")
    # A hostile MIME type cannot smuggle path characters into the key.
    hostile = keys.segment_key(conversation_id, recording_id, 0, "video/../../etc")
    assert ".." not in hostile
    assert hostile.endswith("seg-0000.webm")
    with pytest.raises(ValueError):
        keys.segment_key(conversation_id, recording_id, -1, "video/webm")


def test_segment_keys_sort_in_recording_order() -> None:
    """The ordinal is zero-padded, so a store listing and the concat list agree
    on order past the ninth segment."""
    from app.services.video import keys

    conversation_id, recording_id = uuid.uuid4(), uuid.uuid4()
    generated = [
        keys.segment_key(conversation_id, recording_id, ordinal, "video/webm")
        for ordinal in (0, 2, 9, 10, 11)
    ]
    assert sorted(generated) == generated


# ── Joining the segments (pure) ──────────────────────────────────────────────


def test_the_concat_list_quotes_every_path_so_none_can_split_an_entry(
    tmp_path: pathlib.Path,
) -> None:
    from app.services.video.processing import concat_list

    odd = tmp_path / "it's a dir" / "seg-0001.webm"
    listing = concat_list([tmp_path / "seg-0000.webm", odd])
    lines = listing.strip().split("\n")
    assert len(lines) == 2
    assert all(line.startswith("file '") and line.endswith("'") for line in lines)
    assert "it'\\''s a dir" in lines[1]


# ── No scorer imports services/video ─────────────────────────────────────────

#: The only modules that may import the video package: the routes, the task
#: module, the delivery and deletion layers, and the package's own internals.
#: Since 2026-09-24 the pipeline writes NOTHING a scorer reads (the
#: video-interview transcript writer is deleted), so this is the list of who
#: may touch assessment MEDIA, and no entry on it scores.
PERMITTED_IMPORTERS = {
    # The recording routes, carved out of api/assessments.py on 2026-09-24
    # (PLAN-p3 WP0) and rebuilt around segments (WP5).
    "api/assessment_recording.py",
    # The processing, repair and retention tasks.
    "workers/tasks_media.py",
    # The client-side delivery/metadata layer. It reads the lifecycle
    # constants and the per-link accessor to render honest status words and
    # mint presigned URLs; it is not a scorer.
    "services/assessment_video_access.py",
    # The deletion side of the same artifact. It reads
    # `video.storage.delete_verified` so a deletion is HEAD-confirmed by the
    # SAME function the pipeline uses; a second deleter would be a second
    # answer to "is the object really gone".
    "services/assessment_media_retention.py",
    # Candidate erasure reaches stored recordings through the same verified
    # deletion. It scores nothing; it only deletes.
    "services/erasure.py",
    # The spoken-answer path (PLAN-p3 WP3, landing beside this change): the
    # voice routes in the conversation module and their Route.LAMBDA task
    # store the answer audio through `services/video`, the one sanctioned
    # media writer `tests/test_proctoring_no_media.py` names.
    "api/assessment_conversation.py",
    "workers/tasks_voice.py",
}

FORBIDDEN_TARGET = "app.services.video"


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_no_scorer_or_anything_else_unexpected_imports_the_video_package() -> None:
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith("services/video/"):
            continue
        imports = _imports_of(path)
        if any(name.startswith(FORBIDDEN_TARGET) for name in imports):
            if rel not in PERMITTED_IMPORTERS:
                offenders.append(rel)
    assert offenders == [], (
        "these modules import services/video and are not on the permitted "
        f"list: {offenders}"
    )


def test_the_video_package_imports_no_scorer() -> None:
    """The inward direction, exactly as the proctoring isolation test argues:
    the tempting change is the pipeline reaching for a grade, and once the
    import exists the dependency runs both ways in review."""
    forbidden = (
        "app.services.functional_assessment",
        "app.services.miti",
        "app.services.siddhi",
        "app.services.matching",
        "app.services.rating",
        "app.services.job_candidates",
        "app.services.proctoring",
        "app.services.evidence",
    )
    for path in (APP / "services" / "video").rglob("*.py"):
        imports = _imports_of(path)
        for name in imports:
            assert not any(name.startswith(target) for target in forbidden), (
                f"{path.name} imports {name}"
            )
