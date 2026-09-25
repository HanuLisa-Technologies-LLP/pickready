"""A real-database world for the ranked table and the AI Matching routes.

One tenant (optionally two), a client super admin, one published job whose
Skills step is saved, three skills, and links written with exactly the Yukti
columns a matching run leaves behind. Everything is inserted with SQL under the
superadmin scope and torn down by deleting the tenant (every row here cascades
from it), so a test reads what a real request reads.

Used by `test_ranked_candidates_api.py`, `test_matching_task_status_tenancy.py`
and `test_yukti_rank_expression.py`.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope

NOW = datetime.now(timezone.utc)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM job_candidate_links LIMIT 0"))
        return True
    except (OSError, sa.exc.SQLAlchemyError):
        return False
    finally:
        await engine.dispose()


def require_database() -> None:
    if not run(_reachable()):
        pytest.skip("no database reachable")


async def execute(query: str, params: dict[str, Any] | None = None) -> None:
    async with sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(sa.text(query), params or {})


def committed(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Rows as a SECOND connection sees them, after the request is over."""

    async def _read() -> list[dict[str, Any]]:
        async with sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(sa.text(query), params or {})
                    return [dict(row) for row in result.mappings().all()]

    return run(_read())


@dataclass
class Tenant:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    #: skill name -> job_competencies.id
    skills: dict[str, uuid.UUID] = field(default_factory=dict)


SKILLS: tuple[tuple[str, str, int], ...] = (
    ("must_have", "Python", 1),
    ("nice_to_have", "Kafka", 1),
    ("behavioural", "Ownership", 1),
)


async def _seed_tenant(tenant: Tenant, *, skills_saved: bool = True) -> None:
    async with sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, status) "
                        "VALUES (:id, :name, :domain, 'active')"
                    ),
                    {
                        "id": str(tenant.id),
                        "name": f"Ranking {tenant.id.hex[:8]}",
                        "domain": f"rk-{tenant.id.hex[:10]}.invalid",
                    },
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                        "VALUES (:id, :tid, :email, 'Meera Pillai', 'client', 'active')"
                    ),
                    {
                        "id": str(tenant.user),
                        "tid": str(tenant.id),
                        "email": f"{tenant.user.hex[:10]}@rk.invalid",
                    },
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO jobs (id, tenant_id, title, jd_json, jd_markdown, "
                        " status, lifecycle_state, assessment_grade, ratified_at, "
                        " posting_start_date, compensation_json, framework_approved_at, "
                        " assessment_context_json, created_at) "
                        "VALUES (:id, :tid, 'Backend Engineer', CAST(:jd AS jsonb), "
                        " '## The role\nBuilds the payments services.', 'ratified', "
                        " 'PUBLISHED', 'non_managerial', :start, :start, "
                        " CAST(:comp AS jsonb), :saved, CAST(:ctx AS jsonb), :start)"
                    ),
                    {
                        "id": str(tenant.job),
                        "tid": str(tenant.id),
                        "jd": json.dumps({"education": "Bachelor's degree in engineering"}),
                        "start": NOW - timedelta(days=10),
                        "comp": json.dumps({"ctc_min": 1500000, "ctc_max": 2500000}),
                        "saved": NOW - timedelta(days=9) if skills_saved else None,
                        "ctx": json.dumps(
                            {"role_summary": "Builds payments services.", "generated_by": "sutra"}
                        )
                        if skills_saved
                        else None,
                    },
                )
                for ordinal, (bucket, name, rank) in enumerate(SKILLS):
                    skill_id = uuid.uuid4()
                    tenant.skills[name] = skill_id
                    await session.execute(
                        sa.text(
                            "INSERT INTO job_competencies (id, tenant_id, job_id, category, "
                            " name, ordinal, force_rank, observable_evidence, authored_by, "
                            " is_active) VALUES (:id, :tid, :job, :cat, :name, :ord, :rank, "
                            " 'Shipped it.', 'human', true)"
                        ),
                        {
                            "id": str(skill_id),
                            "tid": str(tenant.id),
                            "job": str(tenant.job),
                            "cat": bucket,
                            "name": name,
                            "ord": ordinal,
                            "rank": rank,
                        },
                    )


async def _teardown(tenant: Tenant) -> None:
    await execute("DELETE FROM tenants WHERE id = :id", {"id": str(tenant.id)})


def current_digest(tenant: Tenant) -> str:
    """The contract digest the ranked page compares a reading against."""
    from app.services import assessment_contract

    async def _read() -> str:
        async with sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    contract = await assessment_contract.load_contract(session, tenant.job)
                    return contract.digest

    return run(_read())


