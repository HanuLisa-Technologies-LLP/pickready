"""The Final Job Posting: what the recruiter previews and what candidates read
(CONTRACT v10).

* `GET /api/v2/assessments/jobs/{id}/posting-preview` shows the JD, the grade
  as a word, the experience band in words, the company narrative and the
  skills by NAME in three buckets, alphabetical, with nothing else about a
  skill: no evidence line, no priority, no role summary.
* The PUBLIC posting (`GET /api/v1/jobs/public/{id}`) carries NO skill tier
  (owner ruling 2026-11-02); a frozen job's preview shows the snapshot's names,
  whatever the live rows say.
* `/setup` reports the freeze: `frozen`, `frozen_at` and the verbatim
  `frozen_reason`, with `skills_locked` and `grade_locked` equal to it.

Through the real routers on the RLS application role; stored state is written
and read on SECOND connections.

MUTATION CHECK, recorded: putting `skill_buckets` back on the public builder
fails `test_the_public_posting_never_carries_the_skill_tiers`.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.api.deps import get_public_db
from app.core import cache
from app.core.db import superadmin_scope
from app.main import app
from app.services import assessment_contract
from tests import job_setup_api_fixtures as http
from tests import skills_fixtures as fx

MUST, NICE, BEHAV = "must_have", "nice_to_have", "behavioural"
SKILLS = [
    (MUST, "Python data engineering", True, "sutra", None, "Shipped a Python pipeline."),
    (MUST, "Kafka stream processing", True, "sutra", None, "Ran Kafka in production."),
    (NICE, "Airflow orchestration", True, "human", None, None),
    (BEHAV, "Production incident ownership", True, "sutra", None, "Led an incident."),
    (MUST, "A removed skill", False, "sutra", None, None),
]
PREVIEW = "/api/v2/assessments/jobs/{job}/posting-preview"
SETUP = "/api/v2/assessments/jobs/{job}/setup"


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        kwargs.setdefault("skills", SKILLS)
        w = await fx.seed(**kwargs)
        worlds.append(w)
        await cache.invalidate(cache.key("public-job", w.job))
        return w

    yield _make
    for w in worlds:
        # Candidates are platform-level rows the tenant cascade does not reach.
        await http.sql(
            w,
            "DELETE FROM candidates WHERE id IN (SELECT candidate_id FROM "
            "job_candidate_links WHERE job_id = :j)",
            j=w.job,
        )
        await http.drop(w)


async def _publish(w: fx.World) -> None:
    await http.sql(
        w,
        "UPDATE jobs SET ratified_at = now(), posting_start_date = now(), "
        "status = 'ratified', lifecycle_state = 'PUBLISHED' WHERE id = :j",
        j=w.job,
    )


async def _public_db():
    """`get_public_db`'s own scope (bypass, a fresh session) on this test's
    event loop: the app's pooled engine outlives the loop pytest gives each
    test, and a pooled connection from a closed loop cannot be reused."""
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                yield session


async def _public(api, job) -> dict:
    """The public posting, through the real route. Called inside
    `http.api(...)`, which restores the overrides on exit."""
    app.dependency_overrides[get_public_db] = _public_db
    response = await api.http.get(f"{http.JOBS}/public/{job}")
    assert response.status_code == 200, response.text
    return response.json()


def _names(buckets: list[dict]) -> dict[str, list[str]]:
    return {bucket["bucket"]: bucket["names"] for bucket in buckets}


EXPECTED = {
    MUST: ["Kafka stream processing", "Python data engineering"],
    NICE: ["Airflow orchestration"],
    BEHAV: ["Production incident ownership"],
}
LABELS = ["Must-have skills", "Nice-to-have skills", "Behavioural competencies"]


async def test_the_preview_is_the_posting_by_name_and_nothing_hidden(world) -> None:
    w = await world(saved=False)
    async with http.api(w) as api:
        response = await api.http.get(PREVIEW.format(job=w.job))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [bucket["label"] for bucket in body["skill_buckets"]] == LABELS
    assert _names(body["skill_buckets"]) == EXPECTED, "alphabetical, removed rows absent"
    assert body["skills_saved"] is False
    assert body["grade"] == "managerial" and body["grade_label"] == "Managerial"
    assert body["experience_band"] == "5 to 9 years"
    assert body["jd_markdown"] == fx.JD_MARKDOWN
    assert body["about_company"] == "We run payment rails for small lenders."
    assert body["published"] is False and body["public_application_url"] is None
    assert body["publish_blocked_reason"] == "Before this job can be published, save the skills."
    assert (body["frozen"], body["frozen_at"], body["frozen_reason"]) == (False, None, None)
    raw = json.dumps(body).lower()
    for hidden in ("evidence", "priority", "role_summary", "owns the pipelines",
                   "shipped a python pipeline", "force_rank"):
        assert hidden not in raw, f"{hidden!r} crossed into the preview"


def test_no_candidate_facing_schema_declares_the_skill_tiers() -> None:
    from app.schemas.employer_pages import EmployerOpenRoleOut
    from app.schemas.jobs import PublicJobOut
    from app.schemas.portal import PortalJobOut

    for model in (PublicJobOut, PortalJobOut, EmployerOpenRoleOut):
        assert "skill_buckets" not in model.model_fields, model.__name__


async def test_the_public_posting_never_carries_the_skill_tiers(world) -> None:
    """Owner ruling 2026-11-02: must-have, nice-to-have and behavioural skills
    are the recruiting team's. The recruiter's preview keeps them (above)."""
    saved = await world(saved=True)
    await _publish(saved)
    async with http.api(saved) as api:
        public = await _public(api, saved.job)
        preview = (await api.http.get(PREVIEW.format(job=saved.job))).json()
    assert "skill_buckets" not in public
    raw = json.dumps(public).lower()
    for hidden in ("must_have", "nice_to_have", "behavioural", "must-have skills",
                   "python data engineering", "kafka stream processing",
                   "airflow orchestration", "production incident ownership"):
        assert hidden not in raw, f"{hidden!r} crossed into the public posting"
    # The same job still carries every tier for the company.
    assert _names(preview["skill_buckets"]) == EXPECTED


async def test_a_frozen_posting_reads_the_snapshot_and_setup_reports_the_freeze(world) -> None:
    w = await world(saved=True)
    await _publish(w)
    # The freeze an application takes, through the ONE writer of it.
    link = await _application(w)

    async def _freeze(session, _job):
        return await assessment_contract.freeze_at_application(session, w.job, link)

    contract = await fx.run_as(w, _freeze)
    assert contract is not None and contract.locked
    # The live rows moving underneath (never possible through the routes, which
    # refuse) must not move the posting: a frozen job shows its snapshot.
    await http.sql(
        w,
        "UPDATE job_competencies SET name = 'Renamed underneath' WHERE job_id = :j "
        "AND name = 'Airflow orchestration'",
        j=w.job,
    )
    await cache.invalidate(cache.key("public-job", w.job))
    frozen = await fx.frozen_sentence(w)
    async with http.api(w) as api:
        public = await _public(api, w.job)
        preview = (await api.http.get(PREVIEW.format(job=w.job))).json()
        setup = (await api.http.get(SETUP.format(job=w.job))).json()
    assert "skill_buckets" not in public
    assert _names(preview["skill_buckets"]) == EXPECTED
    assert preview["frozen"] is True and preview["frozen_reason"] == frozen
    assert setup["frozen"] is True and setup["frozen_at"] is not None
    assert setup["frozen_reason"] == frozen
    assert setup["skills_locked"] is True and setup["grade_locked"] is True
    assert frozen.startswith(
        "The job description and skills are frozen because a candidate has applied."
    )


async def _application(w: fx.World) -> uuid.UUID:
    """One applied link on the job, written directly (the apply route's own
    freeze is pinned by `tests/test_freeze_at_application.py`)."""
    candidate, profile, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await http.sql(
        w,
        "INSERT INTO candidates (id, full_name, email, consent_databank) "
        "VALUES (:c, 'Preview Applicant', :e, false)",
        c=candidate, e=f"{candidate.hex[:12]}@preview.test",
    )
    await http.sql(
        w,
        "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
        "VALUES (:p, :c, :t, 'Python')",
        p=profile, c=candidate, t=w.tenant,
    )
    await http.sql(
        w,
        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, profile_id, "
        "source, status) VALUES (:l, :t, :j, :c, :p, 'fresh', 'applied')",
        l=link, t=w.tenant, j=w.job, c=candidate, p=profile,
    )
    return link
