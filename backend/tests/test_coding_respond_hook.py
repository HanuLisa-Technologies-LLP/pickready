"""A final coding answer given through the turn engine is handed to execution.

Stage 2 integration (p4-4c hunk 1): `turns.submit_turn` calls
`coding_assessment.final_answer.accept_structured_answer` after the answer row
is written. Before that line a v2 coding answer was stored as an
`assessment_answers` row and NOTHING ran it: no submission row, no hidden-test
execution, and no error anywhere, because a missing call does not fail.

Both halves are read from a SECOND connection after the response, the rule for
any claim about committed state:

- the candidate's own answer writes one `coding_submissions` row and records
  one `pickready.execute_coding_submission` dispatch;
- a coding turn whose clock ran out with no answer in hand submits the code of
  the candidate's latest Run, marked `auto_submitted`.

Mutation-checked: removing the `accept_structured_answer` call from
`submit_turn` leaves no submission row and fails both tests; removing the
latest-Run read fails the second.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services.code_execution import limits as execution_limits
from app.services.coding_assessment import payload as coding_payload
from app.workers import dispatch as dispatch_mod
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session

PROGRAM = "import sys\nprint(sys.stdin.read().strip())\n"

#: The execution bounds exactly as generation freezes them into a payload.
_LIMITS = coding_payload.LanguageLimits.of(execution_limits.for_language("python")).model_dump()

CODING_V2 = {
    "payload_version": 2,
    "title": "Echo the input",
    "io": "stdin_stdout",
    "input_format": "One line.",
    "output_format": "The same line.",
    "constraints": "The line is at most 100 characters.",
    "languages": ["python"],
    "starter_code": {"python": "print()\n"},
    "visible_tests": [
        {"id": "v1", "stdin": "hello\n", "expected_stdout": "hello\n", "explanation": ""},
    ],
    "limits": {"python": dict(_LIMITS)},
}

CODING_PLAN: cw.Plan = (("coding", CODING_V2, "must_have"),)


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


async def _submissions(factory, world) -> list:
    return await cw.committed(
        factory,
        "SELECT auto_submitted, execution_status FROM coding_submissions "
        "WHERE conversation_id = :c",
        c=str(world.conversation),
    )


async def test_a_final_coding_answer_is_handed_to_execution(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    dispatch_mod.clear_recorded()
    factory = cw.sessions()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, plan=CODING_PLAN,
        started=True, open_turn=True, turn_age_seconds=20,
    )
    try:
        with cw.client(candidate) as http:
            answered = _respond(
                http, world,
                {
                    "turn_seq": 1,
                    "answer": "",
                    "answer_payload": {"language": "python", "code": PROGRAM},
                },
            )
        assert answered.status_code == 200, answered.text
        rows = await _submissions(factory, world)
        assert [(row.auto_submitted, row.execution_status) for row in rows] == [(False, "pending")]
        assert "pickready.execute_coding_submission" in dispatch_mod.recorded_names()
    finally:
        await cw.cleanup(factory, world)


async def test_an_expired_coding_turn_submits_the_latest_run(candidate, monkeypatch) -> None:
    cw.quiet_models(monkeypatch)
    dispatch_mod.clear_recorded()
    factory = cw.sessions()
    settings = get_settings()
    world = await cw.seed(
        factory, candidate_id=candidate.candidate_id, plan=CODING_PLAN,
        started=True, open_turn=True,
        turn_age_seconds=settings.assessment_time_coding_seconds
        + settings.assessment_submit_grace_seconds + 30,
    )
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(
                        text(
                            "INSERT INTO coding_runs (id, tenant_id, conversation_id, "
                            "question_id, client_token, language, source, status, "
                            "error_class, created_at, completed_at) VALUES (:id, :t, :c, :q, :tok, "
                            "'python', :src, 'unavailable', 'ExecutionUnavailable', :at, :at)"
                        ),
                        {
                            "id": str(uuid.uuid4()), "t": str(world.tenant),
                            "c": str(world.conversation), "q": str(world.questions[0]),
                            "tok": uuid.uuid4().hex, "src": PROGRAM,
                            "at": datetime.now(timezone.utc) - timedelta(minutes=5),
                        },
                    )
        with cw.client(candidate) as http:
            late = _respond(http, world, {"turn_seq": 1, "answer": "", "timed_out": True})
        assert late.status_code == 200, late.text
        rows = await _submissions(factory, world)
        assert [(row.auto_submitted, row.execution_status) for row in rows] == [(True, "pending")]
    finally:
        await cw.cleanup(factory, world)
