"""Blocking, exhaustive tenant-isolation regression suite for Change 8.

Unlike the earlier single-table probe, this seeds a complete resource graph in
two tenants and checks every tenant-scoped product resource named in the
execution brief. A one-connection pool then alternates A/B scopes to reproduce
the warm pooled-connection mechanism that originally looked suspicious.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope


@dataclass(frozen=True)
class TenantGraph:
    tenant: uuid.UUID
    user: uuid.UUID
    job: uuid.UUID
    candidate: uuid.UUID
    profile: uuid.UUID
    link: uuid.UUID
    report: uuid.UUID
    dimension: uuid.UUID
    competency: uuid.UUID
    invite: uuid.UUID
    company: uuid.UUID


RESOURCE_IDS = {
    "tenants": ("id", "tenant"),
    "users": ("id", "user"),
    "jobs": ("id", "job"),
    "candidates": ("id", "candidate"),
    "job_candidate_links": ("id", "link"),
    "functional_skills_reports": ("id", "report"),
    "report_dimensions": ("id", "dimension"),
    "job_competencies": ("id", "competency"),
    "staff_invites": ("id", "invite"),
    "companies": ("id", "company"),
    # Resume access is represented by the tenant-owned profile row.
    "profiles": ("id", "profile"),
}


def _graph() -> TenantGraph:
    return TenantGraph(*(uuid.uuid4() for _ in range(11)))


async def _factory_or_skip():
    # One physical connection is intentional: alternating transactions must
    # reuse it so stale SET LOCAL/GUC state cannot hide.
    engine = create_async_engine(
        get_settings().database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable — skipping cross-tenant integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_graph(session, graph: TenantGraph, label: str) -> None:
    values = {
        name: str(getattr(graph, name))
        for name in TenantGraph.__dataclass_fields__
    }
    values["label"] = label
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:tenant, :label, :domain, 'pending')"
        ),
        {**values, "domain": f"{graph.tenant}.change8.test"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role, email, status) "
            "VALUES (:user, :tenant, 'recruiter', :email, 'active')"
        ),
        {**values, "email": f"{graph.user}@change8.test"},
    )
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
            "VALUES (:job, :tenant, :label, '{}'::jsonb, 'draft')"
        ),
        values,
    )
    await session.execute(
        text(
            "INSERT INTO candidates "
            "(id, tenant_id, full_name, email, consent_databank) "
            "VALUES (:candidate, :tenant, :label, :email, false)"
        ),
        {**values, "email": f"{graph.candidate}@change8.test"},
    )
    await session.execute(
        text(
            "INSERT INTO profiles (id, candidate_id, source_tenant_id, resume_text) "
            "VALUES (:profile, :candidate, :tenant, :label)"
        ),
        values,
    )
    await session.execute(
        text(
            "INSERT INTO job_candidate_links "
            "(id, tenant_id, job_id, candidate_id, profile_id, source) "
            "VALUES (:link, :tenant, :job, :candidate, :profile, 'manual')"
        ),
        values,
    )
    await session.execute(
        text(
            "INSERT INTO functional_skills_reports "
            "(id, tenant_id, job_id, job_candidate_link_id, grade, status, "
            " overall_summary, validation_json, suggested_probes_json, synthesized_at) "
            "VALUES (:report, :tenant, :job, :link, 'A', 'ready', :label, "
            " '{}'::jsonb, '[]'::jsonb, now())"
        ),
        values,
    )
    await session.execute(
        text(
            "INSERT INTO report_dimensions "
            "(id, tenant_id, report_id, category, name, score, remark, ordinal) "
            "VALUES (:dimension, :tenant, :report, 'primary_skill', :label, 90, :remark, 1)"
        ),
        {**values, "remark": label},
    )
    await session.execute(
        text(
            "INSERT INTO job_competencies "
            "(id, tenant_id, job_id, category, name, ordinal) "
            "VALUES (:competency, :tenant, :job, 'primary_skill', :label, 1)"
        ),
        values,
    )
    await session.execute(
        text(
            "INSERT INTO staff_invites "
            "(id, tenant_id, user_id, email, role, token_hash, expires_at) "
            "VALUES (:invite, :tenant, :user, :email, 'recruiter', :token, "
            " now() + interval '1 day')"
        ),
        {
            **values,
            "email": f"{graph.user}@change8.test",
            "token": uuid.uuid4().hex,
        },
    )
    await session.execute(
        text(
            "INSERT INTO companies (id, tenant_id, about_company) "
            "VALUES (:company, :tenant, :label)"
        ),
        values,
    )


async def _seed_pair(factory) -> tuple[TenantGraph, TenantGraph]:
    a, b = _graph(), _graph()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await _seed_graph(session, a, "Change8 Tenant A")
                await _seed_graph(session, b, "Change8 Tenant B")
    return a, b


async def _cleanup(factory, a: TenantGraph, b: TenantGraph) -> None:
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                # Tenant deletion cascades every seeded tenant-owned row.
                await session.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                    {"ids": [str(a.tenant), str(b.tenant)]},
                )
                # candidates/source profiles intentionally have no tenant FK
                # because databank rows can be global; remove our markers.
                await session.execute(
                    text("DELETE FROM profiles WHERE id = ANY(:ids)"),
                    {"ids": [str(a.profile), str(b.profile)]},
                )
                await session.execute(
                    text("DELETE FROM candidates WHERE id = ANY(:ids)"),
                    {"ids": [str(a.candidate), str(b.candidate)]},
                )


async def _assert_scope(session, own: TenantGraph, other: TenantGraph) -> None:
    for table, (column, attribute) in RESOURCE_IDS.items():
        own_id = getattr(own, attribute)
        other_id = getattr(other, attribute)
        rows = (
            await session.execute(
                text(
                    f"SELECT {column} FROM {table} "
                    f"WHERE {column} = ANY(:marker_ids)"
                ),
                {"marker_ids": [str(own_id), str(other_id)]},
            )
        ).scalars().all()
        assert {uuid.UUID(str(value)) for value in rows} == {own_id}, (
            f"{table} exposed a cross-tenant marker"
        )


async def test_every_tenant_resource_isolated_on_interleaved_warm_connection() -> None:
    engine, factory = await _factory_or_skip()
    a, b = await _seed_pair(factory)
    try:
        # Repeated separate transactions over pool_size=1 force A -> B -> A GUC
        # reuse. Any stale tenant value or missing policy fails deterministically.
        for own, other in ((a, b), (b, a), (a, b), (b, a), (a, b), (b, a)):
            async with factory() as session:
                async with session.begin():
                    async with tenant_scope(session, own.tenant):
                        await _assert_scope(session, own, other)
    finally:
        await _cleanup(factory, a, b)
        await engine.dispose()


async def test_tenants_and_users_rls_is_enabled_forced_and_bypass_is_explicit() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity "
                        "FROM pg_class WHERE relname IN ('tenants', 'users')"
                    )
                )
            ).all()
        assert {row[0]: (row[1], row[2]) for row in rows} == {
            "tenants": (True, True),
            "users": (True, True),
        }

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    policies = (
                        await session.execute(
                            text(
                                "SELECT tablename, policyname FROM pg_policies "
                                "WHERE tablename IN ('tenants', 'users')"
                            )
                        )
                    ).all()
        assert set(policies) == {
            ("tenants", "tenants_tenant_isolation"),
            ("users", "users_tenant_isolation"),
        }
    finally:
        await engine.dispose()
