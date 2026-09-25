"""The answer key never leaves the server: no response, no log line, no prompt.

ONE JOURNEY, SWEPT END TO END. A v2 coding question is persisted with its
answer key carrying SENTINEL strings (the hidden tests' inputs and expected
outputs, the reference solution, the approach notes). Then everything a
candidate and a recruiter can do with it happens over HTTP, against a real
Postgres:

  the candidate presses Run (visible samples only) and polls the result;
  the final answer is written the way `respond` writes it and handed to the
  respond hook (`coding_assessment.final_answer`), then the candidate reads
  its state;
  the hidden-test run executes against a program that ECHOES its stdin, so a
  hidden input comes straight back as output and in an error message, and
  the code-quality review is written by a stubbed model that records every
  prompt it was sent;
  the candidate reads the state again, and a recruiter opens the transcript.

Then every response body, every log record at DEBUG, every prompt, every
dispatched argument and the stored run, submission and transcript rows (read
from a SECOND connection) are swept for the sentinels. The approach notes
are allowed in exactly one place, the review prompt, because they describe
the solution rather than a test and the reviewer is told what the code was
meant to do; they may reach no response and no log.

What this does NOT prove, said rather than implied: it covers the routes and
paths that exist on this branch. The candidate conversation's own question
payload is covered through `candidate_view`, the projection its routes
serialise, rather than through the start route, whose consent and credit
preconditions belong to other tests.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import uuid

from sqlalchemy import text

from app.core.db import superadmin_scope, tenant_scope
from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.services import code_execution, llm_router
from app.services.assessment_formats import coding_generation as gen
from app.services.assessment_formats import types
from app.services.code_execution import ExecutionOutcome
from app.services.code_execution.fake import FakeProvider, ScriptedRun
from app.services.coding_assessment import final_answer, submissions
from app.services.coding_assessment import payload as coding_payload
from app.workers import dispatch
from app.workers.runtime import worker_session
from tests.coding_api_fixtures import (  # noqa: F401  (fixtures)
    CONV,
    _fresh_rate_windows,
    candidate,
    candidate_client,
    staff_client,
    write_final_answer,
)
from tests.test_coding_submit_flow import CODE, HIDDEN, VISIBLE, Router, _Clock
from tests.test_coding_tables import (  # noqa: F401  (fixtures)
    HIDDEN_OUT,
    HIDDEN_STDIN,
    REFERENCE,
    _draft,
    _one,
    factory,
    world,
)

APPROACH = "zqsentinelapproachnotes"
#: Never in a response, a log line, a stored run/submission row or a prompt.
KEY_MATERIAL = (HIDDEN_STDIN, HIDDEN_OUT, REFERENCE)


async def _persist_with_sentinels(factory, w) -> uuid.UUID:
    draft = dataclasses.replace(_draft(), expected_approach=f"Count server errors per path. {APPROACH}")
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            question = CandidateQuestion(
                tenant_id=w.tenant, job_id=w.job, job_candidate_link_id=w.link,
                competency_id=w.competency, ordinal=0, prompt="written by persist",
            )
            await gen.persist_coding_question(session, question, draft)
            await session.commit()
            return question.id


def _v2_candidate_view(monkeypatch) -> None:
    """ASSUMPTION, test-only, until 4B1's pending hunk is merged
    (`docs/release/2026-09-vivekium/hunks/p4-4b1-types-v2-dispatch.patch`):
    `types.candidate_view` on this branch still reads every coding payload
    as the legacy v1 shape, so it raises on a v2 row, and the recruiter
    transcript serialises the candidate view of every structured answer.
    This installs exactly the dispatch that hunk adds and nothing more: a v2
    payload is projected by `coding_assessment.payload.candidate_projection`.
    Once the hunk lands this is a no-op wrapper around the same call."""
    original = types.candidate_view

    def view(question_id, question_type, payload):
        if question_type == types.CODING and coding_payload.is_v2(payload):
            return coding_payload.candidate_projection(payload)
        return original(question_id, question_type, payload)

    monkeypatch.setattr(types, "candidate_view", view)


def _script(provider: FakeProvider) -> None:
    for stdin, expected in VISIBLE.values():
        provider.script(CODE, stdin, ScriptedRun(stdout=expected))
    for key, (stdin, _expected) in HIDDEN.items():
        if key in ("h1", "h2"):
            # The program ECHOES its stdin: the hidden input is its output.
            provider.script(CODE, stdin, ScriptedRun(stdout=stdin))
        elif key == "h3":
            provider.script(CODE, stdin, ScriptedRun(outcome=ExecutionOutcome.RUNTIME_ERROR,
                                                     stderr=f"ValueError: {stdin}"))
        elif key == "h4":
            provider.script(CODE, stdin, ScriptedRun(outcome=ExecutionOutcome.TIME_LIMIT, stdout=stdin))
        else:
            provider.script(CODE, stdin, ScriptedRun(stdout=HIDDEN[key][1]))


async def test_the_answer_key_reaches_no_response_no_log_and_no_prompt(factory, candidate, monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _v2_candidate_view(monkeypatch)
    router = Router()
    monkeypatch.setattr(llm_router, "invoke_llm", router)
    world = candidate.world
    question_id = await _persist_with_sentinels(factory, world)
    provider = FakeProvider()
    _script(provider)
    runs_path = f"{CONV}/{world.conversation}/coding/{question_id}/runs"
    state_path = f"{CONV}/{world.conversation}/coding/{question_id}/submission"
    bodies: list[str] = []

    # What the conversation's routes serialise as the question on screen.
    async with factory() as session:
        async with superadmin_scope(session):
            question = await session.get(CandidateQuestion, question_id)
            bodies.append(json.dumps(types.candidate_view(question.id, question.question_type, question.payload_json)))

    dispatch.clear_recorded()
    with code_execution.override_provider(provider):
        async with candidate_client(factory, {"principal": candidate.principal}) as client:
            started = await client.post(
                runs_path, json={"language": "python", "source": CODE, "client_token": "sentinel-run"}
            )
            assert started.status_code == 202, started.text
            polled = await client.get(f"{runs_path}/{started.json()['run_id']}")
            assert polled.json()["status"] == "complete", polled.text
            bodies += [started.text, polled.text]

            # The final answer, exactly as the respond turn writes it, then the hook.
            await write_final_answer(factory, world, question_id, CODE)
            async with factory() as session:
                async with superadmin_scope(session):
                    submission = await final_answer.accept_structured_answer(
                        session,
                        conversation=await session.get(AssessmentConversation, world.conversation),
                        question=await session.get(CandidateQuestion, question_id),
                    )
                    submission_id = submission.id
                    await session.commit()
            before = await client.get(state_path)
            bodies.append(before.text)

            clock = _Clock()
            async with worker_session() as session:
                report = await submissions.execute_submission(
                    session, submission_id, provider=provider, sleep=clock.sleep, clock=clock
                )
            assert (report.execution_status, report.review_status) == ("complete", "complete")

            after = await client.get(state_path)
            bodies.append(after.text)

    assert before.json() == {"state_word": submissions.STATE_WORD_SUBMITTED}
    assert after.json() == {"state_word": submissions.STATE_WORD_CHECKED}

    async with staff_client(factory, world) as staff:
        transcript = await staff.get(f"/api/v2/assessments/transcripts/links/{world.link}")
    assert transcript.status_code == 200, transcript.text
    bodies.append(transcript.text)
    (exchange,) = transcript.json()["exchanges"]
    detail = exchange["detail"]
    # The recruiter reads the sentence and the review, never the key.
    assert detail["answer_key"] == {}
    assert detail["coding_outcome"].startswith("Passed one of the five hidden tests")
    assert detail["review_citations"] and detail["review_reasoning"]
    assert detail["compile_error"] is None

    stored = []
    for sql in (
        "SELECT * FROM coding_runs WHERE question_id = :q",
        "SELECT * FROM coding_submissions WHERE question_id = :q",
        "SELECT * FROM assessment_messages WHERE question_key = :k",
        "SELECT * FROM assessment_answers WHERE question_id = :q",
    ):
        async with factory() as session:
            async with superadmin_scope(session):
                rows = (await session.execute(text(sql), {"q": question_id, "k": str(question_id)})).mappings().all()
        assert rows, sql
        stored.append(json.dumps([dict(r) for r in rows], default=str))

    prompts = json.dumps(router.calls)
    logged = [record.getMessage() for record in caplog.records]
    dispatched = json.dumps([list(entry.args) for entry in dispatch.recorded()], default=str)
    assert router.calls, "the review never ran, so the prompt sweep would be vacuous"
    assert len(logged) > 5, "almost nothing was logged, so the log sweep would be vacuous"
    for sentinel in KEY_MATERIAL:
        assert all(sentinel not in body for body in bodies), f"{sentinel} reached a response"
        assert all(sentinel not in line for line in logged), f"{sentinel} reached a log line"
        assert sentinel not in prompts, f"{sentinel} reached a prompt"
        assert sentinel not in dispatched, f"{sentinel} reached a dispatch"
        assert all(sentinel not in row for row in stored), f"{sentinel} was stored outside the key"
    assert all(APPROACH not in body for body in bodies), "the approach notes reached a response"
    assert all(APPROACH not in line for line in logged), "the approach notes reached a log line"


async def test_the_sweep_is_not_vacuous(factory, candidate) -> None:
    """The sentinels ARE in the key, so their absence everywhere else means
    something: read the key row back and find every one of them."""
    question_id = await _persist_with_sentinels(factory, candidate.world)
    row = await _one(factory, "SELECT * FROM coding_question_keys WHERE question_id = :q", q=question_id)
    dumped = json.dumps(dict(row), default=str)
    for sentinel in (*KEY_MATERIAL, APPROACH):
        assert sentinel in dumped
