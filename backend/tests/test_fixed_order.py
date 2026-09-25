"""Fixed order, no skipping, no editing: an answer names its turn, and only the current one.

Before 2026-09-24 an answer was whatever arrived next. A client retry after a
lost response was filed as the answer to the NEXT question, a double click
answered two questions with one text, and a past answer could be rewritten
through `PATCH .../answers/{id}`. Now every answer carries `turn_seq`; any
other number is a 409 with NOTHING written, and there is no route that edits a
past answer. Past exchanges come back read-only on every response.
"""
from __future__ import annotations

import pytest

from app.services.assessment_conversation import turns
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

SECOND = "A second, different answer that must never be filed under the first question."


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


def _respond(http, world, body: dict):
    return http.post(f"{cw.BASE}/conversations/{world.conversation}/respond", json=body)


async def test_a_retry_after_a_lost_response_is_refused_not_misfiled(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            first = _respond(http, world, {"turn_seq": 1, "answer": cw.TEXT_ANSWER})
            # The browser never saw `first`'s response and sends again.
            retried = _respond(http, world, {"turn_seq": 1, "answer": SECOND})
        assert first.status_code == 200, first.text
        assert first.json()["turn_seq"] == 2
        assert retried.status_code == 409
        assert retried.json()["detail"] == turns.STALE_TURN_DETAIL

        candidate_rows = [
            row for row in await cw.messages(factory, world) if row.speaker == "candidate"
        ]
        assert [row.content for row in candidate_rows] == [cw.TEXT_ANSWER]
        assert [row.question_key for row in candidate_rows] == [str(world.questions[0])]
        row = await cw.conversation_row(factory, world)
        assert row.next_question_index == 1
        assert row.turn_seq == 2
    finally:
        await cw.cleanup(factory, world)


async def test_no_turn_ahead_can_be_answered(candidate, monkeypatch) -> None:
    """Skipping is naming a turn that is not on screen yet."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            skipped = _respond(http, world, {"turn_seq": 2, "answer": cw.TEXT_ANSWER})
        assert skipped.status_code == 409
        assert await cw.messages(factory, world) == []
        assert (await cw.conversation_row(factory, world)).next_question_index == 0
    finally:
        await cw.cleanup(factory, world)


async def test_past_answers_are_read_only(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            answered = _respond(http, world, {"turn_seq": 1, "answer": cw.TEXT_ANSWER})
            message_id = answered.json()["answer_message_id"]
            edited = http.patch(
                f"{cw.BASE}/conversations/{world.conversation}/answers/{message_id}",
                json={"answer": SECOND},
            )
            reloaded = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert edited.status_code in (404, 405), "a past answer is still editable"
        history = reloaded.json()["history"]
        assert history == [
            {"question": "Question 0: tell me about Payments reconciliation.", "answer": cw.TEXT_ANSWER}
        ]
        filed = [row.content for row in await cw.messages(factory, world) if row.speaker == "candidate"]
        assert filed == [cw.TEXT_ANSWER]
    finally:
        await cw.cleanup(factory, world)


async def test_a_structured_question_is_answered_by_its_form(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True,
        plan=(("mcq_single", cw.MCQ_SINGLE_PAYLOAD, "must_have"),)
        + tuple(cw.PROSE_ONLY[1:]),
    )
    try:
        with cw.client(candidate) as http:
            prose = _respond(http, world, {"turn_seq": 1, "answer": "It is a write-ahead log."})
            answered = _respond(
                http, world, {"turn_seq": 1, "answer_payload": {"selected_option_id": "a"}}
            )
        assert prose.status_code == 422
        assert answered.status_code == 200, answered.text
        assert answered.json()["turn"]["allocation_seconds"] == (
            cw.get_settings().assessment_time_prose_seconds
        )
        records = await cw.committed(
            factory,
            "SELECT answer_json, auto_score FROM assessment_answers WHERE conversation_id = :c",
            c=str(world.conversation),
        )
        assert records[0].answer_json["selected_option_id"] == "a"
    finally:
        await cw.cleanup(factory, world)
