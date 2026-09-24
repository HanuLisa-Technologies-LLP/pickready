"""Shared setup for the candidate portal's HTTP tests (WP6-B).

These tests drive the portal the way a browser does: a REAL candidate session
from `tests/candidate_session.py`, real cookies, no dependency overrides, and
every state assertion read back on a SECOND connection after the response. A
dependency override is a claim about who is calling, and the BGV candidate
routes shipped unreachable for weeks behind exactly that claim.

What lives here is only the scaffolding every such test repeats: an engine, a
tenant with published jobs, a resume store that never leaves the process, and
cleanup that removes what the test made.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services.resume_storage import ResumeAsset


def engine_and_factory():
    """A NullPool engine, so no connection outlives the loop that opened it."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def fake_asset(label: str = "cv") -> ResumeAsset:
    """What `store_resume` would have returned, without the object store."""
    return ResumeAsset(
        public_id=f"pickready/resumes/{uuid.uuid4().hex}",
        secure_url=f"https://res.cloudinary.test/raw/upload/{label}.pdf",
        original_filename="cv.pdf",
        mime_type="application/pdf",
        size_bytes=24,
        uploaded_at=datetime.now(timezone.utc),
        sha256=uuid.uuid4().hex * 2,
        metadata={"resource_type": "raw"},
    )


def resume_file() -> tuple[str, io.BytesIO, str]:
    """A multipart file tuple for `files={"resume": ...}`."""
    return ("cv.pdf", io.BytesIO(b"%PDF-1.4 minimal resume bytes"), "application/pdf")


@dataclass
class Employer:
    """A tenant with published jobs, and everything a test hung off it."""

    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    job_ids: list[uuid.UUID] = field(default_factory=list)
    extra_candidates: list[uuid.UUID] = field(default_factory=list)
    extra_users: list[uuid.UUID] = field(default_factory=list)


async def seed_employer(
    factory: async_sessionmaker[AsyncSession], *, jobs: int = 2
) -> Employer:
    from app.models import Job, JobStatus, Tenant

    employer = Employer()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                session.add(Tenant(
                    id=employer.tenant_id,
                    name="Portal Test Employer",
                    domain=f"{employer.tenant_id}.portal.test",
                ))
                await session.flush()
                for _ in range(jobs):
                    job_id = uuid.uuid4()
                    session.add(Job(
                        id=job_id, tenant_id=employer.tenant_id, title="Platform Engineer",
                        jd_json={}, status=JobStatus.ratified, ratified_at=now,
                    ))
                    employer.job_ids.append(job_id)
    return employer


async def drop_employer(
    factory: async_sessionmaker[AsyncSession], employer: Employer
) -> None:
    """Remove the tenant (its jobs and links cascade) and any extra rows."""
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :t"),
                    {"t": str(employer.tenant_id)},
                )
                for candidate_id in employer.extra_candidates:
                    await session.execute(
                        text("DELETE FROM candidates WHERE id = :c"),
                        {"c": str(candidate_id)},
                    )
                for user_id in employer.extra_users:
                    await session.execute(
                        text("DELETE FROM users WHERE id = :u"),
                        {"u": str(user_id)},
                    )


async def scalar(factory: async_sessionmaker[AsyncSession], sql: str, **params):
    """One value, read on a FRESH connection."""
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return (await session.execute(text(sql), params)).scalar()


async def row(factory: async_sessionmaker[AsyncSession], sql: str, **params):
    """One row as a mapping, read on a FRESH connection."""
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return (await session.execute(text(sql), params)).mappings().first()
