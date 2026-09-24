"""Create saves a draft; publish is the one way live, behind three setup steps.

Through the REAL routers, the real grant engine and a session on the RLS
application role (`job_setup_api_fixtures`), with every stored fact read back
from a SECOND connection after the request committed or rolled back:

* `POST /jobs` NEVER publishes. `publish: true` is a loud 422 naming the new
  flow, and no job row, no `ratified_at` and no dispatch come of it. It used
  to go live under `create_job` alone, skipping the gate, the JD index and the
  lifecycle.
* The creator is ASSIGNED: a Recruiter who creates a job holds the Recruiter
  assignment on it, which is what every SCOPED cell of RBAC 24 reads, and so
  can publish it. A Recruiter who did not create it cannot, and neither can an
  HR Manager (RBAC 24 NO*).
* Publication names EVERY missing step, in order: the JD, the saved SWOT, the
  saved skills. A generated SWOT nobody saved is not the team's analysis.
* Matching and the JD index are dispatched AFTER the commit: a publish that
  rolls back starts nothing.

MUTATION CHECKS, recorded: dropping `rbac.assign_creator` from `create_job`
makes `test_the_recruiter_who_created_a_job_can_publish_it` fail (403);
replacing `dispatch_after_commit` with the in-request `dispatch` in
`publish_job` makes `test_a_publish_that_rolls_back_dispatches_nothing` fail.
"""
from __future__ import annotations

import uuid

import pytest

from app.api import jobs as jobs_api
from app.models.enums import Role
from app.schemas.jobs import PUBLISH_IS_SEPARATE_DETAIL
from tests import job_setup_api_fixtures as http
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"
SAVED_SKILLS = [
    (MUST, "Python", True, "sutra", None, "Shipped a Python service."),
    (BEHAV, "Ownership", True, "sutra", None, "Owned an incident end to end."),
]
CREATE_BODY = {
    "title": "Platform Engineer",
    "grade": "managerial",
    "experience_min_years": 4,
    "experience_max_years": 8,
    "jd_markdown": fx.JD_MARKDOWN,
}
PUBLISH_TASKS = {"pickready.run_matching", "pickready.index_document"}


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        kwargs.setdefault(
            "extra_users",
            [
                ("recruiter", "recruiter"),
                ("other_recruiter", "recruiter"),
                ("hr_manager", "hr_manager"),
            ],
        )
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await http.drop(w)


async def _job(job_id) -> dict:
    rows = await http.read(
        "SELECT id, lifecycle_state, ratified_at, status::text AS status, created_by "
        "FROM jobs WHERE id = :j",
        j=job_id,
    )
    return rows[0] if rows else {}


async def _make_publishable(world: fx.World, job_id) -> None:
    """The state a saved SWOT and Save Skills leave, written directly: this
    suite is about who may publish and what publication checks, and the two
    steps that produce this state are pinned by their own suites."""
    await http.sql(
        world,
        "INSERT INTO job_swot_analyses (id, tenant_id, job_id, status, strengths, "
        "weaknesses, opportunities, threats, human_edited, version, last_modified_at) "
        "VALUES (:i, :t, :j, 'edited', 'a', 'b', 'c', 'd', true, 1, now())",
        i=uuid.uuid4(), t=world.tenant, j=job_id,
    )
    await http.sql(
        world,
        "UPDATE jobs SET framework_approved_at = now(), "
        "assessment_context_json = CAST(:ctx AS jsonb), "
        "assessment_status = 'ready_for_candidates', lifecycle_state = 'FINALIZED' "
        "WHERE id = :j",
        ctx='{"role_summary": "", "generated_by": "sutra"}', j=job_id,
    )


# ── Create saves a draft ─────────────────────────────────────────────────────


async def test_create_with_publish_true_is_a_422_and_writes_nothing(world) -> None:
    w = await world()
    await http.demo(w)
    await http.company_profile(w, "We run payment rails for small lenders.")
    async with http.api(w) as api:
        response = await api.http.post(http.JOBS, json={**CREATE_BODY, "publish": True})
    assert response.status_code == 422, response.text
    assert PUBLISH_IS_SEPARATE_DETAIL in response.text
    created = await http.read(
        "SELECT id FROM jobs WHERE tenant_id = :t AND title = :title",
        t=w.tenant, title=CREATE_BODY["title"],
    )
    assert created == []
    assert http.recorded("pickready.run_matching") == []


async def test_create_saves_a_draft_assigns_the_creator_and_dispatches_nothing(world) -> None:
    w = await world()
    await http.demo(w)
    await http.company_profile(w, "We run payment rails for small lenders.")
    async with http.api(w) as api:
        api.acting_as("recruiter", Role.recruiter)
        response = await api.http.post(http.JOBS, json=CREATE_BODY)
    assert response.status_code == 201, response.text
    job_id = uuid.UUID(response.json()["id"])
    job = await _job(job_id)
    assert job["lifecycle_state"] == "DRAFT"
    assert job["ratified_at"] is None and job["status"] == "draft"
    assert job["created_by"] == w.users["recruiter"]
    assignments = await http.read(
        "SELECT user_id, assignment_role, active FROM job_assignments WHERE job_id = :j",
        j=job_id,
    )
    assert assignments == [
        {"user_id": w.users["recruiter"], "assignment_role": "recruiter", "active": True}
    ]
    assert http.dispatch.recorded() == []
    audit = await http.read(
        "SELECT actor_role, new_state FROM audit_log WHERE job_id = :j AND action = 'job_created'",
        j=job_id,
    )
    assert len(audit) == 1 and audit[0]["actor_role"] == "recruiter"


