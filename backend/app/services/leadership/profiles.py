"""The ONE writer and reader of a leader's own Leadership Intelligence.

Owner spec 2026-09-29, sections 12, 16, 18 and 28, and rules 37.11 and 37.12.

WHOSE STREAM A SAVE WRITES IS READ FROM THE DATABASE, NEVER FROM A REQUEST
--------------------------------------------------------------------------
The author's ROLE and, for a Functional Head, their DEPARTMENT come from the
`users` row at the moment of the save. The request carries text and nothing
that says whose it is, so a CEO cannot write the MD's stream, an MD cannot
write the CEO's, and a Functional Head cannot write another department's by
naming it (spec 34.5, 34.6, rule 37.12). A department a CEO or MD writes an
expectation FOR is checked against the tenant's own departments.

EVERY SAVE IS A NEW VERSION
---------------------------
Nothing is rewritten (the tables are insert-only in the database). The version
number is the stream's highest plus one, under an advisory lock per stream so
two saves of one stream cannot mint the same number; the partial UNIQUE
indexes of 0135 are the second line. The previous version stays readable, and
the audit trail says a new version superseded it.

AN AI DRAFT IS NEVER SAVED BY ITSELF
------------------------------------
`draft_provenance` only RECORDS that the leader generated a draft before
saving and which sources it read. The text saved is always the text the leader
submitted: this module is the only writer and it takes text from a person.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import CompanyDepartment
from app.models.leadership import (
    AUTHOR_CEO,
    AUTHOR_FUNCTIONAL_HEAD,
    AUTHOR_MD,
    AUTHOR_ROLES,
    LeadershipDepartmentExpectation,
    LeadershipProfile,
)
from app.models.user import User
from app.services import departments as departments_service
from app.services import locks
from app.services.audit import record_action
from app.services.leadership import compiler
from app.services.leadership.context import latest_profile

__all__ = [
    "AUTHOR_LABELS",
    "FIELD_MAX_CHARS",
    "LeadershipRefused",
    "NOT_A_LEADER",
    "WRONG_FIELDS_CEO_MD",
    "WRONG_FIELDS_FUNCTIONAL_HEAD",
    "author_of",
    "company_overview",
    "my_view",
    "save",
]

#: A leader's input is a summary of intent, not a document.
FIELD_MAX_CHARS = 4000

#: The words a reader is shown for each author role.
AUTHOR_LABELS: dict[str, str] = {
    AUTHOR_CEO: "CEO",
    AUTHOR_MD: "MD",
    AUTHOR_FUNCTIONAL_HEAD: "Functional Head",
}

#: Server-authored refusals, rendered verbatim by the screen.
NOT_A_LEADER = (
    "Leadership Intelligence is written by a CEO, an MD or a Functional Head. "
    "Your role can read it but not write it."
)
WRONG_FIELDS_FUNCTIONAL_HEAD = (
    "A Functional Head writes their own department's requirements and the "
    "ideal employee for it. Company-wide requirements and other departments' "
    "expectations are written by the CEO and the MD."
)
WRONG_FIELDS_CEO_MD = (
    "A CEO or MD writes company-wide requirements and an expectation for each "
    "department. A department's own requirements are written by its Functional "
    "Head."
)
NO_DEPARTMENT = (
    "Your account has no department, so there is nothing to write yet. Ask your "
    "company's Super Admin to assign your department."
)

#: The advisory-lock namespace: one writer per stream at a time.
LOCK_NAMESPACE = "leadership.profile"


class LeadershipRefused(Exception):
    """A refusal with the status the route answers and the sentence it says."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Author:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    department_id: uuid.UUID | None
    name: str


def _role_value(role: Any) -> str:
    return str(getattr(role, "value", role))


async def author_of(session: AsyncSession, user_id: uuid.UUID) -> Author | None:
    """The leader behind this session, from the users row, or None."""
    user = await session.get(User, user_id)
    if user is None or user.tenant_id is None:
        return None
    role = _role_value(user.role)
    if role not in AUTHOR_ROLES:
        return None
    return Author(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=role,
        department_id=getattr(user, "department_id", None),
        name=user.full_name or user.email or "",
    )


def _clean(text: str | None) -> str:
    return (text or "").strip()


async def _expectations(
    session: AsyncSession, profile_id: uuid.UUID
) -> list[LeadershipDepartmentExpectation]:
    return list(
        (
            await session.execute(
                select(LeadershipDepartmentExpectation).where(
                    LeadershipDepartmentExpectation.profile_id == profile_id
                )
            )
        ).scalars()
    )


