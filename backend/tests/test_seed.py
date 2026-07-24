"""Dev-seed guarantees (contract rev 2).

Two layers:
- Pure assertions on the seed module's multi-context design (always run).
- An integration test that runs the seed twice against the live database and
  asserts idempotency + the owner/multi-context invariants. It SKIPS cleanly
  when no database is reachable (e.g. a pure-unit CI run), and executes for
  real inside the backend container where Postgres is up.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Role, Tenant, User
from app.scripts import seed_dev_data as seed_mod


# ── Pure design assertions (DB-free) ─────────────────────────────────────────

def test_multi_context_email_is_defined() -> None:
    assert seed_mod.MULTI_CONTEXT_EMAIL.endswith("@pickready.test")
    assert seed_mod.MULTI_CONTEXT_PHONE


def test_second_tenant_uses_non_deliverable_domain() -> None:
    # `.test` / non-example.com — Resend rejects example.com with 422.
    assert seed_mod.TECHSTART_TENANT_DOMAIN.endswith(".test")
    assert "example.com" not in seed_mod.TECHSTART_TENANT_DOMAIN


# ── Live integration (skips if the database is unreachable) ──────────────────

async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable — skipping seed integration test")
    return engine


async def test_seed_is_idempotent_and_holds_owner_invariant() -> None:
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # Run the seed twice; the second run must add nothing.
        await seed_mod.seed()
        await seed_mod.seed()

        async with factory() as session:
            # Exactly one super_admin, and it is the platform owner.
            super_admins = (
                await session.execute(
                    select(User).where(User.role == Role.super_admin)
                )
            ).scalars().all()
            assert len(super_admins) == 1
            assert super_admins[0].email.strip().lower() == get_settings().owner_email

            # The multi-context identifier resolves to 2+ users (choose-workspace).
            matches = (
                await session.execute(
                    select(User).where(
                        (User.email == seed_mod.MULTI_CONTEXT_EMAIL)
                        | (User.phone == seed_mod.MULTI_CONTEXT_PHONE)
                    )
                )
            ).scalars().all()
            assert len(matches) >= 2
            # ...and they span two different tenants (cross-context).
            assert len({m.tenant_id for m in matches}) >= 2

            # Both demo tenants exist.
            tenant_domains = set(
                (await session.execute(select(Tenant.domain))).scalars().all()
            )
            assert seed_mod.DEMO_TENANT_DOMAIN in tenant_domains
            assert seed_mod.TECHSTART_TENANT_DOMAIN in tenant_domains

            # Idempotency of the candidate login account (exactly one row).
            cand_count = (
                await session.execute(
                    select(func.count()).select_from(User).where(
                        User.email == "candidate.demo@pickready.test",
                        User.role == Role.candidate,
                    )
                )
            ).scalar_one()
            assert cand_count == 1
    finally:
        await engine.dispose()
