"""The candidate's coding routes over HTTP, against a real Postgres (Phase 4 WP-4C).

`test_coding_run_service.py` pins what `runs.start_run`/`refresh_run`
guarantee on their own. This module pins what the ROUTES add in front of
them and what crosses the boundary: ownership (404, never 403), the
proctoring gate, the open-question check, the refusal statuses and their
server sentences, the Redis window, the DATABASE cap that holds when that
window fails open, an outage answered 503 with its row COMMITTED, the
response shape the editor reads, and the final answer's state words.

Every state that matters is read back from a SECOND connection after the
response, because a write that answered and then rolled back at commit is
invisible to the request that made it (the 2026-09-20 audit_log class).
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.api import assessment_coding
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.services import code_execution
from app.services import rate_limit as rate_limit_module
from app.services.code_execution import ExecutionOutcome
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.coding_assessment import final_answer, runs, submissions
from app.workers import dispatch
from tests.coding_api_fixtures import (  # noqa: F401  (fixtures)
    CONV,
    _fresh_rate_windows,
    candidate,
    candidate_client,
    sign_in,
    write_final_answer,
)
from tests.test_coding_tables import _one, _persist, factory, world  # noqa: F401  (fixtures)

CODE = "print(input())\n"
VISIBLE = {"v1": ("0\n", "NONE\n"), "v2": ("1\n/a 500\n", "/a\n")}
DIGIT = re.compile(r"\d")


def _script(provider: FakeProvider, code: str = CODE, **overrides: ScriptedRun) -> None:
    for key, (stdin, expected) in VISIBLE.items():
        provider.script(code, stdin, overrides.get(key, ScriptedRun(stdout=expected)))


def _runs_path(candidate, question_id) -> str:
    return f"{CONV}/{candidate.world.conversation}/coding/{question_id}/runs"


def _body(token: str | None = None, *, code: str = CODE, language: str = "python") -> dict:
    return {"language": language, "source": code, "client_token": token or uuid.uuid4().hex}


async def _exec(factory, sql: str, **params) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(text(sql), params)
            await session.commit()


# ── The happy path and its shape ────────────────────────────────────────────


async def test_a_run_is_accepted_polled_and_read_back_in_words(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider()
    _script(provider, v2=ScriptedRun(stdout="/b\n"))
    holder = {"principal": candidate.principal}
    with code_execution.override_provider(provider):
        async with candidate_client(factory, holder) as client:
            started = await client.post(_runs_path(candidate, question_id), json=_body("click-1"))
            assert started.status_code == 202, started.text
            assert started.json()["status"] == "queued"
            run_id = started.json()["run_id"]
            polled = await client.get(f"{_runs_path(candidate, question_id)}/{run_id}")
    assert polled.status_code == 200, polled.text
    body = polled.json()
    assert body["status"] == "complete" and body["message"] is None
    assert [(t["key"], t["passed"], t["result_word"]) for t in body["tests"]] == [
        ("v1", True, "Passed"),
        ("v2", False, "Wrong answer"),
    ]
    assert body["tests"][1]["stdout"] == "/b\n" and body["tests"][1]["expected_stdout"] == "/a\n"
    assert set(body["tests"][0]) == {
        "key", "passed", "result_word", "stdout", "expected_stdout", "stderr", "compile_output",
    }
    row = await _one(factory, "SELECT status, source, language FROM coding_runs WHERE id = :i", i=run_id)
    assert (row["status"], row["source"], row["language"]) == ("complete", CODE, "python")


async def test_a_run_response_carries_no_number(factory, candidate) -> None:
    """The result is words and the program's own output; nothing numeric the
    server computed (a timing, a count, a score) is in the body."""
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider()
    _script(provider, v1=ScriptedRun(outcome=ExecutionOutcome.TIME_LIMIT))
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            run_id = (await client.post(_runs_path(candidate, question_id), json=_body())).json()["run_id"]
            body = (await client.get(f"{_runs_path(candidate, question_id)}/{run_id}")).json()
    for test in body["tests"]:
        assert not DIGIT.search(test["result_word"])
        assert all(isinstance(value, (str, bool)) for value in test.values())
    assert {k for k, v in body.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} == set()


async def test_the_same_client_token_is_one_run(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider()
    _script(provider)
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            first = await client.post(_runs_path(candidate, question_id), json=_body("same"))
            second = await client.post(_runs_path(candidate, question_id), json=_body("same"))
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert len(provider.submissions) == 1


# ── Ownership, proctoring, the open question ────────────────────────────────


async def test_another_candidate_gets_404_on_every_route(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider()
    _script(provider)
    stranger_candidate = uuid.uuid4()
    await _exec(
        factory,
        "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
        "VALUES (:c, :t, 'Someone Else', :e, false)",
        c=stranger_candidate, t=candidate.world.tenant, e=f"{stranger_candidate}@coding.test",
    )
    stranger_user = await sign_in(factory, stranger_candidate)
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models.enums import Role

    holder = {"principal": candidate.principal}
    with code_execution.override_provider(provider):
        async with candidate_client(factory, holder) as client:
            run_id = (await client.post(_runs_path(candidate, question_id), json=_body())).json()["run_id"]
            holder["principal"] = CurrentUser(
                user_id=stranger_user, tenant_id=None, role=Role.candidate, audience=AUDIENCE_CANDIDATE
            )
            answers = [
                await client.post(_runs_path(candidate, question_id), json=_body()),
                await client.get(f"{_runs_path(candidate, question_id)}/{run_id}"),
                await client.get(f"{CONV}/{candidate.world.conversation}/coding/{question_id}/submission"),
                await client.post(f"{CONV}/{uuid.uuid4()}/coding/{question_id}/runs", json=_body()),
            ]
    assert [a.status_code for a in answers] == [404, 404, 404, 404]
    assert len(provider.submissions) == 1
    await _exec(factory, "DELETE FROM users WHERE id = :u", u=stranger_user)


async def test_a_question_that_is_not_on_this_application_is_404(factory, candidate) -> None:
    await _persist(factory, candidate.world)
    async with candidate_client(factory, {"principal": candidate.principal}) as client:
        refused = await client.post(_runs_path(candidate, uuid.uuid4()), json=_body())
    assert refused.status_code == 404
    assert refused.json()["detail"] == assessment_coding.NOT_FOUND_DETAIL


async def test_no_active_proctoring_session_refuses_a_run(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    await _exec(factory, "UPDATE proctoring_sessions SET outcome = 'terminated_integrity', ended_at = now() "
                "WHERE id = :p", p=candidate.proctoring_id)
    provider = FakeProvider()
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            refused = await client.post(_runs_path(candidate, question_id), json=_body())
    assert refused.status_code == 409
    assert provider.submissions == []


@pytest.mark.parametrize(
    "setup",
    [
        "UPDATE assessment_conversations SET next_question_index = 1 WHERE id = :c",
        "UPDATE assessment_conversations SET status = 'completed' WHERE id = :c",
        "UPDATE assessment_conversations SET pending_prompt = 'Tell me more.', pending_kind = 'probe' WHERE id = :c",
    ],
    ids=["already-answered", "conversation-complete", "follow-up-pending"],
)
async def test_only_the_open_question_can_run(factory, candidate, setup) -> None:
    question_id = await _persist(factory, candidate.world)
    await _persist(factory, candidate.world, ordinal=1, title="The next problem")
    await _exec(factory, setup, c=candidate.world.conversation)
    provider = FakeProvider()
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            refused = await client.post(_runs_path(candidate, question_id), json=_body())
    assert refused.status_code == 409
    assert refused.json()["detail"] == assessment_coding.QUESTION_NOT_OPEN_DETAIL
    assert provider.submissions == []


async def test_a_question_not_yet_reached_cannot_run(factory, candidate) -> None:
    await _persist(factory, candidate.world)
    later = await _persist(factory, candidate.world, ordinal=1, title="The next problem")
    async with candidate_client(factory, {"principal": candidate.principal}) as client:
        refused = await client.post(_runs_path(candidate, later), json=_body())
    assert refused.status_code == 409


# ── Refusals: status and the server's own sentence ──────────────────────────


async def test_refusals_answer_with_the_services_sentence(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider(pending_polls=5)
    _script(provider)
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            assert (await client.post(_runs_path(candidate, question_id), json=_body())).status_code == 202
            busy = await client.post(_runs_path(candidate, question_id), json=_body())
            language = await client.post(_runs_path(candidate, question_id), json=_body(language="java"))
            empty = await client.post(_runs_path(candidate, question_id), json=_body(code="   \n"))
    assert (busy.status_code, busy.json()["detail"]) == (409, runs.REFUSAL_SENTENCES[runs.REFUSAL_IN_PROGRESS])
    assert (language.status_code, language.json()["detail"]) == (422, runs.REFUSAL_SENTENCES[runs.REFUSAL_LANGUAGE])
    assert (empty.status_code, empty.json()["detail"]) == (422, runs.REFUSAL_SENTENCES[runs.REFUSAL_SOURCE])


def test_every_refusal_has_a_status() -> None:
    assert set(assessment_coding.REFUSAL_STATUS) == set(runs.REFUSAL_SENTENCES)


async def test_the_database_cap_holds_when_the_rate_window_fails_open(factory, candidate, monkeypatch) -> None:
    """Redis down makes the rate window allow everything (by design); the
    per-question cap counted in `coding_runs` is the fence that still holds."""
    monkeypatch.setattr(rate_limit_module.cache, "_redis", lambda: None)
    monkeypatch.setattr(get_settings(), "coding_run_max_per_question", 2)
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider()
    _script(provider)
    statuses = []
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            for _ in range(3):
                started = await client.post(_runs_path(candidate, question_id), json=_body())
                statuses.append(started.status_code)
                if started.status_code == 202:
                    await client.get(f"{_runs_path(candidate, question_id)}/{started.json()['run_id']}")
                else:
                    limit = started
    assert statuses == [202, 202, 429]
    assert limit.json()["detail"] == runs.REFUSAL_SENTENCES[runs.REFUSAL_LIMIT]
    row = await _one(factory, "SELECT count(*) AS n FROM coding_runs WHERE question_id = :q", q=question_id)
    assert row["n"] == 2 and len(provider.submissions) == 2


async def test_the_rate_window_limits_presses_before_anything_is_read(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    provider = FakeProvider()
    _script(provider)
    limit = get_settings().coding_run_rate_per_minute
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            codes = [
                (await client.post(_runs_path(candidate, question_id), json=_body("replayed"))).status_code
                for _ in range(limit + 1)
            ]
    assert codes[:limit] == [202] * limit
    assert codes[limit] == 429
    assert len(provider.submissions) == 1


# ── An outage is a state, and it is committed ───────────────────────────────


async def test_a_disabled_sandbox_is_503_with_the_sentence_and_the_row_is_kept(factory, candidate, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "code_execution_backend", "disabled")
    question_id = await _persist(factory, candidate.world)
    async with candidate_client(factory, {"principal": candidate.principal}) as client:
        down = await client.post(_runs_path(candidate, question_id), json=_body("outage"))
        again = await client.post(_runs_path(candidate, question_id), json=_body("outage"))
    assert (down.status_code, down.json()["detail"]) == (503, runs.UNAVAILABLE_SENTENCE)
    assert (again.status_code, again.json()["detail"]) == (503, runs.UNAVAILABLE_SENTENCE)
    row = await _one(
        factory, "SELECT count(*) AS n, min(status) AS s, min(error_class) AS e FROM coding_runs WHERE question_id = :q",
        q=question_id,
    )
    assert (row["n"], row["s"], row["e"]) == (1, "unavailable", "ExecutionNotConfigured")


async def test_a_run_the_sandbox_lost_reads_unavailable_with_the_sentence(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)

    class Lost(FakeProvider):
        async def collect(self, ticket):
            from app.services.code_execution import ExecutionTicketLost

            raise ExecutionTicketLost("pruned", reason="not_found")

    with code_execution.override_provider(Lost()):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            run_id = (await client.post(_runs_path(candidate, question_id), json=_body())).json()["run_id"]
            polled = await client.get(f"{_runs_path(candidate, question_id)}/{run_id}")
    assert polled.json() == {
        "run_id": run_id, "status": "unavailable", "tests": [], "message": runs.UNAVAILABLE_SENTENCE,
    }
    row = await _one(factory, "SELECT status FROM coding_runs WHERE id = :i", i=run_id)
    assert row["status"] == "unavailable"


async def test_a_run_of_another_question_is_404(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    other = await _persist(factory, candidate.world, ordinal=1, title="The next problem")
    provider = FakeProvider()
    _script(provider)
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            run_id = (await client.post(_runs_path(candidate, question_id), json=_body())).json()["run_id"]
            crossed = await client.get(f"{_runs_path(candidate, other)}/{run_id}")
    assert crossed.status_code == 404


# ── The final answer: the hook and the state words ──────────────────────────


async def test_the_hook_accepts_a_final_v2_answer_once_and_dispatches_after_commit(factory, candidate) -> None:
    world = candidate.world
    question_id = await _persist(factory, world)
    await write_final_answer(factory, world, question_id, CODE)
    dispatch.clear_recorded()
    for _ in range(2):
        async with factory() as session:
            async with superadmin_scope(session):
                conversation = await session.get(AssessmentConversation, world.conversation)
                question = await session.get(CandidateQuestion, question_id)
                row = await final_answer.accept_structured_answer(
                    session, conversation=conversation, question=question
                )
                assert row is not None
                await session.commit()
    stored = await _one(
        factory, "SELECT count(*) AS n, min(execution_status) AS s FROM coding_submissions WHERE question_id = :q",
        q=question_id,
    )
    assert (stored["n"], stored["s"]) == (1, "pending")
    assert [e.name for e in dispatch.recorded()] == [submissions.TASK_NAME]

    async with candidate_client(factory, {"principal": candidate.principal}) as client:
        state = await client.get(f"{CONV}/{world.conversation}/coding/{question_id}/submission")
    assert state.status_code == 200
    assert state.json() == {"state_word": submissions.STATE_WORD_SUBMITTED}


async def test_the_hook_rolled_back_stores_and_dispatches_nothing(factory, candidate) -> None:
    world = candidate.world
    question_id = await _persist(factory, world)
    await write_final_answer(factory, world, question_id, CODE)
    dispatch.clear_recorded()
    async with factory() as session:
        async with superadmin_scope(session):
            await final_answer.accept_structured_answer(
                session,
                conversation=await session.get(AssessmentConversation, world.conversation),
                question=await session.get(CandidateQuestion, question_id),
            )
            await session.rollback()
    stored = await _one(factory, "SELECT count(*) AS n FROM coding_submissions WHERE question_id = :q", q=question_id)
    assert stored["n"] == 0
    assert dispatch.recorded() == []


async def test_the_hook_is_a_no_op_for_anything_that_is_not_executed(factory, candidate) -> None:
    world = candidate.world
    question_id = await _persist(factory, world)
    await _exec(
        factory,
        "UPDATE candidate_questions SET question_type = 'mcq_single', payload_json = '{}'::jsonb WHERE id = :q",
        q=question_id,
    )
    async with factory() as session:
        async with superadmin_scope(session):
            result = await final_answer.accept_structured_answer(
                session,
                conversation=await session.get(AssessmentConversation, world.conversation),
                question=await session.get(CandidateQuestion, question_id),
            )
    assert result is None


async def test_the_hook_refuses_a_v2_answer_whose_row_was_never_written(factory, candidate) -> None:
    world = candidate.world
    question_id = await _persist(factory, world)
    async with factory() as session:
        async with superadmin_scope(session):
            with pytest.raises(ValueError):
                await final_answer.accept_structured_answer(
                    session,
                    conversation=await session.get(AssessmentConversation, world.conversation),
                    question=await session.get(CandidateQuestion, question_id),
                )


async def test_the_submission_state_is_404_before_a_final_answer(factory, candidate) -> None:
    question_id = await _persist(factory, candidate.world)
    async with candidate_client(factory, {"principal": candidate.principal}) as client:
        missing = await client.get(f"{CONV}/{candidate.world.conversation}/coding/{question_id}/submission")
    assert (missing.status_code, missing.json()["detail"]) == (404, assessment_coding.NO_FINAL_ANSWER_DETAIL)


@pytest.mark.parametrize(
    ("execution", "review", "word"),
    [
        ("no_code", "not_applicable", submissions.STATE_WORD_SUBMITTED),
        ("pending", "pending", submissions.STATE_WORD_SUBMITTED),
        ("submitted", "pending", submissions.STATE_WORD_CHECKING),
        ("complete", "failed", submissions.STATE_WORD_CHECKING),
        ("complete", "complete", submissions.STATE_WORD_CHECKED),
    ],
)
async def test_the_state_word_follows_the_row(factory, candidate, execution, review, word) -> None:
    world = candidate.world
    question_id = await _persist(factory, world)
    await write_final_answer(factory, world, question_id, CODE)
    async with factory() as session:
        async with superadmin_scope(session):
            await final_answer.accept_structured_answer(
                session,
                conversation=await session.get(AssessmentConversation, world.conversation),
                question=await session.get(CandidateQuestion, question_id),
            )
            await session.commit()
    await _exec(
        factory,
        "UPDATE coding_submissions SET execution_status = :e, review_status = :r, "
        "provider_ref_json = '{}'::jsonb, tests_total = 5, tests_passed = 2, "
        "test_results_json = '[]'::jsonb, executed_at = now(), review_json = '{}'::jsonb, "
        "reviewed_at = now() WHERE question_id = :q",
        e=execution, r=review, q=question_id,
    )
    async with candidate_client(factory, {"principal": candidate.principal}) as client:
        state = await client.get(f"{CONV}/{world.conversation}/coding/{question_id}/submission")
    assert state.json() == {"state_word": word}
