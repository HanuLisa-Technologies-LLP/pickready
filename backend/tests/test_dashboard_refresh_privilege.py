"""The dashboard refresh must stay callable by the least-privileged app role.

WHY THIS TEST EXISTS, AND WHY ITS ABSENCE COST A WEEK
------------------------------------------------------
`grep -rl dashboard_job_metrics tests/` returned NOTHING before this file. The
refresh had no coverage at all, so when the 2026-09-11 credential split moved
the application onto `pickready_app`, a NOINHERIT role that owns nothing,
`REFRESH MATERIALIZED VIEW` began raising "must be owner of materialized view
dashboard_job_metrics" on every run and every test still passed. The view did
not refresh once for a week. The only symptom was a CloudWatch alarm whose SNS
subscription was unconfirmed, so it fired into nothing.

That is the exact shape this repository already has a rule about: a timestamp,
or a green suite, is not evidence that work happened. The check has to ask the
database.

WHAT IS PINNED, AND IN BOTH DIRECTIONS
---------------------------------------
A test that only asserted "the function exists" would pass against a function
that is not SECURITY DEFINER, or one PUBLIC can execute, or one whose
`search_path` is mutable. Each is a different defect and the last is a
privilege-escalation primitive rather than a missing feature, so all four
properties are asserted separately.

The negative half matters just as much: `pickready_app` must still be REFUSED
the raw statement. If a later change grants it ownership, or lets it inherit
the owner's rights, every assertion about the function would still pass while
the least-privilege property this whole design exists for had quietly gone.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

FUNCTION = "refresh_dashboard_job_metrics"
APP_ROLE = "pickready_app"
VIEW = "dashboard_job_metrics"


async def _engine_or_skip():
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping dashboard refresh test")
    return engine


def _acl_entries(acl: str) -> list[str]:
    """`{alice=X/owner,bob=X/owner}` to its entries.

    PostgreSQL writes PUBLIC as an entry with an EMPTY grantee, `=X/owner`, so
    the check for "PUBLIC can still execute this" is a check for an entry whose
    grantee is the empty string. Parsed rather than substring-matched, because
    `"=X/" in acl` is true of every entry including the legitimate ones.
    """
    return [entry for entry in acl.strip("{}").split(",") if entry]


async def test_the_refresh_function_has_all_four_security_properties() -> None:
    """Existence is the weakest of the four and the only obvious one."""
    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT prosecdef, proconfig, proacl::text "
                        "FROM pg_proc WHERE proname = :name"
                    ),
                    {"name": FUNCTION},
                )
            ).first()

            assert row is not None, (
                f"{FUNCTION}() is missing. Migration 0099 creates it, and "
                "without it the scheduled refresh raises 'must be owner' on "
                "every run while every test still passes."
            )
            security_definer, config, acl = row

            assert security_definer is True, (
                "the function is not SECURITY DEFINER, so it executes with the "
                "CALLER's privileges and buys nothing: the refresh still needs "
                "ownership the app role does not have"
            )

            # A SECURITY DEFINER function with a mutable search_path lets any
            # caller who can create a schema decide what `set_config` and
            # `dashboard_job_metrics` resolve to, with the body then running as
            # the role that owns every object in the database.
            assert config is not None and any(
                entry.startswith("search_path=") for entry in config
            ), f"{FUNCTION}() does not pin its search_path: {config!r}"

            # PUBLIC holds EXECUTE on a new function by default, and a NULL acl
            # means exactly that default is still in force.
            assert acl is not None, (
                "the function carries the DEFAULT acl, so EXECUTE was never "
                "revoked from PUBLIC and every role in the database can call it"
            )
            entries = _acl_entries(acl)
            public_grants = [e for e in entries if e.startswith("=")]
            assert not public_grants, (
                f"PUBLIC still holds EXECUTE on {FUNCTION}(): {public_grants}"
            )
            assert any(e.startswith(f"{APP_ROLE}=") for e in entries), (
                f"{APP_ROLE} cannot EXECUTE {FUNCTION}(), so the scheduled "
                f"task still cannot refresh the view. acl={acl}"
            )
    finally:
        await engine.dispose()


async def test_the_app_role_can_refresh_through_the_function_and_not_around_it(
) -> None:
    """Both directions, because only one of them raises.

    The positive half proves the fix works. The negative half proves it is
    still a FIX rather than a widening: if `pickready_app` could issue the raw
    statement, the role would have acquired ownership and the function would be
    decoration.
    """
    from sqlalchemy.exc import ProgrammingError

    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")

            # THROUGH the function, as the app role.
            await conn.execute(text(f'SET ROLE "{APP_ROLE}"'))
            await conn.execute(text(f"SELECT {FUNCTION}()"))

            # AROUND it, as the same role, must still be refused.
            with pytest.raises(ProgrammingError) as refused:
                await conn.execute(
                    text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {VIEW}")
                )
            assert "must be owner" in str(refused.value).lower(), (
                f"{APP_ROLE} was refused for some reason other than ownership, "
                "so this test is no longer measuring least privilege: "
                f"{refused.value}"
            )
            await conn.execute(text("RESET ROLE"))
    finally:
        await engine.dispose()


async def test_the_refresh_does_not_rebuild_the_view_empty() -> None:
    """The failure that raises NOTHING, and has happened on this product.

    `jobs` and `job_candidate_links` carry FORCE ROW LEVEL SECURITY, so owning
    them does not exempt the refresh from their policies. A refresh on a
    connection belonging to no tenant rebuilds the view from zero rows, returns
    successfully, and leaves every dashboard blank behind a clean 200. The
    bypass lives inside the function precisely so no caller can omit it, and
    this asserts the consequence rather than the mechanism.
    """
    engine = await _engine_or_skip()
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            await conn.execute(
                text(
                    "SELECT set_config('app.tenant_id',"
                    " '00000000-0000-0000-0000-000000000000', false)"
                )
            )
            jobs = (
                await conn.execute(text("SELECT count(*) FROM jobs"))
            ).scalar_one()
            if not jobs:
                pytest.skip("no jobs seeded, an empty view would be correct")

            await conn.execute(text(f"SELECT {FUNCTION}()"))
            rows = (
                await conn.execute(text(f"SELECT count(*) FROM {VIEW}"))
            ).scalar_one()

            assert rows > 0, (
                f"the view rebuilt EMPTY against {jobs} job rows. The refresh "
                "ran under row level security and filtered every base row out, "
                "which raises nothing and renders every dashboard blank."
            )
    finally:
        await engine.dispose()
