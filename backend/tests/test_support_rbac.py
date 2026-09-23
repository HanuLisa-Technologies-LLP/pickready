"""The two support capabilities exist as ROWS, not only as constants.

WHY THIS FILE EXISTS SEPARATELY FROM `test_capability_seed_parity.py`
-----------------------------------------------------------------------
That test walks `DEFAULT_PERMISSION_MATRIX` and asserts every True grant has a
global row. It cannot see `handle_support_threads`, because that capability is
DELIBERATELY not in the matrix: the matrix is copied into per-tenant rows for
every customer the Owner console creates (`api/admin._seed_permissions`), and
the role holding this one has no tenant.

So it would have been seeded by migration 0093 and defended by nothing, which
is the exact shape of the defect 0075 exists to record: fifteen capabilities in
code, no seeding migration, and every dashboard control answering 403 for a
whole phase. This file is the missing half.

Skips cleanly when no database is reachable, like every integration test here.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.capabilities import (
    DEFAULT_PERMISSION_MATRIX,
    HANDLE_SUPPORT_THREADS,
    OPEN_SUPPORT_THREADS,
)


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable -- skipping support RBAC test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _global_rows(session, capability: str) -> dict[str, bool]:
    rows = await session.execute(
        text(
            "SELECT role, allowed FROM role_permissions "
            "WHERE tenant_id IS NULL AND capability = :cap"
        ),
        {"cap": capability},
    )
    template: dict[str, bool] = {}
    for role, allowed in rows:
        # Duplicate global rows are possible under NULLS DISTINCT; any allowed
        # row wins, matching rbac's dict-keyed dedupe.
        template[str(role)] = template.get(str(role), False) or bool(allowed)
    return template


async def test_the_platform_capability_has_a_global_row() -> None:
    """The one the parity test structurally cannot cover.

    Without this row `has_capability(session, None, super_admin, ...)` resolves
    False, the notification fan-out finds nobody, and a customer's message sits
    in a queue nobody was told about. That failure is silent: an empty
    recipient list and a working system produce the same log line.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            template = await _global_rows(session, HANDLE_SUPPORT_THREADS)
        assert template.get("super_admin") is True, (
            "handle_support_threads has no allowed global row for super_admin; "
            "migration 0093 seeds it, and without it nobody is ever notified"
        )
    finally:
        await engine.dispose()


async def test_the_platform_capability_is_not_in_the_tenant_matrix() -> None:
    """Asserted as an absence, on purpose.

    `DEFAULT_PERMISSION_MATRIX` is copied into per-tenant rows for every new
    customer. A tenantless role listed there would write a meaningless
    tenant-scoped row into every customer forever, and nothing would fail.
    """
    for role, grants in DEFAULT_PERMISSION_MATRIX.items():
        assert HANDLE_SUPPORT_THREADS not in grants, role


async def test_every_customer_role_can_raise_a_ticket() -> None:
    """Including the Interview Manager, who holds the narrowest set here.

    Support is not a recruitment capability. A role that could not report a
    broken screen would have to relay it through a colleague, which is how a
    bug report loses the detail that made it actionable.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            template = await _global_rows(session, OPEN_SUPPORT_THREADS)
        missing = sorted(
            role
            for role in (
                "client",
                "hr_manager",
                "recruitment_manager",
                "recruiter",
                "hiring_manager",
                "interview_manager",
            )
            if template.get(role) is not True
        )
        assert not missing, f"no allowed global row for: {missing}"
    finally:
        await engine.dispose()


async def test_the_candidate_role_cannot_raise_a_ticket() -> None:
    """A candidate is not a customer.

    They have their own portal and their own Updates feed. Admitting them here
    would put candidate-authored text into the one place this release has just
    spent its whole design keeping candidate data out of.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            template = await _global_rows(session, OPEN_SUPPORT_THREADS)
        assert template.get("candidate") is not True, template
    finally:
        await engine.dispose()
