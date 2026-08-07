"""Async SQLAlchemy engine/session factory with RLS tenant-variable support.

Every tenant-scoped request MUST run inside `tenant_scope(session, tenant_id)` —
the Postgres RLS policies key off `current_setting('app.tenant_id')`. App-level
filtering is defense in depth only; RLS is the real boundary (claude.md rule 1).

Super Admin cross-tenant reads use `superadmin_scope(session)`, which sets
`app.bypass_rls` for the dedicated bypass policies and must only be reached
through the audit-logged super-admin code path.
"""
import re
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

# A SQL role name can't be passed as a bind parameter to SET ROLE, so it is
# interpolated. It comes from trusted config, but we still hard-validate it as a
# plain identifier to make injection impossible even if config is misconfigured.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    """Process-wide async engine.

    Pool sizing matters more than it looks. SQLAlchemy's default is
    ``pool_size=5, max_overflow=10``; with a handful of concurrent browser tabs
    each firing several parallel calls, requests queue waiting for a connection
    and every one of them looks slow even though the SQL is instant. The sizes
    below are settings-driven so a small production instance can lower them.

    ``pool_recycle`` retires a connection before a managed Postgres (or a proxy)
    silently drops it; ``pool_pre_ping`` still catches the ones that die early.
    ``pool_use_lifo`` hands back the most recently used connection, which keeps
    the idle tail of the pool closeable instead of round-robining every socket.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=settings.db_pool_recycle_seconds,
            pool_pre_ping=True,
            pool_use_lifo=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Raw session dependency with no tenant variable, for global tables only.

    Auth bootstrap uses ``get_identity_session``; tenant-scoped work must go
    through ``tenant_scope``. Only genuinely global tables belong here."""
    async with get_session_factory()() as session:
        yield session


async def get_identity_session() -> AsyncIterator[AsyncSession]:
    """RLS-bypassed session used only while resolving an identity.

    Login, refresh, ``/auth/me`` and the workspace chooser must read ``users``
    across tenants before (or while validating) a tenant context. Now that
    ``users`` and ``tenants`` are RLS protected, that structural exception is
    explicit here instead of leaving both tables globally readable.

    Business endpoints must never use this dependency: they already know their
    tenant and belong in ``tenant_scope``. Auth handlers still bind each issued
    token to exactly one user and tenant and audit context selection.
    """
    async with get_session_factory()() as session:
        async with superadmin_scope(session):
            yield session


def _app_role() -> str | None:
    """The non-superuser Postgres role tenant sessions run as, or None to skip.

    The RLS policies are only a real boundary when the *connection* role does not
    bypass RLS. In dev/docker (and some managed setups) the app connects as a
    superuser/owner role, which bypasses RLS entirely — making the WHERE-clause
    filtering the ONLY protection, exactly what claude.md rule 1 forbids. So we
    drop to `POSTGRES_RLS_APP_ROLE` (NOLOGIN, no BYPASSRLS) for the transaction
    via SET LOCAL ROLE; it auto-resets at COMMIT/ROLLBACK.
    """
    role = get_settings().postgres_rls_app_role
    if not role or not _IDENT_RE.match(role):
        return None
    return role


@asynccontextmanager
async def tenant_scope(session: AsyncSession, tenant_id: uuid.UUID | str):
    """Set the RLS tenant variable for the current transaction (SET LOCAL) and
    drop privileges to the RLS-enforcing app role so the Postgres policy — not
    the app's WHERE clause — is the tenant boundary (claude.md rule 1)."""
    role = _app_role()
    if role is not None:
        # SET LOCAL ROLE is transaction-scoped; identifier is validated above.
        await session.execute(text(f'SET LOCAL ROLE "{role}"'))
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )
    yield session


@asynccontextmanager
async def superadmin_scope(session: AsyncSession):
    """Cross-tenant access path (Super Admin owner console + public tokenized +
    candidate portal). Callers are responsible for writing the corresponding
    audit_log row where required (FR-11.3).

    We drop to the RLS-enforcing app role here too and grant the cross-tenant
    reach through the EXPLICIT `app.bypass_rls='on'` flag rather than relying on
    the connection being a superuser. That way the RLS policy is a real boundary
    on every path, and the only escape hatch is the flag the audited code sets."""
    role = _app_role()
    if role is not None:
        await session.execute(text(f'SET LOCAL ROLE "{role}"'))
    # Pin a sentinel tenant id so the RLS policies' `app.tenant_id::uuid` cast
    # never sees an empty string. Custom GUCs revert to '' (not NULL) after a
    # prior transaction on a pooled connection set them, and ''::uuid raises —
    # the classic placeholder-GUC gotcha. The sentinel is a valid uuid that no
    # real (uuid4) tenant can hold; `app.bypass_rls='on'` is what actually
    # grants the cross-tenant reach, this cast just stays well-defined.
    await session.execute(
        text("SELECT set_config('app.tenant_id', "
             "'00000000-0000-0000-0000-000000000000', true)")
    )
    await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    yield session
