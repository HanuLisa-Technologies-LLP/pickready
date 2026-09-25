"""The question the candidate reads, the stored prompt and the stored rubric are one question.

Audit P1 3.10. Until 2026-09-24 the conversation asked the question writer for
a fresh question, which PERSISTED the new prompt and its rubric onto the row,
and only THEN checked whether it repeated one already asked; a repeat was
hidden and the old text shown. The candidate read one question and was graded
against the rubric of another, with nothing recording it.

Now the repeat is a criterion of the writer's own loop and a result that still
repeats is DEGRADED and writes nothing. This file drives the real writer
through the real routes, with only the model call doubled, and asserts after
every turn that what the transcript says the candidate read is the row's
prompt, and the row's rubric is the one written with it.
"""
from __future__ import annotations

import json

import pytest

from app.services import ppi_interview
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

FIRST = "Which payments reconciliation break did you find in the Northwind capture files?"
THIRD = "Describe a time you showed ownership under pressure on a live release, and what you decided."
RUBRIC_FIRST = {
    "0_39": "No reconciliation named.",
    "40_59": "Names a file only.",
    "60_74": "One break found.",
    "75_89": "Break found and traced.",
    "90_100": "Break found, traced and prevented.",
}


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


async def _rows(factory, world):
    return await cw.committed(
        factory,
        "SELECT id, prompt, rubric_json, generated_at FROM candidate_questions "
        "WHERE job_candidate_link_id = :l ORDER BY ordinal",
        l=str(world.link),
    )


async def test_a_rejected_rewrite_leaves_the_row_and_the_screen_on_the_same_question(
    candidate, monkeypatch
) -> None:
    real_writer = ppi_interview.write_question
    cw.quiet_models(monkeypatch)
    monkeypatch.setattr(ppi_interview, "write_question", real_writer)

    # Question 0 is written fresh; question 1 comes back as question 0 on every
    # attempt (a result the loop must refuse); question 2 is written fresh.
    # The skill is read from the system prompt, which names it.
    async def _invoke(task_type, messages, **kwargs):
        system = messages[0]["content"]
        text = THIRD if cw.SKILLS["behavioural"][0] in system else FIRST
        rubric = RUBRIC_FIRST if text == FIRST else None
        return json.dumps({"question": text, "rubric": rubric})

    monkeypatch.setattr(ppi_interview.llm_router, "invoke_llm", _invoke)

    factory = cw.sessions()
    world = await cw.seed(factory, candidate_id=candidate.candidate_id)
    before = {row.id: row for row in await _rows(factory, world)}
    try:
        with cw.client(candidate) as http:
            started = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            assert started.status_code == 200, started.text
            assert started.json()["prompt"] == FIRST
            second = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 1, "answer": cw.TEXT_ANSWER},
            )
            assert second.status_code == 200, second.text
            third = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 2, "answer": cw.TEXT_ANSWER},
            )
            assert third.status_code == 200, third.text

        rows = await _rows(factory, world)
        q0, q1, q2 = rows
        # Written and accepted: the prompt AND its rubric, together.
        assert q0.prompt == FIRST and q0.rubric_json == RUBRIC_FIRST
        assert q0.generated_at is not None
        # Refused as a repeat: nothing written, and the screen showed the row.
        assert q1.prompt == before[q1.id].prompt
        assert q1.rubric_json == before[q1.id].rubric_json
        assert q1.generated_at is None
        assert second.json()["prompt"] == q1.prompt, (
            "the candidate was shown a question the row does not hold"
        )
        # Written and accepted again, for a judgement-scored skill (no rubric).
        assert q2.prompt == THIRD
        assert third.json()["prompt"] == THIRD

        agent_lines = [
            (row.question_key, row.content)
            for row in await cw.messages(factory, world)
            if row.speaker == "agent"
        ]
        assert agent_lines == [(str(q0.id), q0.prompt), (str(q1.id), q1.prompt)], (
            "the transcript records a question other than the one the row "
            "(and its rubric) holds"
        )
        conversation = await cw.conversation_row(factory, world)
        assert conversation.delivered_prompt in (None, q2.prompt)
    finally:
        await cw.cleanup(factory, world)
