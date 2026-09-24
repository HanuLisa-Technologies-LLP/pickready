"""Save Skills: one Sutra call first, one transaction of writes second.

The order is the contract (the 2026-09-23 Save Matrix rule, carried over): every
refusal and the model call finish BEFORE the first row changes, so an outage or
a refusal leaves NOTHING changed, read back from a second connection. An outage
names no skill; a refusal names every refused skill; an edit that lands during
the model call is a 409, never lost.

The model is doubled at the router boundary (`skills_fixtures.FakeRouter`).

"Nothing changed" on an outage is carried by the transaction: the refusal
rolls back whatever the request did. What the ORDER buys is the lock: the
advisory lock is taken only around the writes, so an edit (or a candidate's
start) can land while the model is thinking, and the save must notice.

MUTATION CHECK, recorded: removing the re-read comparison in `skills.save`
(`if _signature(current) != before`) makes
`test_an_edit_during_the_model_call_is_a_409_not_a_lost_edit` fail: the save
then writes a context that describes a set of skills nobody saved.
"""
from __future__ import annotations

import pytest

from app.services import assessment_contract, llm_router, skills
from app.services.hiring import sutra
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"

SKILLS = [
    (MUST, "Kafka stream processing", True, "sutra", fx.SWOT["weaknesses"], None),
    (MUST, "SQL query optimisation", True, "human", None, None),
    (NICE, "Airflow orchestration", True, "sutra", None, None),
    (BEHAV, "Production incident ownership", True, "sutra", None, None),
    (MUST, "Removed skill", False, "sutra", None, None),
]
ACTIVE = [(bucket, name) for bucket, name, active, *_ in SKILLS if active]


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


def _save(w: fx.World):
    return lambda s, j: skills.save(s, j, actor_user_id=w.client, actor_role="client")


async def test_a_save_makes_exactly_one_sutra_call_with_every_name(world, monkeypatch) -> None:
    w = await world()
    router = fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    assert len(router.calls) == 1
    task, _messages = router.calls[0]
    assert task == sutra.CONTEXT_TASK_TYPE
    sent = [(item["bucket"], item["name"]) for item in router.payload()["skills"]]
    assert sorted(sent) == sorted(ACTIVE), "every saved name, and no removed one"


async def test_a_save_writes_the_hidden_context_and_readies_the_job(world, monkeypatch) -> None:
    w = await world()
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    rows = {row["name"]: row for row in await fx.committed_skills(w)}
    for bucket, name in ACTIVE:
        assert rows[name]["observable_evidence"].startswith("Has shipped work"), name
        assert rows[name]["description"] == rows[name]["observable_evidence"]
    assert rows["Kafka stream processing"]["force_rank"] == 1
    assert rows["SQL query optimisation"]["force_rank"] == 2
    assert rows["Production incident ownership"]["force_rank"] == 1
    assert rows["Removed skill"]["observable_evidence"] is None, "a removed row is not written"

    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is not None
    assert job["assessment_status"] == skills.READY_FOR_CANDIDATES
    assert job["lifecycle_state"] == "FINALIZED"
    assert str(job["finalized_by"]) == str(w.client)
    context = job["assessment_context_json"]
    assert context["generated_by"] == "sutra"
    assert context["role_summary"].startswith("Builds and runs")
    assert context["model_id"] and context["prompt_version"]

    # The contract a candidate's start would lock is exactly what was saved.
    async def _contract(session, job_):
        return await assessment_contract.load_contract(session, job_.id)

    contract = await fx.run_as(w, _contract, commit=False)
    assert context["skills_digest"] == contract.digest
    assert {skill.name for skill in contract.skills} == {name for _b, name in ACTIVE}


async def test_the_audit_rows_really_commit_each_in_one_insert(world, monkeypatch) -> None:
    """The 2026-09-20 class: an audit row mutated after its flush is an UPDATE
    the app role cannot run, and it rolls the whole save back at commit."""
    w = await world()
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    (saved,) = await fx.committed_audit(w, "job_skills_saved")
    assert str(saved["actor_user_id"]) == str(w.client)
    assert saved["actor_role"] == "client"
    assert saved["new_state"]["lifecycle_state"] == "FINALIZED"
    (context,) = await fx.committed_audit(w, "job_assessment_context_written")
    assert context["agent_name"] == "sutra"
    assert str(context["actor_user_id"]) == str(w.client)


async def test_an_outage_names_no_skill_and_changes_nothing(world, monkeypatch) -> None:
    w = await world()
    fx.install(
        monkeypatch,
        fx.FakeRouter(
            llm_router.LLMUnavailableError("down"), llm_router.LLMUnavailableError("down")
        ),
    )
    before = await fx.committed_skills(w)

    with pytest.raises(skills.SkillsContextUnavailable) as refused:
        await fx.run_as(w, _save(w))

    assert refused.value.http_status == 503
    assert refused.value.detail == skills.SKILLS_CONTEXT_UNAVAILABLE
    for _bucket, name in ACTIVE:
        assert name not in refused.value.detail, "an outage is not a badly written skill"
    assert await fx.committed_skills(w) == before
    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None
    assert job["lifecycle_state"] == "DRAFT"
    assert await fx.committed_audit(w, "job_skills_saved") == []


