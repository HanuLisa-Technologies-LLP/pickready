"""A tenant's departments: the one implementation of naming, finding and
assigning them (the leadership release, 2026-09-29, spec 13).

`normalize` is the matching key (`company_departments.normalized_name`,
UNIQUE per tenant); migration 0133's backfill states the same rule in SQL
(`lower`, whitespace collapsed and trimmed) and `test_departments` holds the
two together. `assign_job_department` is the ONE writer of
`jobs.department_id` and keeps the legacy `jobs.department` string equal to
the department's display name, so the text a candidate reads and the id a
Functional Head is scoped by can never name two departments.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import CompanyDepartment

__all__ = [
    "DEPARTMENT_NAME_MAX",
    "DEPARTMENT_NOT_FOUND",
    "assign_job_department",
    "display_name",
    "get_department",
    "list_departments",
    "normalize",
    "resolve_or_create",
]

#: The column width, so a name is refused before the database truncates it.
DEPARTMENT_NAME_MAX = 255

#: One sentence for a department id that does not belong to the caller's
#: tenant or does not exist. The two are indistinguishable from outside, the
#: rule every cross-tenant read in this product follows.
DEPARTMENT_NOT_FOUND = "That department does not exist in your company."


def display_name(name: str) -> str:
    """The name as a person typed it, with whitespace collapsed."""
    return " ".join(str(name).split())


def normalize(name: str) -> str:
    """The matching key: whitespace collapsed and trimmed, then lower case.

    `lower` rather than `casefold` because the backfill's SQL twin is
    Postgres `lower`, and two definitions of one key that disagree on a
    single character mint two departments for one name.
    """
    return display_name(name).lower()


def _require_name(name: str | None) -> str:
    shown = display_name(name or "")
    if not shown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A department needs a name.",
        )
    if len(shown) > DEPARTMENT_NAME_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A department name is at most {DEPARTMENT_NAME_MAX} characters.",
        )
    return shown


async def list_departments(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    include_inactive: bool = False,
) -> list[CompanyDepartment]:
    """The tenant's departments, alphabetical by their matching key."""
    query = select(CompanyDepartment).where(CompanyDepartment.tenant_id == tenant_id)
    if not include_inactive:
        query = query.where(CompanyDepartment.is_active.is_(True))
    rows = await session.execute(
        query.order_by(CompanyDepartment.normalized_name, CompanyDepartment.id)
    )
    return list(rows.scalars().all())


async def get_department(
    session: AsyncSession, tenant_id: uuid.UUID, department_id: uuid.UUID
) -> CompanyDepartment | None:
    """One department of THIS tenant, or None. The tenant predicate is
    defence in depth beside RLS, and it is also what makes the answer right
    under a bypass session."""
    return (
        await session.execute(
            select(CompanyDepartment).where(
                CompanyDepartment.id == department_id,
                CompanyDepartment.tenant_id == tenant_id,
            )
        )
    ).scalars().first()


async def require_department(
    session: AsyncSession, tenant_id: uuid.UUID, department_id: uuid.UUID
) -> CompanyDepartment:
    """`get_department`, or the 422 a person reads."""
    department = await get_department(session, tenant_id, department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=DEPARTMENT_NOT_FOUND,
        )
    return department


async def resolve_or_create(
    session: AsyncSession, tenant_id: uuid.UUID, name: str
) -> CompanyDepartment:
    """The department this name means in this tenant, created when absent.

    Race-safe: two requests naming one new department at once both land on
    the one row, through `ON CONFLICT DO NOTHING` on the (tenant, key)
    constraint and a re-read. A department somebody retired is brought back
    (`is_active`), because naming it again on a job is the explicit act of
    using it.
    """
    shown = _require_name(name)
    key = normalize(shown)
    await session.execute(
        insert(CompanyDepartment)
        .values(id=uuid.uuid4(), tenant_id=tenant_id, name=shown, normalized_name=key)
        .on_conflict_do_nothing(constraint="uq_company_departments_tenant_name")
    )
    department = (
        await session.execute(
            select(CompanyDepartment).where(
                CompanyDepartment.tenant_id == tenant_id,
                CompanyDepartment.normalized_name == key,
            )
        )
    ).scalars().one()
    if not department.is_active:
        department.is_active = True
        department.updated_at = datetime.now(timezone.utc)
        await session.flush()
    return department


async def assign_job_department(
    session: AsyncSession,
    job: Any,
    *,
    department_id: uuid.UUID | None,
    name: str | None,
    scope: uuid.UUID | None,
) -> None:
    """Point a job at a department: the ONE writer of `jobs.department_id`.

    `department_id` wins when given (it must be this tenant's); otherwise a
    non-blank `name` is resolved or created; otherwise the job has no
    department. `scope` is the caller's department boundary
    (`department_access.department_scope`): a department-scoped person may
    only put a job in THEIR department, so a grant that let them write a job
    could never be used to place one outside what they can see.
    """
    if department_id is not None:
        department = await require_department(session, job.tenant_id, department_id)
    elif name is not None and display_name(name):
        department = await resolve_or_create(session, job.tenant_id, name)
    else:
        department = None

    target = department.id if department is not None else None
    if scope is not None and target != scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only place a job in your own department.",
        )
    job.department_id = target
    job.department = department.name if department is not None else None
