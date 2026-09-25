"""The Run button's service half, against a real Postgres.

The API route (Phase 4 WP-4C) adds ownership, proctoring, the open-question
check and the Redis rate window in front of this. What is pinned here is what
the service itself guarantees: visible tests only, one run per client token,
one run in flight, a HARD cap counted in the table (so it holds with Redis
down, because this code never asks Redis), an outage recorded as a state, and
a result the candidate reads in words.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.models.coding import CODE_SOURCE_MAX_CHARS, CodingRun
from app.services.code_execution import ExecutionOutcome, ExecutionRejected, ExecutionTicketLost
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.coding_assessment import runs
from tests.test_coding_tables import (  # noqa: F401  (fixtures)
    HIDDEN_STDIN,
    _one,
    _persist,
    factory,
    world,
)

CODE = "print(input())\n"
VISIBLE = {"v1": ("0\n", "NONE\n"), "v2": ("1\n/a 500\n", "/a\n")}


def _script(provider: FakeProvider, code: str = CODE, **overrides: ScriptedRun) -> None:
    for key, (stdin, expected) in VISIBLE.items():
        provider.script(code, stdin, overrides.get(key, ScriptedRun(stdout=expected)))


async def _start(factory, world, question_id, provider, *, token=None, code=CODE, language="python") -> CodingRun:
    async with factory() as session:
        async with superadmin_scope(session):
            conversation = await session.get(AssessmentConversation, world.conversation)
            question = await session.get(CandidateQuestion, question_id)
            run = await runs.start_run(
                session,
                conversation=conversation,
                question=question,
                language=language,
                source=code,
                client_token=token or uuid.uuid4().hex,
                provider=provider,
            )
            await session.commit()
            return run


async def _refresh(factory, run_id, question_id, provider) -> CodingRun:
    async with factory() as session:
        async with superadmin_scope(session):
            run = await session.get(CodingRun, run_id)
            question = await session.get(CandidateQuestion, question_id)
            run = await runs.refresh_run(session, run=run, question=question, provider=provider)
            await session.commit()
            return run


async def test_a_run_is_queued_against_the_visible_tests_only(factory, world) -> None:
    question_id = await _persist(factory, world)
    provider = FakeProvider()
    run = await _start(factory, world, question_id, provider)
    assert run.status == "queued" and run.provider_ref_json["refs"]
    (submitted,) = provider.submissions
    assert submitted.keys == ("v1", "v2")
    assert all(HIDDEN_STDIN not in stdin for stdin in submitted.stdins)


async def test_the_same_client_token_is_the_same_run(factory, world) -> None:
    question_id = await _persist(factory, world)
    provider = FakeProvider()
    first = await _start(factory, world, question_id, provider, token="click-1")
    second = await _start(factory, world, question_id, provider, token="click-1")
    assert first.id == second.id
    assert len(provider.submissions) == 1


async def test_a_second_press_while_one_is_queued_is_refused(factory, world) -> None:
    question_id = await _persist(factory, world)
    provider = FakeProvider()
    await _start(factory, world, question_id, provider)
    with pytest.raises(runs.RunRefused) as refused:
        await _start(factory, world, question_id, provider)
    assert refused.value.code == runs.REFUSAL_IN_PROGRESS
    assert refused.value.sentence == runs.REFUSAL_SENTENCES[runs.REFUSAL_IN_PROGRESS]


async def test_the_cap_is_counted_in_the_table_and_outages_do_not_spend_it(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "coding_run_max_per_question", 2)
    question_id = await _persist(factory, world)
    down = FakeProvider(unavailable="network")
    for _ in range(3):
        run = await _start(factory, world, question_id, down)
        assert run.status == "unavailable" and run.error_class == "ExecutionUnavailable"
    provider = FakeProvider()
    _script(provider)
    for _ in range(2):
        run = await _start(factory, world, question_id, provider)
        assert (await _refresh(factory, run.id, question_id, provider)).status == "complete"
    with pytest.raises(runs.RunRefused) as refused:
        await _start(factory, world, question_id, provider)
    assert refused.value.code == runs.REFUSAL_LIMIT
    row = await _one(factory, "SELECT count(*) AS n FROM coding_runs WHERE question_id = :q", q=question_id)
    assert row["n"] == 5


@pytest.mark.parametrize(
    ("language", "code", "token", "refusal"),
    [
        ("java", CODE, "t", runs.REFUSAL_LANGUAGE),
        ("python", "   \n", "t", runs.REFUSAL_SOURCE),
        ("python", "x" * (CODE_SOURCE_MAX_CHARS + 1), "t", runs.REFUSAL_SOURCE),
        ("python", CODE, "", runs.REFUSAL_TOKEN),
        ("python", CODE, "t" * 65, runs.REFUSAL_TOKEN),
    ],
)
async def test_malformed_runs_are_refused_before_anything_runs(factory, world, language, code, token, refusal) -> None:
    question_id = await _persist(factory, world)
    provider = FakeProvider()
    with pytest.raises(runs.RunRefused) as refused:
        async with factory() as session:
            async with superadmin_scope(session):
                await runs.start_run(
                    session,
                    conversation=await session.get(AssessmentConversation, world.conversation),
                    question=await session.get(CandidateQuestion, question_id),
                    language=language,
                    source=code,
                    client_token=token,
                    provider=provider,
                )
    assert refused.value.code == refusal
    assert provider.submissions == []


async def test_a_question_that_is_not_executed_cannot_be_run(factory, world) -> None:
    question_id = await _persist(factory, world)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("UPDATE candidate_questions SET question_type = 'short_answer', payload_json = '{}'::jsonb WHERE id = :q"),
                {"q": question_id},
            )
            await session.commit()
    with pytest.raises(runs.RunRefused) as refused:
        await _start(factory, world, question_id, FakeProvider())
    assert refused.value.code == runs.REFUSAL_NOT_EXECUTABLE


async def test_a_completed_run_reads_back_in_words(factory, world) -> None:
    question_id = await _persist(factory, world)
    provider = FakeProvider()
    _script(provider, v2=ScriptedRun(stdout="/b\n"))
    run = await _start(factory, world, question_id, provider)
    refreshed = await _refresh(factory, run.id, question_id, provider)
    assert refreshed.status == "complete" and refreshed.completed_at is not None
    async with factory() as session:
        async with superadmin_scope(session):
            question = await session.get(CandidateQuestion, question_id)
            shown = runs.candidate_results(refreshed, question)
    assert [(row["name"], row["result"]) for row in shown] == [
        ("Example One", "Passed"),
        ("Example Two", "Wrong answer"),
    ]
    assert shown[1]["stdout"] == "/b\n" and shown[1]["expected_stdout"] == "/a\n"
    assert set(shown[0]) == {"name", "result", "stdin", "stdout", "expected_stdout", "stderr", "compile_output"}
    assert provider.discarded


async def test_a_lost_run_is_unavailable_and_a_slow_one_stays_queued_until_its_deadline(factory, world, monkeypatch) -> None:
    question_id = await _persist(factory, world)

    class Lost(FakeProvider):
        async def collect(self, ticket):
            raise ExecutionTicketLost("pruned", reason="not_found")

    lost = Lost()
    run = await _start(factory, world, question_id, lost)
    refreshed = await _refresh(factory, run.id, question_id, lost)
    assert (refreshed.status, refreshed.error_class) == ("unavailable", "ExecutionTicketLost")

    slow = FakeProvider(pending_polls=10)
    _script(slow)
    run = await _start(factory, world, question_id, slow)
    assert (await _refresh(factory, run.id, question_id, slow)).status == "queued"
    monkeypatch.setattr(get_settings(), "coding_run_deadline_seconds", 0.0001)
    overdue = await _refresh(factory, run.id, question_id, slow)
    assert (overdue.status, overdue.error_class) == ("unavailable", "deadline")


async def test_a_request_the_sandbox_refuses_is_a_failed_run(factory, world) -> None:
    question_id = await _persist(factory, world)

    class Refusing(FakeProvider):
        async def submit(self, **kwargs):
            raise ExecutionRejected("malformed", reason="rejected")

    run = await _start(factory, world, question_id, Refusing())
    assert (run.status, run.error_class) == ("failed", "ExecutionRejected")


async def test_a_sandbox_fault_on_a_sample_test_is_unavailable_not_a_result(factory, world) -> None:
    question_id = await _persist(factory, world)
    provider = FakeProvider()
    _script(provider, v1=ScriptedRun(outcome=ExecutionOutcome.INTERNAL_ERROR))
    run = await _start(factory, world, question_id, provider)
    refreshed = await _refresh(factory, run.id, question_id, provider)
    assert (refreshed.status, refreshed.results_json) == ("unavailable", None)
