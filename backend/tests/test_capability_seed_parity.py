"""The global role_permissions template must agree with the code matrix.

WHY THIS TEST EXISTS (2026-09-01)
---------------------------------
The grant engine reads ROWS; `DEFAULT_PERMISSION_MATRIX` is only what the
rows are supposed to say. The spec-doc6 phase added fifteen capabilities and
the interview_manager role to the CODE and never wrote the seeding migration,
so on a migrations-only database every dashboard control answered 403 for
every role -- and the gap stayed invisible because `tests/test_seed.py` runs
`seed_dev_data` (which reconciles the full matrix) against the shared test
database later in the same session. Forty dashboard tests failed on every
fresh database and passed on every reused one, which read as flakiness for a
whole phase. Migration 0075 repaired the rows; this test is what makes the
NEXT half-done capability batch fail loudly instead of intermittently.

ORDERING CAVEAT, STATED HONESTLY: this file sorts before `test_seed.py`, so
in a full suite run it executes before the dev seed can mask a missing
migration -- that ordering is load-bearing. Against a database that already
ran `seed_dev_data` in an EARLIER session, a missing migration is masked and
this test cannot see it; the canonical `scripts/test.sh` run recreates the
database precisely so this comparison means something.

Skips cleanly when no database is reachable, like every integration test.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.capabilities import DEFAULT_PERMISSION_MATRIX


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable -- skipping seed parity test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_every_granted_capability_has_a_global_template_row() -> None:
    """Every (role, capability) the code matrix grants True resolves to an
    allowed=true GLOBAL row on the migrated database.

    Only True grants are asserted: an absent row and an allowed=false row both
    deny, so a False entry in the matrix has two legitimate representations.
    A True entry has exactly one, and its absence is the defect this test
    exists to catch.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls','on',false)")
            )
            rows = await session.execute(
                text(
                    "SELECT role, capability, allowed FROM role_permissions "
                    "WHERE tenant_id IS NULL"
                )
            )
            template: dict[tuple[str, str], bool] = {}
            for role, capability, allowed in rows:
                # Duplicate global rows are possible (NULLS DISTINCT); any
                # allowed row wins, matching rbac's dict-keyed dedupe.
                key = (str(role), str(capability))
                template[key] = template.get(key, False) or bool(allowed)

        missing: list[str] = []
        for role, grants in DEFAULT_PERMISSION_MATRIX.items():
            for capability, allowed in grants.items():
                if not allowed:
                    continue
                if not template.get((role.value, capability), False):
                    missing.append(f"{role.value}:{capability}")
        assert not missing, (
            "The code matrix grants these but no allowed global "
            "role_permissions row exists; a capability was added without its "
            "seeding migration (write one like 0075): " + ", ".join(sorted(missing))
        )
    finally:
        await engine.dispose()
