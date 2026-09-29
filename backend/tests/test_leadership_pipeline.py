"""Leadership Intelligence through the hiring pipeline (spec 20 to 22, 34.8).

For the saved leadership sources, over a real database with only the model
doubled at the router boundary:

* Save Skills gives Sutra's hidden context the leadership lines, freezes the
  context as a `job_leadership_contexts` row with where each saved skill came
  from, names it on the job, and the contract carries it and hashes it;
* with no leadership input nothing is written, no key is sent, and the
  contract digest is exactly the three-part formula it always was;
* the first application freezes the context onto the snapshot, and a CEO
  editing leadership AFTER the freeze moves neither the contract's leadership
  nor its digest, while the live resolution sees the new version;
* Yukti reads leadership needs from the CONTRACT and names the context in its
  provenance; question generation gives a leadership-sourced slot the frozen
  expectations and no other slot any; and the PRISM plan names the same
  frozen version.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import assessment_contract, skills
from app.services.leadership import context as leadership_context
from tests import leadership_fixtures as lf
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"

SKILLS = [
    (MUST, "Kafka stream processing", True, "sutra", None, None),
    (BEHAV, "Production incident ownership", True, "sutra", None, None),
]
ACTIVE = [(bucket, name) for bucket, name, *_ in SKILLS]


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**{"skills": SKILLS, **kwargs})
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


async def _source(w: fx.World, name: str, source: str) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE job_competencies SET provenance_json = CAST(:p AS jsonb) "
                        "WHERE id = :i"
                    ),
                    {"p": json.dumps({"generated_by": "sutra", "source": source}), "i": w.skills[name]},
                )


async def _with_leadership(w: fx.World) -> dict[str, uuid.UUID]:
    engineering = await lf.add_department(w, "Engineering")
    await lf.set_job_department(w, engineering, "Engineering")
    ceo = await lf.add_leader(w, "ceo")
    head = await lf.add_leader(w, "functional_head", department_id=engineering)
    await lf.save(w, ceo, company=lf.CEO_LINE)
    await lf.save(w, head, department=lf.FH_LINE)
    await _source(w, "Production incident ownership", "leadership_ceo")
    await _source(w, "Kafka stream processing", "leadership_functional_head")
    return {"engineering": engineering, "ceo": ceo, "head": head}


def _save(w: fx.World):
    return lambda s, j: skills.save(s, j, actor_user_id=w.client, actor_role="client")


async def _contract(w: fx.World):
    return await fx.run_as(
        w, lambda s, j: assessment_contract.load_contract(s, j.id), commit=False
    )


async def _contexts(w: fx.World) -> list[dict]:
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            rows = (
                await session.execute(
                    text("SELECT * FROM job_leadership_contexts WHERE job_id = :j ORDER BY version"),
                    {"j": w.job},
                )
            ).mappings().all()
            return [dict(row) for row in rows]


async def test_save_skills_freezes_the_leadership_context_it_used(world, monkeypatch) -> None:
    w = await world()
    people = await _with_leadership(w)
    router = fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    sent = router.payload()["leadership_context"]
    assert {line["source"] for line in sent} == {"leadership_ceo", "leadership_functional_head"}
    (row,) = await _contexts(w)
    assert row["version"] == 1
    assert row["department_id"] == people["engineering"]
    assert row["ceo_profile_id"] is not None and row["functional_head_profile_id"] is not None
    assert row["md_profile_id"] is None
    assert row["skill_sources_json"] == {
        str(w.skills["Production incident ownership"]): "leadership_ceo",
        str(w.skills["Kafka stream processing"]): "leadership_functional_head",
    }
    job = await fx.committed_job(w)
    assert job["assessment_context_json"]["leadership_context_id"] == str(row["id"])

    contract = await _contract(w)
    assert contract.leadership is not None
    assert contract.leadership.context_id == row["id"]
    assert contract.leadership.is_leadership_skill(w.skills["Production incident ownership"])
    assert contract.digest == assessment_contract.compute_digest(
        contract.skills, contract.role_summary, contract.grade, row["digest"]
    )
    assert contract.digest != assessment_contract.compute_digest(
        contract.skills, contract.role_summary, contract.grade
    )
    assert job["assessment_context_json"]["skills_digest"] == contract.digest


async def test_missing_leadership_changes_nothing(world, monkeypatch) -> None:
    w = await world()
    router = fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    assert "leadership_context" not in router.payload()
    assert "leadership_context" not in router.calls[0][1][0]["content"]
    assert await _contexts(w) == []
    job = await fx.committed_job(w)
    assert "leadership_context_id" not in job["assessment_context_json"]
    contract = await _contract(w)
    assert contract.leadership is None
    # The three-part formula, byte for byte what it was before 0135.
    assert contract.digest == assessment_contract.compute_digest(
        contract.skills, contract.role_summary, contract.grade
    )


async def _freeze(w: fx.World) -> None:
    """The first genuine application's freeze, on a link the test writes."""
    link = uuid.uuid4()
    candidate = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, consent_databank) "
                        "VALUES (:c, :t, 'Test Applicant', :e, false)"
                    ),
                    {"c": candidate, "t": w.tenant, "e": f"{candidate.hex[:10]}@lead.test"},
                )
                await session.execute(
                    text(
                        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
                        "source, status) VALUES (:l, :t, :j, :c, 'fresh', 'applied')"
                    ),
                    {"l": link, "t": w.tenant, "j": w.job, "c": candidate},
                )
                await assessment_contract.freeze_at_application(session, w.job, link)