async def test_a_refusal_names_every_refused_skill_and_changes_nothing(world, monkeypatch) -> None:
    w = await world()
    refused = [(MUST, "SQL query optimisation"), (NICE, "Airflow orchestration")]
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE, refused=refused)))
    before = await fx.committed_skills(w)

    with pytest.raises(skills.SkillsContextRefused) as error:
        await fx.run_as(w, _save(w))

    assert error.value.http_status == 422
    assert '"SQL query optimisation"' in error.value.detail
    assert '"Airflow orchestration"' in error.value.detail
    assert "Kafka" not in error.value.detail
    assert await fx.committed_skills(w) == before


async def test_a_line_that_stays_unobservable_is_reflected_on_then_refused_by_name(
    world, monkeypatch
) -> None:
    """The loop feeds the rejection back verbatim and asks again; a skill whose
    line is still an adjective after that is named in the refusal."""
    w = await world()

    def _bad(messages):
        answer = fx.context_answer(ACTIVE)
        for entry in answer["skills"]:
            if entry["name"] == "Airflow orchestration":
                entry["evidence_line"] = "Strong Airflow."
        return answer

    router = fx.install(monkeypatch, fx.FakeRouter(_bad, _bad))

    with pytest.raises(skills.SkillsContextRefused) as error:
        await fx.run_as(w, _save(w))

    assert len(router.calls) == 2
    reflection = router.calls[1][1][-1]["content"]
    assert "Airflow orchestration" in reflection
    assert "watched happen" in reflection
    assert error.value.refused == ((NICE, "Airflow orchestration"),)


async def test_a_renamed_skill_is_rejected_never_accepted(world, monkeypatch) -> None:
    """The model never renames a skill; a renamed answer is reflected on."""
    w = await world()

    def _renamed(messages):
        answer = fx.context_answer(ACTIVE)
        answer["skills"][0]["name"] = "Kafka"
        return answer

    router = fx.install(monkeypatch, fx.FakeRouter(_renamed, fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    assert len(router.calls) == 2
    assert "never rename" in router.calls[1][1][-1]["content"]
    assert "Kafka stream processing" in fx.active(await fx.committed_skills(w))


@pytest.mark.parametrize(
    "extra, expected",
    [
        ([(MUST, f"Extra {n}", True, "human", None, None) for n in range(4)],
         "Must-have holds six skills and the limit is five. Remove one before saving."),
        ([], None),
    ],
)
async def test_invalid_skills_are_refused_listing_every_problem_before_any_call(
    world, monkeypatch, extra, expected
) -> None:
    if expected is None:
        # No Behavioural and no Must-have at all.
        w = await world(skills=[(NICE, "Airflow orchestration", True, "sutra", None, None)])
        expected_problems = (
            "Add at least one Must-have skill before saving.",
            "Add at least one Behavioural skill before saving.",
        )
    else:
        w = await world(skills=SKILLS + extra)
        expected_problems = (expected,)
    router = fx.install(monkeypatch, fx.FakeRouter())

    with pytest.raises(skills.SkillsInvalid) as refused:
        await fx.run_as(w, _save(w))

    assert refused.value.http_status == 422
    assert refused.value.problems == expected_problems
    assert router.calls == [], "no model call for skills that cannot be saved"


async def test_an_edit_during_the_model_call_is_a_409_not_a_lost_edit(world, monkeypatch) -> None:
    """The lock is NOT held across the model call, so an edit can land during
    it. The save compares the rows it sent with the rows it would write."""
    w = await world()

    async def _meanwhile(messages):
        await fx.run_as(
            w, lambda s, j: skills.add(s, j, NICE, "Terraform", actor_user_id=w.client)
        )
        return fx.context_answer(ACTIVE)

    fx.install(monkeypatch, fx.FakeRouter(_meanwhile))

    with pytest.raises(skills.SkillsChangedDuringSave) as refused:
        await fx.run_as(w, _save(w))

    assert refused.value.detail == skills.SKILLS_CHANGED_DURING_SAVE
    assert "Terraform" in fx.active(await fx.committed_skills(w)), "the edit survived"
    assert (await fx.committed_job(w))["framework_approved_at"] is None


async def test_a_published_job_is_never_moved_backwards(world, monkeypatch) -> None:
    w = await world(lifecycle_state="PUBLISHED")
    fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    assert (await fx.committed_job(w))["lifecycle_state"] == "PUBLISHED"


async def test_compensation_never_reaches_the_context_call(world, monkeypatch) -> None:
    w = await world(compensation={"ctc_min": 1800000, "ctc_max": 2600000, "currency": "INR"})
    router = fx.install(monkeypatch, fx.FakeRouter(fx.context_answer(ACTIVE)))

    await fx.run_as(w, _save(w))

    # The USER message is what carries the job; the system prompt's own
    # instruction never to mention compensation is not a leak.
    sent = router.calls[0][1][1]["content"].lower()
    for needle in ("1800000", "2600000", "ctc", "compensation", "salary", "inr"):
        assert needle not in sent, needle