async def test_a_super_admin_creator_is_not_assigned(world) -> None:
    """The Super Admin reaches every job in the tenant; no assignment role
    maps to them, so none is written."""
    w = await world()
    await http.demo(w)
    await http.company_profile(w, "We run payment rails for small lenders.")
    async with http.api(w) as api:
        response = await api.http.post(http.JOBS, json=CREATE_BODY)
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    assert await http.read("SELECT 1 FROM job_assignments WHERE job_id = :j", j=job_id) == []


# ── The publish gate ─────────────────────────────────────────────────────────


async def test_publish_names_every_missing_step_in_order(world) -> None:
    w = await world(swot_saved=False)
    await http.sql(w, "UPDATE jobs SET jd_markdown = '## Description' WHERE id = :j", j=w.job)
    async with http.api(w) as api:
        response = await api.http.post(f"{http.JOBS}/{w.job}/publish")
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Before this job can be published, write and save the job description, "
        "save the SWOT analysis and save the skills."
    )
    assert (await _job(w.job))["ratified_at"] is None


async def test_a_generated_swot_nobody_saved_still_blocks(world) -> None:
    w = await world(swot_saved=False, skills=SAVED_SKILLS, saved=True, lifecycle_state="FINALIZED")
    async with http.api(w) as api:
        response = await api.http.post(f"{http.JOBS}/{w.job}/publish")
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Before this job can be published, save the SWOT analysis."
    )


async def test_skills_edited_after_saving_block_publication_again(world) -> None:
    """Save Skills moved the job to FINALIZED, and an edit since cleared the
    saved stamp. The lifecycle alone would let it through; the TABLE does not."""
    w = await world(skills=SAVED_SKILLS, saved=False, lifecycle_state="FINALIZED")
    async with http.api(w) as api:
        response = await api.http.post(f"{http.JOBS}/{w.job}/publish")
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Before this job can be published, save the skills."


async def test_publish_goes_live_and_dispatches_after_the_commit(world) -> None:
    w = await world(skills=SAVED_SKILLS, saved=True, lifecycle_state="FINALIZED")
    async with http.api(w) as api:
        response = await api.http.post(f"{http.JOBS}/{w.job}/publish")
        again = await api.http.post(f"{http.JOBS}/{w.job}/publish")
        setup = (await api.http.get(http.SETUP.format(job=w.job))).json()
    assert response.status_code == 200, response.text
    assert response.json()["public_application_url"]
    job = await _job(w.job)
    assert job["lifecycle_state"] == "PUBLISHED"
    assert job["ratified_at"] is not None
    assert {entry.name for entry in http.dispatch.recorded()} == PUBLISH_TASKS
    assert http.recorded("pickready.index_document")[0].args == ("jd", str(w.job))
    audit = await http.read(
        "SELECT previous_state, new_state FROM audit_log "
        "WHERE job_id = :j AND action = 'job_published'",
        j=w.job,
    )
    assert len(audit) == 1
    assert audit[0]["new_state"] == {"lifecycle_state": "PUBLISHED"}
    # Publishing twice would restart the fixed window: refused.
    assert again.status_code == 409
    assert setup["published"] is True and setup["publish_blocked_reason"] is None


async def test_a_publish_that_rolls_back_dispatches_nothing(world, monkeypatch) -> None:
    w = await world(skills=SAVED_SKILLS, saved=True, lifecycle_state="FINALIZED")

    def _boom(job):
        raise RuntimeError("fails after the dispatches were requested")

    monkeypatch.setattr(jobs_api, "jd_markdown_for", _boom)
    async with http.api(w) as api:
        with pytest.raises(RuntimeError):
            await api.http.post(f"{http.JOBS}/{w.job}/publish")
    assert http.dispatch.recorded() == []
    job = await _job(w.job)
    assert job["ratified_at"] is None and job["lifecycle_state"] == "FINALIZED"


# ── Who may publish ──────────────────────────────────────────────────────────


async def test_the_recruiter_who_created_a_job_can_publish_it(world) -> None:
    w = await world()
    await http.demo(w)
    await http.company_profile(w, "We run payment rails for small lenders.")
    async with http.api(w) as api:
        api.acting_as("recruiter", Role.recruiter)
        created = await api.http.post(http.JOBS, json=CREATE_BODY)
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        await _make_publishable(w, job_id)

        api.acting_as("other_recruiter", Role.recruiter)
        other = await api.http.post(f"{http.JOBS}/{job_id}/publish")
        api.acting_as("hr_manager", Role.hr_manager)
        hr = await api.http.post(f"{http.JOBS}/{job_id}/publish")
        api.acting_as("recruiter", Role.recruiter)
        own = await api.http.post(f"{http.JOBS}/{job_id}/publish")
    assert other.status_code == 403, other.text
    assert hr.status_code == 403, hr.text
    assert own.status_code == 200, own.text
    assert (await _job(job_id))["lifecycle_state"] == "PUBLISHED"


async def test_a_refusal_by_scope_is_not_told_the_setup_steps(world) -> None:
    """Tenant, ceiling, grant and scope refuse FIRST. Only a caller who passes
    all of them and is stopped by the job's STATE is told what is missing."""
    w = await world(swot_saved=False)
    async with http.api(w) as api:
        api.acting_as("other_recruiter", Role.recruiter)
        response = await api.http.post(f"{http.JOBS}/{w.job}/publish")
    assert response.status_code == 403, response.text
    assert "SWOT" not in response.text


async def test_another_tenants_job_is_a_404(world) -> None:
    w = await world(skills=SAVED_SKILLS, saved=True, lifecycle_state="FINALIZED")
    other = await world(skills=SAVED_SKILLS, saved=True, lifecycle_state="FINALIZED")
    async with http.api(w) as api:
        response = await api.http.post(f"{http.JOBS}/{other.job}/publish")
    assert response.status_code == 404
    assert (await _job(other.job))["ratified_at"] is None
