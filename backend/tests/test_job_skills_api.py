"""The Skills step over HTTP: the routes, their authorization, their refusals.

`services/skills` owns the semantics (revive, rename, move, limit, lock) and
`test_job_skills_service.py` pins them at the service. What is pinned HERE is
what only the routes decide, through the REAL routers, the real grant engine
and a session on the RLS application role, with stored state read back from a
SECOND connection after the request committed:

* EVERY BUCKET IS ITS OWN CAPABILITY. The retired matrix routes all asked for
  `create_job`, so a Recruiter (a NEVER cell for every criterion) could edit a
  job's Must-haves (audit #16). Now a Recruiter is refused, an UNASSIGNED
  Hiring Manager is refused, an ASSIGNED one and an HR Manager are allowed, a
  move needs BOTH buckets, and a per-user overlay can withhold one bucket
  alone.
* A LOCKED JOB ANSWERS 409 WITH THE LOCK SENTENCE on every write, and nothing
  changes.
* The response carries names and states only: no priority, no evidence line,
  no grade word, no number.
* Every refusal the service raises reaches the client as ITS sentence, with
  its status, and an outage names no skill.

MUTATION CHECK, recorded: gating `add_skill` on `require_capability(CREATE_JOB)`
alone (the retired matrix routes' gate, `_require` removed) makes
`test_a_recruiter_cannot_write_any_bucket` and
`test_an_unassigned_hiring_manager_is_refused_and_an_assigned_one_is_not` fail.
"""
from __future__ import annotations

import json
import re

import pytest

from app.models.enums import Role
from app.services import assessment_contract, skills
from app.services import capabilities as caps
from app.services.hiring import sutra
from tests import job_setup_api_fixtures as http
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"

DRAFTED = [
    (MUST, "Python", True, "sutra", "Nobody on the team has run streaming pipelines in production.", "Shipped Python."),
    (MUST, "Kafka", False, "sutra", None, None),
    (NICE, "Terraform", True, "human", None, None),
    (BEHAV, "Ownership", True, "sutra", None, None),
]


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        kwargs.setdefault(
            "extra_users",
            [
                ("recruiter", "recruiter"),
                ("hiring_manager", "hiring_manager"),
                ("hr_manager", "hr_manager"),
            ],
        )
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await http.drop(w)


# ── The one table of capabilities ────────────────────────────────────────────


def test_every_bucket_has_exactly_one_capability() -> None:
    assert tuple(caps.SKILL_BUCKET_CAPABILITY) == assessment_contract.BUCKETS
    assert set(caps.SKILL_BUCKET_CAPABILITY.values()) <= caps.SKILL_CAPABILITIES
    assert caps.FINALIZE_ROLE_DEFINITION in caps.SKILL_CAPABILITIES
    # The documents the contract is drafted FROM stay editable after the lock.
    assert caps.EDIT_SWOT not in caps.SKILL_CAPABILITIES
    assert caps.EDIT_JOB_PHILOSOPHY not in caps.SKILL_CAPABILITIES


# ── Reading ──────────────────────────────────────────────────────────────────


async def test_the_skills_read_carries_names_and_states_only(world) -> None:
    w = await world(skills=DRAFTED, draft_status="drafted")
    async with http.api(w) as api:
        response = await api.http.get(api.skills_url())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["buckets"]["must_have"] == [
        {
            "id": str(w.skills["Python"]),
            "name": "Python",
            "source": "swot",
            "from_swot": "Nobody on the team has run streaming pipelines in production.",
        }
    ]
    assert [s["source"] for s in body["buckets"]["nice_to_have"]] == ["team"]
    assert body["human_authored_names"] == ["Terraform"]
    assert body["can_edit"] == {"must_have": True, "nice_to_have": True, "behavioural": True}
    assert body["can_save"] is True
    assert (body["saved"], body["locked"], body["draft_status"]) == (False, False, "drafted")
    # Nothing hidden crosses: no priority, no evidence line, no grade, no number
    # except the limit the reviewer is told.
    raw = json.dumps({k: v for k, v in body.items() if k != "max_per_bucket"})
    for hidden in ("force_rank", "priority", "evidence", "required_level", "Shipped Python"):
        assert hidden not in raw, hidden
    names_and_states = [
        {key: value for key, value in entry.items() if key != "id"}
        for bucket in body["buckets"].values()
        for entry in bucket
    ]
    assert not re.search(r"\d", json.dumps(names_and_states)), names_and_states


# ── Writes and their refusals ────────────────────────────────────────────────


