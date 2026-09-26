"""`POST /jobs/generate-jd` asks the create gates BEFORE the writer runs.

The credit gate and Gate 1 (the Company Profile) used to run only at
`POST /jobs`, after the model had already been paid to write a draft the
recruiter could then never save. Through the real router and a session on the
RLS application role:

* exhausted credits: 402 with the create gate's own sentence, and the JD
  writer is NEVER reached;
* a Company Profile that says nothing (absent, or whitespace): 409, and the
  writer is never reached;
* a template answer is passed through as `generated_by_ai: false`, never
  relabelled as generation (rule 6).

MUTATION CHECK, recorded: moving `_require_create_gates` below the
`agent_client.generate_jd_document` call makes both refusal tests fail on the
"never reached" assertion.
"""
from __future__ import annotations

import pytest

from app.api import jobs as jobs_api
from tests import job_setup_api_fixtures as http
from tests import skills_fixtures as fx

BRIEF = {
    "title": "Platform Engineer",
    "skills": ["Python", "Kafka"],
    "grade": "managerial",
    "experience_min_years": 4,
    "experience_max_years": 8,
}
URL = f"{http.JOBS}/generate-jd"


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make() -> fx.World:
        w = await fx.seed()
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await http.drop(w)


@pytest.fixture
def writer(monkeypatch):
    """The JD writer as the route reaches it, recording every call."""
    calls: list[dict] = []
    answer: dict = {
        "jd_markdown": fx.JD_MARKDOWN,
        "jd": {"skills": ["Python"]},
        "generated_by_ai": True,
    }

    async def _generate(brief: dict) -> dict:
        calls.append(brief)
        return dict(answer)

    monkeypatch.setattr(jobs_api.agent_client, "generate_jd_document", _generate)
    return calls, answer


async def test_exhausted_credits_refuse_before_the_writer_is_reached(world, writer) -> None:
    calls, _ = writer
    w = await world()
    await http.company_profile(w, "We run payment rails for small lenders.")
    async with http.api(w) as api:
        response = await api.http.post(URL, json=BRIEF)
    assert response.status_code == 402, response.text
    assert response.json()["detail"] == jobs_api.CREDITS_EXHAUSTED_DETAIL
    assert calls == []
    assert await http.read("SELECT id FROM jd_drafts WHERE tenant_id = :t", t=w.tenant) == []


@pytest.mark.parametrize("about", [None, "   "])
async def test_a_blank_company_profile_refuses_before_the_writer_is_reached(
    world, writer, about
) -> None:
    calls, _ = writer
    w = await world()
    await http.demo(w)
    await http.company_profile(w, about)
    async with http.api(w) as api:
        response = await api.http.post(URL, json=BRIEF)
    assert response.status_code == 409, response.text
    assert calls == []


async def test_a_template_answer_is_never_labelled_as_generation(world, writer) -> None:
    calls, answer = writer
    answer["generated_by_ai"] = False
    w = await world()
    await http.demo(w)
    await http.company_profile(w, "We run payment rails for small lenders.")
    async with http.api(w) as api:
        response = await api.http.post(URL, json=BRIEF)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated_by_ai"] is False
    assert body["jd_draft_id"]
    assert len(calls) == 1 and calls[0]["title"] == BRIEF["title"]
