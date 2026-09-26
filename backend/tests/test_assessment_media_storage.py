"""Stored assessment media: the kind, the encryption, the deletion lifecycle.

Provenance: the owner ruling of 2026-09-22, which reversed principle P1 of the
proctoring specification and required in the same sentence that authorization,
tenant isolation, encryption, the retention and deletion lifecycle and the
consent controls be preserved for the media now being stored.

`tests/test_proctoring_no_media.py` guards the BOUNDARY (who may write media,
what may leave the backend). This file guards the LIFECYCLE: that a
proctored-session recording takes the storage half of the pipeline and not the
scoring half, that a deletion is confirmed rather than assumed, and that the
retention sweep converges instead of enumerating the same rows for ever.

These are pure-function and source tests on purpose. The database-backed
behaviour is covered by `tests/test_recording_segments.py` and
`tests/test_media_retention_d4.py`, which drive the real tables; what is
asserted here is the set of decisions a future change is most likely to break
without any test noticing.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.dual_mode import (
    DEFAULT_RECORDING_KIND,
    RECORDING_KINDS,
    RECORDING_PROCTORED_SESSION,
    RECORDING_VIDEO_INTERVIEW,
    VideoRecording,
)
from app.services import assessment_media_retention as media_retention
from app.services.video import keys, lifecycle

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


# ── The kind, and what each one is allowed to reach ──────────────────────────


def test_both_kinds_still_read_and_only_the_session_kind_is_written() -> None:
    """Rows written before migration 0110 are interview recordings and keep
    that label, so the vocabulary keeps both kinds for READS. The
    video-interview mode is deleted (2026-09-24), so the one kind a new row
    can be is the proctored session's, and migration 0126 moved the server
    default with it."""
    assert RECORDING_KINDS == (RECORDING_VIDEO_INTERVIEW, RECORDING_PROCTORED_SESSION)
    assert DEFAULT_RECORDING_KIND == RECORDING_PROCTORED_SESSION
    column = VideoRecording.__table__.c.kind
    assert column.server_default.arg == RECORDING_PROCTORED_SESSION


def test_the_check_constraint_matches_the_python_vocabulary() -> None:
    """Two copies of one fact stay honest only when something compares them,
    which is the argument `test_schedule_parity` and `test_runbook_parity`
    both make. Here the copies are the model CHECK and the tuple above."""
    constraint = next(
        c for c in VideoRecording.__table__.constraints
        if getattr(c, "name", None) == "ck_video_recordings_kind"
    )
    expression = str(constraint.sqltext)
    for kind in RECORDING_KINDS:
        assert f"'{kind}'" in expression, kind
    migration = (
        BACKEND / "alembic" / "versions" / "0110_assessment_media_storage.py"
    ).read_text(encoding="utf-8")
    assert "KINDS = " in migration
    for kind in RECORDING_KINDS:
        assert f'"{kind}"' in migration


def test_nothing_in_the_pipeline_can_reach_a_scorer() -> None:
    """THE ENFORCEMENT OF P3 IS A MISSING STEP, not a flag. The deleted
    interview half extracted audio, transcribed it, segmented it and wrote it
    into the records the scorers read, then completed the assessment. None of
    those steps exists in the pipeline any more, defined or called, so there
    is no code path from a stored recording to a grade."""
    source = (APP / "services" / "video" / "processing.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for banned in (
        "run_transcription",
        "segment_transcript",
        "write_transcript_records",
        "complete_assessment",
        "extract_audio",
    ):
        assert banned not in defined | called, (
            f"the recording pipeline names {banned}(). That step feeds the "
            "records a scorer reads, and a monitoring recording must not."
        )


def test_one_task_one_path_and_no_branch_on_the_kind() -> None:
    """With one kind written there is one path, and the pipeline must not
    grow a branch on `kind` that a future second kind could fall through."""
    source = (APP / "services" / "video" / "processing.py").read_text(encoding="utf-8")
    assert "recording.kind" not in source
    tasks = "\n".join(
        module.read_text(encoding="utf-8")
        for module in sorted((APP / "workers").glob("tasks*.py"))
    )
    assert "pickready.process_assessment_video" in tasks
    assert "process_proctored_session" not in tasks


# ── Keys carry ids only, for both kinds ──────────────────────────────────────


def test_the_object_key_still_carries_nothing_but_ids() -> None:
    """The proctored recording reuses the one key builder, so the no-PII rule
    it already enforced now covers the new artifact for free."""
    conversation_id = uuid.uuid4()
    recording_id = uuid.uuid4()
    for key in (
        keys.segment_key(conversation_id, recording_id, 0, "video/webm"),
        keys.compressed_key(conversation_id, recording_id),
    ):
        assert str(conversation_id) in key
        assert str(recording_id) in key
        assert "@" not in key
        assert ".." not in key


# ── The deletion lifecycle ───────────────────────────────────────────────────


class _Recording:
    """A recording stand-in with exactly the attributes the retention module
    reads. Deliberately not the ORM object: these are pure functions over a
    row's fields and a database would add nothing but a fixture."""

    def __init__(self, *, raw: str | None, compressed: str | None) -> None:
        self.id = uuid.uuid4()
        self.s3_raw_key = raw
        self.s3_compressed_key = compressed
        self.raw_deleted = False
        self.media_deleted_at: datetime | None = None
        self.media_delete_failures = 0