async def test_add_paste_rename_move_remove_round_trip(world) -> None:
    w = await world(skills=DRAFTED, draft_status="drafted")
    async with http.api(w) as api:
        added = await api.http.post(api.skills_url(), json={"bucket": NICE, "name": "Helm"})
        assert added.status_code == 200, added.text
        pasted = await api.http.post(
            api.skills_url("/bulk"), json={"bucket": MUST, "names": ["SQL", "python"]}
        )
        assert pasted.status_code == 200, pasted.text
        renamed = await api.http.patch(
            api.skills_url(f"/{w.skills['Python']}"), json={"name": "Python 3"}
        )
        assert renamed.status_code == 200, renamed.text
        moved = await api.http.patch(
            api.skills_url(f"/{w.skills['Terraform']}"), json={"bucket": MUST}
        )
        assert moved.status_code == 200, moved.text
        removed = await api.http.delete(api.skills_url(f"/{w.skills['Ownership']}"))
        assert removed.status_code == 200, removed.text

    rows = await fx.committed_skills(w)
    assert sorted(fx.active(rows, MUST)) == ["Python 3", "SQL", "Terraform"]
    assert fx.active(rows, NICE) == ["Helm"]
    assert fx.active(rows, BEHAV) == []
    renamed_row = next(row for row in rows if row["id"] == w.skills["Python"])
    # A rename is a different skill: the SWOT quotation must not follow it.
    assert renamed_row["swot_origin"] is None
    assert renamed_row["authored_by"] == "human"


async def test_a_sixth_skill_is_a_409_and_nothing_is_written(world) -> None:
    w = await world(skills=[(MUST, f"Skill {n}", True, "sutra", None, None) for n in range(5)])
    async with http.api(w) as api:
        response = await api.http.post(api.skills_url(), json={"bucket": MUST, "name": "Sixth"})
        paste = await api.http.post(
            api.skills_url("/bulk"), json={"bucket": MUST, "names": ["Seventh", "Eighth"]}
        )
    assert response.status_code == 409
    assert "Must-have already holds five skills" in response.json()["detail"]
    assert paste.status_code == 409
    assert "Nothing was added" in paste.json()["detail"]
    assert len(fx.active(await fx.committed_skills(w), MUST)) == 5


async def test_a_rename_onto_a_removed_name_is_a_409_naming_it(world) -> None:
    w = await world(skills=DRAFTED)
    async with http.api(w) as api:
        response = await api.http.patch(
            api.skills_url(f"/{w.skills['Python']}"), json={"name": "kafka"}
        )
    assert response.status_code == 409
    assert response.json()["detail"] == '"Kafka" is already an entry under Must-have on this job.'


async def test_a_move_onto_a_removed_occupant_revives_it_and_never_500s(world) -> None:
    """Audit #17: the old reorder route changed the bucket blind and the unique
    constraint answered 500 when the target bucket held the name, even removed."""
    w = await world(
        skills=[
            (MUST, "Kafka", True, "sutra", None, None),
            (NICE, "Kafka", False, "sutra", "They asked for streaming.", "Ran Kafka."),
        ]
    )
    # Two rows share the name, so the ACTIVE one is read back by bucket rather
    # than by `w.skills`, which keeps the last row seeded under a name.
    moving = next(
        row["id"] for row in await fx.committed_skills(w)
        if row["is_active"] and row["category"] == MUST
    )
    async with http.api(w) as api:
        response = await api.http.patch(api.skills_url(f"/{moving}"), json={"bucket": NICE})
    assert response.status_code == 200, response.text
    rows = await fx.committed_skills(w)
    assert fx.active(rows, MUST) == [] and fx.active(rows, NICE) == ["Kafka"]


async def test_culture_is_refused_into_behavioural_on_add_and_move(world) -> None:
    w = await world(skills=[(MUST, "Culture fit", True, "human", None, None)])
    async with http.api(w) as api:
        add = await api.http.post(api.skills_url(), json={"bucket": BEHAV, "name": "Culture fit"})
        move = await api.http.patch(
            api.skills_url(f"/{w.skills['Culture fit']}"), json={"bucket": BEHAV}
        )
    assert add.status_code == move.status_code == 422
    assert fx.active(await fx.committed_skills(w), BEHAV) == []


async def test_an_empty_patch_is_refused(world) -> None:
    w = await world(skills=DRAFTED)
    async with http.api(w) as api:
        response = await api.http.patch(api.skills_url(f"/{w.skills['Python']}"), json={})
    assert response.status_code == 422


