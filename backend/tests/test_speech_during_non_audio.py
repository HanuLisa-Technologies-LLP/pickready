"""Speaking during a question that does not take a spoken answer.

    "Speaking during a non-audio question is logged each time; 3 or more
     occurrences are highlighted in the report; it never counts toward
     termination."  (master prompt, Phase 3)

"Each time" is read as each OCCURRENCE: a run of consecutive chunks with
speech in them is one occurrence whose duration grows, so a minute of reading
aloud is one entry of about a minute rather than four of fifteen seconds. A
chunk with no speech, or one heard while a spoken answer is being captured,
ends the run.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.models.proctoring import OUTCOME_ACTIVE, POLICY_CONTINUE_AND_NOTE, POLICY_TERMINATE
from app.services.proctoring import audio as proctoring_audio
from app.services.proctoring import catalog, phrasing
from app.services.proctoring import report as proctoring_report
from app.services.proctoring.audio import ChunkAnalysis
from app.services.proctoring.config import get_config
from app.services.proctoring.report import EventView

from tests.test_proctoring_pipeline import _Fx, _cleanup, _events, _factory_or_skip, _load, _seed

CONFIG = get_config()
MS = 1000
SPEECH = CONFIG.speech_min_seconds + 1.0
T0 = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)


def _one_voice(seconds: float) -> ChunkAnalysis:
    return ChunkAnalysis(
        speaker_count=1 if seconds else 0,
        speaker_seconds=(seconds,) if seconds else (),
        speech_seconds=seconds,
    )


def _poster(analysis: ChunkAnalysis):
    async def _post(chunk, content_type, config):  # noqa: ANN001
        return analysis

    return _post


def _probe(answer: bool):
    async def _check(session, conversation_id, now, config):  # noqa: ANN001
        return answer

    return _check


@pytest.fixture
def analysis_configured(monkeypatch):
    configured = dataclasses.replace(CONFIG, analysis_service_url="http://analysis.invalid:8100")
    monkeypatch.setattr(proctoring_audio, "get_config", lambda: configured)
    return configured


async def _chunks(s, ps, fx, sequence, *, policy=POLICY_CONTINUE_AND_NOTE):
    out = None
    for index, (seconds, capturing) in enumerate(sequence):
        out = await proctoring_audio.analyse_chunk(
            s, ps, policy, b"x", "audio/webm",
            now=T0 + timedelta(seconds=CONFIG.audio_chunk_seconds * index),
            post=_poster(_one_voice(seconds)), probe=_probe(capturing), enqueue=fx.enqueue,
        )
    return out


def _speech_events(rows):
    return [r for r in rows if r.event_type == "SPEECH_DURING_NON_AUDIO_QUESTION"]


@pytest.mark.asyncio
async def test_a_run_of_speaking_chunks_is_one_occurrence_whose_duration_grows(
    analysis_configured,
) -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await _chunks(s, ps, fx, [(SPEECH, False)] * 3)
                    events = _speech_events(await _events(s, fx))
                    assert len(events) == 1
                    assert events[0].path == catalog.PATH_C
                    await s.refresh(events[0])
                    assert events[0].duration_ms == int(SPEECH * MS) * 3
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_silence_ends_a_run_and_the_next_speech_is_a_new_occurrence(
    analysis_configured,
) -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        quiet = CONFIG.speech_min_seconds - 0.5
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await _chunks(s, ps, fx, [
                        (SPEECH, False), (quiet, False), (SPEECH, False),
                        (0.0, False), (SPEECH, False),
                    ])
                    assert len(_speech_events(await _events(s, fx))) == 3
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_speaking_while_a_spoken_answer_is_captured_is_never_logged(
    analysis_configured,
) -> None:
    """The candidate's own voice while the mic is active for an answer is
    expected (master prompt). Mutation-checked: dropping the probe from the
    rule makes this fail."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await _chunks(s, ps, fx, [(SPEECH, True)] * 4)
                    assert _speech_events(await _events(s, fx)) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_speaking_never_warns_and_never_ends_the_session_even_under_terminate(
    analysis_configured,
) -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, policy=POLICY_TERMINATE)
        from app.core.db import superadmin_scope

        quiet = CONFIG.speech_min_seconds - 0.5
        pattern = [(SPEECH, False), (quiet, False)] * (CONFIG.max_warnings * 3)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    out = await _chunks(s, ps, fx, pattern, policy=POLICY_TERMINATE)
                    assert out.warning is None and out.termination is None
                    assert ps.outcome == OUTCOME_ACTIVE
                    assert ps.warnings_used == 0
                    events = _speech_events(await _events(s, fx))
                    assert len(events) == CONFIG.max_warnings * 3
                    assert not any(e.warning_issued for e in events)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── The report ───────────────────────────────────────────────────────────────


class _Ps:
    """The slice of a session row the composer reads."""

    def __init__(self) -> None:
        self.started_at = T0
        self.consented_at = T0
        self.ended_at = T0 + timedelta(minutes=40)
        self.outcome = "completed"
        self.warnings_used = 0
        self.termination_reason = None
        self.session_quality = "good"


