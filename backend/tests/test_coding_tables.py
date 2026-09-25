"""Migration 0124 against a real Postgres: the answer key, runs, submissions.

Every assertion that matters reads COMMITTED state from a SECOND connection,
because a write that answered and then rolled back at commit is invisible to
the connection that made it (the 2026-09-20 audit_log class). No reachability
skip: these are constraints the database itself enforces, and a green run
that never touched Postgres would say nothing about them.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.models import coding as coding_models
from app.models.assessment import CandidateQuestion
from app.services.assessment_formats import coding_generation as gen
from app.services.assessment_formats import types
from app.services.coding_assessment import keys as coding_keys
from app.services.erasure import job_closure_erasure

HIDDEN_STDIN = "zqtablehiddenstdin"
HIDDEN_OUT = "zqtablehiddenexpected"
REFERENCE = "zqtablereference"

_LIMITS = {
    "cpu_seconds": 2.0,
    "cpu_extra_seconds": 0.5,
    "wall_seconds": 5.0,
    "memory_kb": 262144,
    "stack_kb": 65536,
    "max_processes": 60,
    "max_file_kb": 1024,
    "max_output_chars": 4000,
}


def _draft(title: str = "Busiest failing endpoint") -> gen.CodingQuestionDraft:
    hidden = tuple(
        coding_keys.HiddenTest(key=f"h{i}", stdin=f"{HIDDEN_STDIN}{i}\n", expected_stdout=f"{HIDDEN_OUT}{i}\n")
        for i in range(1, 6)
    )
    return gen.CodingQuestionDraft(
        title=title,
        statement="Print the endpoint with the most server errors, or NONE.",
        payload={
            "payload_version": 2,
            "title": title,
            "io": "stdin_stdout",
            "input_format": "n, then n lines.",
            "output_format": "One line.",
            "constraints": "0 <= n <= 10000",
            "languages": ["python"],
            "starter_code": {"python": "print()\n"},
            "visible_tests": [
                {"id": "v1", "stdin": "0\n", "expected_stdout": "NONE\n", "explanation": ""},
                {"id": "v2", "stdin": "1\n/a 500\n", "expected_stdout": "/a\n", "explanation": ""},
            ],
            "limits": {"python": dict(_LIMITS)},
        },
        rubric={"criteria": ["code_quality"], "payload_version": 2},
        reference_language="python",
        validation={"provider": "fake", "hidden_digest": coding_keys.hidden_digest(hidden)},
        generated_at=datetime.now(timezone.utc),
        hidden_tests=hidden,
        reference_source=f"# {REFERENCE}\nprint()\n",
        expected_approach="Count server errors per path.",
    )


@dataclass
class _World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    competency: uuid.UUID = field(default_factory=uuid.uuid4)
    candidate: uuid.UUID = field(default_factory=uuid.uuid4)
    profile: uuid.UUID = field(default_factory=uuid.uuid4)
    link: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation: uuid.UUID = field(default_factory=uuid.uuid4)


@pytest.fixture
async def factory():
    engine = create_async_engine(get_settings().database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def world(factory):
    w = _World()
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("INSERT INTO tenants (id, name, domain, spf_dkim_status) VALUES (:t, :n, :d, 'pending')"),
                {"t": w.tenant, "n": f"coding-{w.tenant}", "d": f"{w.tenant}.coding.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, status, assessment_grade) "
                    "VALUES (:j, :t, 'Site Reliability Engineer', '{}'::jsonb, 'draft', 'managerial')"
                ),
                {"j": w.job, "t": w.tenant},
            )
            await session.execute(
                text(
                    "INSERT INTO job_competencies (id, tenant_id, job_id, category, name, "
                    "required_level, ordinal, is_active) VALUES "
                    "(:c, :t, :j, 'must_have', 'Log analysis', 82, 0, true)"
                ),
                {"c": w.competency, "t": w.tenant, "j": w.job},
            )
            await session.execute(
                text(
                    "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
                    "VALUES (:c, :t, 'Coding Subject', :e, false)"
                ),
                {"c": w.candidate, "t": w.tenant, "e": f"{w.candidate}@coding.test"},
            )
            await session.execute(
                text("INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) VALUES (:p, :c, :t, 'x')"),
                {"p": w.profile, "c": w.candidate, "t": w.tenant},
            )
            await session.execute(
                text(
                    "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, profile_id, source) "
                    "VALUES (:l, :t, :j, :c, :p, 'manual')"
                ),
                {"l": w.link, "t": w.tenant, "j": w.job, "c": w.candidate, "p": w.profile},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_conversations (id, tenant_id, job_id, job_candidate_link_id, "
                    "grade, status, next_question_index, reminders_sent, follow_ups_used, reasks_used, mode) "
                    "VALUES (:i, :t, :j, :l, 'managerial', 'active', 0, 0, 0, 0, 'conversational')"
                ),
                {"i": w.conversation, "t": w.tenant, "j": w.job, "l": w.link},
            )
            await session.commit()
    try:
        yield w
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": w.tenant})
                await session.commit()


def _question(w: _World, ordinal: int = 0) -> CandidateQuestion:
    return CandidateQuestion(
        tenant_id=w.tenant,
        job_id=w.job,
        job_candidate_link_id=w.link,
        competency_id=w.competency,
        ordinal=ordinal,
        prompt="written by persist",
    )


async def _persist(factory, w: _World, *, ordinal: int = 0, title: str = "Busiest failing endpoint") -> uuid.UUID:
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            question = _question(w, ordinal)
            await gen.persist_coding_question(session, question, _draft(title))
            await session.commit()
            return question.id


async def _one(factory, sql: str, **params):
    async with factory() as session:
        async with superadmin_scope(session):
            return (await session.execute(text(sql), params)).mappings().first()


async def _count(factory, table: str, column: str, value) -> int:
    row = await _one(factory, f"SELECT count(*) AS n FROM {table} WHERE {column} = :v", v=value)
    return int(row["n"])


# ── The question and its key, together ──────────────────────────────────────


async def test_persist_writes_the_payload_and_the_key_in_one_transaction(factory, world) -> None:
    question_id = await _persist(factory, world)

    row = await _one(
        factory,
        "SELECT question_type, prompt, payload_json, rubric_json, generated_at "
        "FROM candidate_questions WHERE id = :q",
        q=question_id,
    )
    assert row["question_type"] == types.CODING
    assert row["prompt"] == _draft().statement
    assert row["payload_json"]["payload_version"] == 2
    assert row["generated_at"] is not None
    stored = json.dumps(row["payload_json"]) + json.dumps(row["rubric_json"]) + row["prompt"]
    for sentinel in (HIDDEN_STDIN, HIDDEN_OUT, REFERENCE):
        assert sentinel not in stored

    key = await _one(
        factory,
        "SELECT tenant_id, hidden_tests_json, reference_language, reference_source, "
        "expected_approach, validation_json FROM coding_question_keys WHERE question_id = :q",
        q=question_id,
    )
    assert key["tenant_id"] == world.tenant
    assert [t["key"] for t in key["hidden_tests_json"]] == ["h1", "h2", "h3", "h4", "h5"]
    assert key["hidden_tests_json"][0]["stdin"] == f"{HIDDEN_STDIN}1\n"
    assert REFERENCE in key["reference_source"]
    assert key["validation_json"]["hidden_digest"] == coding_keys.hidden_digest(_draft().hidden_tests)


async def test_a_rolled_back_transaction_keeps_neither(factory, world) -> None:
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            question = _question(world)
            await gen.persist_coding_question(session, question, _draft())
            question_id = question.id
            await session.rollback()
    assert await _count(factory, "candidate_questions", "id", question_id) == 0
    assert await _count(factory, "coding_question_keys", "question_id", question_id) == 0


async def test_persist_refuses_a_payload_that_carries_the_key(factory, world) -> None:
    draft = _draft()
    leaky = gen.CodingQuestionDraft(
        **{**{f: getattr(draft, f) for f in draft.__dataclass_fields__},
           "payload": {**draft.payload, "hidden_tests": [{"stdin": "x", "expected_stdout": "y"}]}}
    )
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            with pytest.raises(ValueError):
                await gen.persist_coding_question(session, _question(world), leaky)


# ── Immutable, by privilege and by trigger ──────────────────────────────────


async def test_the_app_role_may_read_insert_and_delete_the_key_but_never_update_it(factory) -> None:
    row = await _one(
        factory,
        "SELECT has_table_privilege('pickready_app', 'coding_question_keys', 'SELECT') AS s, "
        "has_table_privilege('pickready_app', 'coding_question_keys', 'INSERT') AS i, "
        "has_table_privilege('pickready_app', 'coding_question_keys', 'DELETE') AS d, "
        "has_table_privilege('pickready_app', 'coding_question_keys', 'UPDATE') AS u",
    )
    assert (row["s"], row["i"], row["d"], row["u"]) == (True, True, True, False)


async def test_an_update_is_refused_to_the_app_role_and_to_the_owner(factory, world) -> None:
    question_id = await _persist(factory, world)
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            with pytest.raises(DBAPIError) as raised:
                await session.execute(
                    text("UPDATE coding_question_keys SET expected_approach = 'x' WHERE question_id = :q"),
                    {"q": question_id},
                )
            assert "permission denied" in str(raised.value)
            await session.rollback()
    # The owner (no SET ROLE): only the trigger stands in the way.
    async with factory() as session:
        with pytest.raises(DBAPIError) as raised:
            await session.execute(
                text("UPDATE coding_question_keys SET expected_approach = 'x' WHERE question_id = :q"),
                {"q": question_id},
            )
        assert "insert-only" in str(raised.value)
        await session.rollback()
    row = await _one(factory, "SELECT expected_approach FROM coding_question_keys WHERE question_id = :q", q=question_id)
    assert row["expected_approach"] == _draft().expected_approach


# ── The one reader ───────────────────────────────────────────────────────────


async def test_the_answer_key_judges_without_disclosing(factory, world) -> None:
    question_id = await _persist(factory, world)
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            key = await coding_keys.load_answer_key(session, question_id)
            notes = await coding_keys.review_notes(session, question_id)
    assert key.count == 5 and key.keys == ("h1", "h2", "h3", "h4", "h5")
    inputs = key.test_inputs()
    assert inputs[0].key == "h1" and inputs[0].stdin == f"{HIDDEN_STDIN}1\n"
    assert not hasattr(inputs[0], "expected_stdout")
    assert key.passed("h1", f"{HIDDEN_OUT}1   \r\n\n")
    assert not key.passed("h1", f"  {HIDDEN_OUT}1\n")
    with pytest.raises(KeyError):
        key.passed("h9", "anything")
    assert notes == _draft().expected_approach
    for sentinel in (HIDDEN_STDIN, HIDDEN_OUT):
        assert sentinel not in repr(key) and sentinel not in repr(key.tests)


async def test_another_tenant_cannot_see_the_key(factory, world) -> None:
    question_id = await _persist(factory, world)
    async with factory() as session:
        async with tenant_scope(session, uuid.uuid4()):
            with pytest.raises(coding_keys.KeyNotFound):
                await coding_keys.load_answer_key(session, question_id)


async def test_a_key_that_no_longer_matches_its_digest_is_refused(factory, world) -> None:
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            question = _question(world, ordinal=5)
            question.question_type = types.CODING
            session.add(question)
            await session.flush()
            question_id = question.id
            await session.execute(
                text(
                    "INSERT INTO coding_question_keys (question_id, tenant_id, hidden_tests_json, "
                    "reference_language, reference_source, expected_approach, validation_json) "
                    "VALUES (:q, :t, CAST(:h AS jsonb), 'python', 'print()', 'x', CAST(:v AS jsonb))"
                ),
                {
                    "q": question_id,
                    "t": world.tenant,
                    "h": json.dumps([{"key": "h1", "stdin": "1", "expected_stdout": "2"}]),
                    "v": json.dumps({"hidden_digest": "0" * 64}),
                },
            )
            await session.commit()
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            with pytest.raises(coding_keys.KeyIntegrityError):
                await coding_keys.load_answer_key(session, question_id)


def test_stage_key_refuses_a_key_that_could_not_be_graded() -> None:
    hidden = _draft().hidden_tests
    common = dict(
        question_id=uuid.uuid4(), tenant_id=uuid.uuid4(), reference_language="python",
        reference_source="print()", expected_approach="x",
    )

    class _Session:
        def add(self, row) -> None:
            raise AssertionError("nothing may be added for a refused key")

    with pytest.raises(ValueError, match="in order"):
        coding_keys.stage_key(_Session(), tests=tuple(reversed(hidden)),
                              validation={"hidden_digest": coding_keys.hidden_digest(tuple(reversed(hidden)))},
                              **common)
    with pytest.raises(ValueError, match="does not describe"):
        coding_keys.stage_key(_Session(), tests=hidden, validation={"hidden_digest": "0" * 64}, **common)
    with pytest.raises(ValueError):
        coding_keys.HiddenTest(key="h1", stdin="x", expected_stdout="  ")


# ── The payload may never carry the key ─────────────────────────────────────


async def _insert_question(factory, w: _World, ordinal: int, question_type: str, payload: dict) -> None:
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            await session.execute(
                text(
                    "INSERT INTO candidate_questions (id, tenant_id, job_id, job_candidate_link_id, "
                    "competency_id, ordinal, prompt, question_type, payload_json) VALUES "
                    "(:i, :t, :j, :l, :c, :o, 'p', :q, CAST(:p AS jsonb))"
                ),
                {"i": uuid.uuid4(), "t": w.tenant, "j": w.job, "l": w.link, "c": w.competency,
                 "o": ordinal, "q": question_type, "p": json.dumps(payload)},
            )
            await session.commit()


@pytest.mark.parametrize(
    "payload",
    [
        {"payload_version": 2, "hidden_tests": []},
        {"payload_version": 2, "reference_solution": "x"},
        {"payload_version": 2, "expected_approach": "x"},
        {"language": "python", "reference_source": "x"},
        {"language": "python", "reference": {"python": "x"}},
    ],
)
async def test_the_database_refuses_a_coding_payload_that_carries_the_key(factory, world, payload) -> None:
    with pytest.raises(IntegrityError, match="ck_candidate_questions_coding_key_private"):
        await _insert_question(factory, world, 10, "coding", payload)


async def test_the_constraint_leaves_legacy_rows_and_other_formats_alone(factory, world) -> None:
    await _insert_question(factory, world, 11, "coding", {"language": "python", "expected_approach": "x"})
    await _insert_question(factory, world, 12, "mcq_single", {"reference": "not a coding payload"})


# ── Runs and submissions keep their states honest ───────────────────────────


async def _run(factory, w: _World, question_id: uuid.UUID, **values) -> None:
    row = dict(id=uuid.uuid4(), client_token=uuid.uuid4().hex, language="python", source="print()",
               status="queued", completed_at=None, results_json=None)
    row.update(values)
    row["results_json"] = None if row["results_json"] is None else json.dumps(row["results_json"])
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            await session.execute(
                text(
                    "INSERT INTO coding_runs (id, tenant_id, conversation_id, question_id, client_token, "
                    "language, source, status, completed_at, results_json) VALUES "
                    "(:id, :t, :c, :q, :client_token, :language, :source, :status, :completed_at, "
                    "CAST(:results_json AS jsonb))"
                ),
                {**row, "t": w.tenant, "c": w.conversation, "q": question_id},
            )
            await session.commit()


async def test_a_run_row_cannot_misstate_its_state(factory, world) -> None:
    question_id = await _persist(factory, world)
    now = datetime.now(timezone.utc)
    await _run(factory, world, question_id)
    await _run(factory, world, question_id, status="complete", completed_at=now, results_json=[])
    await _run(factory, world, question_id, status="unavailable", completed_at=now)
    for values, constraint in (
        ({"status": "queued", "completed_at": now}, "ck_coding_runs_completion"),
        ({"status": "complete", "completed_at": now}, "ck_coding_runs_results"),
        ({"status": "unavailable"}, "ck_coding_runs_completion"),
        ({"status": "running", "completed_at": now}, "ck_coding_runs_status"),
        ({"source": "x" * (coding_models.CODE_SOURCE_MAX_CHARS + 1)}, "ck_coding_runs_source"),
        ({"client_token": ""}, "ck_coding_runs_client_token"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            await _run(factory, world, question_id, **values)
    await _run(factory, world, question_id, client_token="same-click")
    with pytest.raises(IntegrityError, match="uq_coding_runs_client_token"):
        await _run(factory, world, question_id, client_token="same-click")


async def _answer(factory, w: _World, question_id: uuid.UUID) -> uuid.UUID:
    answer_id = uuid.uuid4()
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            await session.execute(
                text(
                    "INSERT INTO assessment_answers (id, tenant_id, conversation_id, question_id, "
                    "question_type, answer_json, submitted_at) VALUES "
                    "(:a, :t, :c, :q, 'coding', CAST(:j AS jsonb), now())"
                ),
                {"a": answer_id, "t": w.tenant, "c": w.conversation, "q": question_id,
                 "j": json.dumps({"language": "python", "code": "print()"})},
            )
            await session.commit()
    return answer_id


async def _submission(factory, w: _World, answer_id, question_id, **values) -> None:
    row = dict(
        id=uuid.uuid4(), source_sha256=coding_models.submission_source_digest("python", "print()"),
        execution_status="pending", review_status="pending", tests_total=None, tests_passed=None,
        test_results_json=None, executed_at=None, provider_ref_json=None,
    )
    row.update(values)
    for name in ("test_results_json", "provider_ref_json"):
        row[name] = None if row[name] is None else json.dumps(row[name])
    async with factory() as session:
        async with tenant_scope(session, w.tenant):
            await session.execute(
                text(
                    "INSERT INTO coding_submissions (id, tenant_id, answer_id, conversation_id, "
                    "question_id, source_sha256, execution_status, review_status, tests_total, "
                    "tests_passed, test_results_json, executed_at, provider_ref_json) VALUES "
                    "(:id, :t, :a, :c, :q, :source_sha256, :execution_status, :review_status, "
                    ":tests_total, :tests_passed, CAST(:test_results_json AS jsonb), :executed_at, "
                    "CAST(:provider_ref_json AS jsonb))"
                ),
                {**row, "t": w.tenant, "a": answer_id, "c": w.conversation, "q": question_id},
            )
            await session.commit()


async def _fresh_answer(factory, w: _World, question_id: uuid.UUID) -> uuid.UUID:
    """One answer per question, so the UNIQUE on answer_id is never what refuses."""
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(
                text("DELETE FROM assessment_answers WHERE question_id = :q"), {"q": question_id}
            )
            await session.commit()
    return await _answer(factory, w, question_id)


async def test_a_submission_row_cannot_misstate_its_state(factory, world) -> None:
    question_id = await _persist(factory, world)
    for values, constraint in (
        ({"execution_status": "complete"}, "ck_coding_submissions_complete"),
        ({"execution_status": "submitted"}, "ck_coding_submissions_ticket"),
        ({"execution_status": "no_code"}, "ck_coding_submissions_no_code"),
        ({"review_status": "not_applicable"}, "ck_coding_submissions_no_code"),
        ({"tests_total": 5, "tests_passed": 6}, "ck_coding_submissions_counts"),
        ({"tests_total": 31, "tests_passed": 0}, "ck_coding_submissions_counts"),
        ({"review_status": "complete"}, "ck_coding_submissions_review"),
        ({"source_sha256": "abc"}, "ck_coding_submissions_digest"),
        ({"execution_status": "running"}, "ck_coding_submissions_execution_status"),
    ):
        answer_id = await _fresh_answer(factory, world, question_id)
        with pytest.raises(IntegrityError, match=constraint):
            await _submission(factory, world, answer_id, question_id, **values)


async def test_valid_submission_states_are_admitted_once_per_answer(factory, world) -> None:
    question_id = await _persist(factory, world)
    answer_id = await _fresh_answer(factory, world, question_id)
    await _submission(
        factory, world, answer_id, question_id, execution_status="complete", tests_total=5,
        tests_passed=3, executed_at=datetime.now(timezone.utc),
        test_results_json=[{"key": "h1", "outcome": "ok", "passed": True}],
    )
    with pytest.raises(IntegrityError, match="uq_coding_submissions_answer"):
        await _submission(factory, world, answer_id, question_id)

    empty = await _fresh_answer(factory, world, question_id)
    await _submission(
        factory, world, empty, question_id, execution_status="no_code", review_status="not_applicable"
    )
    submitted = await _fresh_answer(factory, world, question_id)
    await _submission(
        factory, world, submitted, question_id, execution_status="submitted",
        provider_ref_json={"provider": "fake", "refs": ["fake-0"], "keys": ["h1"]},
    )


# ── Erasure reaches all three through the existing cascades ─────────────────


async def test_job_closure_erasure_removes_the_key_the_runs_and_the_submissions(factory, world) -> None:
    question_id = await _persist(factory, world)
    await _run(factory, world, question_id)
    answer_id = await _answer(factory, world, question_id)
    await _submission(factory, world, answer_id, question_id)
    async with factory() as session:
        async with superadmin_scope(session):
            await job_closure_erasure(session, job_id=world.job)
            await session.commit()
    assert await _count(factory, "coding_question_keys", "question_id", question_id) == 0
    assert await _count(factory, "coding_runs", "question_id", question_id) == 0
    assert await _count(factory, "coding_submissions", "question_id", question_id) == 0


async def test_deleting_the_conversation_removes_its_runs_and_submissions(factory, world) -> None:
    question_id = await _persist(factory, world)
    await _run(factory, world, question_id)
    answer_id = await _answer(factory, world, question_id)
    await _submission(factory, world, answer_id, question_id)
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(text("DELETE FROM assessment_conversations WHERE id = :c"), {"c": world.conversation})
            await session.commit()
    assert await _count(factory, "coding_runs", "conversation_id", world.conversation) == 0
    assert await _count(factory, "coding_submissions", "conversation_id", world.conversation) == 0
    # The question outlives its conversation, and so does its key.
    assert await _count(factory, "coding_question_keys", "question_id", question_id) == 1


# ── Tenant isolation and the catalogue ──────────────────────────────────────


@pytest.mark.parametrize("table", ["coding_question_keys", "coding_runs", "coding_submissions"])
async def test_every_table_forces_row_level_security_under_its_policy(factory, table: str) -> None:
    row = await _one(
        factory,
        "SELECT c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced, "
        "(SELECT count(*) FROM pg_policies p WHERE p.tablename = :t "
        " AND p.policyname = :t || '_tenant_isolation') AS policies "
        "FROM pg_class c WHERE c.relname = :t",
        t=table,
    )
    assert (row["enabled"], row["forced"], row["policies"]) == (True, True, 1)


async def test_another_tenant_sees_no_runs(factory, world) -> None:
    question_id = await _persist(factory, world)
    await _run(factory, world, question_id)
    async with factory() as session:
        async with tenant_scope(session, uuid.uuid4()):
            seen = (await session.execute(text("SELECT count(*) FROM coding_runs"))).scalar_one()
    assert seen == 0


@pytest.mark.parametrize(
    "constraint, vocabulary",
    [
        ("ck_coding_runs_status", coding_models.RUN_STATUSES),
        ("ck_coding_submissions_execution_status", coding_models.EXECUTION_STATUSES),
        ("ck_coding_submissions_review_status", coding_models.REVIEW_STATUSES),
    ],
)
async def test_the_status_checks_admit_exactly_the_model_vocabularies(factory, constraint, vocabulary) -> None:
    row = await _one(factory, "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = :n", n=constraint)
    admitted = set(__import__("re").findall(r"'([a-z_]+)'", row["d"]))
    assert admitted == set(vocabulary)


def test_the_column_ceilings_match_the_answer_shape_and_the_settings() -> None:
    assert coding_models.CODE_SOURCE_MAX_CHARS == types.MAX_CODE_CHARS
    assert coding_models.CODING_OUTPUT_EXCERPT_MAX_CHARS == get_settings().code_execution_max_output_chars
    assert get_settings().coding_hidden_tests_max <= coding_models.HIDDEN_TESTS_MAX


def test_the_submission_digest_binds_the_language_and_the_code() -> None:
    digest = coding_models.submission_source_digest
    assert digest("python", "print(1)") == digest("python", "print(1)")
    assert digest("python", "print(1)") != digest("javascript", "print(1)")
    assert digest("python", "print(1)") != digest("python", "print(2)")
    assert len(digest("python", "")) == 64


async def test_issued_titles_are_the_jobs_own_v2_titles(factory, world) -> None:
    await _persist(factory, world, ordinal=0, title="First problem")
    await _persist(factory, world, ordinal=1, title="Second problem")
    async with factory() as session:
        async with tenant_scope(session, world.tenant):
            titles = await gen.issued_titles(session, job_id=world.job)
        async with tenant_scope(session, uuid.uuid4()):
            elsewhere = await gen.issued_titles(session, job_id=world.job)
    assert set(titles) == {"First problem", "Second problem"}
    assert elsewhere == ()
