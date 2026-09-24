"""An HTTP client over the REAL job-setup and job routes, for the API suites.

`skills_fixtures` builds the world (tenant, users, job, SWOT, skills) and reads
it back from a second connection. This adds the one thing those service-level
suites do not need: requests through the real routers and the real
authorization chain, as a chosen person.

THE SESSION IS ENTERED THE WAY PRODUCTION ENTERS IT. `get_tenant_db` is
overridden with a session that drops to the RLS application role
(`tenant_scope`) and commits when the request is over, so a write that would
be refused by a policy or a revoked grant is refused here too, and
`dispatch_after_commit` fires on the commit exactly as it does live. Every
assertion about stored state reads from a SECOND connection afterwards.

`get_current_user` is overridden with whoever the test says is acting
(`Api.acting_as`), so the capability grants, the per-user overlay and the
per-job assignment scope are all the database's own answers.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from sqlalchemy import text

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.workers import dispatch
from tests import skills_fixtures as fx

SKILLS = "/api/v2/assessments/jobs/{job}/skills"
SETUP = "/api/v2/assessments/jobs/{job}/setup"
JOBS = "/api/v1/jobs"


class Api:
    """The client plus who is acting. Starts as the client super admin."""

    def __init__(self, world: fx.World, client: httpx.AsyncClient) -> None:
        self.world = world
        self.http = client
        self.user = CurrentUser(
            user_id=world.client,
            tenant_id=world.tenant,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    def acting_as(self, key: str, role: Role) -> "Api":
        self.user = CurrentUser(
            user_id=self.world.users[key],
            tenant_id=self.world.tenant,
            role=role,
            audience=AUDIENCE_ORG,
        )
        return self

    def skills_url(self, suffix: str = "") -> str:
        return SKILLS.format(job=self.world.job) + suffix


@asynccontextmanager
async def api(world: fx.World) -> AsyncIterator[Api]:
    sessions = fx.sessions()
    holder: dict[str, Api] = {}

    async def _current_user() -> CurrentUser:
        return holder["api"].user

    async def _tenant_db():
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    dispatch.clear_recorded()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            holder["api"] = Api(world, client)
            yield holder["api"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def assign(world: fx.World, key: str, assignment_role: str) -> None:
    """One ACTIVE per-job assignment, the row every SCOPED cell reads."""
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO job_assignments (tenant_id, job_id, user_id, "
                        "assignment_role, active) VALUES (:t, :j, :u, :r, true)"
                    ),
                    {"t": world.tenant, "j": world.job, "u": world.users[key],
                     "r": assignment_role},
                )


async def lock(world: fx.World) -> None:
    """A snapshot row, which IS the lock (D5). Written directly: this is the
    state a candidate's first start leaves, and the tests here are about what
    the routes do with it, not about how the start takes it."""
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO job_skill_snapshots (id, tenant_id, job_id, version, "
                        "skills_json, grade, digest, source, locked_at) VALUES (:i, :t, :j, "
                        "1, '[]'::jsonb, 'managerial', :d, 'lock', now())"
                    ),
                    {"i": uuid.uuid4(), "t": world.tenant, "j": world.job, "d": "a" * 64},
                )


async def overlay(world: fx.World, key: str, grants: dict[str, bool]) -> None:
    """The sparse per-user permission overlay (`users.permissions_json`)."""
    import json

    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE users SET permissions_json = CAST(:p AS jsonb) WHERE id = :u"
                    ),
                    {"p": json.dumps(grants), "u": world.users[key]},
                )


async def drop(world: fx.World) -> None:
    """Assignments first: `job_assignments.user_id` is ON DELETE RESTRICT, so
    the tenant cascade would otherwise stop at the users."""
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("DELETE FROM job_assignments WHERE tenant_id = :t"),
                    {"t": world.tenant},
                )
    await fx.drop(world)


def recorded(name: str) -> list[dispatch.RecordedDispatch]:
    return [entry for entry in dispatch.recorded() if entry.name == name]


async def company_profile(world: fx.World, about: str | None) -> None:
    """The tenant's Company Profile row: what Gate 1 asks at create and at JD
    generation. `about=None` or blank is a profile that says nothing."""
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO companies (id, tenant_id, about_company) "
                        "VALUES (:i, :t, :a)"
                    ),
                    {"i": uuid.uuid4(), "t": world.tenant, "a": about},
                )


async def demo(world: fx.World) -> None:
    """A demonstration tenant: never refused by the credit gate, so a create
    test does not have to buy a bundle first. The gate's own refusal is tested
    on a tenant that is NOT demo."""
    await sql(world, "UPDATE tenants SET is_demo = true WHERE id = :t", t=world.tenant)


async def sql(world: fx.World, statement: str, **params) -> None:
    """One committed write under the bypass scope, for state a test sets up."""
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(text(statement), params)


async def read(statement: str, **params) -> list[dict]:
    """Committed state, read from a SECOND connection."""
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            rows = (await session.execute(text(statement), params)).mappings().all()
            return [dict(row) for row in rows]