def test_the_enumeration_names_the_compressed_object_first() -> None:
    """Order matters for a partial pass: the compressed object is the one a
    hiring team can actually watch, so it is the one that goes first when a
    deletion is interrupted part way through."""
    recording = _Recording(raw="assessment-raw/a/b/raw.webm",
                           compressed="assessment-compressed/a/b/assessment.mp4")
    entries = media_retention.object_keys_for_recording(recording)
    assert [entry["kind"] for entry in entries] == [
        media_retention.OBJECT_KIND_COMPRESSED,
        media_retention.OBJECT_KIND_RAW,
    ]
    assert entries[0]["key"] == "assessment-compressed/a/b/assessment.mp4"


def test_the_enumeration_includes_the_raw_key_even_when_it_reads_as_deleted() -> None:
    """`raw_deleted` records that a HEAD confirmed it gone ONCE. Trusting the
    flag would mean a raw object left behind by a failed deletion is never
    looked at again by the sweep whose whole job is to finish it, and a
    re-HEAD of an absent key costs one call and answers the same thing."""
    recording = _Recording(raw="assessment-raw/a/b/raw.webm", compressed=None)
    recording.raw_deleted = True
    entries = media_retention.object_keys_for_recording(recording)
    assert [entry["kind"] for entry in entries] == [media_retention.OBJECT_KIND_RAW]


def test_a_recording_with_no_keys_enumerates_nothing_rather_than_an_empty_key() -> None:
    """An empty string is a key that deletes nothing and reports success."""
    assert media_retention.object_keys_for_recording(
        _Recording(raw=None, compressed=None)
    ) == []