async def test_a_leadership_edit_after_the_freeze_moves_no_contract(world, monkeypatch) -> None:
    """Rule 37.9: the snapshot names the frozen context, so a new CEO version
    changes the LIVE resolution and nothing a candidate is assessed against."""
    w = await world()
    people = await _with_leadership(w)
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))
    await fx.run_as(w, _save(w))
    await _freeze(w)
    frozen = await _contract(w)
    assert frozen.locked and frozen.leadership is not None
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            snapshot_context = (
                await session.execute(
                    text("SELECT leadership_context_id FROM job_skill_snapshots WHERE job_id = :j"),
                    {"j": w.job},
                )
            ).scalar_one()
    assert snapshot_context == frozen.leadership.context_id

    await lf.save(w, people["ceo"], company=lf.MD_LINE)

    after = await _contract(w)
    assert after.digest == frozen.digest
    assert after.leadership.context_id == frozen.leadership.context_id
    assert [line.text for line in after.leadership.context.lines] == [
        line.text for line in frozen.leadership.context.lines
    ]
    live = await fx.run_as(
        w, lambda s, j: leadership_context.resolve_for_job(s, j), commit=False
    )
    assert lf.MD_LINE in [line.text for line in live.lines]
    assert lf.MD_LINE not in [line.text for line in after.leadership.context.lines]


async def test_yukti_reads_leadership_needs_from_the_contract(world, monkeypatch) -> None:
    from app.services.yukti import config as yukti_config
    from app.services.yukti import inputs as yukti_inputs
    from app.services.yukti import scoring as yukti_scoring

    w = await world()
    await _with_leadership(w)
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))
    await fx.run_as(w, _save(w))

    ctx = await fx.run_as(w, lambda s, j: yukti_inputs.job_context(s, j), commit=False)
    leadership_needs = [n for n in ctx.needs if n.source in yukti_config.LEADERSHIP_NEED_SOURCES]
    assert {n.text for n in leadership_needs} == {lf.CEO_LINE, lf.FH_LINE}
    # The SWOT's own needs are still there, and the whole stays within the cap.
    assert [n for n in ctx.needs if n.source in yukti_config.SWOT_NEED_SOURCES]
    assert len(ctx.needs) <= yukti_config.MAX_NEEDS
    assert [n.ref for n in ctx.needs] == [f"n{i}" for i in range(1, len(ctx.needs) + 1)]
    provenance = yukti_scoring._base_provenance(ctx, model_id="m", prompt_version="p")
    contract = await _contract(w)
    assert provenance["leadership_context_id"] == str(contract.leadership.context_id)
    assert provenance["leadership_digest"] == contract.leadership.digest


def _slot(skill, index: int):
    from app.services.assessment_formats import composition

    return composition.Slot(
        index=index,
        competency_id=skill.id,
        category=skill.bucket,
        skill_name=skill.name,
        question_type="short_answer",
        planned_family="prose",
        weight=1.0,
        time_allocation_seconds=180,
    )


async def test_question_generation_sees_leadership_only_on_its_skills(world, monkeypatch) -> None:
    from app.services.assessment_questions import generate

    w = await world()
    await _with_leadership(w)
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))
    await fx.run_as(w, _save(w))
    contract = await _contract(w)
    by_name = {skill.name: skill for skill in contract.skills}
    skills_by_id = {skill.id: skill for skill in contract.skills}

    incident = generate._slot_payload(
        _slot(by_name["Production incident ownership"], 0), skills_by_id, contract, {}
    )
    assert incident["leadership_expectations"] == [lf.CEO_LINE]
    kafka = generate._slot_payload(
        _slot(by_name["Kafka stream processing"], 1), skills_by_id, contract, {}
    )
    assert kafka["leadership_expectations"] == [lf.FH_LINE]


async def test_question_generation_carries_no_leadership_key_without_it(world, monkeypatch) -> None:
    from app.services.assessment_questions import generate

    w = await world()
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))
    await fx.run_as(w, _save(w))
    contract = await _contract(w)
    skill = contract.skills[0]
    payload = generate._slot_payload(_slot(skill, 0), {skill.id: skill}, contract, {})
    assert "leadership_expectations" not in payload


async def test_the_prism_plan_names_the_same_frozen_version(world, monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services.assessment_pipeline import composition as pipeline_composition

    w = await world()
    await _with_leadership(w)
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))
    await fx.run_as(w, _save(w))
    await _freeze(w)
    contract = await _contract(w)
    miti = SimpleNamespace(contract=contract)
    dimensions = [
        {"name": "Kafka stream processing", "grade": "Matching"},
        {"name": "Production incident ownership", "grade": "Not Matching"},
    ]
    payload, nodes = pipeline_composition.leadership_plan(miti, dimensions, [])
    assert {skill["name"]: skill["demonstrated"] for skill in payload["skills"]} == {
        "Kafka stream processing": True,
        "Production incident ownership": False,
    }
    assert all(
        node.locators[0].startswith(f"job_leadership_contexts:{contract.leadership.context_id}#")
        for node in nodes
    )
    (row,) = await _contexts(w)
    assert contract.leadership.version == row["version"]
