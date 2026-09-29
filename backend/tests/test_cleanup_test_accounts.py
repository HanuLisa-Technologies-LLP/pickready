"""The test-account cleanup: dry run by default, an allowlist, and refusals.

Real Postgres. `within` narrows the run to the rows this test made, so the
suite's other tenants are never candidates for removal; every other decision
(the allowlist, the demo guard, the environment check, the cascade through
`services/tenant_deletion`) is the command's own, and the result is read back
from a second connection.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.company_registration import CompanyRegistration
from app.models.enums import Role, UserStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.scripts import cleanup_test_accounts as cleanup

ENV = get_settings().environment


async def test_it_refuses_without_the_environment_name() -> None:
    for wrong in (None, "", "definitely-not-" + ENV):
        status, lines = await cleanup.run(
            environment=wrong, keep_emails=["a@b.com"], include_demo_tenants=[], apply=True
        )
        assert status == 2 and lines[0].startswith("REFUSED")


async def test_it_refuses_an_empty_allowlist() -> None:
    status, lines = await cleanup.run(
        environment=ENV, keep_emails=["  "], include_demo_tenants=[], apply=True
    )
    assert status == 2 and "keep-email" in lines[0]


@pytest.fixture
async def world(monkeypatch):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable at DATABASE_URL")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: factory)
    marker = uuid.uuid4().hex[:8]
    ids = {name: uuid.uuid4() for name in ("kept", "disposable", "demo")}
    registration_ids = {"kept": uuid.uuid4(), "disposable": uuid.uuid4()}
    async with factory() as session, session.begin():
        async with superadmin_scope(session):
            for name, tenant_id in ids.items():
                session.add(Tenant(
                    id=tenant_id, name=f"Cleanup {name} {marker}",
                    domain=f"{name}-{marker}.cleanup.local", is_demo=(name == "demo"),
                ))
            await session.flush()
            for name, tenant_id in ids.items():
                session.add(User(
                    tenant_id=tenant_id, role=Role.client, status=UserStatus.active,
                    email=f"{name}-{marker}@cleanup-{marker}.com",
                ))
            session.add(CompanyRegistration(
                id=registration_ids["kept"], email=f"kept-{marker}@cleanup-{marker}.com",
                first_name="K", last_name="K", phone="+919876543210",
                company_name="Kept", industry="Technology", status="expired",
            ))
            session.add(CompanyRegistration(
                id=registration_ids["disposable"],
                email=f"gone-{marker}@cleanup-{marker}.com",
                first_name="G", last_name="G", phone="+919876543210",
                company_name="Gone", industry="Technology", status="expired",
            ))
    within = set(ids.values()) | set(registration_ids.values())
    try:
        yield factory, marker, ids, registration_ids, within
    finally:
        async with factory() as session, session.begin():
            async with superadmin_scope(session):
                await session.execute(CompanyRegistration.__table__.delete().where(
                    CompanyRegistration.id.in_(registration_ids.values())))
                await session.execute(User.__table__.delete().where(User.tenant_id.in_(ids.values())))
                await session.execute(Tenant.__table__.delete().where(Tenant.id.in_(ids.values())))
        await engine.dispose()


async def _present(factory, ids) -> set[str]:
    async with factory() as session, session.begin():
        async with superadmin_scope(session):
            found = set((await session.execute(
                select(Tenant.id).where(Tenant.id.in_(ids.values()))
            )).scalars().all())
    return {name for name, tenant_id in ids.items() if tenant_id in found}


async def test_a_dry_run_counts_and_writes_nothing(world) -> None:
    factory, marker, ids, _regs, within = world
    status, lines = await cleanup.run(
        environment=ENV, keep_emails=[f"KEPT-{marker}@cleanup-{marker}.com"],
        include_demo_tenants=[], apply=False, within=within,
    )
    assert status == 0
    assert "tenants_to_remove=1" in lines
    assert "tenants_kept_by_email=1" in lines
    assert "demo_tenants_skipped=1" in lines
    assert "registrations_to_remove=1" in lines
    assert lines[-1].startswith("DRY RUN")
    # Counts only: no name and no address reaches the output.
    assert not any(marker in line for line in lines)
    assert await _present(factory, ids) == {"kept", "disposable", "demo"}


async def test_apply_removes_only_what_the_allowlist_and_the_demo_guard_allow(world) -> None:
    factory, marker, ids, regs, within = world
    status, lines = await cleanup.run(
        environment=ENV, keep_emails=[f"kept-{marker}@cleanup-{marker}.com"],
        include_demo_tenants=[], apply=True, within=within,
    )
    assert status == 0 and "tenants_removed=1" in lines
    assert await _present(factory, ids) == {"kept", "demo"}
    async with factory() as session, session.begin():
        async with superadmin_scope(session):
            left = set((await session.execute(select(CompanyRegistration.id).where(
                CompanyRegistration.id.in_(regs.values())))).scalars().all())
            users = (await session.execute(select(func.count()).select_from(User).where(
                User.tenant_id == ids["disposable"]))).scalar_one()
    assert left == {regs["kept"]} and users == 0

    # A demonstration tenant goes only when it is named.
    status, _lines = await cleanup.run(
        environment=ENV, keep_emails=[f"kept-{marker}@cleanup-{marker}.com"],
        include_demo_tenants=[f"Cleanup demo {marker}"], apply=True, within=within,
    )
    assert status == 0
    assert await _present(factory, ids) == {"kept"}