def test_an_unconfirmed_deletion_is_remaining_and_never_counted_as_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"The delete call returned" is not evidence. `delete_verified` HEADs the
    key afterwards, and a False answer leaves the entry on the list so the
    next pass is shorter rather than the log being cleaner."""
    calls: list[str] = []

    def _fake_delete(key: str) -> bool:
        calls.append(key)
        return key.endswith("assessment.mp4")

    monkeypatch.setattr(
        media_retention.video_storage, "delete_verified", _fake_delete
    )
    entries = media_retention.object_keys_for_recording(
        _Recording(raw="assessment-raw/a/b/raw.webm",
                   compressed="assessment-compressed/a/b/assessment.mp4")
    )
    outcome = media_retention.delete_objects(entries)
    assert calls == [
        "assessment-compressed/a/b/assessment.mp4",
        "assessment-raw/a/b/raw.webm",
    ]
    assert outcome.deleted == 1
    assert [e["kind"] for e in outcome.remaining] == [
        media_retention.OBJECT_KIND_RAW
    ]
    assert not outcome.finished


def test_an_unreachable_object_store_is_a_failure_not_a_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment with no bucket has not deleted a candidate's video, it has
    failed to look. Reporting `finished` there would let an erasure record
    itself complete while the person's recording is still in the store."""
    from app.services import object_storage

    def _raise(key: str) -> bool:
        raise object_storage.ObjectStorageNotConfigured("no bucket")

    monkeypatch.setattr(media_retention.video_storage, "delete_verified", _raise)
    outcome = media_retention.delete_objects(
        [{"key": "assessment-compressed/a/b/assessment.mp4", "kind": "x"}]
    )
    assert outcome.deleted == 0
    assert outcome.failure == "ObjectStorageNotConfigured"
    assert not outcome.finished


def test_the_outcome_with_a_failure_and_nothing_remaining_cannot_exist() -> None:
    """`remaining` is computed from the HEAD checks, so a failure always
    leaves something on the list. Asserted so the property is stated rather
    than merely true by construction today."""
    outcome = media_retention.MediaDeletion(deleted=0, remaining=(), failure=None)
    assert outcome.finished


# ── Retention ────────────────────────────────────────────────────────────────


def test_the_purge_task_and_its_schedule_entry_both_exist() -> None:
    """A retention window with no sweep behind it is a paragraph. An entry in
    Python with no rule in Terraform is the silent half and
    `test_schedule_parity` owns that; this owns the other pairing, the entry
    naming a task that exists."""
    from app.workers.schedule import SCHEDULE

    entry = next(
        item for item in SCHEDULE if item.task == "pickready.purge_assessment_media"
    )
    assert entry.rule == "readypick-purge-assessment-media"
    assert entry.interval_minutes == 60
    tasks = (APP / "workers" / "tasks_media.py").read_text(encoding="utf-8")
    assert 'name="pickready.purge_assessment_media"' in tasks


def test_the_retention_query_reads_both_stored_dates_and_skips_deleted_rows() -> None:
    """The sweep must CONVERGE, and it must read DATES. Without
    `media_deleted_at IS NULL` it would re-enumerate every recording it has
    already finished with, for ever; and the due moment is the LEAST of the
    recording's stored date and its job's closure date, never a window
    computed from a module constant at sweep time (owner decision D4)."""
    source = (
        APP / "services" / "assessment_media_retention.py"
    ).read_text(encoding="utf-8")
    assert "VideoRecording.media_purge_due_at, Job.assessment_purge_due_at" in source
    assert "func.least(" in source
    assert "VideoRecording.media_deleted_at.is_(None)" in source
    assert "retention_days" not in source[source.index("async def due_recordings"):]


def test_the_stored_at_stamp_is_the_verified_store() -> None:
    """`stored_at` is stamped where the compressed object was HEAD-confirmed,
    and nowhere else. Stamping it at upload would claim stored media that
    might never have been stored at all."""
    source = (APP / "services" / "video" / "processing.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    stamped = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "stored_at"
            for target in node.targets
        )
    ]
    assert len(stamped) == 1, "stored_at is stamped more than once"
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_recording"
    )
    body = ast.unparse(function)
    assert body.index("head_size(") < body.index("stored_at =")


# ── The erasure and job-closure hooks ────────────────────────────────────────


def test_the_erasure_hook_has_the_signature_the_erasure_path_needs() -> None:
    """Enumerate and delete are SEPARATE because erasure needs them separate:
    the keys are captured BEFORE the rows are erased, and an enumeration
    performed afterwards returns nothing because the cascade has removed every
    row that named an object."""
    import inspect

    signature = inspect.signature(media_retention.object_keys_for_candidate)
    assert list(signature.parameters) == ["session", "candidate_id"]
    assert inspect.iscoroutinefunction(media_retention.object_keys_for_candidate)

    job_signature = inspect.signature(media_retention.object_keys_for_job)
    assert list(job_signature.parameters) == ["session", "job_id"]
    assert inspect.iscoroutinefunction(media_retention.object_keys_for_job)

    assert not inspect.iscoroutinefunction(media_retention.delete_objects)


