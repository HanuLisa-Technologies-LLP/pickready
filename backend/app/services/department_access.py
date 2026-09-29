"""The department boundary: the ONE place it is decided (spec 2.11, 14).

A Functional Head belongs to exactly one department and may see only that
department's jobs, the candidates attached to those jobs, and the reports
from them. Frontend hiding is not a boundary (spec 14.3): every backend read
of a job, an application, a candidate or a report asks this module, and
`rbac.decide` applies the same rule to every `authorize` call through
`Principal.department_id`, so a route that runs the RBAC chain is scoped
without knowing this module exists.

WHAT DECIDES THE SCOPE IS THE STORED DEPARTMENT, NEVER THE TOKEN
----------------------------------------------------------------
`department_scope` reads `users.department_id` from the database on every
request (cached on the session for the length of the request only). A token
is minted at sign-in and outlives any change a Super Admin makes; a
department moved, or a Recruiter re-seated as a Functional Head, must bind
on the next request, not at the next sign-in. The database CHECK
`ck_users_department_iff_functional_head` makes "has a department" and "is a
Functional Head" the same fact, so the rule is data rather than a role
branch.

It FAILS CLOSED in the one case the data cannot answer: a principal whose
token says functional_head and whose row cannot be read (deleted, or a
different tenant) is scoped to a department that matches nothing, rather
than to everything.

CROSS-DEPARTMENT IS A 404
-------------------------
The tenant-safe convention every cross-tenant read follows (spec 14.3 allows
403 or a tenant-safe 404 "according to existing API conventions"): telling a
Functional Head that the Finance job exists is the leak a boundary exists to
prevent. A job with NO department is outside every department and so outside
every Functional Head's scope.

CEO and MD carry no department: tenant-wide read (spec 14.2).
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Role
from app.services import role_hierarchy

__all__ = [
    "NO_DEPARTMENT",
    "department_scope",
    "in_scope",
    "job_scope_clause",
    "job_scope_sql",
    "require_candidate_in_scope",
    "require_job_in_scope",
    "require_link_in_scope",
]

#: The scope of a department-scoped principal whose department cannot be
#: read. It matches no row, so the principal sees nothing.
NO_DEPARTMENT = uuid.UUID(int=0)

_CACHE_KEY = "department_access.scope"


class _Principal(Protocol):
    user_id: Any
    tenant_id: Any
    role: Any


def _role(value: Any) -> Role | None:
    try:
        return value if isinstance(value, Role) else Role(str(getattr(value, "value", value)))
    except ValueError:
        return None


async def department_scope(session: AsyncSession, user: _Principal) -> uuid.UUID | None:
    """The one department this principal is confined to, or None for
    unrestricted (within their tenant; RLS draws that line).

    Read from `users.department_id` under the caller's own session, once per
    request.
    """
    if user is None or getattr(user, "user_id", None) is None:
        return None
    # The per-request cache rides the session's `info` dict. A session double
    # without one simply reads every time; the answer is the same.
    info = getattr(session, "info", None)
    cache: dict[str, uuid.UUID | None] = (
        info.setdefault(_CACHE_KEY, {}) if isinstance(info, dict) else {}
    )
    key = str(user.user_id)
    if key in cache:
        return cache[key]
    row = (
        await session.execute(
            text(
                "SELECT department_id FROM users "
                "WHERE id = CAST(:uid AS uuid) "
                "AND tenant_id IS NOT DISTINCT FROM CAST(:tid AS uuid)"
            ),
            {
                "uid": str(user.user_id),
                "tid": str(user.tenant_id) if user.tenant_id is not None else None,
            },
        )
    ).first()
    scope: uuid.UUID | None
    if row is not None and row[0] is not None:
        scope = uuid.UUID(str(row[0]))
    elif _role(getattr(user, "role", None)) in role_hierarchy.DEPARTMENT_SCOPED_ROLES:
        # The token says the principal is department-scoped and no department
        # can be read for them: confine them to nothing, never to everything.
        scope = NO_DEPARTMENT
    else:
        scope = None
    cache[key] = scope
    return scope


def in_scope(scope: uuid.UUID | None, department_id: Any) -> bool:
    """Pure: may a principal with `scope` see a resource in `department_id`."""
    if scope is None:
        return True
    if department_id is None:
        return False
    return str(department_id) == str(scope)


def job_scope_sql(scope: uuid.UUID | None, alias: str = "j") -> tuple[str, dict[str, str]]:
    """The predicate a query over `jobs AS <alias>` adds, and its parameters.

    `("TRUE", {})` for an unrestricted principal, so a caller composes it
    unconditionally and there is no branch at the call site to forget.
    """
    if scope is None:
        return "TRUE", {}
    return (
        f"{alias}.department_id = CAST(:dept_scope AS uuid)",
        {"dept_scope": str(scope)},
    )


def job_scope_clause(scope: uuid.UUID | None, department_column: Any) -> Any:
    """The same predicate for an ORM query: `jobs.department_id` (or an
    aliased column) equal to the scope, or TRUE for an unrestricted principal.
    """
    from sqlalchemy import true

    if scope is None:
        return true()
    return department_column == scope


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


async def require_job_in_scope(
    session: AsyncSession,
    user: _Principal,
    job_id: uuid.UUID | str,
    *,
    detail: str = "Job not found",
) -> None:
    """404 unless the job is inside the caller's department scope."""
    scope = await department_scope(session, user)
    if scope is None:
        return
    row = (
        await session.execute(
            text("SELECT department_id FROM jobs WHERE id = CAST(:jid AS uuid)"),
            {"jid": str(job_id)},
        )
    ).first()
    if row is None or not in_scope(scope, row[0]):
        raise _not_found(detail)


async def require_link_in_scope(
    session: AsyncSession,
    user: _Principal,
    link_id: uuid.UUID | str,
    *,
    detail: str = "Not found",
) -> None:
    """404 unless the application's job is inside the caller's scope."""
    scope = await department_scope(session, user)
    if scope is None:
        return
    row = (
        await session.execute(
            text(
                "SELECT j.department_id FROM job_candidate_links l "
                "JOIN jobs j ON j.id = l.job_id WHERE l.id = CAST(:lid AS uuid)"
            ),
            {"lid": str(link_id)},
        )
    ).first()
    if row is None or not in_scope(scope, row[0]):
        raise _not_found(detail)


async def require_candidate_in_scope(
    session: AsyncSession,
    user: _Principal,
    candidate_id: uuid.UUID | str,
    *,
    detail: str = "Candidate not found",
) -> None:
    """404 unless the candidate holds an application on a job inside the
    caller's scope. The tenant half is the caller's own check (and RLS); this
    adds only the department."""
    scope = await department_scope(session, user)
    if scope is None:
        return
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM job_candidate_links l JOIN jobs j ON j.id = l.job_id "
                "WHERE l.candidate_id = CAST(:cid AS uuid) "
                "AND j.department_id = CAST(:dept AS uuid) LIMIT 1"
            ),
            {"cid": str(candidate_id), "dept": str(scope)},
        )
    ).first()
    if found is None:
        raise _not_found(detail)