def _speech_view(minute: int) -> EventView:
    return EventView(
        event_type="SPEECH_DURING_NON_AUDIO_QUESTION",
        occurred_at=T0 + timedelta(minutes=minute),
        duration_ms=20 * MS,
        path=catalog.PATH_C,
        warning_issued=False,
        warning_number=None,
        metadata={},
    )


def _compose(count: int) -> dict:
    return proctoring_report.compose(
        candidate_name="A Candidate",
        assessment_name="Tatva Assessment for a role",
        ps=_Ps(),
        events=[_speech_view(minute) for minute in range(count)],
        audio_available=True,
    )


def test_every_occurrence_is_listed_under_audio_and_in_the_activity_log() -> None:
    content = _compose(2)
    assert any("spoke twice" in line for line in content["findings"]["audio"])
    assert len(content["activity_log"]) == 2
    for row in content["activity_log"]:
        assert row["what_the_system_did"] == "Noted it"


def test_below_the_highlight_count_it_stays_out_of_the_summary() -> None:
    threshold = CONFIG.speech_highlight_threshold
    content = _compose(threshold - 1)
    assert phrasing.speech_highlight(threshold - 1) not in content["summary"]


def test_at_the_highlight_count_it_is_lifted_into_the_summary() -> None:
    """Mutation-checked: comparing with `>` instead of `>=` fails this."""
    threshold = CONFIG.speech_highlight_threshold
    content = _compose(threshold)
    assert phrasing.speech_highlight(threshold) in content["summary"]
    assert "highlighted" in content["summary"]


def test_the_speech_sentence_says_it_never_ends_an_assessment() -> None:
    sentence = phrasing.finding_sentence(
        "SPEECH_DURING_NON_AUDIO_QUESTION", times=3, duration_ms=60 * MS
    )
    assert "never ends an assessment" in sentence


# ── The capture probe against the real table ────────────────────────────────


@pytest.mark.asyncio
async def test_the_capture_probe_reads_the_voice_answer_table() -> None:
    """`voice_answers` is created by the conversation engine's migration
    (PLAN-p3 3.6, a sibling work package in this release). Where it exists,
    the probe is exercised against it; where it does not yet, the skip says so
    rather than passing."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            present = (
                await s.execute(text("SELECT to_regclass('public.voice_answers') IS NOT NULL"))
            ).scalar_one()
        if not present:
            pytest.skip(
                "voice_answers is created by the Phase 3 conversation migration; "
                "this probe test runs once that migration is in the chain"
            )
        now = datetime.now(timezone.utc)
        window = timedelta(seconds=CONFIG.audio_chunk_seconds * 2)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    assert await proctoring_audio.voice_capture_open(
                        s, fx.conv_id, now, CONFIG
                    ) is False
                    question_id = await _voice_question(s, fx)

                    async def _voice(status: str, *, started: datetime, uploaded=None):
                        await s.execute(
                            text(
                                "DELETE FROM voice_answers WHERE conversation_id = :c"
                            ),
                            {"c": fx.conv_id},
                        )
                        await s.execute(
                            text(
                                "INSERT INTO voice_answers (id, tenant_id, conversation_id, "
                                "question_id, turn_seq, status, capture_started_at, uploaded_at) "
                                "VALUES (:id, :t, :c, :q, 1, :status, :started, :uploaded)"
                            ),
                            {
                                "id": uuid.uuid4(), "t": fx.tenant_id, "c": fx.conv_id,
                                "q": question_id, "status": status, "started": started,
                                "uploaded": uploaded,
                            },
                        )

                    # A capture in progress excuses the speech.
                    await _voice("recording", started=now - timedelta(seconds=20))
                    assert await proctoring_audio.voice_capture_open(s, fx.conv_id, now, CONFIG)
                    # A capture abandoned longer ago than a spoken answer can
                    # last does not.
                    stale = now - timedelta(
                        seconds=get_settings().assessment_voice_max_seconds
                    ) - window - timedelta(seconds=5)
                    await _voice("recording", started=stale)
                    assert not await proctoring_audio.voice_capture_open(
                        s, fx.conv_id, now, CONFIG
                    )
                    # The tail of an answer just submitted is still the answer...
                    await _voice(
                        "uploaded", started=now - timedelta(seconds=60),
                        uploaded=now - timedelta(seconds=5),
                    )
                    assert await proctoring_audio.voice_capture_open(s, fx.conv_id, now, CONFIG)
                    # ...and an answer submitted long before this chunk is not.
                    await _voice(
                        "transcribed", started=now - timedelta(seconds=300),
                        uploaded=now - window - timedelta(seconds=5),
                    )
                    assert not await proctoring_audio.voice_capture_open(
                        s, fx.conv_id, now, CONFIG
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def _voice_question(s, fx) -> uuid.UUID:
    """One question row for a voice answer to point at (its FK)."""
    from app.models.assessment import CandidateQuestion, JobCompetency

    competency_id, question_id = uuid.uuid4(), uuid.uuid4()
    s.add(JobCompetency(
        id=competency_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
        category="must_have", name="Voice item", description="A spoken answer.",
        required_level=82, ordinal=1,
    ))
    await s.flush()
    s.add(CandidateQuestion(
        id=question_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
        job_candidate_link_id=fx.link_id, competency_id=competency_id,
        prompt="Tell us about it.", ordinal=0, rubric_json={},
    ))
    await s.flush()
    return question_id
