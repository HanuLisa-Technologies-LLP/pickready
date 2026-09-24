"""Shared world and model double for the Skills step tests.

Four suites (`test_job_skills_service`, `test_job_skills_save`,
`test_job_skills_draft`, `test_reconcile_job_setup`, and the reminder) drive the
skills service and the job-setup tasks against a REAL database. They share one
way of building the world, so a column added to `jobs` or `job_competencies`
breaks them in one place rather than five.

EVERY ASSERTION THAT MATTERS READS FROM A SECOND CONNECTION after the writer
committed (`committed_*`), because a write that answered and then rolled back at
commit is invisible to the connection that made it (the 2026-09-20 class).

THE MODEL IS DOUBLED AT THE ROUTER BOUNDARY, never inside Sutra:
`FakeRouter` replaces `llm_router.chat_completion` as Sutra imports it, answers
from a script, and records every message list it was sent, so a test can read
the exact payload a real provider would have received.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope

#: A JD long enough for the SWOT sufficiency gate, with required skills.
JD_MARKDOWN = "## Description\n" + " ".join(
    (
        "The data platform team builds the streaming pipelines behind every "
        "finance report, and this engineer owns them in production, from the "
        "Kafka topics to the warehouse tables the analysts query each morning."
    ).split()
    * 3
)
JD_SKILLS = ["Python", "Apache Kafka", "SQL"]

SWOT = {
    "strengths": "The team already owns a well run warehouse.",
    "weaknesses": "Nobody on the team has run streaming pipelines in production.",
    "opportunities": "Analytics engineers could grow into the role.",
    "threats": "Three competitors are hiring the same profile.",
}


def sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


@dataclass
class World:
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    client: uuid.UUID = field(default_factory=uuid.uuid4)
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    skills: dict[str, uuid.UUID] = field(default_factory=dict)


#: (bucket, name, active, authored_by, swot_origin, evidence)
Skill = tuple[str, str, bool, str, str | None, str | None]


async def seed(
    *,
    skills: Sequence[Skill] = (),
    swot_saved: bool = True,
    swot_version: int = 2,
    saved: bool = False,
    draft_status: str = "not_started",
    draft_requested_at: datetime | None = None,
    drafted_at: datetime | None = None,
    drafted_swot_version: int | None = None,
    compensation: dict | None = None,
    department: str | None = None,
    extra_users: Sequence[tuple[str, str]] = (),
    lifecycle_state: str = "DRAFT",
) -> World:
    """One tenant, one client super admin, one job, its SWOT and skills."""
    w = World()
    async with sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:t, :n, :d, 'pending')"
                    ),
                    {"t": w.tenant, "n": f"skills-{w.tenant}", "d": f"{w.tenant}.skills.test"},
                )
                users = [("client", "client")] + list(extra_users)
                for key, role in users:
                    user_id = w.client if key == "client" else uuid.uuid4()
                    w.users[key] = user_id
                    await session.execute(
                        text(
                            "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
                            "VALUES (:u, :t, :e, :n, :r, 'active')"
                        ),
                        {"u": user_id, "t": w.tenant, "e": f"{user_id.hex[:12]}@skills.test",
                         "n": f"Person {key}", "r": role},
                    )
                await session.execute(
                    text(
                        "INSERT INTO jobs (id, tenant_id, title, department, jd_json, "
                        "jd_markdown, status, assessment_grade, lifecycle_state, "
                        "about_company, experience_min_years, experience_max_years, "
                        "compensation_json, framework_approved_at, assessment_context_json, "
                        "skills_draft_status, skills_draft_requested_at, skills_drafted_at, "
                        "skills_drafted_swot_version, created_by) VALUES (:j, :t, "
                        "'Senior Data Engineer', :dept, CAST(:jd AS jsonb), :md, 'draft', "
                        "'managerial', :life, 'We run payment rails for small lenders.', "
                        "5, 9, CAST(:comp AS jsonb), :approved, CAST(:ctx AS jsonb), :ds, "
                        ":dra, :dat, :dsv, :creator)"
                    ),
                    {
                        "j": w.job, "t": w.tenant, "dept": department,
                        "jd": json.dumps({"skills": JD_SKILLS}), "md": JD_MARKDOWN,
                        "life": lifecycle_state,
                        "comp": json.dumps(compensation) if compensation else None,
                        "approved": datetime.now(timezone.utc) if saved else None,
                        "ctx": (
                            json.dumps({"role_summary": "Owns the pipelines.", "generated_by": "sutra"})
                            if saved else None
                        ),
                        "ds": draft_status, "dra": draft_requested_at, "dat": drafted_at,
                        "dsv": drafted_swot_version, "creator": w.client,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO job_swot_analyses (id, tenant_id, job_id, status, "
                        "strengths, weaknesses, opportunities, threats, human_edited, "
                        "version, last_modified_at, last_modified_by) VALUES (:i, :t, :j, "
                        ":s, :st, :we, :op, :th, :he, :v, :lm, :lb)"
                    ),
                    {
                        "i": uuid.uuid4(), "t": w.tenant, "j": w.job,
                        "s": "edited" if swot_saved else "generated",
                        "st": SWOT["strengths"], "we": SWOT["weaknesses"],
                        "op": SWOT["opportunities"], "th": SWOT["threats"],
                        "he": swot_saved, "v": swot_version,
                        "lm": datetime.now(timezone.utc) if swot_saved else None,
                        "lb": w.client if swot_saved else None,
                    },
                )
                for ordinal, (bucket, name, active, authored, origin, evidence) in enumerate(
                    skills, 1
                ):
                    skill_id = uuid.uuid4()
                    w.skills[name] = skill_id
                    await session.execute(
                        text(
                            "INSERT INTO job_competencies (id, tenant_id, job_id, category, "
                            "name, required_level, ordinal, is_active, authored_by, "
                            "swot_origin, observable_evidence, description) VALUES (:i, :t, "
                            ":j, :c, :n, 82, :o, :a, :ab, :so, :e, :e)"
                        ),
                        {"i": skill_id, "t": w.tenant, "j": w.job, "c": bucket, "n": name,
                         "o": ordinal, "a": active, "ab": authored, "so": origin,
                         "e": evidence},
                    )
    return w


async def drop(w: World) -> None:
    async with sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": w.tenant})


async def run_as(w: World, work: Callable[[Any, Any], Any], *, commit: bool = True) -> Any:
    """Run `work(session, job)` in one transaction under the bypass scope, as a
    worker does, then commit (or roll back) exactly as the request would."""
    from app.models.job import Job

    async with sessions()() as session:
        await session.begin()
        async with superadmin_scope(session):
            job = await session.get(Job, w.job)
            try:
                result = await work(session, job)
            except BaseException:
                await session.rollback()
                raise
            if commit:
                await session.commit()
            else:
                await session.rollback()
            return result


async def committed_skills(w: World) -> list[dict]:
    async with sessions()() as session:
        async with superadmin_scope(session):
            rows = (
                await session.execute(
                    text(
                        "SELECT id, category, name, is_active, authored_by, swot_origin, "
                        "observable_evidence, description, force_rank, ordinal, provenance_json "
                        "FROM job_competencies WHERE job_id = :j ORDER BY category, ordinal, name"
                    ),
                    {"j": w.job},
                )
            ).mappings().all()
            return [dict(row) for row in rows]


async def committed_job(w: World) -> dict:
    async with sessions()() as session:
        async with superadmin_scope(session):
            row = (
                await session.execute(
                    text(
                        "SELECT framework_approved_at, assessment_status, lifecycle_state, "
                        "assessment_context_json, skills_draft_status, skills_draft_error, "
                        "skills_drafted_swot_version, criteria_version, finalized_by, "
                        "question_reminder_sent_at FROM jobs WHERE id = :j"
                    ),
                    {"j": w.job},
                )
            ).mappings().one()
            return dict(row)


async def committed_audit(w: World, action: str) -> list[dict]:
    async with sessions()() as session:
        async with superadmin_scope(session):
            rows = (
                await session.execute(
                    text(
                        "SELECT action, actor_user_id, actor_role, agent_name, metadata, "
                        "new_state FROM audit_log WHERE job_id = :j AND action = :a"
                    ),
                    {"j": w.job, "a": action},
                )
            ).mappings().all()
            return [dict(row) for row in rows]


def active(rows: Iterable[dict], bucket: str | None = None) -> list[str]:
    return [
        row["name"] for row in rows
        if row["is_active"] and (bucket is None or row["category"] == bucket)
    ]


class FakeRouter:
    """`llm_router.chat_completion`, answered from a script.

    Each entry in `answers` is a dict (sent back as JSON), an exception
    instance (raised), or a callable taking the messages and returning either.
    Every call's messages are recorded in `calls`.
    """

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, list[dict]]] = []

    async def __call__(self, task_type, messages, response_format_json=False, session=None):
        self.calls.append((task_type, [dict(message) for message in messages]))
        if not self.answers:
            raise AssertionError("the model was called more times than the test scripted")
        answer = self.answers.pop(0)
        if callable(answer) and not isinstance(answer, (dict, BaseException)):
            answer = await answer(messages) if _is_coroutine(answer) else answer(messages)
        if isinstance(answer, BaseException):
            raise answer
        return json.dumps(answer)

    def payload(self, index: int = 0) -> dict:
        """The user message of call `index`, parsed: what the model was GIVEN."""
        _task, messages = self.calls[index]
        return json.loads(messages[1]["content"])

    def raw(self, index: int = 0) -> str:
        _task, messages = self.calls[index]
        return json.dumps(messages)


def _is_coroutine(fn: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


def install(monkeypatch, router: FakeRouter) -> FakeRouter:
    from app.services.hiring import sutra

    monkeypatch.setattr(sutra.llm_router, "chat_completion", router)
    return router


def context_answer(skills: Sequence[tuple[str, str]], *, refused: Sequence[tuple[str, str]] = ()):
    """A valid assessment-context answer for exactly these (bucket, name) pairs."""
    priorities: dict[str, int] = {}
    entries = []
    for bucket, name in skills:
        if (bucket, name) in set(refused):
            continue
        priorities[bucket] = priorities.get(bucket, 0) + 1
        entries.append(
            {
                "bucket": bucket,
                "name": name,
                "evidence_line": (
                    f"Has shipped work that depended on {name} to production and "
                    "explained the decisions they made along the way."
                ),
                "priority": priorities[bucket],
            }
        )
    return {
        "role_summary": "Builds and runs the streaming pipelines that feed finance reporting.",
        "skills": entries,
        "refused": [{"bucket": b, "name": n, "reason": "not a capability"} for b, n in refused],
    }
