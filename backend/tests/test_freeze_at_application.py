"""The JD and the skills FREEZE at the first genuine application (CONTRACT v10).

Owner ruling v10 (2026-09-28) moved D5's lock point from the first START to
the first GENUINE APPLICATION: a candidate's own application through the ONE
apply path (`POST /portal/jobs/{id}/apply`, the portal and the public
`/apply/{job}` page alike), including a sourced link the candidate converts by
applying. What each test pins:

* the freeze commits WITH the application (a snapshot `source='application'`,
  `locked_by_link_id` the application), read on a SECOND connection, and a
  rolled-back application leaves neither the link nor a snapshot;
* later applications add no snapshot, and the start binds the snapshot the
  application took (the start's lock is the idempotent backstop);
* a legacy job with no saved skills takes the application and freezes
  nothing, and the first application AFTER the skills are saved freezes them;
* a sourced link converted by applying freezes;
* NOTHING ELSE freezes: the single sourced upload, the databank bulk upload
  (both routes, over HTTP), the matching run's databank links (its
  `start_sourced` door) and an invitation (`invite_batch`), each asserted
  from a second connection on a job whose skills are saved, plus an AST sweep
  that the apply route is the one caller;
* erasing the application keeps the snapshot (the ON DELETE SET NULL is the
  ONE update migration 0131's trigger admits), and every direct UPDATE is
  still refused;
* the migration's vocabulary is the model's.

Real Postgres, real candidate sessions (`tests/candidate_session.py`), no
dependency overrides on the candidate side.

MUTATION CHECKS, recorded: removing the `freeze_at_application` call from
`api/portal.apply_to_job` fails
`test_the_first_application_freezes_the_job_in_its_own_transaction` and the
one-caller sweep; committing the session straight after it fails
`test_a_rolled_back_application_leaves_no_snapshot`; dropping the nested
SET NULL branch from 0131's trigger fails
`test_erasing_the_application_keeps_the_snapshot`.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import superadmin_scope, tenant_scope
from app.main import app
from app.models.job_skill_snapshot import SNAPSHOT_SOURCES
from app.services import assessment_contract, assessment_invitations, hiring_pipeline
from tests.application_fixtures import VALIDATION_PAYLOAD
from tests.candidate_session import close_candidate_session, create_candidate_session
from tests.portal_fixtures import (
    drop_employer,
    engine_and_factory,
    fake_asset,
    resume_file,
    row,
    scalar,
    seed_employer,
)

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0131_freeze_at_application.py"

SAVED_SKILLS = (
    ("must_have", "Kafka stream processing", "Ran a Kafka pipeline in production."),
    ("behavioural", "Production incident ownership", "Led an incident to its close."),
)


# ── The world ────────────────────────────────────────────────────────────────


@pytest.fixture
async def world(monkeypatch):
    """An employer with one published job, and a factory for signed-in
    candidates (each cleaned up)."""
    from app.api import portal as portal_mod

    async def fake_store(_resume):
        return fake_asset("freeze")

    monkeypatch.setattr(portal_mod, "store_resume", fake_store)
    engine, factory = engine_and_factory()
    employer = await seed_employer(factory, jobs=1)
    sessions: list = []

    async def _candidate(name: str = "Freeze Applicant"):
        candidate = await create_candidate_session(factory, full_name=name)
        sessions.append(candidate)
        return candidate

    try:
        yield factory, employer, _candidate
    finally:
        await drop_employer(factory, employer)
        for candidate in sessions:
            await close_candidate_session(factory, candidate)
        await engine.dispose()


async def _save_skills(factory, employer) -> None:
    """The state Save Skills leaves: active rows with evidence lines, the
    saved stamp and the hidden context. Written directly, because Save Skills
    is pinned by its own suite and this one is about what freezes it."""
    job_id = employer.job_ids[0]
    for ordinal, (bucket, name, evidence) in enumerate(SAVED_SKILLS, 1):
        await scalar(
            factory,
            "INSERT INTO job_competencies (id, tenant_id, job_id, category, name, "
            "required_level, ordinal, is_active, authored_by, observable_evidence, "
            "description, force_rank) VALUES (:i, :t, :j, :c, :n, 82, :o, true, "
            "'sutra', :e, :e, 1) RETURNING id",
            i=str(uuid.uuid4()), t=str(employer.tenant_id), j=str(job_id),
            c=bucket, n=name, o=ordinal, e=evidence,
        )
    await scalar(
        factory,
        "UPDATE jobs SET framework_approved_at = now(), "
        "assessment_context_json = CAST(:ctx AS jsonb), "
        "assessment_status = 'ready_for_candidates' WHERE id = :j RETURNING id",
        ctx=json.dumps({"role_summary": "Owns the pipelines.", "generated_by": "sutra"}),
        j=str(job_id),
    )


def _apply(candidate, job_id, *, raise_server_exceptions: bool = True):
    with TestClient(
        app, cookies=candidate.cookies(), raise_server_exceptions=raise_server_exceptions
    ) as http:
        return http.post(
            f"/api/v1/portal/jobs/{job_id}/apply",
            data={"validation": VALIDATION_PAYLOAD, "reuse_previous": "false"},
            files={"resume": resume_file()},
            headers=candidate.headers(activity=True),
        )


async def _snapshots(factory, job_id) -> list[dict]:
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, version, source, locked_by_link_id, "
                            "locked_by_conversation_id, digest, skills_json, grade "
                            "FROM job_skill_snapshots WHERE job_id = :j ORDER BY version"
                        ),
                        {"j": str(job_id)},
                    )
                ).mappings().all()
                return [dict(r) for r in rows]


# ── The freeze happens with the application ──────────────────────────────────


async def test_the_first_application_freezes_the_job_in_its_own_transaction(world) -> None:
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    await _save_skills(factory, employer)
    candidate = await candidate_for()

    response = _apply(candidate, job_id)

    assert response.status_code == 201, response.text
    link_id = uuid.UUID(response.json()["link_id"])
    (snapshot,) = await _snapshots(factory, job_id)
    assert snapshot["source"] == "application"
    assert snapshot["locked_by_link_id"] == link_id
    assert snapshot["locked_by_conversation_id"] is None
    assert snapshot["version"] == 1
    assert sorted(skill["name"] for skill in snapshot["skills_json"]) == sorted(
        name for _bucket, name, _evidence in SAVED_SKILLS
    )
    audit = await row(
        factory,
        "SELECT actor_role, application_id, metadata_json AS metadata FROM audit_log "
        "WHERE job_id = :j AND action = 'job_skills_locked'",
        j=str(job_id),
    )
    assert audit["actor_role"] == "candidate"
    assert audit["application_id"] == link_id
    assert audit["metadata"]["source"] == "application"
    assert audit["metadata"]["contract_digest"] == snapshot["digest"]


async def test_a_rolled_back_application_leaves_no_snapshot(world, monkeypatch) -> None:
    """The freeze is IN the application's transaction: a failure after it
    rolls back the link and the snapshot together."""
    from app.api import portal as portal_mod

    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    await _save_skills(factory, employer)
    candidate = await candidate_for()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("the request fails after the freeze")

    monkeypatch.setattr(portal_mod.telemetry_events, "emit", _boom)
    response = _apply(candidate, job_id, raise_server_exceptions=False)

    assert response.status_code == 500
    assert await _snapshots(factory, job_id) == []
    assert await scalar(
        factory, "SELECT count(*) FROM job_candidate_links WHERE job_id = :j", j=str(job_id)
    ) == 0


async def test_later_applications_add_no_snapshot_and_the_start_binds_the_first(world) -> None:
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    await _save_skills(factory, employer)
    first, second = await candidate_for("First Applicant"), await candidate_for("Second")

    assert _apply(first, job_id).status_code == 201
    later = _apply(second, job_id)
    assert later.status_code == 201, later.text
    (snapshot,) = await _snapshots(factory, job_id)

    # The start's lock is the idempotent backstop: it binds the conversation
    # to the snapshot the application took and writes no second version.
    conversation = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO assessment_conversations (id, tenant_id, job_id, "
                        "job_candidate_link_id, grade, status, next_question_index, "
                        "reminders_sent, follow_ups_used, reasks_used, mode) VALUES "
                        "(:i, :t, :j, :l, 'non_managerial', 'active', 0, 0, 0, 0, "
                        "'conversational')"
                    ),
                    {"i": str(conversation), "t": str(employer.tenant_id),
                     "j": str(job_id), "l": later.json()["link_id"]},
                )
                contract = await assessment_contract.lock_contract(
                    session, job_id, conversation
                )
    assert contract.version == 1 and contract.digest == snapshot["digest"]
    assert [s["id"] for s in await _snapshots(factory, job_id)] == [snapshot["id"]]
    bound = await row(
        factory,
        "SELECT skill_snapshot_id, contract_digest FROM assessment_conversations "
        "WHERE id = :i",
        i=str(conversation),
    )
    assert (bound["skill_snapshot_id"], bound["contract_digest"]) == (
        snapshot["id"], snapshot["digest"]
    )


async def test_a_job_without_saved_skills_takes_the_application_and_freezes_later(world) -> None:
    """A legacy published job: the application succeeds and nothing freezes;
    the first genuine application AFTER the skills are saved freezes them."""
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    early, late = await candidate_for("Early Applicant"), await candidate_for("Late")

    assert _apply(early, job_id).status_code == 201
    assert await _snapshots(factory, job_id) == []

    await _save_skills(factory, employer)
    response = _apply(late, job_id)

    assert response.status_code == 201, response.text
    (snapshot,) = await _snapshots(factory, job_id)
    assert snapshot["locked_by_link_id"] == uuid.UUID(response.json()["link_id"])


async def test_a_sourced_link_converted_by_applying_freezes(world) -> None:
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    await _save_skills(factory, employer)
    candidate = await candidate_for("Sourced Then Applied")
    sourced_link = await _sourced_link(factory, employer, candidate.candidate_id)
    assert await _snapshots(factory, job_id) == [], "sourcing froze nothing"

    response = _apply(candidate, job_id)

    assert response.status_code == 201, response.text
    assert uuid.UUID(response.json()["link_id"]) == sourced_link, "converted, not duplicated"
    (snapshot,) = await _snapshots(factory, job_id)
    assert snapshot["locked_by_link_id"] == sourced_link


async def _sourced_link(factory, employer, candidate_id) -> uuid.UUID:
    """A databank link through `hiring_pipeline.start_sourced`, the one door
    the single upload, the bulk upload and the matching run's discovery all
    take to put a person on a job without their applying."""
    from app.models.candidate import SOURCE_TYPE_DATABANK, JobCandidateLink
    from app.models.enums import LinkSource

    async with factory() as session:
        async with session.begin():
            async with tenant_scope(session, employer.tenant_id):
                link = JobCandidateLink(
                    tenant_id=employer.tenant_id,
                    job_id=employer.job_ids[0],
                    candidate_id=candidate_id,
                    source=LinkSource.databank,
                    source_type=SOURCE_TYPE_DATABANK,
                )
                await hiring_pipeline.start_sourced(session, link, remarks="Found in the databank")
                return link.id


# ── Nothing else freezes ─────────────────────────────────────────────────────


async def test_the_sourcing_door_and_an_invitation_never_freeze(world) -> None:
    """`start_sourced` (the single upload, the bulk upload, the matching run's
    discovery) and `invite_batch` on a job whose skills ARE saved: no snapshot.
    The application that preceded the invitation came before the skills were
    saved, so it froze nothing either, and the invitation must not."""
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    applicant, found = await candidate_for("Invited Applicant"), await candidate_for("Found")
    applied = _apply(applicant, job_id)
    assert applied.status_code == 201, applied.text
    await _save_skills(factory, employer)

    await _sourced_link(factory, employer, found.candidate_id)
    assert await _snapshots(factory, job_id) == []

    await scalar(
        factory,
        "UPDATE tenants SET is_demo = true WHERE id = :t RETURNING id",
        t=str(employer.tenant_id),
    )
    recruiter = uuid.uuid4()
    await scalar(
        factory,
        "INSERT INTO users (id, tenant_id, email, full_name, role, status) VALUES "
        "(:u, :t, :e, 'Inviting Recruiter', 'recruiter', 'active') RETURNING id",
        u=str(recruiter), t=str(employer.tenant_id), e=f"{recruiter.hex[:12]}@freeze.test",
    )
    employer.extra_users.append(recruiter)
    async with factory() as session:
        async with session.begin():
            async with tenant_scope(session, employer.tenant_id):
                result = await assessment_invitations.invite_batch(
                    session,
                    tenant_id=employer.tenant_id,
                    job_id=job_id,
                    link_ids=[uuid.UUID(applied.json()["link_id"])],
                    actor_user_id=recruiter,
                )
    assert len(result.invited) == 1, result
    assert await _snapshots(factory, job_id) == [], "an invitation froze the job"


async def test_the_recruiter_upload_routes_never_freeze(monkeypatch) -> None:
    """The single sourced upload and the databank bulk upload, over HTTP on a
    job whose skills are saved: links at `sourced`, no snapshot."""
    from app.services import resume_storage
    from tests import job_setup_api_fixtures as fxapi
    from tests import skills_fixtures as fx
    from tests.test_sourced_links import PDF, _stored

    monkeypatch.setattr(resume_storage, "_upload_or_get_existing", _stored)
    monkeypatch.setattr(resume_storage, "_object_uri", lambda name: f"s3://test-private/{name}")
    w = await fx.seed(
        saved=True,
        skills=[
            ("must_have", "Kafka", True, "sutra", None, "Ran Kafka in production."),
            ("behavioural", "Ownership", True, "sutra", None, "Owned a migration."),
        ],
    )
    try:
        await fxapi.sql(
            w,
            "UPDATE jobs SET ratified_at = now(), status = 'ratified', "
            "lifecycle_state = 'PUBLISHED' WHERE id = :j",
            j=w.job,
        )
        async with fxapi.api(w) as api:
            single = await api.http.post(
                f"/api/v1/candidates/jobs/{w.job}/upload-resume",
                data={"email": f"{uuid.uuid4().hex[:10]}@found.test", "full_name": "Found"},
                files={"file": ("cv.pdf", PDF, "application/pdf")},
            )
            bulk = await api.http.post(
                f"/api/v1/jobs/{w.job}/candidates/databank",
                files=[("files", ("one.pdf", PDF + b"one", "application/pdf"))],
            )
        assert single.status_code == 201, single.text
        assert bulk.status_code == 201, bulk.text
        statuses = await fxapi.read(
            "SELECT status FROM job_candidate_links WHERE job_id = :j", j=w.job
        )
        assert [r["status"] for r in statuses] == ["sourced", "sourced"]
        assert await fxapi.read(
            "SELECT id FROM job_skill_snapshots WHERE job_id = :j", j=w.job
        ) == []
    finally:
        await fxapi.sql(
            w,
            "DELETE FROM candidates WHERE id IN (SELECT candidate_id FROM "
            "job_candidate_links WHERE job_id = :j)",
            j=w.job,
        )
        await fxapi.drop(w)


def test_the_apply_route_is_the_one_caller_of_the_freeze() -> None:
    """A structural pin beside the behaviour: a second caller of the freeze
    would make something that is not an application freeze the job."""
    callers: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and (
                    getattr(inner.func, "attr", None) == "freeze_at_application"
                    or getattr(inner.func, "id", None) == "freeze_at_application"
                ):
                    callers.append(f"{path.relative_to(BACKEND).as_posix()}::{node.name}")
    assert sorted(set(callers)) == ["app/api/portal.py::apply_to_job"], callers


# ── The snapshot outlives the application; nothing rewrites it ──────────────


async def test_erasing_the_application_keeps_the_snapshot(world) -> None:
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    await _save_skills(factory, employer)
    candidate = await candidate_for()
    link_id = _apply(candidate, job_id).json()["link_id"]
    (before,) = await _snapshots(factory, job_id)

    await scalar(
        factory, "DELETE FROM job_candidate_links WHERE id = :l RETURNING id", l=link_id
    )

    (after,) = await _snapshots(factory, job_id)
    assert after["locked_by_link_id"] is None
    assert {k: v for k, v in after.items() if k != "locked_by_link_id"} == {
        k: v for k, v in before.items() if k != "locked_by_link_id"
    }


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE job_skill_snapshots SET locked_by_link_id = NULL WHERE job_id = :j",
        "UPDATE job_skill_snapshots SET role_summary = 'rewritten' WHERE job_id = :j",
    ],
    ids=["clear_the_link_directly", "rewrite_content"],
)
async def test_every_direct_update_of_a_snapshot_is_still_refused(world, statement) -> None:
    """Migration 0131 admits ONE update: the nested SET NULL of the link. A
    statement issued directly, even one that only clears the link, is refused
    exactly as 0118 refused it, twice: the application role holds no UPDATE,
    and the table's owner (which does) meets the trigger."""
    factory, employer, candidate_for = world
    job_id = employer.job_ids[0]
    await _save_skills(factory, employer)
    candidate = await candidate_for()
    assert _apply(candidate, job_id).status_code == 201

    with pytest.raises(DBAPIError) as as_the_app:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(text(statement), {"j": str(job_id)})
    assert "permission denied" in str(as_the_app.value)

    with pytest.raises(DBAPIError) as as_the_owner:
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
                await session.execute(text(statement), {"j": str(job_id)})
    assert "insert-only" in str(as_the_owner.value)
    (snapshot,) = await _snapshots(factory, job_id)
    assert snapshot["locked_by_link_id"] is not None


def test_the_migration_vocabulary_is_the_models() -> None:
    spec = importlib.util.spec_from_file_location("migration_0131", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.SOURCES_AFTER == SNAPSHOT_SOURCES
    assert module.revision == "0131_freeze_at_application"
    assert module.down_revision == "0130_report_immutability"


# ── What a frozen job reports ────────────────────────────────────────────────


def test_the_frozen_sentence_names_the_day_of_the_first_snapshot() -> None:
    frozen_at = datetime(2026, 9, 28, 10, 15, tzinfo=timezone.utc)
    assert assessment_contract.frozen_detail(frozen_at) == (
        "The job description and skills are frozen because a candidate has "
        "applied. Frozen since 28 Sep 2026."
    )