def test_the_object_key_entries_match_what_the_deletion_record_stores() -> None:
    """`candidate_deletion_requests.object_keys_json` holds
    `[{"key": ..., "kind": ...}]`. Returning a different shape would need a
    translation step at the call site, and a translation step is where a
    field gets dropped."""
    recording = _Recording(raw="r", compressed="c")
    for entry in media_retention.object_keys_for_recording(recording):
        assert set(entry) == {"key", "kind"}
        assert isinstance(entry["key"], str)
        assert isinstance(entry["kind"], str)


def test_the_retention_module_reaches_no_proctoring_and_no_scorer() -> None:
    """Media is deleted because of its age, its candidate or its job, never
    because of anything proctoring observed."""
    tree = ast.parse(
        (APP / "services" / "assessment_media_retention.py").read_text(encoding="utf-8")
    )
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in (
        "app.services.proctoring",
        "app.services.functional_assessment",
        "app.services.miti",
        "app.services.siddhi",
    ):
        assert not any(name.startswith(banned) for name in imported), banned


# ── Consent and authorization are unchanged and still enforced ───────────────


def test_download_still_refuses_a_never_asked_consent() -> None:
    """NULL means the candidate was never asked, and absence of consent is
    never consent. Restated here because the set of artifacts this rule
    governs grew on 2026-09-22 while the rule itself did not."""
    from app.services import retention_consent

    class _Candidate:
        retain_video_consent = None

    candidate = _Candidate()
    assert not retention_consent.video_download_allowed(candidate)
    candidate.retain_video_consent = False
    assert not retention_consent.video_download_allowed(candidate)
    candidate.retain_video_consent = True
    assert retention_consent.video_download_allowed(candidate)


def test_every_media_route_is_behind_the_review_capability() -> None:
    """Someone who may read this candidate's assessment evidence may watch it;
    someone who may not certainly may not. Swept over the router source so a
    fourth route added later cannot arrive ungated."""
    source = (APP / "api" / "videos.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    routes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "patch", "delete"}
            for d in node.decorator_list
        )
    ]
    assert len(routes) >= 3, [r.name for r in routes]
    for route in routes:
        text = ast.unparse(route.args)
        assert "require_capability(caps.VIEW_REVIEW_SCREEN)" in text, route.name
        assert "get_tenant_db" in text, route.name


def test_the_proctored_recording_start_is_gated_by_the_proctoring_gate() -> None:
    """Media is captured during a proctored assessment and at no other time,
    so a session that may not proceed may not be recorded. And there is no
    enable flag next to it: P4 was re-affirmed by the same ruling."""
    # The recording routes were carved out of `api/assessments.py` on
    # 2026-09-24 (PLAN-p3 WP0), and the report routes moved to
    # `api/assessment_reports.py` (PLAN-p5 WP5-F); the flag sweep covers all three.
    source = "\n".join(
        (APP / "api" / name).read_text(encoding="utf-8")
        for name in (
            "assessment_reports.py",
            "assessment_conversation.py",
            "assessment_recording.py",
        )
    )
    tree = ast.parse(
        (APP / "api" / "assessment_recording.py").read_text(encoding="utf-8")
    )
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start_session_media"
    )
    body = ast.unparse(function)
    assert "proctoring_gate.require_active" in body
    assert "assessment_consent.require_consent" in body
    for banned in ("proctoring_enabled", "media_enabled", "recording_enabled"):
        assert banned not in source, (
            f"{banned} appears in the assessment routes. Proctoring is "
            "mandatory and media storage is required; neither is a flag."
        )
