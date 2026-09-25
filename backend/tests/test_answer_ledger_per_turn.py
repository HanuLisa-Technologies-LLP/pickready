"""Every substantive answer is in Miti's ledger as it arrives, and the next question reads it.

Audit P2 7.3. The ledger used to be written only at grading, after the
conversation had ended, so the contradiction probing the question writer
offers (`conflicting`) read a ledger that was always empty while the candidate
was still answering. Now `assessment_pipeline.evidence.record_answer_evidence`,
the one ledger writer, files each substantive answer from inside the turn,
and the next question is written knowing which skills the ledger contradicts.

State is read from a second connection BEFORE the conversation completes, which
is the whole point: after completion the old grading-time write would have
passed this test too.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import agent_loop, ppi_interview
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session


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


async def _evidence(factory, world) -> list:
    return await cw.committed(
        factory,
        "SELECT source_type, source_id FROM evidence_items WHERE link_id = :l",
        l=str(world.link),
    )


async def test_a_substantive_answer_is_filed_before_the_conversation_ends(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            answered = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 1, "answer": cw.TEXT_ANSWER},
            )
        assert answered.status_code == 200, answered.text
        assert answered.json()["status"] == "active", "the fixture completed the conversation"
        filed = await _evidence(factory, world)
        assert [(row.source_type, str(row.source_id)) for row in filed] == [
            ("answer", answered.json()["answer_message_id"])
        ], "the answer reached the ledger only at grading, or not at all"
        claims = await cw.committed(
            factory,
            "SELECT dimension FROM evidence_claims WHERE link_id = :l",
            l=str(world.link),
        )
        assert [row.dimension for row in claims] == [cw.SKILLS["must_have"][0]]
    finally:
        await cw.cleanup(factory, world)


async def test_a_non_answer_files_nothing(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            answered = http.post(
                f"{cw.BASE}/conversations/{world.conversation}/respond",
                json={"turn_seq": 1, "answer": "asdfghjkl"},
            )
        assert answered.status_code == 200, answered.text
        assert await _evidence(factory, world) == []
    finally:
        await cw.cleanup(factory, world)


async def test_the_next_question_is_told_what_the_ledger_contradicts(
    candidate, monkeypatch
) -> None:
    """The Miti side of the loop, wired: a contradicted claim on the skill the
    next question probes turns that question into a contradiction probe."""
    from app.services.evidence import ledger

    cw.quiet_models(monkeypatch)
    seen: list[tuple[str, bool]] = []

    async def _written(*, row, competency, conflicting=False, **kwargs):
        seen.append((competency.name, conflicting))
        return agent_loop.LoopResult(
            value={"question": row.prompt, "rubric": dict(row.rubric_json or {})},
            degraded=False, attempts=1,
        )

    monkeypatch.setattr(ppi_interview, "write_question", _written)
    contradicted = cw.SKILLS["nice_to_have"][0]

    async def _claims(session, **kwargs):
        return [
            SimpleNamespace(dimension=contradicted, status=ledger.CLAIM_CONTRADICTED),
            SimpleNamespace(dimension=cw.SKILLS["behavioural"][0], status="supported"),
        ]

    monkeypatch.setattr(ledger, "load_claims", _claims)
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, started=True, open_turn=True
    )
    try:
        with cw.client(candidate) as http:
            for turn in (1, 2):
                answered = http.post(
                    f"{cw.BASE}/conversations/{world.conversation}/respond",
                    json={"turn_seq": turn, "answer": cw.TEXT_ANSWER},
                )
                assert answered.status_code == 200, answered.text
        assert seen == [
            (contradicted, True),
            (cw.SKILLS["behavioural"][0], False),
        ]
    finally:
        await cw.cleanup(factory, world)
