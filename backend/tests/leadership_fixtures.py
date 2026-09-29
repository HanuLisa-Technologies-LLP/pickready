"""Leadership Intelligence fixtures over a `skills_fixtures` world.

States the product writes, through the product's own writer: a department row
(`company_departments`), the job pointed at it, a leader of a role, and a
saved leadership version written by `leadership.profiles.save` itself, never
an INSERT that could hold a shape the service would refuse.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.core.db import superadmin_scope
from tests import skills_fixtures as fx

#: Observable, job-related lines a leader might write (each passes
#: `observable.is_observable`).
CEO_LINE = (
    "We need people who have led the response to a production incident and "
    "written the review that changed how their team worked."
)
MD_LINE = (
    "Every hire should have delivered a project to a customer deadline and "
    "explained the trade-offs they made along the way."
)
FH_LINE = (
    "Engineers joining us have designed a streaming pipeline, shipped it to "
    "production and fixed it when it failed."
)
IDEAL_LINE = (
    "A new engineer has taken a service from an unclear brief to a shipped "
    "outcome within their first quarter."
)
#: Lines the compiler must refuse, one per reason.
UNSAFE_LINE = "We prefer young candidates under 30 for this team."
VAGUE_LINE = "We need aggressive people."


async def add_department(w: fx.World, name: str) -> uuid.UUID:
    department_id = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO company_departments (id, tenant_id, name, normalized_name) "
                        "VALUES (:i, :t, :n, :k)"
                    ),
                    {"i": department_id, "t": w.tenant, "n": name, "k": name.lower()},
                )
    return department_id


async def set_job_department(w: fx.World, department_id: uuid.UUID, name: str) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text("UPDATE jobs SET department_id = :d, department = :n WHERE id = :j"),
                    {"d": department_id, "n": name, "j": w.job},
                )


async def add_leader(
    w: fx.World, role: str, *, department_id: uuid.UUID | None = None
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, full_name, role, status, "
                        "department_id) VALUES (:u, :t, :e, :n, :r, 'active', :d)"
                    ),
                    {
                        "u": user_id, "t": w.tenant, "e": f"{user_id.hex[:12]}@lead.test",
                        "n": f"Leader {role}", "r": role, "d": department_id,
                    },
                )
    w.users[f"{role}:{user_id.hex[:6]}"] = user_id
    return user_id


async def save(
    w: fx.World,
    user_id: uuid.UUID,
    *,
    company: str = "",
    department: str = "",
    ideal: str = "",
    expectations: dict[uuid.UUID, str] | None = None,
) -> dict[str, Any]:
    """`profiles.save` in one committed transaction, as the route runs it."""
    from app.services.leadership import profiles

    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return await profiles.save(
                    session,
                    user_id=user_id,
                    company_requirements=company,
                    department_requirements=department,
                    ideal_employee_expectations=ideal,
                    department_expectations=expectations or {},
                    ai_draft_sources=None,
                )


async def leadership_rows(w: fx.World) -> dict[str, int]:
    """Counts of every leadership table for the tenant, from a second connection."""
    out: dict[str, int] = {}
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            for table in (
                "leadership_profiles",
                "leadership_department_expectations",
                "job_leadership_contexts",
            ):
                out[table] = (
                    await session.execute(
                        text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # noqa: S608
                        {"t": w.tenant},
                    )
                ).scalar_one()
    return out
