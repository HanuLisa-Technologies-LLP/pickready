"""Dual-mode assessment: consent gates, mode freeze, the recording lifecycle,
the canonical model, and the isolation lines (2026-09-05 spec).

What is pinned here and why:

  * CONSENT IS A HARD PREREQUISITE FOR BOTH MODES (spec 3.1). Starting either
    the conversational session or the video interview without a consent row
    for THIS session in THIS mode answers 409, and the gate asks the table.
  * The consent row records the VERSIONS in force when it was accepted (3.4),
    because the wording is configurable and a dispute is settled by which
    wording was accepted.
  * The MODE FREEZES once the assessment begins: the records already written
    belong to the chosen mode.
  * The recording status machine only moves along its defined edges, and every
    failure state names the step that failed.
  * Both modes produce the SAME canonical shape (spec 13, 21).
  * An S3 key carries IDS ONLY, never candidate PII (spec 5).
  * NO SCORER IMPORTS `services/video` (the proctoring isolation posture,
    applied to the new package): the video pipeline's output is the same
    transcript records everyone already reads, so a scorer that imported the
    package would be reaching for something it must not have.
  * TRANSCRIBE DISABLED IS AN HONEST FAILURE: `transcription_failed` with a
    message naming the configuration, never a fake transcript, no completion,
    no charge.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

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
    lifecycle.advance(row, lifecycle.UPLOADING)
    lifecycle.advance(row, lifecycle.UPLOADED)
    lifecycle.advance(row, lifecycle.PROCESSING)
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.advance(row, lifecycle.READY)
    lifecycle.advance(
        row, lifecycle.TRANSCRIPTION_FAILED, error_detail="not configured"
    )
    assert row.error_detail == "not configured"
    # The retry path: a failure goes back to uploaded, and the stale failure
    # message is cleared so it cannot read as current.
    lifecycle.advance(row, lifecycle.UPLOADED)
    assert row.error_detail is None


def test_every_failure_state_names_a_step_and_ready_is_terminal() -> None:
    from app.services.video import lifecycle

    for failure in lifecycle.FAILURE_STATUSES:
        assert failure.endswith("_failed")
    assert not lifecycle.can_transition(lifecycle.READY, lifecycle.PROCESSING)
    # `upload_failed` is deliberately NOT server-retryable: there are no bytes.
    assert lifecycle.UPLOAD_FAILED not in lifecycle.RETRYABLE_FAILURES


# ── S3 keys carry no PII (spec 5) ────────────────────────────────────────────


def test_object_keys_are_ids_only_and_a_hostile_mime_cannot_escape() -> None:
    from app.services.video import keys

    conversation_id = uuid.uuid4()
    recording_id = uuid.uuid4()
    raw = keys.raw_key(conversation_id, recording_id, "video/webm")
    compressed = keys.compressed_key(conversation_id, recording_id)
    audio = keys.audio_key(conversation_id, recording_id)
    for key in (raw, compressed, audio):
        assert str(conversation_id) in key
        assert str(recording_id) in key
        assert "@" not in key
        # Nothing but the two ids, the fixed prefixes and fixed file names.
        for segment in key.split("/"):
            assert segment in (
                keys.RAW_PREFIX, keys.COMPRESSED_PREFIX,
                str(conversation_id), str(recording_id),
            ) or segment.split(".")[0] in ("raw", "audio", "assessment")
    # A hostile MIME type cannot smuggle path characters into the key.
    hostile = keys.raw_key(conversation_id, recording_id, "video/../../etc")
    assert ".." not in hostile
    assert hostile.endswith("raw.webm")


# ── No scorer imports services/video ─────────────────────────────────────────

#: The only modules that may import the video package: the routes, the task
#: module, and the package's own internals. The pipeline WRITES the shared
#: transcript records; everything that scores reads those, mode-blind.
PERMITTED_IMPORTERS = {
    "api/assessments.py",
    "workers/tasks.py",
    # The client-side delivery/metadata layer (2026-09-05 dashboard/Executive
    # Profile/video spec). It reads the lifecycle constants and the per-link
    # accessor to render honest status words and mint presigned URLs; it is
    # not a scorer and its own importers (the dashboard, the /videos routes)
    # deliberately do not touch the pipeline package.
    "services/assessment_video_access.py",
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
    )
    for path in (APP / "services" / "video").rglob("*.py"):
        imports = _imports_of(path)
        for name in imports:
            assert not any(name.startswith(target) for target in forbidden), (
                f"{path.name} imports {name}"
            )


# ── Transcript segmentation (pure) ───────────────────────────────────────────


def test_segmentation_files_words_under_the_question_on_screen() -> None:
    from app.services.video.processing import segment_transcript

    q1, q2 = str(uuid.uuid4()), str(uuid.uuid4())
    items = [
        {"type": "pronunciation", "start_time": "1.0", "end_time": "1.4",
         "alternatives": [{"content": "I"}]},
        {"type": "pronunciation", "start_time": "1.5", "end_time": "2.0",
         "alternatives": [{"content": "led"}]},
        {"type": "punctuation", "alternatives": [{"content": "."}]},
        {"type": "pronunciation", "start_time": "31.0", "end_time": "31.5",
         "alternatives": [{"content": "Kafka"}]},
    ]
    marks = [
        {"question_id": q1, "offset_seconds": 0.0},
        {"question_id": q2, "offset_seconds": 30.0},
    ]
    segments = segment_transcript(items, marks, total_duration=60.0)
    by_id = {segment.question_id: segment for segment in segments}
    assert by_id[q1].text == "I led."
    assert by_id[q2].text == "Kafka"
    assert by_id[q1].start_time == pytest.approx(1.0)
    assert by_id[q2].end_time == pytest.approx(31.5)


def test_run_transcription_refuses_honestly_when_disabled() -> None:
    from app.services.video import processing

    with pytest.raises(processing.TranscriptionError) as raised:
        processing.run_transcription(
            audio_key="a", output_key="b", job_name="c"
        )
    assert "not configured" in str(raised.value)
    assert "retried" in str(raised.value)


# ── Database-backed flows ────────────────────────────────────────────────────


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fx:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.conv_id = uuid.uuid4()
        self.q_ids: list[uuid.UUID] = []


async def _seed(factory, fx: _Fx, *, question_count: int = 3, mode: str = "conversational") -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import (
        AssessmentConversation,
        CandidateQuestion,
        JobCompetency,
    )
    from app.models.candidate import JobCandidateLink
    from app.models.proctoring import OUTCOME_ACTIVE, ProctoringSession
    from app.services import ppi as _ppi

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name=f"Dual {fx.tenant_id.hex[:6]}",
                             domain=f"{fx.tenant_id}.dual.test"))
                await s.flush()
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Engineer",
                          jd_json={}, status=JobStatus.ratified, ratified_at=now,
                          assessment_status="ready_for_candidates",
                          assessment_grade="non_managerial"))
                s.add(Candidate(id=fx.cand_id, email=f"d{fx.cand_id.hex[:8]}@t.test",
                                full_name="Dual Candidate", consent_databank=False))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id, candidate_id=fx.cand_id,
                                       source=LinkSource.fresh, status="applied"))
                await s.flush()
                competency_ids = []
                for index, category in enumerate(_ppi.CATEGORIES, 1):
                    competency_id = uuid.uuid4()
                    competency_ids.append(competency_id)
                    s.add(JobCompetency(
                        id=competency_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        category=category, name=f"Item {index}",
                        description=f"What item {index} measures.",
                        required_level=82, ordinal=index,
                    ))
                await s.flush()
                for ordinal in range(question_count):
                    qid = uuid.uuid4()
                    fx.q_ids.append(qid)
                    s.add(CandidateQuestion(
                        id=qid, tenant_id=fx.tenant_id, job_id=fx.job_id,
                        job_candidate_link_id=fx.link_id,
                        competency_id=competency_ids[ordinal % len(competency_ids)],
                        prompt=f"Question {ordinal}?",
                        ordinal=ordinal, rubric_json={},
                    ))
                await s.flush()
                s.add(AssessmentConversation(
                    id=fx.conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    status="active", next_question_index=0,
                    invitation_sent_at=now, mode=mode,
                ))
                await s.flush()
                s.add(ProctoringSession(
                    tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                    job_candidate_link_id=fx.link_id, candidate_id=fx.cand_id,
                    job_id=fx.job_id, consented_at=now, started_at=now,
                    outcome=OUTCOME_ACTIVE,
                ))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM credit_ledger WHERE tenant_id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})


def _user(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


def _patch_link(monkeypatch, fx: _Fx) -> None:
    from app.api import assessments as mod
    from app.models import Job
    from app.models.candidate import JobCandidateLink

    async def _resolve(session, user, link_id):
        link = await session.get(JobCandidateLink, fx.link_id)
        job = await session.get(Job, fx.job_id)
        return link, job

    monkeypatch.setattr(mod, "_candidate_link", _resolve)


@pytest.mark.asyncio
async def test_starting_either_mode_without_consent_is_refused(monkeypatch) -> None:
    """The gate, in both directions (spec 3.1). The proctoring session exists,
    the invitation exists, the questions exist; the ONLY missing thing is the
    assessment consent row, and that alone answers 409."""
    from app.api import assessments as mod

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as conversational:
                        await mod.start_conversation(
                            fx.link_id, user=_user(fx), session=s
                        )
        assert conversational.value.status_code == 409
        assert "consent" in str(conversational.value.detail).lower()

        # Switch the session to the video interview: same gate, same answer.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    from app.models.assessment import AssessmentConversation

                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    conversation.mode = "video_interview"
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as video:
                        await mod.start_video_interview(
                            fx.link_id, user=_user(fx), session=s
                        )
        assert video.value.status_code == 409
        assert "consent" in str(video.value.detail).lower()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_consent_records_the_versions_in_force(monkeypatch) -> None:
    """Spec 3.4: the row stamps the configured versions, so a dispute is
    settled by which wording was accepted, and acceptance is idempotent."""
    from app.core.config import get_settings
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation
    from app.services import assessment_consent

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, mode="video_interview")
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    first = await assessment_consent.record_consent(
                        s, conversation, candidate_id=fx.cand_id
                    )
                    again = await assessment_consent.record_consent(
                        s, conversation, candidate_id=fx.cand_id
                    )
                    settings = get_settings()
                    assert first.assessment_mode == "video_interview"
                    assert first.consent_status == "granted"
                    assert first.consent_version == settings.assessment_consent_version
                    assert (
                        first.privacy_policy_version
                        == settings.assessment_privacy_policy_version
                    )
                    assert first.terms_version == settings.assessment_terms_version
                    assert again.id == first.id
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_mode_freezes_once_the_assessment_has_begun(monkeypatch) -> None:
    from app.api import assessments as mod
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation
    from app.schemas.assessments import AssessmentModeIn

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        _patch_link(monkeypatch, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    # Before starting, the mode may change freely.
                    state = await mod.select_assessment_mode(
                        fx.link_id, AssessmentModeIn(mode="video_interview"),
                        user=_user(fx), session=s,
                    )
                    assert state.mode == "video_interview"
                    assert state.mode_frozen is False
                    conversation.started_at = datetime.now(timezone.utc)
                    await s.flush()
                    with pytest.raises(HTTPException) as frozen:
                        await mod.select_assessment_mode(
                            fx.link_id, AssessmentModeIn(mode="conversational"),
                            user=_user(fx), session=s,
                        )
        assert frozen.value.status_code == 409
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def _write_video_answers(factory, fx: _Fx) -> None:
    """Drive the pipeline's structuring step directly over seeded marks."""
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation
    from app.models.dual_mode import VideoRecording
    from app.services.video import lifecycle
    from app.services.video.processing import Segment, write_transcript_records

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                conversation = await s.get(AssessmentConversation, fx.conv_id)
                recording = VideoRecording(
                    tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                    candidate_id=fx.cand_id, job_candidate_link_id=fx.link_id,
                    status=lifecycle.PROCESSING, started_at=now,
                    question_marks_json=[
                        {"question_id": str(qid), "question_type": "short_answer",
                         "offset_seconds": float(index * 30)}
                        for index, qid in enumerate(fx.q_ids)
                    ],
                )
                s.add(recording)
                await s.flush()
                segments = [
                    Segment(question_id=str(qid),
                            text=f"My spoken answer to question {index}.",
                            start_time=index * 30 + 1.0,
                            end_time=index * 30 + 20.0)
                    for index, qid in enumerate(fx.q_ids)
                ]
                written = await write_transcript_records(
                    s, conversation=conversation, recording=recording,
                    segments=segments,
                )
                assert written == len(fx.q_ids)
                # Idempotent: a re-run doubles nothing.
                rerun = await write_transcript_records(
                    s, conversation=conversation, recording=recording,
                    segments=segments,
                )
                assert rerun == 0


