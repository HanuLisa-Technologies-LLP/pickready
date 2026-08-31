"""Shared harness for the dashboard's HTTP tests. Not a test module itself.

Named without a `test_` prefix so pytest does not collect it, the same way
`tests/application_fixtures.py` already is.

WHAT IS REAL WHEN A TEST USES THIS
-----------------------------------
Real FastAPI routing, real `rbac.require_authorized` dependencies, real
`capabilities.RBAC_INVARIANTS`, real Postgres, real Row Level Security, real
`job_assignments` rows. Exactly one thing is injected: WHO is calling. Minting
a Firebase session per case would be testing the login flow, which has its own
suite, and it would make every authorization assertion depend on it.

The RLS scope is entered here exactly as `deps.get_tenant_db` enters it, so a
cross-tenant case is refused by Postgres and not only by the application's own
WHERE clause (claude.md rule 1).

NOTHING HERE COMMITS, and a test that expects a write to persist should not
either: `get_tenant_db` owns the request transaction and commits on a clean
return. That is also what makes a mutation and its audit row atomic.
"""
from __future__ import annotations

import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role

BASE = "/api/v1/dashboard"


def engine():
    """A NullPool engine on the migrated test database.

    NullPool, deliberately: an asyncpg connection belongs to the event loop
    that opened it, and TestClient runs the application on a fresh loop per
    test. A pooled connection from an earlier test is bound to a loop that has
    already closed, which fails the second test for something the first one
    did.
    """
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def sessions():
    return async_sessionmaker(engine(), expire_on_commit=False)


async def schema_is_current() -> bool:
    """Whether the test database has been migrated far enough to run these.

    Probes the two columns this surface reads that arrived in migrations 0065
    and 0069, rather than reading `alembic_version`: a revision string tells
    you what ran, and these tests care about what EXISTS.
    """
    eng = engine()
    try:
        async with eng.connect() as conn:
            await conn.execute(
                sa.text("SELECT prescreen_grade FROM job_candidate_links LIMIT 0")
            )
            await conn.execute(sa.text("SELECT source FROM calibration_records LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 - the reason is reported by the skip message
        return False
    finally:
        await eng.dispose()


SKIP_REASON = (
    "the migrated test database is not reachable; bring it up and migrate it "
    "with `./scripts/test.sh --keep` (or `alembic upgrade head` against it)"
)


class Caller:
    """A TestClient plus the principal each request is made as."""

    def __init__(self) -> None:
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_user(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role
    ) -> None:
        self.principal = CurrentUser(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            audience=AUDIENCE_ORG,
        )


@pytest.fixture
def caller() -> Iterator[Caller]:
    factory = sessions()
    state = Caller()

    async def _current_user() -> CurrentUser:
        assert state.principal is not None, "no principal set for this request"
        return state.principal

    async def _tenant_db():
        principal = state.principal
        assert principal is not None
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, principal.tenant_id):
                    yield session

    # RESTORE, never clear: `dependency_overrides` is application-global, and
    # clearing it would remove whatever another module installed rather than
    # only what this fixture added.
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            state.http = http
            yield state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture(autouse=True)
def no_permission_cache(monkeypatch):
    """The grant engine caches permission rows in Redis for two minutes.

    A double for the CACHE, never for the decision: resolution still runs
    through the real engine against the real `role_permissions` rows. Without
    it a run depends on a Redis being present and can read rows left by an
    earlier run.
    """
    from app.services import tenant_cache

    async def _miss(key):  # noqa: ANN001
        return None

    async def _noop(key, value, *, ttl=120):  # noqa: ANN001
        return None

    monkeypatch.setattr(tenant_cache, "get_json", _miss)
    monkeypatch.setattr(tenant_cache, "set_json", _noop)
