"""A final coding answer, end to end against a real Postgres.

Accepted in the candidate's own RLS-scoped transaction, dispatched after the
commit, executed by the task body in a worker session, judged against the
answer key, reviewed, filed in the evidence ledger, handed to scoring. The
sandbox is the port's scripted double (installed by argument, never
executing anything); the model is replaced at `llm_router.invoke_llm`.

Every state that matters is read back from a SECOND connection after the
work, because a write that answered and then rolled back is invisible to the
connection that made it (the 2026-09-20 audit_log class).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope, tenant_scope
from app.models.assessment import AssessmentAnswer, AssessmentConversation, CandidateQuestion
from app.services import llm_router
from app.services.code_execution import ExecutionOutcome, ExecutionTicketLost, ExecutionUnavailable
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.coding_assessment import submissions
from app.workers import dispatch
from app.workers.runtime import worker_session
from tests.test_coding_tables import (  # noqa: F401  (fixtures)
    HIDDEN_OUT,
    HIDDEN_STDIN,
    _World,
    _draft,
    _one,
    _persist,
    factory,
    world,
)

CODE = "import sys\nlines = sys.stdin.read().splitlines()\nprint(lines[0] if lines else 'NONE')\n"
CITATION = "lines = sys.stdin.read().splitlines()"
VISIBLE = {"v1": ("0\n", "NONE\n"), "v2": ("1\n/a 500\n", "/a\n")}
HIDDEN = {f"h{i}": (f"{HIDDEN_STDIN}{i}\n", f"{HIDDEN_OUT}{i}\n") for i in range(1, 6)}


def _review_answer() -> dict[str, Any]:
    return {
        "score": 60,
        "criteria": {"code_quality": 0.6, "edge_case_handling": 0.4, "efficiency_awareness": 0.7, "idiomatic_use": 0.6},
        "reasoning": (
            "The program reads standard input once and prints the first line, which is short "
            "and readable but does not implement the counting the problem asks for. It never "
            "groups the lines by path, so the approach cannot scale to the stated constraints "
            "in any meaningful sense, and no edge case beyond empty input is handled here."
        ),
        "citations": [CITATION],
    }


class Router:
    """Answers every review call with `answer`; records what it was sent."""

    def __init__(self, answer: dict[str, Any] | str | None = None) -> None:
        self.answer = _review_answer() if answer is None else answer
        self.calls: list[list[dict[str, str]]] = []

    async def __call__(self, task_type: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
        assert task_type == "coding_quality_review"
        self.calls.append(messages)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


class _Clock:
    """A clock that moves only when the code under test sleeps, so a poll
    deadline is reached without waiting for it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _script(provider: FakeProvider, code: str = CODE, hidden: dict[str, ScriptedRun] | None = None,
            visible: dict[str, ScriptedRun] | None = None) -> None:
    for key, (stdin, expected) in VISIBLE.items():
        provider.script(code, stdin, (visible or {}).get(key, ScriptedRun(stdout=expected)))
    for key, (stdin, expected) in HIDDEN.items():
        provider.script(code, stdin, (hidden or {}).get(key, ScriptedRun(stdout=expected)))


async def _answer(factory, w: _World, question_id: uuid.UUID, code: str = CODE, *, ordinal: int = 1) -> tuple[uuid.UUID, uuid.UUID]:
    message_id, answer_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text(
                    "INSERT INTO assessment_messages (id, tenant_id, conversation_id, ordinal, speaker, "
                    "domain, question_key, content) VALUES (:m, :t, :c, :o, 'candidate', 'technical', :k, :body)"
                ),
                {"m": message_id, "t": w.tenant, "c": w.conversation, "o": ordinal,
                 "k": str(question_id), "body": f"Language: python\n{code}"},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_answers (id, tenant_id, conversation_id, question_id, message_id, "
                    "question_type, answer_json, submitted_at) VALUES (:a, :t, :c, :q, :m, 'coding', "
                    "CAST(:body AS jsonb), now())"
                ),
                {"a": answer_id, "t": w.tenant, "c": w.conversation, "q": question_id, "m": message_id,
                 "body": json.dumps({"language": "python", "code": code})},
            )
            await session.commit()
    return answer_id, message_id


async def _accept(factory, w: _World, question_id, answer_id, *, commit: bool = True, auto: bool = False) -> uuid.UUID:
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            conversation = await session.get(AssessmentConversation, w.conversation)
            question = await session.get(CandidateQuestion, question_id)
            answer = await session.get(AssessmentAnswer, answer_id)
            row = await submissions.accept_final(
                session, conversation=conversation, question=question, answer=answer, auto_submitted=auto
            )
            submission_id = row.id
            if commit:
                await session.commit()
            else:
                await session.rollback()
    return submission_id


