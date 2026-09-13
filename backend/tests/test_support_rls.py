"""Tenant isolation on the support tables, proven at the DATABASE layer.

WHY THIS FILE AND NOT A ROUTE TEST
------------------------------------
A route test proves the handler behaved. This proves the boundary holds when
the handler does not help: every query below is a bare statement with NO tenant
filter in it at all, so what refuses the row is the Postgres policy and nothing
else. That is claude.md rule 1 stated as an assertion rather than as an
intention.

BOTH DIRECTIONS, AND THE WRITE DIRECTION IS THE ONE PEOPLE FORGET
-------------------------------------------------------------------
A `USING` clause hides other tenants' rows on READ. A `WITH CHECK` clause stops
a session WRITING a row attributed to somebody else. A policy carrying only the
first looks completely correct in every read test ever written, and lets tenant
A insert a message into tenant B's thread. So there is a negative WRITE case
here as well as a negative read case.

Modelled on `test_rls.py`, which is this schema's established shape for this,
rather than free-handed SQL. Skips cleanly when no database is reachable.
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
    except Exception:  # noqa: BLE001 -- any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable -- skipping support RLS test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory):
    """Two tenants, one support thread with one message each."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    thread_a, thread_b = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for tid, name in (
                    (tenant_a, "Support-RLS-A"),
                    (tenant_b, "Support-RLS-B"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {"id": str(tid), "name": name, "domain": f"{tid}.support.test"},
                    )
                for thread_id, tid in ((thread_a, tenant_a), (thread_b, tenant_b)):
                    await session.execute(
                        text(
                            "INSERT INTO support_threads "
                            "(id, tenant_id, subject, status) "
                            "VALUES (:id, :tid, :subject, 'open')"
                        ),
                        {
                            "id": str(thread_id),
                            "tid": str(tid),
                            "subject": "Cannot open the billing page",
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO support_messages "
                            "(id, thread_id, tenant_id, author_side, body) "
                            "VALUES (:id, :thread, :tid, 'customer', :body)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "thread": str(thread_id),
                            "tid": str(tid),
                            "body": "The page does not load for anybody here.",
                        },
                    )
    return tenant_a, thread_a, tenant_b, thread_b


async def _cleanup(factory, *tenant_ids) -> None:
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                    {"ids": [str(t) for t in tenant_ids]},
                )  # threads and messages cascade


# ── Reads ────────────────────────────────────────────────────────────────────

async def test_a_tenant_reads_its_own_threads_and_none_of_anothers() -> None:
    """The POSITIVE half matters as much as the negative one.

    A policy that returned nothing to anybody would pass a negative-only test
    perfectly, and the feature would simply be broken instead of leaky.
    """
    engine, factory = await _factory_or_skip()
    tenant_a, thread_a, tenant_b, thread_b = await _seed(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    # Deliberately no WHERE: RLS must do the filtering.
                    rows = (
                        await session.execute(
                            text("SELECT id, tenant_id FROM support_threads")
                        )
                    ).all()
        seen_threads = {uuid.UUID(str(r[0])) for r in rows}
        seen_tenants = {uuid.UUID(str(r[1])) for r in rows}
        assert thread_a in seen_threads, "tenant A must see its own thread"
        assert thread_b not in seen_threads, "RLS breach: A saw B's thread"
        assert tenant_b not in seen_tenants
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()


async def test_a_tenant_cannot_read_anothers_messages() -> None:
    """Asserted separately from threads.

    `support_messages` carries its own `tenant_id` and its own policy precisely
    so it does not depend on the parent's, and a test that only checked threads
    would not notice if the message policy had been left off entirely.
    """
    engine, factory = await _factory_or_skip()
    tenant_a, thread_a, tenant_b, thread_b = await _seed(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    other = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM support_messages "
                                "WHERE thread_id = :t"
                            ),
                            {"t": str(thread_b)},
                        )
                    ).scalar_one()
                    own = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM support_messages "
                                "WHERE thread_id = :t"
                            ),
                            {"t": str(thread_a)},
                        )
                    ).scalar_one()
        assert own == 1, "tenant A must see its own message"
        assert other == 0, "RLS breach: A read B's support message"
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()


# ── Writes ───────────────────────────────────────────────────────────────────

async def test_a_tenant_cannot_write_a_thread_attributed_to_another() -> None:
    """The WITH CHECK half. Without it every read test above still passes and
    tenant A can create rows inside tenant B's account."""
    engine, factory = await _factory_or_skip()
    tenant_a, thread_a, tenant_b, thread_b = await _seed(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    with pytest.raises(Exception) as caught:
                        await session.execute(
                            text(
                                "INSERT INTO support_threads "
                                "(id, tenant_id, subject, status) "
                                "VALUES (:id, :tid, 'planted', 'open')"
                            ),
                            {"id": str(uuid.uuid4()), "tid": str(tenant_b)},
                        )
        # Named rather than accepting any failure: a typo in the SQL would also
        # raise, and would then read as the policy working.
        assert "row-level security" in str(caught.value).lower(), caught.value
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()


async def test_a_tenant_cannot_write_a_message_into_anothers_thread() -> None:
    """The case the message table's own `tenant_id` column exists for.

    Note what is probed: the insert names tenant B's THREAD and tenant B's
    tenant_id, which is the shape a real attempt would take. A policy that
    resolved the boundary by joining to the parent thread would evaluate
    against a row this session cannot see, and the failure mode of that is
    admitting the write.
    """
    engine, factory = await _factory_or_skip()
    tenant_a, thread_a, tenant_b, thread_b = await _seed(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, tenant_a):
                    with pytest.raises(Exception) as caught:
                        await session.execute(
                            text(
                                "INSERT INTO support_messages "
                                "(id, thread_id, tenant_id, author_side, body) "
                                "VALUES (:id, :thread, :tid, 'customer', 'planted')"
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "thread": str(thread_b),
                                "tid": str(tenant_b),
                            },
                        )
        assert "row-level security" in str(caught.value).lower(), caught.value
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()


# ── The platform's own path ──────────────────────────────────────────────────

async def test_the_bypass_scope_sees_every_tenants_threads() -> None:
    """ReadyPick staff read across customers through `superadmin_scope`, which
    `get_superadmin_db` audit-logs on every request.

    Asserted because a policy tightened without this path in mind would leave
    the Provider queue permanently empty, which looks exactly like nobody
    having written in.
    """
    engine, factory = await _factory_or_skip()
    tenant_a, thread_a, tenant_b, thread_b = await _seed(factory)
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = (
                        await session.execute(
                            text("SELECT id FROM support_threads WHERE id = ANY(:ids)"),
                            {"ids": [str(thread_a), str(thread_b)]},
                        )
                    ).all()
        assert {uuid.UUID(str(r[0])) for r in rows} == {thread_a, thread_b}
    finally:
        await _cleanup(factory, tenant_a, tenant_b)
        await engine.dispose()