def add_link(
    tenant: Tenant,
    *,
    name: str,
    status: str = "applied",
    source_type: str = "applied",
    yukti_status: str = "pending",
    pre: float | None = None,
    tags: list[dict[str, Any]] | None = None,
    provenance: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    profile_form: dict[str, Any] | None = None,
    overall: int | None = None,
    must_have_failed: bool = False,
    report: bool | None = None,
    created_days_ago: float = 5,
    read_other_profile: bool = False,
) -> uuid.UUID:
    """One candidate, their profile and their link, as a run left them."""
    candidate, profile, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_profile = uuid.uuid4()
    at = NOW - timedelta(days=created_days_ago)

    async def _insert() -> None:
        async with sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidates (id, tenant_id, email, full_name, "
                            " profile_form_json, created_at) "
                            "VALUES (:id, :tid, :email, :name, CAST(:form AS jsonb), :at)"
                        ),
                        {
                            "id": str(candidate),
                            "tid": str(tenant.id),
                            "email": f"{candidate.hex[:12]}@rk.invalid",
                            "name": name,
                            "form": json.dumps(profile_form) if profile_form is not None else None,
                            "at": at,
                        },
                    )
                    for pid in (profile, other_profile) if read_other_profile else (profile,):
                        await session.execute(
                            sa.text(
                                "INSERT INTO profiles (id, candidate_id, resume_text, "
                                " resume_url, resume_original_filename, resume_mime_type) "
                                "VALUES (:id, :cid, 'Five years of Python services.', "
                                " 's3://private/resumes/x', 'resume.pdf', 'application/pdf')"
                            ),
                            {"id": str(pid), "cid": str(candidate)},
                        )
                    await session.execute(
                        sa.text(
                            "INSERT INTO job_candidate_links (id, tenant_id, job_id, "
                            " candidate_id, profile_id, source, status, source_type, "
                            " validation_json, yukti_status, yukti_pre_score, "
                            " evidence_tags_json, yukti_provenance_json, yukti_profile_id, "
                            " yukti_scored_at, created_at) "
                            "VALUES (:id, :tid, :job, :cid, :pid, :source, :status, :stype, "
                            " CAST(:validation AS jsonb), :ystatus, :pre, CAST(:tags AS jsonb), "
                            " CAST(:prov AS jsonb), :ypid, :scored_at, :at)"
                        ),
                        {
                            "id": str(link),
                            "tid": str(tenant.id),
                            "job": str(tenant.job),
                            "cid": str(candidate),
                            "pid": str(profile),
                            "source": "databank" if source_type == "databank" else "fresh",
                            "status": status,
                            "stype": source_type,
                            "validation": json.dumps(validation) if validation is not None else None,
                            "ystatus": yukti_status,
                            "pre": pre,
                            "tags": json.dumps(tags or []),
                            "prov": json.dumps(provenance) if provenance is not None else None,
                            "ypid": str(other_profile if read_other_profile else profile)
                            if yukti_status != "pending"
                            else None,
                            "scored_at": NOW if yukti_status != "pending" else None,
                            "at": at,
                        },
                    )
                    if report or overall is not None or must_have_failed:
                        await session.execute(
                            sa.text(
                                "INSERT INTO functional_skills_reports (id, tenant_id, job_id, "
                                " job_candidate_link_id, grade, overall_summary, overall_score, "
                                " must_have_failed, synthesized_at) VALUES (:id, :tid, :job, "
                                " :link, 'non_managerial', 'Summary.', :overall, :failed, :at)"
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "tid": str(tenant.id),
                                "job": str(tenant.job),
                                "link": str(link),
                                "overall": overall,
                                "failed": must_have_failed,
                                "at": NOW,
                            },
                        )

    run(_insert())
    return link


@pytest.fixture
def tenant_a() -> Iterator[Tenant]:
    require_database()
    tenant = Tenant()
    run(_seed_tenant(tenant))
    try:
        yield tenant
    finally:
        run(_teardown(tenant))


@pytest.fixture
def tenant_b() -> Iterator[Tenant]:
    require_database()
    tenant = Tenant()
    run(_seed_tenant(tenant))
    try:
        yield tenant
    finally:
        run(_teardown(tenant))


def client_for(tenant: Tenant) -> Iterator[TestClient]:
    """A TestClient signed in as `tenant`'s client super admin, entering the
    database exactly as production does (RLS app role, tenant scope)."""
    from app.api.deps import CurrentUser, get_current_user, get_tenant_db
    from app.core.security import AUDIENCE_ORG
    from app.main import app
    from app.models.enums import Role

    factory = sessions()

    async def _current_user() -> CurrentUser:
        return CurrentUser(
            user_id=tenant.user,
            tenant_id=tenant.id,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    async def _tenant_db():
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant.id):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
