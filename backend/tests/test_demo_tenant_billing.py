"""Demonstration tenants are never refused, and everyone else still is.

Sarkar Corp, ACRM Corp and Specter & Co. are permanent demo companies. They must
behave as fully paid customers forever while billing keeps working normally for
real customers.

The dangerous direction here is NOT "a demo tenant got gated". It is "the
exemption leaked and a paying customer stopped being billed", which produces no
error, no alarm, and no ledger anomaly -- it just quietly stops collecting
money. Every test below therefore has a paying-tenant twin.

These run against the live database (same convention as test_portal), because
the exemption is read with raw SQL from `tenants`, and a mocked session would
prove only that the mock returns what it was told to.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.services import credits

DEMO_IDS = (
    "10000000-0000-4000-8000-000000000001",  # Sarkar Corp
    "10000000-0000-4000-8000-000000000002",  # ACRM Corp
    "10000000-0000-4000-8000-000000000003",  # Specter & Co.
)


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Paying:
    """A throwaway non-demo tenant, driven into deficit."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()


async def _make_paying(factory, fx: _Paying) -> None:
    from app.core.db import superadmin_scope
    from app.models import Tenant

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.id, name=f"Paying {fx.id.hex[:6]}",
                             domain=f"{fx.id}.paying.test"))


async def _drop_paying(factory, fx: _Paying) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM credit_ledger WHERE tenant_id = :t"),
                                {"t": str(fx.id)})
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.id)})


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_id", DEMO_IDS)
async def test_a_demo_tenant_is_flagged(tenant_id: str) -> None:
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as s:
            async with superadmin_scope(s):
                assert await credits.is_demo_tenant(s, uuid.UUID(tenant_id)), (
                    f"{tenant_id} is not marked is_demo; migration 0037 did not "
                    "apply, and this tenant will be billed like a real customer"
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_id", DEMO_IDS)
async def test_a_demo_tenant_always_has_headroom(tenant_id: str) -> None:
    """Even at a negative balance. A demo company that has run assessments has
    a negative ledger like any other, which is exactly the state that must not
    gate it."""
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as s:
            async with superadmin_scope(s):
                assert await credits.has_credit_headroom(s, uuid.UUID(tenant_id))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_id", DEMO_IDS)
async def test_a_demo_tenant_reads_as_unlimited_and_never_in_deficit(
    tenant_id: str,
) -> None:
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as s:
            async with superadmin_scope(s):
                summary = await credits.summarize(s, uuid.UUID(tenant_id))
        assert summary.unlimited is True
        assert summary.in_deficit is False
    finally:
        await engine.dispose()


# ── The twin. The exemption must not leak. ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_paying_tenant_in_deficit_is_still_gated() -> None:
    """The failure this guards against is silent: a leaked exemption raises no
    error and writes no anomaly, it just stops collecting money."""
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Paying()
    try:
        await _make_paying(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    assert not await credits.is_demo_tenant(s, fx.id)
                    # Consume without granting: straight into deficit.
                    await credits.consume(
                        s,
                        tenant_id=fx.id,
                        event_type=credits.EVENT_COMPLETED,
                        idempotency_key=f"test-{fx.id}",
                    )
        async with factory() as s:
            async with superadmin_scope(s):
                assert await credits.balance_subunits(s, fx.id) < 0
                assert not await credits.has_credit_headroom(s, fx.id), (
                    "a paying tenant in deficit was granted headroom; the demo "
                    "exemption has leaked and real customers are not being billed"
                )
                summary = await credits.summarize(s, fx.id)
        assert summary.unlimited is False
        assert summary.in_deficit is True
    finally:
        await _drop_paying(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_usage_is_still_recorded_for_a_demo_tenant() -> None:
    """The requirement is that billing still WORKS for these tenants, not that
    it is switched off. A demo of a billing page with no usage on it
    demonstrates nothing, so the ledger must still be written."""
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    demo = uuid.UUID(DEMO_IDS[0])
    key = f"demo-usage-probe-{uuid.uuid4()}"
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    entry = await credits.consume(
                        s,
                        tenant_id=demo,
                        event_type=credits.EVENT_COMPLETED,
                        idempotency_key=key,
                    )
        assert entry is not None, "a demo tenant's usage was not written"
        # ... and it still does not gate them.
        async with factory() as s:
            async with superadmin_scope(s):
                assert await credits.has_credit_headroom(s, demo)
    finally:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(
                        text("DELETE FROM credit_ledger WHERE idempotency_key = :k"),
                        {"k": key},
                    )
        await engine.dispose()
