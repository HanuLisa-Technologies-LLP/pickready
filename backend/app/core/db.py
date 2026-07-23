"""Async SQLAlchemy engine/session factory with RLS tenant-variable support.

Every tenant-scoped request MUST run inside `tenant_scope(session, tenant_id)` —
the Postgres RLS policies key off `current_setting('app.tenant_id')`. App-level
filtering is defense in depth only; RLS is the real boundary (claude.md rule 1).

Super Admin cross-tenant reads use `superadmin_scope(session)`, which sets
`app.bypass_rls` for the dedicated bypass policies and must only be reached
through the audit-logged super-admin code path.
"""
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Raw session dependency — no tenant var set. Use only for global tables
    (auth bootstrap, tenants, llm_provider_keys). Tenant-scoped work must go
    through `tenant_scope`."""
    async with get_session_factory()() as session:
        yield session


@asynccontextmanager
async def tenant_scope(session: AsyncSession, tenant_id: uuid.UUID | str):
    """Set the RLS tenant variable for the current transaction (SET LOCAL)."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )
    yield session


@asynccontextmanager
async def superadmin_scope(session: AsyncSession):
    """Cross-tenant access path for Super Admin. Callers are responsible for
    writing the corresponding audit_log row (FR-11.3)."""
    await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    yield session
