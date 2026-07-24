"""Database-layer tenant isolation (claude.md rule 1 / ESD §3).

This is the single most important security test in the product: it proves that
the Postgres RLS policy — not the application's WHERE clause — is the tenant
boundary. A session scoped to tenant A must not be able to read tenant B's rows
even with a bare `SELECT` that carries no tenant filter at all.

The test uses the REAL application machinery (`tenant_scope` / `superadmin_scope`
over the app's own engine), so it also exercises the `SET LOCAL ROLE` privilege
drop that makes RLS enforceable when the app connects as a superuser in dev.

It SKIPS cleanly when no database is reachable (pure-unit CI); it runs for real
inside the backend container where Postgres is up.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable — skipping RLS integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_two_tenants_with_a_job_each(factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert two tenants and one job each via the audited bypass scope.
    Returns (tenant_a, job_a, tenant_b, job_b)."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    job_a, job_b = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for tid, name in ((tenant_a, "RLS-Test-A"), (tenant_b, "RLS-Test-B")):
                    await session.execute(
                        text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {"id": str(tid), "name": name, "domain": f"{tid}.rls.test"},
                    )
                for jid, tid in ((job_a, tenant_a), (job_b, tenant_b)):
                    await session.execute(
                        text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                            "VALUES (:id, :tid, :title, '{}'::jsonb, 'draft')"
                        ),
                        {"id": str(jid), "tid": str(tid), "title": "RLS probe"},
                    )
    return tenant_a, job_a, tenant_b, job_b


async def _cleanup(factory, tenant_a, tenant_b) -> None:
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                    {"ids": [str(tenant_a), str(tenant_b)]},
                )  # jobs cascade on tenant delete


async def test_rls_blocks_cross_tenant_read_at_the_database_layer() -> None:
    """With app.tenant_id = A, a bare SELECT over `jobs` returns A's row and
    ZERO of B's — enforced by Postgres, not by an app-level filter."""
    engine, factory = await _factory_or_skip()
    tenant_a, job_a, tenant_b, job_b = await _seed_two_tenants_with_a_job_each(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    # Deliberately NO WHERE clause — RLS must do the filtering.
                    rows = (
                        await session.execute(text("SELECT id, tenant_id FROM jobs"))
                    ).all()
                    seen = {uuid.UUID(str(r[1])) for r in rows}

        assert tenant_a in seen, "tenant A must see its own row"
        assert tenant_b not in seen, "tenant A must NOT see tenant B's rows (RLS breach!)"

        # And the direct count of B's rows from within A's scope is zero.
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    count_b = (
                        await session.execute(
                            text("SELECT count(*) FROM jobs WHERE tenant_id = :b"),
                            {"b": str(tenant_b)},
                        )
                    ).scalar_one()
        assert count_b == 0, "cross-tenant rows must be invisible even when named explicitly"
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()


async def test_rls_scope_sees_own_rows_and_bypass_sees_all() -> None:
    """Sanity companions: A sees A, and the audited bypass scope sees both —
    so the isolation above is real isolation, not a broken (empty) connection."""
    engine, factory = await _factory_or_skip()
    tenant_a, job_a, tenant_b, job_b = await _seed_two_tenants_with_a_job_each(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    count_a = (
                        await session.execute(
                            text("SELECT count(*) FROM jobs WHERE tenant_id = :a"),
                            {"a": str(tenant_a)},
                        )
                    ).scalar_one()
        assert count_a == 1

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    both = (
                        await session.execute(
                            text("SELECT count(*) FROM jobs WHERE tenant_id = ANY(:ids)"),
                            {"ids": [str(tenant_a), str(tenant_b)]},
                        )
                    ).scalar_one()
        assert both == 2, "bypass scope must reach across tenants (owner administration)"
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()


async def test_bypass_write_respects_append_only_audit_grant() -> None:
    """The bypass scope runs as the non-superuser app role, so the append-only
    audit_log grant (no UPDATE/DELETE) is still enforced — proving the bypass is
    a scoped RLS escape hatch, not a return to full superuser power."""
    engine, factory = await _factory_or_skip()
    marker = f"rls-test-{uuid.uuid4()}"
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        text(
                            "INSERT INTO audit_log (id, action, target_type) "
                            "VALUES (:id, :action, 'test')"
                        ),
                        {"id": str(uuid.uuid4()), "action": marker},
                    )
        # UPDATE must be denied for the app role even under bypass.
        with pytest.raises(Exception):
            async with factory() as session:
                async with session.begin():
                    async with superadmin_scope(session):
                        await session.execute(
                            text("UPDATE audit_log SET action = 'tampered' WHERE action = :m"),
                            {"m": marker},
                        )
    finally:
        await engine.dispose()
