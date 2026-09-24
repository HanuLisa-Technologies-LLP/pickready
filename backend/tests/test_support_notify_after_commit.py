"""A support notification leaves only after its message is committed.

`api/support._notify` dispatched `pickready.notify_support_message` BEFORE
`get_tenant_db` committed (CONTRACT v5 assigned the conversion to stage 3).
Two failures followed from that order, and neither is visible on a response:

* the task reads the thread and the message by id, so it could start before
  either row was visible and find nothing to notify about;
* a request that rolled back after the dispatch mailed staff (or the
  customer) about a message that was never stored.

Both are properties of COMMIT ORDER, so the handlers are called inside a real
transaction on a real database and the `record` dispatch backend is read
before the commit, after it, and after a rollback. The durable rows are read
from a SECOND session.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import support as support_api
from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG, AUDIENCE_OWNER
from app.models.enums import Role
from app.schemas.support import MessageIn, ThreadOpenIn
from app.workers import dispatch as dispatch_module

pytestmark = pytest.mark.asyncio

TASK = "pickready.notify_support_message"


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM support_threads LIMIT 0"))
    except Exception:  # noqa: BLE001 -- any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable, skipping support notification tests")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _notifications() -> list:
    return [call for call in dispatch_module.recorded() if call.name == TASK]


class _World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.customer = uuid.uuid4()
        self.staff = uuid.uuid4()

    def as_customer(self) -> CurrentUser:
        return CurrentUser(
            user_id=self.customer,
            tenant_id=self.tenant,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    def as_staff(self) -> CurrentUser:
        return CurrentUser(
            user_id=self.staff,
            tenant_id=None,
            role=Role.super_admin,
            audience=AUDIENCE_OWNER,
        )


async def _seed(factory) -> _World:
    world = _World()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, :name, :domain, 'pending')"
                    ),
                    {
                        "id": world.tenant,
                        "name": f"Support-Notify-{world.tenant.hex[:8]}",
                        "domain": f"{world.tenant.hex}.notify.test",
                    },
                )
                for uid, tid, role in (
                    (world.customer, world.tenant, "client"),
                    (world.staff, None, "super_admin"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO users "
                            "(id, tenant_id, email, full_name, role, status) "
                            "VALUES (:id, :tid, :email, 'Notify', :role, 'active')"
                        ),
                        {
                            "id": uid,
                            "tid": tid,
                            "email": f"{uid.hex[:12]}@notify.test",
                            "role": role,
                        },
                    )
    return world


async def _teardown(factory, world: _World) -> None:
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": world.staff}
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :id"), {"id": world.tenant}
                )


async def _message_count(factory, thread_id: uuid.UUID) -> int:
    async with factory() as reader:
        async with reader.begin():
            async with superadmin_scope(reader):
                return (
                    await reader.execute(
                        text(
                            "SELECT count(*) FROM support_messages "
                            "WHERE thread_id = :id"
                        ),
                        {"id": thread_id},
                    )
                ).scalar_one()


async def test_every_notification_leaves_after_its_message_commits() -> None:
    engine, factory = await _factory_or_skip()
    world = await _seed(factory)
    dispatch_module.clear_recorded()
    try:
        # The customer opens a thread.
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    opened = await support_api.open_thread(
                        ThreadOpenIn(subject="Invoice copy", body="Where is it?"),
                        user=world.as_customer(),
                        session=session,
                    )
                    assert _notifications() == [], (
                        "the notification left before the message was committed"
                    )
        first = _notifications()
        assert len(first) == 1
        assert list(first[0].args) == [str(opened.id), str(opened.messages[0].id)]

        # Staff answer it through the Provider door.
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    staff_reply = await support_api.provider_reply(
                        opened.id,
                        MessageIn(body="Attached it to the thread."),
                        user=world.as_staff(),
                        session=session,
                    )
                    assert len(_notifications()) == 1
        assert len(_notifications()) == 2
        assert list(_notifications()[1].args) == [str(opened.id), str(staff_reply.id)]

        # The customer replies.
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    reply = await support_api.reply_as_customer(
                        opened.id,
                        MessageIn(body="Thank you."),
                        user=world.as_customer(),
                        session=session,
                    )
                    assert len(_notifications()) == 2
        assert len(_notifications()) == 3
        assert list(_notifications()[2].args) == [str(opened.id), str(reply.id)]

        assert await _message_count(factory, opened.id) == 3
    finally:
        dispatch_module.clear_recorded()
        await _teardown(factory, world)
        await engine.dispose()


async def test_a_rolled_back_message_notifies_nobody() -> None:
    engine, factory = await _factory_or_skip()
    world = await _seed(factory)
    dispatch_module.clear_recorded()
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    opened = await support_api.open_thread(
                        ThreadOpenIn(subject="Never stored", body="Rolled back."),
                        user=world.as_customer(),
                        session=session,
                    )
                await session.rollback()
        assert _notifications() == [], "a notification about a message never stored"
        assert await _message_count(factory, opened.id) == 0
    finally:
        dispatch_module.clear_recorded()
        await _teardown(factory, world)
        await engine.dispose()
