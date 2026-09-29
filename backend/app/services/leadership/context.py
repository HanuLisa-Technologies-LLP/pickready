"""Which leadership input applies to a job, and the frozen copy a contract carries.

Owner spec 2026-09-29, sections 20 and 21.

TWO READS, NEVER CONFUSED
-------------------------
* `resolve_for_job` is LIVE: the latest CEO, MD and department Functional Head
  versions as they stand now. Only the steps that happen BEFORE the freeze read
  it: Sutra's skills draft, the SWOT draft, and Save Skills, which writes the
  result as a `job_leadership_contexts` row in the same transaction.
* `FrozenLeadership` is what an `AssessmentContract` carries: the row Save
  Skills wrote, named by the snapshot. Yukti, Vaada, Miti's rubrics and the
  PRISM Report read THIS, so a leadership version saved next month never
  moves how an applied candidate is assessed (spec 21, rule 37.9).

AN ENHANCEMENT LAYER, NOT A DEPENDENCY (spec 20)
------------------------------------------------
Missing CEO, MD or Functional Head input blocks nothing. With no applicable
line the answer is None, and every consumer then sends the request it sent
before Leadership Intelligence existed: the prompt KEY is absent, never
present and empty, so the bytes are identical.

WHAT A JOB READS, IN ORDER
--------------------------
The CEO's expectation of the job's department, the CEO's company-wide lines,
the MD's two of the same, then the department's Functional Head. A job with no
department (a legacy job) reads the company-wide lines only. Every line is
re-judged against today's bar (`compiler.kept_lines`), each source is capped so
one leader cannot crowd out the others, and the whole is capped in lines and
characters so client-authored text can never dominate the instruction above it
(the argument Drishti's prompt caps made, kept).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import CompanyDepartment
from app.models.leadership import (
    AUTHOR_CEO,
    AUTHOR_FUNCTIONAL_HEAD,
    AUTHOR_MD,
    JobLeadershipContext,
    LeadershipDepartmentExpectation,
    LeadershipProfile,
)
from app.services.leadership import compiler

__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "FrozenLeadership",
    "LeadershipContext",
    "LeadershipLine",
    "MAX_CHARS_PER_SOURCE",
    "MAX_CONTEXT_CHARS",
    "MAX_CONTEXT_LINES",
    "MAX_LINES_PER_SOURCE",
    "ROLE_FOR_SOURCE",
    "SCOPE_COMPANY",
    "SCOPE_DEPARTMENT",
    "SOURCE_CEO",
    "SOURCE_FUNCTIONAL_HEAD",
    "SOURCE_LABELS",
    "SOURCE_MD",
    "SOURCES",
    "context_digest",
    "frozen_from_row",
    "latest_profile",
    "load_frozen",
    "load_frozen_many",
    "profile_ids",
    "record_for_job",
    "resolve_for_job",
]

CONTEXT_SCHEMA_VERSION = 1

#: The provenance words a skill, a need and a report line carry (spec 22.2).
SOURCE_CEO = "leadership_ceo"
SOURCE_MD = "leadership_md"
SOURCE_FUNCTIONAL_HEAD = "leadership_functional_head"
SOURCES: tuple[str, ...] = (SOURCE_CEO, SOURCE_MD, SOURCE_FUNCTIONAL_HEAD)
ROLE_FOR_SOURCE: dict[str, str] = {
    SOURCE_CEO: AUTHOR_CEO,
    SOURCE_MD: AUTHOR_MD,
    SOURCE_FUNCTIONAL_HEAD: AUTHOR_FUNCTIONAL_HEAD,
}
_SOURCE_FOR_ROLE = {role: source for source, role in ROLE_FOR_SOURCE.items()}
#: How a reader is told whose expectation a line is. Words only.
SOURCE_LABELS: dict[str, str] = {
    SOURCE_CEO: "CEO",
    SOURCE_MD: "MD",
    SOURCE_FUNCTIONAL_HEAD: "Functional Head",
}

SCOPE_COMPANY = "company"
SCOPE_DEPARTMENT = "department"

#: Per SOURCE, so no leader crowds out another: the CEO's twenty lines cannot
#: leave the Functional Head none. The totals follow from the per-source caps.
MAX_LINES_PER_SOURCE = 5
MAX_CHARS_PER_SOURCE = 800
MAX_CONTEXT_LINES = MAX_LINES_PER_SOURCE * 3
MAX_CONTEXT_CHARS = MAX_CHARS_PER_SOURCE * 3


@dataclass(frozen=True)
class LeadershipLine:
    ref: str            # "l1".. stable within one context
    source: str         # one of SOURCES
    scope: str          # SCOPE_COMPANY | SCOPE_DEPARTMENT
    list_name: str      # one of compiler.LISTS
    text: str
    profile_id: str
    version: int

    def as_json(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "source": self.source,
            "scope": self.scope,
            "list": self.list_name,
            "text": self.text,
            "profile_id": self.profile_id,
            "version": self.version,
        }


@dataclass(frozen=True)
class LeadershipContext:
    """The compiled leadership context of one job. Never empty: no lines is None."""

    department_id: str | None
    department_name: str | None
    lines: tuple[LeadershipLine, ...]
    #: {source: {"profile_id": str, "version": int}} for every source that
    #: contributed at least one line.
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "department_id": self.department_id,
            "department_name": self.department_name,
            "sources": {key: dict(value) for key, value in sorted(self.sources.items())},
            "lines": [line.as_json() for line in self.lines],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LeadershipContext":
        return cls(
            department_id=payload.get("department_id"),
            department_name=payload.get("department_name"),
            lines=tuple(
                LeadershipLine(
                    ref=str(item["ref"]),
                    source=str(item["source"]),
                    scope=str(item["scope"]),
                    list_name=str(item["list"]),
                    text=str(item["text"]),
                    profile_id=str(item["profile_id"]),
                    version=int(item["version"]),
                )
                for item in payload.get("lines") or []
            ),
            sources={
                str(key): dict(value)
                for key, value in (payload.get("sources") or {}).items()
            },
        )

    @property
    def supplied_sources(self) -> frozenset[str]:
        return frozenset(line.source for line in self.lines)

    def lines_for(self, source: str) -> tuple[LeadershipLine, ...]:
        return tuple(line for line in self.lines if line.source == source)

    def prompt_lines(self) -> list[dict[str, str]]:
        """What a model prompt is given: the source, the scope and the text.
        Never an id, a version or a person's name."""
        return [
            {"source": line.source, "scope": line.scope, "text": line.text}
            for line in self.lines
        ]

    def profile_id(self, source: str) -> str | None:
        entry = self.sources.get(source)
        return str(entry["profile_id"]) if entry else None


