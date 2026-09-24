"""Shared seed for the candidate-communications tests (Phase 6 WP6-C).

One tenant with a recruiter, a job, and a candidate linked to the job, written
through a bypass session exactly as a worker writes, plus the readers the
tests use from a SECOND connection. Not a test module: nothing here asserts.

Requires the suite's database. Callers skip when `reachable()` is False, the
pattern every database-backed module in this suite follows.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def bypass(session: AsyncSession) -> None:
    """Session-level bypass, as `workers.runtime.worker_session` sets it, so
    it survives the commits a worker function makes mid-way."""
    await session.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))


async def reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM email_log LIMIT 0"))
        return True
    except (OSError, sa.exc.DBAPIError):
        return False
    finally:
        await engine.dispose()


@dataclass
class World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    recruiter: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    candidate: uuid.UUID = field(default_factory=uuid.uuid4)
    link: uuid.UUID = field(default_factory=uuid.uuid4)
    candidate_email: str = ""
    #: False when the candidate row belongs to a real session helper, which
    #: deletes it itself.
    owns_candidate: bool = True


async def seed(
    sessions: async_sessionmaker[AsyncSession],
    *,
    candidate_id: uuid.UUID | None = None,
    candidate_email: str | None = None,
) -> World:
    world = World()
    if candidate_id is not None:
        world.candidate = candidate_id
        world.owns_candidate = False
    world.candidate_email = candidate_email or f"cand-{world.candidate.hex[:10]}@comms.test"
    async with sessions() as session:
        async with session.begin():
            await session.execute(
                sa.text("SELECT set_config('app.bypass_rls', 'on', true)")
            )
            await session.execute(
                sa.text(
                    "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                    "VALUES (:id, :name, :domain, 'pending')"
                ),
                {
                    "id": str(world.tenant),
                    "name": f"Comms Co {world.tenant.hex[:6]}",
                    "domain": f"{world.tenant.hex[:10]}.comms.test",
                },
            )
            await session.execute(
                sa.text(
                    "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                    "VALUES (:id, :tid, :email, 'Priya Raman', 'recruiter', 'active')"
                ),
                {
                    "id": str(world.recruiter),
                    "tid": str(world.tenant),
                    "email": f"rec-{world.recruiter.hex[:10]}@comms.test",
                },
            )
            await session.execute(
                sa.text(
                    "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                    "VALUES (:id, :tid, 'Backend Engineer', CAST('{}' AS jsonb), 'draft')"
                ),
                {"id": str(world.job), "tid": str(world.tenant)},
            )
            if world.owns_candidate:
                await session.execute(
                    sa.text(
                        "INSERT INTO candidates (id, full_name, email, "
                        " consent_databank, created_at) "
                        "VALUES (:id, 'Karthik Kumar', :email, false, now())"
                    ),
                    {"id": str(world.candidate), "email": world.candidate_email},
                )
            else:
                await session.execute(
                    sa.text(
                        "UPDATE candidates SET full_name = 'Karthik Kumar', "
                        " email = :email WHERE id = :id"
                    ),
                    {"id": str(world.candidate), "email": world.candidate_email},
                )
            await session.execute(
                sa.text(
                    "INSERT INTO job_candidate_links "
                    "(id, tenant_id, job_id, candidate_id, source, status) "
                    "VALUES (:id, :tid, :jid, :cid, 'fresh', 'applied')"
                ),
                {
                    "id": str(world.link),
                    "tid": str(world.tenant),
                    "jid": str(world.job),
                    "cid": str(world.candidate),
                },
            )
    return world


async def add_sender(
    sessions: async_sessionmaker[AsyncSession],
    world: World,
    *,
    status: str = "active",
    is_default: bool = False,
) -> uuid.UUID:
    sender_id = uuid.uuid4()
    async with sessions() as session:
        async with session.begin():
            await session.execute(
                sa.text("SELECT set_config('app.bypass_rls', 'on', true)")
            )
            await session.execute(
                sa.text(
                    "INSERT INTO client_email_senders (id, tenant_id, name, email, "
                    " status, email_verified, is_default, created_at, updated_at) "
                    "VALUES (:id, :tid, 'Rahul', :email, :status, false, :dflt, "
                    " now(), now())"
                ),
                {
                    "id": str(sender_id),
                    "tid": str(world.tenant),
                    "email": f"hr-{sender_id.hex[:8]}@corp.comms.test",
                    "status": status,
                    "dflt": is_default,
                },
            )
    return sender_id


async def cleanup(sessions: async_sessionmaker[AsyncSession], world: World) -> None:
    async with sessions() as session:
        async with session.begin():
            await session.execute(
                sa.text("SELECT set_config('app.bypass_rls', 'on', true)")
            )
            await session.execute(
                sa.text("DELETE FROM candidate_updates WHERE candidate_id = :cid"),
                {"cid": str(world.candidate)},
            )
            await session.execute(
                sa.text("DELETE FROM tenants WHERE id = :id"), {"id": str(world.tenant)}
            )
            if world.owns_candidate:
                await session.execute(
                    sa.text("DELETE FROM candidates WHERE id = :id"),
                    {"id": str(world.candidate)},
                )


async def rows(
    sessions: async_sessionmaker[AsyncSession], sql: str, params: dict | None = None
) -> list[dict]:
    """Read from a SECOND connection, after the fact, under the bypass."""
    async with sessions() as session:
        async with session.begin():
            await session.execute(
                sa.text("SELECT set_config('app.bypass_rls', 'on', true)")
            )
            result = await session.execute(sa.text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]