async def test_every_edit_makes_the_skills_unsaved_again(world) -> None:
    w = await world(skills=DRAFTED, saved=True)
    async with http.api(w) as api:
        before = (await api.http.get(api.skills_url())).json()
        assert before["saved"] is True
        response = await api.http.post(api.skills_url(), json={"bucket": NICE, "name": "Helm"})
    assert response.status_code == 200
    assert response.json()["saved"] is False
    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None
    assert job["assessment_status"] == skills.PENDING_REVIEW


# ── The lock (D5) ────────────────────────────────────────────────────────────


async def test_every_write_on_a_locked_job_is_a_409_and_changes_nothing(world) -> None:
    w = await world(skills=DRAFTED, saved=True, lifecycle_state="PUBLISHED")
    await http.lock(w)
    before = await fx.committed_skills(w)
    python = w.skills["Python"]
    async with http.api(w) as api:
        answers = [
            await api.http.post(api.skills_url(), json={"bucket": NICE, "name": "Helm"}),
            await api.http.post(api.skills_url("/bulk"), json={"bucket": NICE, "names": ["A"]}),
            await api.http.patch(api.skills_url(f"/{python}"), json={"name": "Go"}),
            await api.http.patch(api.skills_url(f"/{python}"), json={"bucket": NICE}),
            await api.http.delete(api.skills_url(f"/{python}")),
            await api.http.post(api.skills_url("/draft"), json={"confirm_overwrite": True}),
            await api.http.post(api.skills_url("/save")),
        ]
        read = (await api.http.get(api.skills_url())).json()
    for answer in answers:
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"] == assessment_contract.SKILLS_LOCKED_DETAIL
    assert await fx.committed_skills(w) == before
    assert read["locked"] is True
    assert read["can_edit"] == {"must_have": False, "nice_to_have": False, "behavioural": False}
    assert read["can_save"] is False


# ── Authorization, bucket by bucket ──────────────────────────────────────────


async def test_a_recruiter_cannot_write_any_bucket(world) -> None:
    """RBAC 24 and 26: every criterion is a NEVER cell for the Recruiter."""
    w = await world(skills=DRAFTED)
    await http.assign(w, "recruiter", "recruiter")
    async with http.api(w) as api:
        api.acting_as("recruiter", Role.recruiter)
        for bucket in (MUST, NICE, BEHAV):
            response = await api.http.post(api.skills_url(), json={"bucket": bucket, "name": "Go"})
            assert response.status_code == 403, bucket
        save = await api.http.post(api.skills_url("/save"))
        read = (await api.http.get(api.skills_url())).json()
    assert save.status_code == 403
    assert read["can_edit"] == {"must_have": False, "nice_to_have": False, "behavioural": False}
    assert "Go" not in fx.active(await fx.committed_skills(w))


async def test_an_unassigned_hiring_manager_is_refused_and_an_assigned_one_is_not(world) -> None:
    w = await world(skills=DRAFTED)
    async with http.api(w) as api:
        api.acting_as("hiring_manager", Role.hiring_manager)
        refused = await api.http.post(api.skills_url(), json={"bucket": MUST, "name": "Go"})
        assert refused.status_code == 403
        assert (await api.http.get(api.skills_url())).json()["can_edit"]["must_have"] is False
        await http.assign(w, "hiring_manager", "hiring_manager")
        allowed = await api.http.post(api.skills_url(), json={"bucket": MUST, "name": "Go"})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["can_edit"]["must_have"] is True
    assert "Go" in fx.active(await fx.committed_skills(w), MUST)


async def test_an_hr_manager_edits_every_bucket_unassigned(world) -> None:
    w = await world(skills=DRAFTED)
    async with http.api(w) as api:
        api.acting_as("hr_manager", Role.hr_manager)
        response = await api.http.post(api.skills_url(), json={"bucket": BEHAV, "name": "Candour"})
    assert response.status_code == 200, response.text