async def _submitted(factory, w: _World, code: str = CODE, *, ordinal: int = 0) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    question_id = await _persist(factory, w, ordinal=ordinal, title=f"Problem {uuid.uuid4().hex[:6]}")
    answer_id, message_id = await _answer(factory, w, question_id, code, ordinal=10 + ordinal)
    submission_id = await _accept(factory, w, question_id, answer_id)
    return submission_id, question_id, message_id


async def _execute(submission_id, provider) -> submissions.ExecutionReport:
    clock = _Clock()
    async with worker_session() as session:
        return await submissions.execute_submission(
            session, submission_id, provider=provider, sleep=clock.sleep, clock=clock
        )


async def _row(factory, submission_id) -> dict[str, Any]:
    return dict(await _one(factory, "SELECT * FROM coding_submissions WHERE id = :i", i=submission_id))


def _dispatched(name: str) -> list[dispatch.RecordedDispatch]:
    return [entry for entry in dispatch.recorded() if entry.name == name]


# ── Accepting ───────────────────────────────────────────────────────────────


async def test_accept_is_idempotent_and_dispatches_once_after_commit(factory, world) -> None:
    question_id = await _persist(factory, world)
    answer_id, _ = await _answer(factory, world, question_id)
    first = await _accept(factory, world, question_id, answer_id)
    second = await _accept(factory, world, question_id, answer_id)
    assert first == second
    row = await _one(factory, "SELECT count(*) AS n, min(execution_status) AS s FROM coding_submissions WHERE answer_id = :a", a=answer_id)
    assert (row["n"], row["s"]) == (1, "pending")
    sent = _dispatched(submissions.TASK_NAME)
    assert [entry.args for entry in sent] == [(str(first),)]


async def test_a_rolled_back_submit_stores_and_dispatches_nothing(factory, world) -> None:
    question_id = await _persist(factory, world)
    answer_id, _ = await _answer(factory, world, question_id)
    await _accept(factory, world, question_id, answer_id, commit=False)
    row = await _one(factory, "SELECT count(*) AS n FROM coding_submissions WHERE answer_id = :a", a=answer_id)
    assert row["n"] == 0
    assert _dispatched(submissions.TASK_NAME) == []


async def test_an_empty_answer_is_no_code_and_is_never_dispatched(factory, world) -> None:
    question_id = await _persist(factory, world)
    answer_id, _ = await _answer(factory, world, question_id, code="  \n")
    submission_id = await _accept(factory, world, question_id, answer_id, auto=True)
    row = await _row(factory, submission_id)
    assert (row["execution_status"], row["review_status"], row["auto_submitted"]) == ("no_code", "not_applicable", True)
    assert _dispatched(submissions.TASK_NAME) == []


async def test_accept_refuses_a_language_the_question_does_not_offer(factory, world) -> None:
    question_id = await _persist(factory, world)
    answer_id, _ = await _answer(factory, world, question_id)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("UPDATE assessment_answers SET answer_json = jsonb_set(answer_json, '{language}', '\"java\"') WHERE id = :a"),
                {"a": answer_id},
            )
            await session.commit()
    with pytest.raises(ValueError):
        await _accept(factory, world, question_id, answer_id)


# ── Executing ───────────────────────────────────────────────────────────────


