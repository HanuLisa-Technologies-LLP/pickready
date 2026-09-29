"""The Leadership Intelligence AI draft (spec 17, rule 37.11).

The real builder (`leadership.draft.draft_for`) over a real database, with the
model doubled at the router boundary. What it pins:

* compensation never reaches the prompt, from any input the draft reads: the
  Company Profile, the department's job descriptions, the leader's previous
  text (the canary registered in `test_ctc_never_in_prompt`);
* with nothing to draft from, the answer is the fixed empty-state sentence and
  the model is never called (rule 6: no generic statement true of any company);
* a draft naming a personal characteristic, or narrating its own sources, is
  reflected on and re-asked; a model that never produces a usable draft is a
  FAILED state, never a template;
* a Functional Head's draft is for their own department only, and nothing the
  draft does writes a leadership row.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import generation_sufficiency, llm_router
from app.services.leadership import draft, profiles
from tests import leadership_fixtures as lf
from tests import skills_fixtures as fx

SENTINEL = "CTC_SENTINEL_5521"
AMOUNT = "55,21,000"
NEEDLES = (SENTINEL, AMOUNT, "5521000")

HEAD_ANSWER = {
    "company_requirements": "",
    "department_requirements": lf.FH_LINE,
    "ideal_employee_expectations": lf.IDEAL_LINE,
    "department_expectations": [],
}


class Router:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.sent: list[list[dict]] = []

    async def __call__(self, task_type, messages, response_format_json=False, session=None):
        assert task_type == draft.TASK_TYPE
        self.sent.append([dict(message) for message in messages])
        if not self.answers:
            raise AssertionError("the model was called more times than scripted")
        return json.dumps(self.answers.pop(0))


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


async def _company(w: fx.World, about: str) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO companies (id, tenant_id, about_company, work_life) "
                        "VALUES (:i, :t, :a, 'Hybrid, three office days a week.')"
                    ),
                    {"i": uuid.uuid4(), "t": w.tenant, "a": about},
                )


async def _draft(w: fx.World, user_id: uuid.UUID) -> draft.DraftOutcome:
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            author = await profiles.author_of(session, user_id)
            assert author is not None
            return await draft.draft_for(session, author)


async def _head(w: fx.World) -> uuid.UUID:
    engineering = await lf.add_department(w, "Engineering")
    await lf.set_job_department(w, engineering, "Engineering")
    return await lf.add_leader(w, "functional_head", department_id=engineering)


async def test_the_leadership_draft_carries_no_compensation(world, monkeypatch) -> None:
    pay = f"We offer a CTC of {AMOUNT} for senior engineers, {SENTINEL}."
    w = await world(jd_markdown=f"## Description\nRuns the settlement services.\n{pay}")
    head = await _head(w)
    await _company(w, f"We run payment rails for small lenders.\n{pay}")
    await lf.save(w, head, department=f"{lf.FH_LINE}\n{pay}")
    router = Router(HEAD_ANSWER)
    monkeypatch.setattr(llm_router, "chat_completion", router)

    outcome = await _draft(w, head)

    assert outcome.status == "drafted"
    assert router.sent, "the builder never reached the router, so the canary proved nothing"
    sent = json.dumps(router.sent)
    assert not [needle for needle in NEEDLES if needle in sent]
    user = json.loads(router.sent[0][1]["content"])
    assert user["department"] == "Engineering"
    assert {"company_profile", "department_jobs", "previous_version"} <= set(outcome.sources)


async def test_nothing_to_draft_from_is_the_fixed_sentence_and_no_model_call(
    world, monkeypatch
) -> None:
    w = await world()
    ceo = await lf.add_leader(w, "ceo")
    router = Router()
    monkeypatch.setattr(llm_router, "chat_completion", router)

    outcome = await _draft(w, ceo)

    assert outcome.status == "empty"
    assert outcome.message == generation_sufficiency.empty_state_copy("leadership.draft.no_inputs")
    assert router.sent == []
    assert outcome.as_payload()["generated_by_ai"] is False


async def test_an_unsafe_draft_is_reflected_on_and_re_asked(world, monkeypatch) -> None:
    w = await world()
    head = await _head(w)
    unsafe = {
        **HEAD_ANSWER,
        "department_requirements": (
            "The source pack does not establish much. We want young engineers "
            "who have shipped services to production."
        ),
    }
    router = Router(unsafe, HEAD_ANSWER)
    monkeypatch.setattr(llm_router, "chat_completion", router)

    outcome = await _draft(w, head)

    assert outcome.status == "drafted"
    assert len(router.sent) == 2
    reflection = router.sent[1][-1]["content"]
    assert "personal characteristic" in reflection
    assert outcome.fields["department_requirements"] == lf.FH_LINE


async def test_a_draft_that_never_takes_shape_is_failed_not_a_template(world, monkeypatch) -> None:
    w = await world()
    head = await _head(w)
    wrong = {**HEAD_ANSWER, "company_requirements": "We need people who shipped a product."}
    router = Router(wrong, wrong, wrong)
    monkeypatch.setattr(llm_router, "chat_completion", router)

    outcome = await _draft(w, head)

    assert outcome.status == "failed"
    assert outcome.fields == {}
    assert outcome.message


async def test_a_ceo_draft_maps_departments_by_reference_and_writes_nothing(
    world, monkeypatch
) -> None:
    w = await world()
    engineering = await lf.add_department(w, "Engineering")
    await lf.set_job_department(w, engineering, "Engineering")
    ceo = await lf.add_leader(w, "ceo")
    answer = {
        "company_requirements": lf.CEO_LINE,
        "department_requirements": "",
        "ideal_employee_expectations": "",
        "department_expectations": [{"department": "d1", "text": lf.FH_LINE}],
    }
    router = Router(answer)
    monkeypatch.setattr(llm_router, "chat_completion", router)
    before = await lf.leadership_rows(w)

    outcome = await _draft(w, ceo)

    assert outcome.status == "drafted"
    assert outcome.fields["department_expectations"] == {str(engineering): lf.FH_LINE}
    assert json.loads(router.sent[0][1]["content"])["departments"] == [
        {"ref": "d1", "name": "Engineering"}
    ]
    assert await lf.leadership_rows(w) == before