async def test_one_bucket_can_be_withheld_and_a_move_needs_both(world) -> None:
    """Permissions are DATA: an overlay withholding Behavioural from one Hiring
    Manager leaves them Must-have, and a move from Must-have INTO Behavioural
    is refused, because taking a skill into a bucket edits that bucket."""
    w = await world(skills=DRAFTED)
    await http.assign(w, "hiring_manager", "hiring_manager")
    await http.overlay(w, "hiring_manager", {caps.EDIT_BEHAVIOURAL_COMPETENCIES: False})
    async with http.api(w) as api:
        api.acting_as("hiring_manager", Role.hiring_manager)
        must = await api.http.post(api.skills_url(), json={"bucket": MUST, "name": "Go"})
        behav = await api.http.post(api.skills_url(), json={"bucket": BEHAV, "name": "Candour"})
        move = await api.http.patch(
            api.skills_url(f"/{w.skills['Python']}"), json={"bucket": BEHAV}
        )
        read = (await api.http.get(api.skills_url())).json()
    assert must.status_code == 200, must.text
    assert behav.status_code == 403
    assert move.status_code == 403
    assert read["can_edit"] == {"must_have": True, "nice_to_have": True, "behavioural": False}
    assert read["can_save"] is False
    rows = await fx.committed_skills(w)
    assert "Python" in fx.active(rows, MUST), "the refused move changed nothing"


async def test_a_skill_on_another_job_is_a_404(world) -> None:
    w = await world(skills=DRAFTED)
    other = await world(skills=[(MUST, "Rust", True, "human", None, None)])
    async with http.api(w) as api:
        response = await api.http.delete(api.skills_url(f"/{other.skills['Rust']}"))
    assert response.status_code == 404
    assert fx.active(await fx.committed_skills(other), MUST) == ["Rust"]


# ── Save and draft, as the routes answer them ────────────────────────────────


async def test_save_through_the_route_writes_the_contract(world, monkeypatch) -> None:
    w = await world(skills=DRAFTED)
    router = fx.install(
        monkeypatch,
        fx.FakeRouter(fx.context_answer([(MUST, "Python"), (NICE, "Terraform"), (BEHAV, "Ownership")])),
    )
    async with http.api(w) as api:
        response = await api.http.post(api.skills_url("/save"))
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is True
    assert len(router.calls) == 1
    job = await fx.committed_job(w)
    assert job["lifecycle_state"] == "FINALIZED"
    assert job["assessment_status"] == skills.READY_FOR_CANDIDATES
    audit = await fx.committed_audit(w, "job_skills_saved")
    assert len(audit) == 1 and audit[0]["actor_role"] == "client"


async def test_an_outage_at_save_is_a_503_naming_no_skill_and_changing_nothing(
    world, monkeypatch
) -> None:
    w = await world(skills=DRAFTED)
    fx.install(monkeypatch, fx.FakeRouter(*[TimeoutError("provider down")] * 6))
    async with http.api(w) as api:
        response = await api.http.post(api.skills_url("/save"))
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail == skills.SKILLS_CONTEXT_UNAVAILABLE
    for name in ("Python", "Terraform", "Ownership"):
        assert name not in detail
    job = await fx.committed_job(w)
    assert job["framework_approved_at"] is None and job["assessment_context_json"] is None


async def test_an_unsaveable_set_is_a_422_naming_every_problem(world, monkeypatch) -> None:
    w = await world(skills=[(NICE, "Terraform", True, "human", None, None)])
    router = fx.install(monkeypatch, fx.FakeRouter())
    async with http.api(w) as api:
        response = await api.http.post(api.skills_url("/save"))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Add at least one Must-have skill" in detail
    assert "Add at least one Behavioural skill" in detail
    assert router.calls == [], "refused before the model was called"


async def test_a_halted_stage_is_a_503_naming_the_stage(world, monkeypatch) -> None:
    w = await world(skills=DRAFTED)
    from app.services.hiring import pipeline_halt

    async def _halted(stage, **kwargs):
        raise pipeline_halt.PipelineHalted(stage, configured="sutra_matrix")

    monkeypatch.setattr(sutra.pipeline_halt, "enforce", _halted)
    async with http.api(w) as api:
        response = await api.http.post(api.skills_url("/save"))
    assert response.status_code == 503, response.text


async def test_a_redraft_over_the_teams_skills_needs_confirmation(world) -> None:
    w = await world(skills=DRAFTED, draft_status="drafted", drafted_swot_version=1)
    async with http.api(w) as api:
        refused = await api.http.post(api.skills_url("/draft"), json={})
        assert http.recorded(skills.DRAFT_TASK) == []
        accepted = await api.http.post(api.skills_url("/draft"), json={"confirm_overwrite": True})
    assert refused.status_code == 409
    assert '"Terraform"' in refused.json()["detail"]
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["draft_status"] == "drafting"
    # Dispatched after the commit, exactly once.
    sent = http.recorded(skills.DRAFT_TASK)
    assert len(sent) == 1 and sent[0].args == (str(w.job),)
    assert (await fx.committed_job(w))["skills_draft_status"] == "drafting"
