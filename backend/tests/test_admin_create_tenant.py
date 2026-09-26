"""Onboarding a customer works, and its invitation leaves only after the commit.

TWO DEFECTS, ONE ROUTE (PLAN-p7 WP-B6)
--------------------------------------
`POST /admin/tenants` is the Provider's "create customer" button and it had
no test of any kind, which is how both of these shipped:

* It raised `AttributeError` after the flush. The cache invalidation read
  `body.tenant_id` and `body.entries`, two fields `TenantCreateIn` does not
  have: the line was copied from the permission-template editor, which this
  release deletes. The request answered 500 and rolled the whole tenant
  back, so no customer could be onboarded from the console.
* The client's invitation email was DISPATCHED BEFORE THE COMMIT. A request
  that failed after the dispatch (which the first defect guaranteed) mailed
  an acceptance link for a tenant that was never stored. It now goes through
  `dispatch_after_commit`, so a rolled-back onboarding sends nothing.

Both are asserted against a real database, calling the handler inside a real
transaction, because the second one is a property of COMMIT ORDER and a
response-shape assertion cannot see it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import admin as admin_api
from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.enums import Role
from app.schemas.admin import TenantCreateIn
from app.workers import dispatch as dispatch_module

pytestmark = pytest.mark.asyncio

#: 120 words, inside the 100 to 500 the schema requires.
CULTURE = " ".join(["We ship small changes often and review each other's work."] * 12)


def _body(suffix: str) -> TenantCreateIn:
    return TenantCreateIn(
        name=f"Onboarded Co {suffix}",
        client_email=f"owner-{suffix}@onboarded.example.com",
        industry="Technology",
        culture=CULTURE,
    )


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable, skipping create-tenant tests")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _owner(session) -> CurrentUser:
    actor_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role, email, full_name, status) "
            "VALUES (:id, NULL, 'super_admin', :email, 'Owner', 'active')"
        ),
        {"id": actor_id, "email": f"owner-{actor_id.hex[:8]}@readypick.ai"},
    )
    return CurrentUser(
        user_id=actor_id, tenant_id=None, role=Role.super_admin, audience="owner"
    )


def _invites_sent() -> list:
    return [
        call
        for call in dispatch_module.recorded()
        if call.name == "pickready.send_email" and call.args[2] == "client_invite"
    ]


async def _cleanup(factory, tenant_ids: list[uuid.UUID], actor: CurrentUser) -> None:
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for tenant_id in tenant_ids:
                    await session.execute(
                        text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id}
                    )
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": actor.user_id}
                )


async def test_onboarding_commits_and_invites_after_the_commit() -> None:
    engine, factory = await _factory_or_skip()
    dispatch_module.clear_recorded()
    suffix = uuid.uuid4().hex[:8]
    created: list[uuid.UUID] = []
    actor = None
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    actor = await _owner(session)
                    result = await admin_api.create_tenant(
                        _body(suffix), user=actor, session=session
                    )
                    created.append(result.tenant.id)
                    assert _invites_sent() == [], (
                        "the invitation left before the tenant was committed"
                    )
        # The transaction has committed: exactly one invitation, addressed to
        # the client owner, carrying an acceptance link.
        sent = _invites_sent()
        assert len(sent) == 1
        assert sent[0].args[0] == str(result.tenant.id)
        assert sent[0].args[1] == f"owner-{suffix}@onboarded.example.com"
        assert sent[0].args[3]["invite_link"]

        # Read back from a SECOND session: the rows are durable.
        async with factory() as reader:
            async with reader.begin():
                async with superadmin_scope(reader):
                    row = (
                        await reader.execute(
                            text(
                                "SELECT t.name, u.role, u.status FROM tenants t "
                                "JOIN users u ON u.tenant_id = t.id WHERE t.id = :id"
                            ),
                            {"id": result.tenant.id},
                        )
                    ).one()
                    grants = (
                        await reader.execute(
                            text(
                                "SELECT count(*) FROM role_permissions "
                                "WHERE tenant_id = :id"
                            ),
                            {"id": result.tenant.id},
                        )
                    ).scalar_one()
        assert row.name == f"Onboarded Co {suffix}"
        assert row.role == "client" and row.status == "invited"
        assert grants > 0, "the tenant's permission rows were not seeded"
    finally:
        if actor is not None:
            await _cleanup(factory, created, actor)
        dispatch_module.clear_recorded()
        await engine.dispose()


async def test_a_rolled_back_onboarding_sends_no_invitation() -> None:
    engine, factory = await _factory_or_skip()
    dispatch_module.clear_recorded()
    suffix = uuid.uuid4().hex[:8]
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    actor = await _owner(session)
                    await admin_api.create_tenant(
                        _body(suffix), user=actor, session=session
                    )
                await session.rollback()
        assert _invites_sent() == [], "an invitation for a tenant that was never stored"
    finally:
        dispatch_module.clear_recorded()
        await engine.dispose()
