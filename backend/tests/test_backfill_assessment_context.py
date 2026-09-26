"""`backfill_assessment_context`: Sutra writes the context for saved, unlocked
jobs whose context it did not write, through `skills.save`, and nothing else.

Migration 0118 kept saved matrices ready with an honest EMPTY context; this
script is how an operator later fills it. Real tables and the real save, with
the model doubled at the router boundary (`skills_fixtures`), every outcome
read from a SECOND connection after the script's own commit. Each test names
its job (`job_ids`), because the eligibility query is platform wide and another
suite's rows must neither pass nor fail a test here.

MUTATION CHECK, recorded: replacing the `NOT EXISTS (... job_skill_snapshots
...)` clause with `TRUE` fails `test_a_locked_job_is_never_offered`.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.scripts import backfill_assessment_context as backfill
from app.services import llm_router
from tests import skills_fixtures as fx

MUST, BEHAV = "must_have", "behavioural"
SAVEABLE = [
    (MUST, "Kafka stream processing", True, "sutra", None, "Old matrix evidence."),
    (BEHAV, "Production incident ownership", True, "sutra", None, None),
]
ACTIVE = [(bucket, name) for bucket, name, active, *_ in SAVEABLE if active]
MIGRATION_CONTEXT = {"role_summary": "", "generated_by": "migration"}


async def _execute(sql: str, params: dict) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(text(sql), params)


@pytest.fixture
async def operator():
    """An active PLATFORM super admin: no tenant."""
    user = uuid.uuid4()
    email = f"{user.hex[:12]}@operators.test"
    await _execute(
        "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
        "VALUES (:u, NULL, :e, 'Platform Operator', 'super_admin', 'active')",
        {"u": user, "e": email},
    )
    yield user, email
    await _execute("DELETE FROM users WHERE id = :u", {"u": user})


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(*, context: dict | None = MIGRATION_CONTEXT, **kwargs) -> fx.World:
        w = await fx.seed(**{"skills": SAVEABLE, "saved": True, **kwargs})
        worlds.append(w)
        await _execute(
            "UPDATE jobs SET assessment_context_json = CAST(:c AS jsonb) WHERE id = :j",
            {"c": json.dumps(context), "j": w.job},
        )
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


async def _run(w: fx.World, **kwargs):
    return await backfill.run(job_ids=(w.job,), factory=fx.sessions(), **kwargs)


async def test_a_dry_run_lists_the_job_and_writes_nothing(world, monkeypatch) -> None:
    w = await world()
    router = fx.install(monkeypatch, fx.FakeRouter())

    status, (line,) = await _run(w, apply=False, operator_email=None)

    assert status == 0
    assert "[migration]" in line and "would save" in line, line
    assert router.calls == [], "a dry run calls no model"
    assert (await fx.committed_job(w))["assessment_context_json"] == MIGRATION_CONTEXT


async def test_an_apply_saves_through_sutra_and_names_the_operator(
    world, operator, monkeypatch
) -> None:
    w = await world()
    operator_id, email = operator
    router = fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    status, (line,) = await _run(w, apply=True, operator_email=email)

    assert status == 0, line
    assert "saved" in line, line
    assert len(router.calls) == 1
    job = await fx.committed_job(w)
    assert job["assessment_context_json"]["generated_by"] == "sutra"
    assert job["assessment_context_json"]["role_summary"]
    (saved,) = await fx.committed_audit(w, "job_skills_saved")
    assert str(saved["actor_user_id"]) == str(operator_id)
    assert saved["actor_role"] == "super_admin"
    (context,) = await fx.committed_audit(w, "job_assessment_context_written")
    assert str(context["actor_user_id"]) == str(operator_id)


async def test_a_locked_job_is_never_offered(world, monkeypatch) -> None:
    w = await world()
    await _execute(
        "INSERT INTO job_skill_snapshots (id, tenant_id, job_id, version, skills_json, "
        "grade, digest, source, locked_at) VALUES (:i, :t, :j, 1, '[]'::jsonb, "
        "'managerial', :d, 'migration', now())",
        {"i": uuid.uuid4(), "t": w.tenant, "j": w.job, "d": "0" * 64},
    )
    fx.install(monkeypatch, fx.FakeRouter())

    status, lines = await _run(w, apply=False, operator_email=None)

    assert status == 0
    assert all(str(w.job) not in line and "Senior Data Engineer" not in line for line in lines), lines


async def test_a_context_sutra_wrote_is_never_offered(world, monkeypatch) -> None:
    w = await world(context={"role_summary": "Owns the pipelines.", "generated_by": "sutra"})
    fx.install(monkeypatch, fx.FakeRouter())

    status, lines = await _run(w, apply=False, operator_email=None)

    assert status == 0
    assert lines == ["no saved, unlocked job carries a context Sutra did not write"]


async def test_an_apply_needs_a_platform_operator(world, monkeypatch) -> None:
    w = await world()
    router = fx.install(monkeypatch, fx.FakeRouter())
    client_email = f"{w.client.hex[:12]}@skills.test"

    assert (await _run(w, apply=True, operator_email=None))[0] == 2
    status, (reason,) = await _run(w, apply=True, operator_email=client_email)

    assert status == 2 and "platform super admin" in reason
    assert router.calls == []
    assert (await fx.committed_job(w))["assessment_context_json"] == MIGRATION_CONTEXT


async def test_a_writer_outage_saves_nothing_and_fails_the_run(
    world, operator, monkeypatch
) -> None:
    w = await world()
    _operator_id, email = operator
    fx.install(
        monkeypatch,
        fx.FakeRouter(
            llm_router.LLMUnavailableError("down"), llm_router.LLMUnavailableError("down")
        ),
    )

    status, (line,) = await _run(w, apply=True, operator_email=email)

    assert status == 1, "a partial run is never read as a clean one"
    assert "NOT SAVED (SkillsContextUnavailable)" in line, line
    assert (await fx.committed_job(w))["assessment_context_json"] == MIGRATION_CONTEXT
    assert await fx.committed_audit(w, "job_skills_saved") == []


async def test_an_over_limit_bucket_is_reported_with_no_model_call(
    world, operator, monkeypatch
) -> None:
    over = [(MUST, f"Skill {n}", True, "sutra", None, None) for n in range(6)] + [SAVEABLE[1]]
    w = await world(skills=over)
    _operator_id, email = operator
    router = fx.install(monkeypatch, fx.FakeRouter())

    status, (line,) = await _run(w, apply=True, operator_email=email)

    assert status == 1
    assert "cannot be saved" in line and "six" in line, line
    assert router.calls == []
