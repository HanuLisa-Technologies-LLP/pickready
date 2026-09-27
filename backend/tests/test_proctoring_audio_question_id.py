"""A server-derived audio event names the question it happened during.

p3-w4 hunk 4 (docs/release/2026-09-vivekium/stage2-deferred-hunks.md), closed
by package s4-audit-close. Speaking during a question that takes no spoken
answer, and a strong second voice, are events the SERVER derives from an
audio chunk. Every client event carries the question on screen; these two were
stored with `question_id` NULL, so a recruiter reading the Proctoring Report
could not tell which question a candidate was reading aloud during.

The turn identity belongs to the conversation engine, so it is exposed there
(`turns.current_question_id`) and the ROUTE hands it to proctoring: the
proctoring package still reads no question-writing code
(`tests/test_proctoring_scoring_isolation.py`).
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.core.db import superadmin_scope
from app.models.assessment import AssessmentConversation
from app.models.proctoring import POLICY_CONTINUE_AND_NOTE, ProctoringSession
from app.services.assessment_conversation import turns
from app.services.proctoring import audio as proctoring_audio
from app.services.proctoring import state as proctoring_state
from app.services.proctoring.audio import ChunkAnalysis
from app.services.proctoring.config import get_config
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

pytestmark = pytest.mark.asyncio

CONFIG = get_config()


@pytest.fixture
async def candidate():
    if not await cw.reachable():
        pytest.skip("no database reachable")
    factory = cw.sessions()
    person = await create_candidate_session(factory)
    try:
        yield person
    finally:
        await close_candidate_session(factory, person)


@pytest.fixture
def analysis_configured(monkeypatch):
    """The audio rules run only where an analysis service is configured."""
    configured = dataclasses.replace(CONFIG, analysis_service_url="http://analysis.invalid:8100")
    monkeypatch.setattr(proctoring_audio, "get_config", lambda: configured)
    return configured


def _heard(*speaker_seconds: float) -> ChunkAnalysis:
    ordered = tuple(sorted(speaker_seconds, reverse=True))
    return ChunkAnalysis(
        speaker_count=len(ordered), speaker_seconds=ordered, speech_seconds=sum(ordered)
    )


def _poster(analysis: ChunkAnalysis):
    async def _post(chunk, content_type, config):  # noqa: ANN001
        return analysis

    return _post


async def _set(factory, world, **values) -> None:
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    update(AssessmentConversation)
                    .where(AssessmentConversation.id == world.conversation)
                    .values(**values)
                )


async def _current(factory, conversation_id: uuid.UUID) -> uuid.UUID | None:
    async with factory() as s:
        async with superadmin_scope(s):
            return await turns.current_question_id(s, conversation_id)


# -- the engine's answer ------------------------------------------------------


async def test_the_open_turn_names_its_question(candidate) -> None:
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        assert await _current(factory, world.conversation) == world.questions[0]

        await _set(factory, world, next_question_index=1)
        assert await _current(factory, world.conversation) == world.questions[1]

        # A follow-up or a re-ask is about its PARENT, the row its answer is
        # filed under, not about whatever base question comes next.
        await _set(
            factory, world, next_question_index=2,
            pending_prompt="Say more about the settlement you reconciled.",
            pending_kind="probe", pending_question_key=str(world.questions[0]),
        )
        assert await _current(factory, world.conversation) == world.questions[0]
    finally:
        await cw.cleanup(factory, world)


async def test_no_open_turn_names_no_question(candidate) -> None:
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=False
    )
    try:
        assert await _current(factory, world.conversation) is None, "no turn opened yet"

        await _set(
            factory, world, turn_seq=4, prompt_shown_at=datetime.now(timezone.utc),
            turn_allocation_seconds=180, completed_at=datetime.now(timezone.utc),
        )
        assert await _current(factory, world.conversation) is None, "every item asked"
        assert await _current(factory, uuid.uuid4()) is None, "unknown conversation"
    finally:
        await cw.cleanup(factory, world)


# -- the route files the derived event against it ------------------------------


async def _proctoring_session_id(factory, world) -> uuid.UUID:
    rows = await cw.committed(
        factory,
        "SELECT id FROM proctoring_sessions WHERE conversation_id = :c",
        c=str(world.conversation),
    )
    return rows[0].id


async def _events(factory, ps_id: uuid.UUID, kind: str) -> list:
    return await cw.committed(
        factory,
        "SELECT question_id FROM proctoring_events "
        "WHERE proctoring_session_id = :p AND event_type = :k",
        p=str(ps_id), k=kind,
    )


async def test_speech_during_a_typed_question_names_that_question(
    candidate, analysis_configured, monkeypatch
) -> None:
    """Through the real route and a real candidate session: the chunk's
    speech event is stored against the question on screen."""
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    ps_id = await _proctoring_session_id(factory, world)
    # `post` is a keyword default bound at definition time; replace the
    # default itself so the route's own call hears a speaking candidate.
    monkeypatch.setitem(
        proctoring_audio.analyse_chunk.__kwdefaults__, "post",
        _poster(_heard(CONFIG.speech_min_seconds + 2.0)),
    )
    try:
        with cw.client(candidate) as http:
            answered = http.post(
                f"/api/v2/proctoring/sessions/{ps_id}/audio",
                files={"chunk": ("chunk.webm", b"\x1a\x45\xdf\xa3", "audio/webm")},
            )
        assert answered.status_code == 200, answered.text
        assert answered.json()["status"] == proctoring_audio.STATUS_ANALYSED

        rows = await _events(factory, ps_id, "SPEECH_DURING_NON_AUDIO_QUESTION")
        assert [row.question_id for row in rows] == [world.questions[0]]
    finally:
        await proctoring_state.clear_session(ps_id)
        await cw.cleanup(factory, world)


async def test_a_second_voice_names_the_question_it_was_heard_during(
    candidate, analysis_configured
) -> None:
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    ps_id = await _proctoring_session_id(factory, world)
    strong = CONFIG.second_voice_min_seconds + 1.0
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await s.get(ProctoringSession, ps_id)
                    question_id = await turns.current_question_id(s, world.conversation)
                    for _ in range(CONFIG.second_voice_consecutive_chunks):
                        await proctoring_audio.analyse_chunk(
                            s, ps, POLICY_CONTINUE_AND_NOTE, b"x", "audio/webm",
                            now=datetime.now(timezone.utc),
                            post=_poster(_heard(strong + 4.0, strong)),
                            question_id=question_id,
                        )
        rows = await _events(factory, ps_id, "SECOND_VOICE_DETECTED")
        assert [row.question_id for row in rows] == [world.questions[0]]
    finally:
        await proctoring_state.clear_session(ps_id)
        await cw.cleanup(factory, world)