async def _write_conversational_answers(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope
    from app.models.assessment import (
        AssessmentAnswer,
        AssessmentConversation,
        AssessmentMessage,
        CandidateQuestion,
    )

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                conversation = await s.get(AssessmentConversation, fx.conv_id)
                ordinal = 0
                for index, qid in enumerate(fx.q_ids):
                    question = await s.get(CandidateQuestion, qid)
                    s.add(AssessmentMessage(
                        tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                        ordinal=ordinal + 1, speaker="agent",
                        domain="must_have", question_key=str(qid),
                        content=question.prompt,
                    ))
                    message = AssessmentMessage(
                        tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                        ordinal=ordinal + 2, speaker="candidate",
                        domain="must_have", question_key=str(qid),
                        content=f"My typed answer to question {index}.",
                        answer_label="substantive",
                    )
                    s.add(message)
                    await s.flush()
                    s.add(AssessmentAnswer(
                        tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                        question_id=qid, message_id=message.id,
                        question_type=question.question_type,
                        answer_json={"text": message.content},
                        submitted_at=now,
                    ))
                    ordinal += 2
                await s.flush()


@pytest.mark.asyncio
async def test_both_modes_produce_the_same_canonical_shape() -> None:
    """Spec 13 and 21: one canonical structure, mode-blind downstream."""
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation
    from app.services.assessment_canonical import build_canonical

    engine, factory = await _factory_or_skip()
    fx_conv, fx_video = _Fx(), _Fx()
    try:
        await _seed(factory, fx_conv, mode="conversational")
        await _seed(factory, fx_video, mode="video_interview")
        await _write_conversational_answers(factory, fx_conv)
        await _write_video_answers(factory, fx_video)

        async with factory() as s:
            async with superadmin_scope(s):
                conv = await build_canonical(
                    s, await s.get(AssessmentConversation, fx_conv.conv_id)
                )
                video = await build_canonical(
                    s, await s.get(AssessmentConversation, fx_video.conv_id)
                )
        assert set(conv) == set(video) == {
            "assessment_id", "candidate_id", "mode", "job_id", "responses",
        }
        assert conv["mode"] == "conversational"
        assert video["mode"] == "video_interview"
        assert len(conv["responses"]) == len(video["responses"]) == 3
        for one, other in zip(conv["responses"], video["responses"]):
            assert set(one) == set(other) == {
                "question_id", "question_type", "question", "answer", "metadata",
            }
        assert "spoken answer" in video["responses"][0]["answer"]
        assert "typed answer" in conv["responses"][0]["answer"]
        # The video answers landed in the SAME records the scorers read: the
        # canonical builder read them with zero mode branching.
    finally:
        await _cleanup(factory, fx_conv)
        await _cleanup(factory, fx_video)
        await engine.dispose()


@pytest.mark.asyncio
async def test_transcribe_disabled_is_an_honest_retryable_failure(monkeypatch) -> None:
    """`transcribe_enabled` is off (the suite default): the pipeline lands the
    recording in `transcription_failed` with a message naming the
    configuration, writes NO transcript, completes NOTHING and charges
    NOTHING. Never a fake transcript."""
    from app.core.db import superadmin_scope
    from app.models.assessment import AssessmentConversation, AssessmentMessage
    from app.models.dual_mode import VideoRecording
    from app.services.video import lifecycle, processing, storage
    from sqlalchemy import func as _func

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, mode="video_interview")
        recording_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(VideoRecording(
                        id=recording_id, tenant_id=fx.tenant_id,
                        conversation_id=fx.conv_id, candidate_id=fx.cand_id,
                        job_candidate_link_id=fx.link_id,
                        status=lifecycle.UPLOADED, started_at=now, ended_at=now,
                        s3_raw_key=f"assessment-raw/{fx.conv_id}/{recording_id}/raw.webm",
                        s3_compressed_key=f"assessment-compressed/{fx.conv_id}/{recording_id}/assessment.mp4",
                        question_marks_json=[],
                    ))

        # The media steps are stubbed: this test is about the HONESTY of the
        # transcription refusal, not about ffmpeg (whose own failures are
        # exercised by the tool wrapper's error paths).
        monkeypatch.setattr(storage, "get_bytes", lambda key: b"raw-bytes")
        monkeypatch.setattr(storage, "put", lambda **kwargs: 9)
        monkeypatch.setattr(
            processing, "probe_duration", lambda path, timeout_seconds: 42.0
        )
        monkeypatch.setattr(
            processing, "extract_audio",
            lambda src, dst, timeout_seconds: dst.write_bytes(b"wav"),
        )

        async with factory() as s:
            # The worker's own posture (`runtime.worker_session`): a
            # SESSION-level bypass, because the pipeline commits at each state
            # transition and a transaction-local scope would revert mid-run.
            await s.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            await processing.process_recording(s, recording_id)

        async with factory() as s:
            async with superadmin_scope(s):
                recording = await s.get(VideoRecording, recording_id)
                conversation = await s.get(AssessmentConversation, fx.conv_id)
                message_count = (
                    await s.execute(
                        select(_func.count()).select_from(AssessmentMessage).where(
                            AssessmentMessage.conversation_id == fx.conv_id
                        )
                    )
                ).scalar_one()
                ledger_rows = (
                    await s.execute(
                        text("SELECT COUNT(*) FROM credit_ledger WHERE tenant_id = :t"),
                        {"t": str(fx.tenant_id)},
                    )
                ).scalar_one()
        assert recording.status == lifecycle.TRANSCRIPTION_FAILED
        assert "not configured" in (recording.error_detail or "")
        assert recording.status in lifecycle.RETRYABLE_FAILURES
        assert conversation.completed_at is None
        assert message_count == 0
        assert ledger_rows == 0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
