"""`seed_demo_applications` saves a demo job's skills through `skills.save`.

The version this replaces stamped `framework_approved_at` and an empty context
beside the product, with no model call and no audit row, so a demo job read as
saved while nothing had ever been saved. These run the real save over a real
table, with the model doubled at the router boundary (`skills_fixtures`), and
read every outcome from a SECOND connection after the script's own commit.

MUTATION CHECK, recorded: replacing the `skills.save` call in
`_save_demo_skills` with the old two-column stamp fails
`test_a_demo_job_is_saved_through_the_one_save_with_its_audit_rows` (no audit
row, no Sutra context).
"""
from __future__ import annotations

import pytest

from app.scripts import seed_demo_applications as seed
from app.services import llm_router
from tests import skills_fixtures as fx

MUST, BEHAV = "must_have", "behavioural"

SKILLS = [
    (MUST, "Kafka stream processing", True, "sutra", fx.SWOT["weaknesses"], None),
    (BEHAV, "Production incident ownership", True, "sutra", None, None),
]
ACTIVE = [(bucket, name) for bucket, name, active, *_ in SKILLS if active]


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


async def _run(w: fx.World, *, dry_run: bool = False) -> list[str]:
    return await seed._save_every_demo_job(fx.sessions(), [(w.tenant, "Demo")], dry_run)


async def test_a_demo_job_is_saved_through_the_one_save_with_its_audit_rows(
    world, monkeypatch
) -> None:
    w = await world(skills=SKILLS)
    router = fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    (line,) = await _run(w)

    assert ": saved (" in line, line
    assert len(router.calls) == 1, "one context call for the whole save"
    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is not None
    assert job["assessment_context_json"]["generated_by"] == "sutra"
    assert job["lifecycle_state"] == "FINALIZED"
    (saved,) = await fx.committed_audit(w, "job_skills_saved")
    assert str(saved["actor_user_id"]) == str(w.client), "the Super Admin is named"
    (context,) = await fx.committed_audit(w, "job_assessment_context_written")
    assert context["agent_name"] == "sutra"


async def test_a_writer_outage_saves_nothing_and_says_so(world, monkeypatch) -> None:
    w = await world(skills=SKILLS)
    fx.install(
        monkeypatch,
        fx.FakeRouter(
            llm_router.LLMUnavailableError("down"), llm_router.LLMUnavailableError("down")
        ),
    )

    (line,) = await _run(w)

    assert "NOT SAVED (SkillsContextUnavailable)" in line, line
    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None
    assert job["assessment_context_json"] is None
    assert await fx.committed_audit(w, "job_skills_saved") == []


async def test_a_dry_run_writes_nothing_and_calls_no_model(world, monkeypatch) -> None:
    w = await world(skills=SKILLS)
    router = fx.install(monkeypatch, fx.FakeRouter())

    (line,) = await _run(w, dry_run=True)

    assert "would save (" in line, line
    assert router.calls == []
    assert (await fx.committed_job(w))["framework_approved_at"] is None


async def test_a_job_with_no_skills_is_reported_never_invented(world, monkeypatch) -> None:
    w = await world()
    router = fx.install(monkeypatch, fx.FakeRouter())

    (line,) = await _run(w)

    assert "NO SKILLS" in line, line
    assert router.calls == []
    assert await fx.committed_skills(w) == []


async def test_an_incomplete_set_is_left_pending_with_every_problem_named(
    world, monkeypatch
) -> None:
    w = await world(skills=[SKILLS[0]])
    router = fx.install(monkeypatch, fx.FakeRouter())

    (line,) = await _run(w)

    assert "NOT SAVED, left pending" in line and "Behavioural" in line, line
    assert router.calls == []
    assert (await fx.committed_job(w))["framework_approved_at"] is None