@dataclass(frozen=True)
class FrozenLeadership:
    """The leadership context a contract was saved with. Read, never re-resolved."""

    context_id: uuid.UUID
    version: int
    digest: str
    context: LeadershipContext
    #: {job_competencies.id as str: source} for the skills saved with it.
    skill_sources: dict[str, str]

    def source_of(self, skill_id: uuid.UUID | str) -> str | None:
        return self.skill_sources.get(str(skill_id))

    def is_leadership_skill(self, skill_id: uuid.UUID | str) -> bool:
        return self.source_of(skill_id) in SOURCES

    def lines_for_skill(self, skill_id: uuid.UUID | str) -> tuple[LeadershipLine, ...]:
        """The lines of the source a leadership-sourced skill was drafted from."""
        source = self.source_of(skill_id)
        return self.context.lines_for(source) if source in SOURCES else ()


def context_digest(compiled_context: Mapping[str, Any], skill_sources: Mapping[str, str]) -> str:
    """sha256 over the frozen content: the compiled context and the sources."""
    encoded = json.dumps(
        {"context": compiled_context, "skill_sources": dict(skill_sources)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def frozen_from_row(row: JobLeadershipContext) -> FrozenLeadership:
    return FrozenLeadership(
        context_id=row.id,
        version=row.version,
        digest=row.digest,
        context=LeadershipContext.from_json(row.compiled_context_json or {}),
        skill_sources={str(k): str(v) for k, v in (row.skill_sources_json or {}).items()},
    )


# ── The live resolution ──────────────────────────────────────────────────────


async def latest_profile(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    role: str,
    department_id: uuid.UUID | None = None,
) -> LeadershipProfile | None:
    """The version in force of one stream (the highest)."""
    query = select(LeadershipProfile).where(
        LeadershipProfile.tenant_id == tenant_id,
        LeadershipProfile.author_role == role,
    )
    if role == AUTHOR_FUNCTIONAL_HEAD:
        if department_id is None:
            return None
        query = query.where(LeadershipProfile.department_id == department_id)
    else:
        query = query.where(LeadershipProfile.department_id.is_(None))
    return (
        await session.execute(query.order_by(LeadershipProfile.version.desc()).limit(1))
    ).scalar_one_or_none()


async def _expectation(
    session: AsyncSession, profile: LeadershipProfile, department_id: uuid.UUID
) -> LeadershipDepartmentExpectation | None:
    return (
        await session.execute(
            select(LeadershipDepartmentExpectation).where(
                LeadershipDepartmentExpectation.profile_id == profile.id,
                LeadershipDepartmentExpectation.department_id == department_id,
            )
        )
    ).scalar_one_or_none()


def _bounded(
    candidates: Sequence[tuple[str, str, str, str, LeadershipProfile]],
) -> tuple[LeadershipLine, ...]:
    """Cap each source in lines and characters. Order is kept."""
    per_source: dict[str, int] = {}
    chars: dict[str, int] = {}
    lines: list[LeadershipLine] = []
    for source, scope, list_name, text, profile in candidates:
        if per_source.get(source, 0) >= MAX_LINES_PER_SOURCE:
            continue
        if chars.get(source, 0) + len(text) > MAX_CHARS_PER_SOURCE:
            continue
        per_source[source] = per_source.get(source, 0) + 1
        chars[source] = chars.get(source, 0) + len(text)
        lines.append(
            LeadershipLine(
                ref=f"l{len(lines) + 1}",
                source=source,
                scope=scope,
                list_name=list_name,
                text=text,
                profile_id=str(profile.id),
                version=profile.version,
            )
        )
    return tuple(lines)


async def resolve_for_job(session: AsyncSession, job: Any) -> LeadershipContext | None:
    """The job's leadership context as it stands NOW, or None when none applies."""
    tenant_id = job.tenant_id
    department_id: uuid.UUID | None = getattr(job, "department_id", None)
    candidates: list[tuple[str, str, str, str, LeadershipProfile]] = []
    for role in (AUTHOR_CEO, AUTHOR_MD):
        profile = await latest_profile(session, tenant_id, role)
        if profile is None:
            continue
        source = _SOURCE_FOR_ROLE[role]
        # The department expectation FIRST: under the per-source cap the lines
        # a leader wrote about THIS department are the more specific ones.
        if department_id is not None:
            expectation = await _expectation(session, profile, department_id)
            if expectation is not None:
                for list_name, text in compiler.kept_lines(expectation.compiled_json):
                    candidates.append((source, SCOPE_DEPARTMENT, list_name, text, profile))
        for list_name, text in compiler.kept_lines(profile.compiled_json):
            candidates.append((source, SCOPE_COMPANY, list_name, text, profile))
    if department_id is not None:
        head = await latest_profile(session, tenant_id, AUTHOR_FUNCTIONAL_HEAD, department_id)
        if head is not None:
            for list_name, text in compiler.kept_lines(head.compiled_json):
                candidates.append(
                    (SOURCE_FUNCTIONAL_HEAD, SCOPE_DEPARTMENT, list_name, text, head)
                )
    lines = _bounded(candidates)
    if not lines:
        return None
    sources: dict[str, dict[str, Any]] = {}
    for line in lines:
        sources.setdefault(
            line.source, {"profile_id": line.profile_id, "version": line.version}
        )
    department_name = None
    if department_id is not None:
        department = await session.get(CompanyDepartment, department_id)
        department_name = department.name if department is not None else None
    return LeadershipContext(
        department_id=str(department_id) if department_id else None,
        department_name=department_name,
        lines=lines,
        sources=sources,
    )


def profile_ids(context: LeadershipContext) -> dict[str, uuid.UUID | None]:
    """The three profile columns of a `job_leadership_contexts` row."""
    return {
        f"{ROLE_FOR_SOURCE[source]}_profile_id": (
            uuid.UUID(context.profile_id(source)) if context.profile_id(source) else None
        )
        for source in SOURCES
    }


# ── The job's frozen copy ────────────────────────────────────────────────────


async def record_for_job(
    session: AsyncSession,
    job: Any,
    context: LeadershipContext,
    skill_sources: Mapping[str, str],
) -> JobLeadershipContext:
    """Write the context one Save Skills was made with. INSERT only.

    The caller (`skills.save`) holds the job's SKILLS advisory lock, so the
    next version number cannot race another save of the same job;
    `uq_job_leadership_contexts_version` is the second line.
    """
    compiled = context.as_json()
    sources = {str(key): str(value) for key, value in sorted(skill_sources.items())}
    next_version = (
        await session.execute(
            select(func.coalesce(func.max(JobLeadershipContext.version), 0)).where(
                JobLeadershipContext.job_id == job.id
            )
        )
    ).scalar_one() + 1
    row = JobLeadershipContext(
        tenant_id=job.tenant_id,
        job_id=job.id,
        department_id=uuid.UUID(context.department_id) if context.department_id else None,
        version=next_version,
        compiled_context_json=compiled,
        skill_sources_json=sources,
        digest=context_digest(compiled, sources),
        **profile_ids(context),
    )
    session.add(row)
    await session.flush()
    return row


async def load_frozen(
    session: AsyncSession, context_id: uuid.UUID | str | None
) -> FrozenLeadership | None:
    """The frozen context a contract names, or None when it names none.

    RAISES `LookupError` when a named row cannot be read: a contract naming a
    leadership context nobody can show is not a contract to read past.
    """
    if context_id is None:
        return None
    row = await session.get(JobLeadershipContext, uuid.UUID(str(context_id)))
    if row is None:
        raise LookupError(f"job_leadership_contexts {context_id} is not readable")
    return frozen_from_row(row)


async def load_frozen_many(
    session: AsyncSession, context_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, FrozenLeadership]:
    """Many frozen contexts in one query (the posting builder reads many jobs)."""
    wanted = [context_id for context_id in context_ids if context_id is not None]
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(JobLeadershipContext).where(JobLeadershipContext.id.in_(wanted))
        )
    ).scalars().all()
    return {row.id: frozen_from_row(row) for row in rows}