async def test_results_are_judged_and_hidden_output_is_never_stored_or_logged(factory, world, monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    router = Router()
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider()
    _script(
        provider,
        hidden={
            # A program that ECHOES its stdin: the hidden input comes back as output.
            "h3": ScriptedRun(stdout=HIDDEN["h3"][0]),
            "h4": ScriptedRun(outcome=ExecutionOutcome.TIME_LIMIT, stdout=HIDDEN["h4"][0]),
            "h5": ScriptedRun(outcome=ExecutionOutcome.RUNTIME_ERROR, stderr=f"ValueError: {HIDDEN['h5'][0]}"),
        },
        visible={"v2": ScriptedRun(outcome=ExecutionOutcome.RUNTIME_ERROR, stderr="Traceback: visible sample")},
    )
    report = await _execute(submission_id, provider)
    assert (report.execution_status, report.review_status) == ("complete", "complete")

    row = await _row(factory, submission_id)
    assert (row["tests_total"], row["tests_passed"]) == (5, 2)
    assert [r["outcome"] for r in row["test_results_json"]] == [
        "passed", "passed", "wrong_answer", "time_limit", "runtime_error",
    ]
    assert row["visible_error_output"] == "Traceback: visible sample"
    assert row["review_json"]["citations"] == [CITATION]
    assert row["review_model_id"] and row["review_prompt_version"]
    stored = json.dumps(row, default=str)
    for sentinel in (HIDDEN_STDIN, HIDDEN_OUT):
        assert sentinel not in stored
        assert sentinel not in json.dumps(router.calls)
        assert all(sentinel not in record.getMessage() for record in caplog.records)
    assert provider.discarded, "the sandbox's copy of the run was not deleted"


async def test_the_ticket_is_committed_before_polling_and_a_retry_collects_it(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    submission_id, _, _ = await _submitted(factory, world)

    class KilledWhilePolling(FakeProvider):
        async def collect(self, ticket):
            if not getattr(self, "killed", False):
                self.killed = True
                raise RuntimeError("the worker was killed mid-poll")
            return await super().collect(ticket)

    provider = KilledWhilePolling()
    _script(provider)
    with pytest.raises(RuntimeError):
        await _execute(submission_id, provider)
    row = await _row(factory, submission_id)
    assert row["execution_status"] == "submitted"
    assert row["provider_ref_json"]["refs"], "the ticket must be durable before anybody polls"

    report = await _execute(submission_id, provider)
    assert report.execution_status == "complete"
    assert len(provider.submissions) == 1, "a retry must collect, never execute the code again"


async def test_a_lost_ticket_is_submitted_again(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    submission_id, _, _ = await _submitted(factory, world)

    class HostReplaced(FakeProvider):
        async def collect(self, ticket):
            if not getattr(self, "replaced", False):
                self.replaced = True
                raise ExecutionTicketLost("pruned", reason="not_found")
            return await super().collect(ticket)

    provider = HostReplaced()
    _script(provider)
    report = await _execute(submission_id, provider)
    assert report.execution_status == "complete"
    assert len(provider.submissions) == 2
    row = await _row(factory, submission_id)
    assert row["execution_attempts"] == 1


async def test_an_unavailable_sandbox_is_recorded_and_raised(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider(unavailable="network")
    with pytest.raises(ExecutionUnavailable):
        await _execute(submission_id, provider)
    row = await _row(factory, submission_id)
    assert (row["execution_status"], row["execution_attempts"], row["last_error_class"]) == (
        "pending", 1, "ExecutionUnavailable",
    )


async def test_a_poll_that_never_finishes_keeps_the_ticket_and_raises(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider(pending_polls=10_000)
    _script(provider)
    with pytest.raises(ExecutionUnavailable):
        await _execute(submission_id, provider)
    row = await _row(factory, submission_id)
    assert row["execution_status"] == "submitted" and row["provider_ref_json"]
    assert row["execution_attempts"] == 1


async def test_code_changed_after_acceptance_is_a_defect_not_a_result(factory, world) -> None:
    submission_id, question_id, _ = await _submitted(factory, world)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("UPDATE assessment_answers SET answer_json = jsonb_set(answer_json, '{code}', '\"print(1)\"') "
                     "WHERE question_id = :q"),
                {"q": question_id},
            )
            await session.commit()
    provider = FakeProvider()
    with pytest.raises(submissions.SubmissionDefect):
        await _execute(submission_id, provider)
    assert provider.submissions == []
    row = await _row(factory, submission_id)
    assert (row["execution_status"], row["last_error_class"]) == ("pending", "SubmissionDefect")


async def test_a_compile_error_keeps_the_compiler_message(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider()
    broken = ScriptedRun(outcome=ExecutionOutcome.COMPILE_ERROR, compile_output="SyntaxError: invalid syntax")
    _script(provider, hidden={key: broken for key in HIDDEN}, visible={key: broken for key in VISIBLE})
    await _execute(submission_id, provider)
    row = await _row(factory, submission_id)
    assert (row["tests_passed"], row["compile_output"]) == (0, "SyntaxError: invalid syntax")


async def test_a_sandbox_fault_is_retried_never_graded(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider()
    _script(provider, hidden={"h2": ScriptedRun(outcome=ExecutionOutcome.INTERNAL_ERROR)})
    with pytest.raises(ExecutionUnavailable):
        await _execute(submission_id, provider)
    row = await _row(factory, submission_id)
    assert (row["execution_status"], row["provider_ref_json"], row["tests_total"]) == ("pending", None, None)


async def test_a_degraded_review_is_failed_then_a_later_run_completes_it(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router("not json"))
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider()
    _script(provider)
    report = await _execute(submission_id, provider)
    assert (report.execution_status, report.review_status) == ("complete", "failed")
    row = await _row(factory, submission_id)
    assert row["review_json"] is None and row["review_model_id"] is None

    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    report = await _execute(submission_id, provider)
    assert report.review_status == "complete"
    assert len(provider.submissions) == 1, "a completed execution is never run again"


async def test_the_answer_is_filed_in_the_evidence_ledger_once(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    submission_id, _, message_id = await _submitted(factory, world)
    provider = FakeProvider()
    _script(provider)
    await _execute(submission_id, provider)
    await _execute(submission_id, provider)
    row = await _one(
        factory,
        "SELECT count(*) AS n, min(text_ref) AS ref FROM evidence_items WHERE source_id = :m",
        m=message_id,
    )
    assert row["n"] == 1
    assert "assessment_messages" in row["ref"]


async def test_scoring_is_dispatched_only_when_the_last_coding_work_finishes(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    first, _, _ = await _submitted(factory, world, ordinal=0)
    second, _, _ = await _submitted(factory, world, ordinal=1)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("UPDATE assessment_conversations SET status = 'completed', completed_at = now() WHERE id = :c"),
                {"c": world.conversation},
            )
            await session.commit()
    provider = FakeProvider()
    _script(provider)
    assert (await _execute(first, provider)).scoring_dispatched is False
    assert _dispatched(submissions.SCORING_TASK_NAME) == []
    assert (await _execute(second, provider)).scoring_dispatched is True
    assert [entry.args for entry in _dispatched(submissions.SCORING_TASK_NAME)] == [(str(world.link),)]


async def test_an_active_conversation_is_not_handed_to_scoring(factory, world, monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "invoke_llm", Router())
    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider()
    _script(provider)
    assert (await _execute(submission_id, provider)).scoring_dispatched is False
    assert _dispatched(submissions.SCORING_TASK_NAME) == []


async def test_a_second_worker_holding_the_lock_makes_this_run_a_no_op(factory, world) -> None:
    from app.services import locks

    submission_id, _, _ = await _submitted(factory, world)
    provider = FakeProvider()
    async with factory() as holder:
        assert await locks.try_advisory_lock(holder, submissions.LOCK_NAMESPACE, submission_id)
        report = await _execute(submission_id, provider)
        await holder.rollback()
    assert report.outcome == "busy_or_gone"
    assert provider.submissions == []


# ── Holding scoring, and the auto-submit draft ──────────────────────────────


async def test_scoring_holds_while_work_is_open_and_stops_holding_after_the_window(factory, world) -> None:
    submission_id, _, _ = await _submitted(factory, world)
    async with worker_session() as session:
        hold = await submissions.scoring_hold(session, conversation_id=world.conversation)
        assert (hold.hold, hold.open_submissions, hold.expired_submissions) == (True, 1, 0)
        later = datetime.now(timezone.utc) + timedelta(hours=25)
        hold = await submissions.scoring_hold(session, conversation_id=world.conversation, now=later)
        assert (hold.hold, hold.expired_submissions) == (False, 1)
        assert await submissions.pending_for_conversation(session, world.conversation) == 1


async def test_latest_draft_is_the_most_recent_run(factory, world) -> None:
    question_id = await _persist(factory, world)
    async with factory() as session:
        async with superadmin_scope(session):
            for offset, source in ((2, "print('old')"), (1, "print('new')")):
                await session.execute(
                    text(
                        "INSERT INTO coding_runs (id, tenant_id, conversation_id, question_id, client_token, "
                        "language, source, status, created_at, completed_at) VALUES (:i, :t, :c, :q, :k, 'python', :s, "
                        "'unavailable', now() - make_interval(mins => :o), now())"
                    ),
                    {"i": uuid.uuid4(), "t": world.tenant, "c": world.conversation, "q": question_id,
                     "k": uuid.uuid4().hex, "s": source, "o": offset},
                )
            await session.commit()
    async with worker_session() as session:
        draft = await submissions.latest_draft(session, conversation_id=world.conversation, question_id=question_id)
        assert draft is not None and draft.source == "print('new')" and draft.language == "python"
        assert "print('new')" not in repr(draft)
        assert await submissions.latest_draft(session, conversation_id=world.conversation, question_id=uuid.uuid4()) is None