async def _author_name(session: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    return (user.full_name or user.email) if user is not None else None


def _excluded(compiled: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The lines the compiler set aside, with their reason word (spec 19.1)."""
    out: list[dict[str, str]] = []
    for item in (compiled or {}).get("excluded_or_unsafe_claims") or []:
        if isinstance(item, Mapping) and item.get("text"):
            out.append({"text": str(item["text"]), "reason": str(item.get("reason") or "")})
    return out


async def _profile_out(
    session: AsyncSession, profile: LeadershipProfile | None
) -> dict[str, Any] | None:
    if profile is None:
        return None
    expectations = await _expectations(session, profile.id)
    excluded = _excluded(profile.compiled_json)
    for row in expectations:
        excluded.extend(_excluded(row.compiled_json))
    return {
        "id": profile.id,
        "author_role": profile.author_role,
        "author_label": AUTHOR_LABELS.get(profile.author_role, profile.author_role),
        "department_id": profile.department_id,
        "version": profile.version,
        "saved_by": await _author_name(session, profile.author_user_id),
        "saved_at": profile.created_at,
        "company_requirements": profile.company_requirements or "",
        "department_requirements": profile.department_requirements or "",
        "ideal_employee_expectations": profile.ideal_employee_expectations or "",
        "department_expectations": {
            str(row.department_id): row.requirements for row in expectations
        },
        "lines_not_used": excluded,
        "used_ai_draft": bool((profile.draft_provenance_json or {}).get("ai_draft_used")),
    }


async def _department(
    session: AsyncSession, tenant_id: uuid.UUID, department_id: uuid.UUID | None
) -> CompanyDepartment | None:
    if department_id is None:
        return None
    return await departments_service.get_department(session, tenant_id, department_id)


async def my_view(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """What the leader's own page shows: their stream's latest version, and
    the departments they may write for. A reader who is not a leader is told
    so, with no stream."""
    author = await author_of(session, user_id)
    if author is None:
        return {"can_author": False, "refusal": NOT_A_LEADER}
    if author.role == AUTHOR_FUNCTIONAL_HEAD:
        department = await _department(session, author.tenant_id, author.department_id)
        profile = await latest_profile(
            session, author.tenant_id, AUTHOR_FUNCTIONAL_HEAD, author.department_id
        )
        return {
            "can_author": department is not None,
            "refusal": None if department is not None else NO_DEPARTMENT,
            "author_role": author.role,
            "author_label": AUTHOR_LABELS[author.role],
            "department": (
                {"id": department.id, "name": department.name} if department else None
            ),
            "departments": [],
            "profile": await _profile_out(session, profile),
        }
    active = await departments_service.list_departments(session, author.tenant_id)
    profile = await latest_profile(session, author.tenant_id, author.role)
    return {
        "can_author": True,
        "refusal": None,
        "author_role": author.role,
        "author_label": AUTHOR_LABELS[author.role],
        "department": None,
        "departments": [{"id": row.id, "name": row.name} for row in active],
        "profile": await _profile_out(session, profile),
    }


async def _next_version(
    session: AsyncSession, tenant_id: uuid.UUID, role: str, department_id: uuid.UUID | None
) -> tuple[int, LeadershipProfile | None]:
    previous = await latest_profile(session, tenant_id, role, department_id)
    return (previous.version + 1 if previous is not None else 1), previous


async def save(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    company_requirements: str | None,
    department_requirements: str | None,
    ideal_employee_expectations: str | None,
    department_expectations: Mapping[uuid.UUID, str],
    ai_draft_sources: list[str] | None,
) -> dict[str, Any]:
    """Save a NEW version of the caller's own stream. Returns `my_view`.

    Refuses (`LeadershipRefused`) a caller who is not a leader (403), a field
    that belongs to another role (422), a department that is not the tenant's
    (422) and an empty save (422). Nothing is written on any refusal.
    """
    author = await author_of(session, user_id)
    if author is None:
        raise LeadershipRefused(403, NOT_A_LEADER)
    company = _clean(company_requirements)
    department_text = _clean(department_requirements)
    ideal = _clean(ideal_employee_expectations)
    expectations = {
        department_id: _clean(text)
        for department_id, text in department_expectations.items()
        if _clean(text)
    }
    if author.role == AUTHOR_FUNCTIONAL_HEAD:
        if company or department_expectations:
            raise LeadershipRefused(422, WRONG_FIELDS_FUNCTIONAL_HEAD)
        # THE DEPARTMENT IS THE USER ROW'S, never the request's (rule 37.12).
        department = await _department(session, author.tenant_id, author.department_id)
        if department is None:
            raise LeadershipRefused(422, NO_DEPARTMENT)
        stream_department: uuid.UUID | None = department.id
        if not (department_text or ideal):
            raise LeadershipRefused(422, "Write at least one of the two sections before saving.")
    else:
        if department_text or ideal:
            raise LeadershipRefused(422, WRONG_FIELDS_CEO_MD)
        for department_id in department_expectations:
            if await _department(session, author.tenant_id, department_id) is None:
                raise LeadershipRefused(422, departments_service.DEPARTMENT_NOT_FOUND)
        stream_department = None
        if not (company or expectations):
            raise LeadershipRefused(
                422,
                "Write the company-wide requirements or at least one department's "
                "expectations before saving.",
            )

    stream = f"{author.tenant_id}:{author.role}:{stream_department or 'company'}"
    await locks.advisory_xact_lock(session, LOCK_NAMESPACE, stream)
    version, previous = await _next_version(
        session, author.tenant_id, author.role, stream_department
    )
    profile_id = uuid.uuid4()
    compiled = compiler.compile_profile(
        role=author.role,
        profile_id=profile_id,
        version=version,
        department_id=stream_department,
        company_requirements=company or None,
        department_requirements=department_text or None,
        ideal_employee_expectations=ideal or None,
    )
    profile = LeadershipProfile(
        id=profile_id,
        tenant_id=author.tenant_id,
        author_user_id=author.user_id,
        author_role=author.role,
        department_id=stream_department,
        version=version,
        company_requirements=company or None,
        department_requirements=department_text or None,
        ideal_employee_expectations=ideal or None,
        compiled_json=compiled,
        draft_provenance_json={
            "ai_draft_used": ai_draft_sources is not None,
            "sources": sorted(set(ai_draft_sources or [])),
        },
    )
    session.add(profile)
    await session.flush()
    kept = sum(len(compiled[name]) for name in compiler.LISTS)
    excluded = len(compiled["excluded_or_unsafe_claims"])
    for department_id, text in sorted(expectations.items(), key=lambda item: str(item[0])):
        artifact = compiler.compile_expectation(
            role=author.role,
            profile_id=profile_id,
            version=version,
            department_id=department_id,
            requirements=text,
        )
        kept += sum(len(artifact[name]) for name in compiler.LISTS)
        excluded += len(artifact["excluded_or_unsafe_claims"])
        session.add(
            LeadershipDepartmentExpectation(
                profile_id=profile_id,
                tenant_id=author.tenant_id,
                department_id=department_id,
                requirements=text,
                compiled_json=artifact,
            )
        )
    await session.flush()

    # Identifiers and counts only: never a leader's words (spec 29).
    facts = {
        "author_role": author.role,
        "version": version,
        "department_id": str(stream_department) if stream_department else None,
        "department_expectations": len(expectations),
        "lines_kept": kept,
        "lines_not_used": excluded,
        "ai_draft_used": ai_draft_sources is not None,
    }
    await record_action(
        session,
        action="leadership_profile_saved",
        actor_user_id=author.user_id,
        actor_role=author.role,
        tenant_id=author.tenant_id,
        resource_type="leadership_profile",
        resource_id=profile_id,
        new_state={"version": version},
        metadata=facts,
    )
    if previous is not None:
        await record_action(
            session,
            action="leadership_profile_superseded",
            actor_user_id=author.user_id,
            actor_role=author.role,
            tenant_id=author.tenant_id,
            resource_type="leadership_profile",
            resource_id=previous.id,
            previous_state={"version": previous.version},
            new_state={"superseded_by_version": version},
            metadata={"author_role": author.role},
        )
    return await my_view(session, user_id)


async def company_overview(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Every leader's latest version, for the company-wide readers (Super
    Admin, CEO, MD). Read-only; the capability gate is the route's."""
    departments = await departments_service.list_departments(
        session, tenant_id, include_inactive=True
    )
    heads = []
    for department in departments:
        profile = await latest_profile(
            session, tenant_id, AUTHOR_FUNCTIONAL_HEAD, department.id
        )
        if profile is not None:
            heads.append(
                {
                    "department": {"id": department.id, "name": department.name},
                    "profile": await _profile_out(session, profile),
                }
            )
    return {
        "ceo": await _profile_out(session, await latest_profile(session, tenant_id, AUTHOR_CEO)),
        "md": await _profile_out(session, await latest_profile(session, tenant_id, AUTHOR_MD)),
        "functional_heads": heads,
        "departments": [
            {"id": row.id, "name": row.name} for row in departments if row.is_active
        ],
    }
