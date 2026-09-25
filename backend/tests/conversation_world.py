"""One seeded assessment, for the conversation engine's tests (PLAN-p3 WP3).

WHY IT IS SHARED
----------------
The engine is reached through six routes (start, respond, draft, and the three
voice routes) and every one of them runs the same gates in the same order: the
invitation, proctoring, the one mode, consent, the credit re-check on a first
start, the contract, and the turn. A world that got any of those wrong would
make its test pass on a path no candidate can take, so the world is built once,
here, with every gate satisfied by the row the product itself would have
written, and each test switches off exactly the one it is about.

THE CANDIDATE IS REAL. `candidate_session.create_candidate_session` mints the
user, the candidate record and the session cookies through the production
path, and the routes resolve the caller through `candidate_identity`, so a test
that passes here passes for a browser.

EVERY ASSERTION ABOUT STATE READS A SECOND CONNECTION (`committed`), after the
request's transaction has committed or rolled back. A write that answered 200
and rolled back at commit is invisible to the connection that made it (the
2026-09-20 lesson), and a dispatch registered after the commit is only visible
once the commit ran.

THE MODEL IS NEVER CALLED. `quiet_models` stubs the four places a turn reaches
a provider (classification, the question writer, the follow-up decision and
the fill-in-the-blank equivalence check) at the module attribute the engine
reads, so a test asserts the ENGINE and never a provider's mood.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope

BASE = "/api/v2/assessments"

RESUME = (
    "Senior Engineer, Northwind Payments. Led the checkout migration from the "
    "session table to edge-verified tokens, cutting read amplification."
)
ROLE_SUMMARY = (
    "The payments platform team owns checkout and settlement; this engineer "
    "keeps both correct under load."
)
TEXT_ANSWER = (
    "I owned the checkout migration end to end, moved reads to the token first "
    "and backfilled behind it, and rolled back once when the window slipped."
)

MCQ_SINGLE_PAYLOAD = {
    "options": [
        {"id": "a", "text": "A write-ahead log"},
        {"id": "b", "text": "A read replica"},
        {"id": "c", "text": "A connection pool"},
        {"id": "d", "text": "A materialised view"},
    ],
    "correct_option_id": "a",
}

FILL_BLANK_PAYLOAD = {
    "template": "A ___ index speeds up a lookup on ___.",
    "blanks": [
        {"index": 0, "accepted": ["covering"], "case_sensitive": False},
        {"index": 1, "accepted": ["the orders table"], "case_sensitive": False},
    ],
}

#: bucket -> (name, hidden evidence line)
SKILLS: dict[str, tuple[str, str]] = {
    "must_have": ("Payments reconciliation", "Names a settlement it reconciled and the break it found."),
    "nice_to_have": ("Postgres tuning", "Describes an index it chose and the query it changed."),
    "behavioural": ("Ownership under pressure", "Tells of a rollback it decided on and why."),
}

#: A plan is a list of (question_type, payload, bucket).
Plan = Sequence[tuple[str, dict[str, Any], str]]

PROSE_ONLY: Plan = (
    ("evidence_based", {}, "must_have"),
    ("short_answer", {}, "nice_to_have"),
    ("short_answer", {}, "behavioural"),
)


def sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            return True
    except OSError:
        return False
    finally:
        await engine.dispose()


@dataclass
class World:
    candidate_id: uuid.UUID
    tenant: uuid.UUID = field(default_factory=uuid.uuid4)
    job: uuid.UUID = field(default_factory=uuid.uuid4)
    profile: uuid.UUID = field(default_factory=uuid.uuid4)
    link: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation: uuid.UUID = field(default_factory=uuid.uuid4)
    skills: dict[str, uuid.UUID] = field(default_factory=dict)
    questions: list[uuid.UUID] = field(default_factory=list)


async def seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    candidate_id: uuid.UUID,
    plan: Plan = PROSE_ONLY,
    demo: bool = True,
    started: bool = False,
    digest: str = "match",
    consented: bool = True,
    proctored: bool = True,
    role_classification: str = "NON_STEM",
    open_turn: bool = False,
    turn_age_seconds: float = 0.0,
) -> World:
    """Seed one invited assessment.

    `digest` is what the questions were written against: "match" (the job's
    contract as it stands), "null" (a legacy row, generated before the digest
    existed) or "stale" (another contract). `started` stamps `started_at` and
    binds the contract the way the first start does (`lock_contract`).
    `open_turn` puts the first question on screen `turn_age_seconds` ago,
    which is what the first start does, so a respond test need not start.
    """
    from app.models import Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import (
        AssessmentConversation,
        CandidateQuestion,
        JobCompetency,
    )
    from app.models.candidate import JobCandidateLink, Profile
    from app.models.dual_mode import AssessmentConsent
    from app.models.proctoring import OUTCOME_ACTIVE, ProctoringSession
    from app.services import assessment_contract

    world = World(candidate_id=candidate_id)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(
                    id=world.tenant, name=f"Conv {world.tenant.hex[:6]}",
                    domain=f"{world.tenant}.conv.test", is_demo=demo,
                ))
                await s.flush()
                s.add(Job(
                    id=world.job, tenant_id=world.tenant, title="Payments Engineer",
                    jd_json={}, jd_markdown="Build and run the payments platform.",
                    status=JobStatus.ratified, ratified_at=now,
                    assessment_status="ready_for_candidates",
                    assessment_grade="non_managerial",
                    role_classification=role_classification,
                    framework_approved_at=now,
                    assessment_context_json={"role_summary": ROLE_SUMMARY},
                ))
                s.add(Profile(id=world.profile, candidate_id=candidate_id, resume_text=RESUME))
                await s.flush()
                s.add(JobCandidateLink(
                    id=world.link, tenant_id=world.tenant, job_id=world.job,
                    candidate_id=candidate_id, profile_id=world.profile,
                    source=LinkSource.fresh,
                    status="assessment_in_progress" if started else "assessment_invited",
                ))
                await s.flush()
                for ordinal, (bucket, (name, evidence)) in enumerate(SKILLS.items(), 1):
                    skill_id = uuid.uuid4()
                    world.skills[bucket] = skill_id
                    s.add(JobCompetency(
                        id=skill_id, tenant_id=world.tenant, job_id=world.job,
                        category=bucket, name=name, description=evidence,
                        observable_evidence=evidence, required_level=82,
                        ordinal=ordinal, force_rank=1,
                    ))
                await s.flush()
                for ordinal, (question_type, payload, bucket) in enumerate(plan):
                    question_id = uuid.uuid4()
                    world.questions.append(question_id)
                    s.add(CandidateQuestion(
                        id=question_id, tenant_id=world.tenant, job_id=world.job,
                        job_candidate_link_id=world.link,
                        competency_id=world.skills[bucket],
                        prompt=f"Question {ordinal}: tell me about {SKILLS[bucket][0]}.",
                        ordinal=ordinal, rubric_json={"bands": ordinal},
                        question_type=question_type, payload_json=dict(payload),
                        weight=1.0,
                    ))
                await s.flush()
                contract = await assessment_contract.load_contract(s, world.job)
                questions_digest = {
                    "match": contract.digest,
                    "null": None,
                    "stale": "0" * 64,
                }[digest]
                shown = now - timedelta(seconds=turn_age_seconds)
                s.add(AssessmentConversation(
                    id=world.conversation, tenant_id=world.tenant, job_id=world.job,
                    job_candidate_link_id=world.link, grade="non_managerial",
                    status="active", next_question_index=0,
                    invitation_sent_at=now,
                    started_at=now if started else None,
                    questions_contract_digest=questions_digest,
                    questions_contract_version=contract.version,
                    turn_seq=1 if open_turn else 0,
                    prompt_shown_at=shown if open_turn else None,
                    turn_allocation_seconds=(
                        _allocation(plan[0][0]) if open_turn and plan else None
                    ),
                ))
                await s.flush()
                if started:
                    await assessment_contract.lock_contract(s, world.job, world.conversation)
                if consented:
                    s.add(AssessmentConsent(
                        tenant_id=world.tenant, candidate_id=candidate_id,
                        conversation_id=world.conversation,
                        job_candidate_link_id=world.link,
                        assessment_mode="conversational",
                        consent_status="granted", consented_at=now,
                        consent_version="test", privacy_policy_version="test",
                        terms_version="test",
                    ))
                if proctored:
                    s.add(ProctoringSession(
                        tenant_id=world.tenant, conversation_id=world.conversation,
                        job_candidate_link_id=world.link, candidate_id=candidate_id,
                        job_id=world.job, consented_at=now, started_at=now,
                        outcome=OUTCOME_ACTIVE,
                    ))
    return world


def _allocation(question_type: str) -> int:
    settings = get_settings()
    if question_type in ("mcq_single", "mcq_multi", "fill_blank"):
        return settings.assessment_time_objective_seconds
    if question_type == "coding":
        return settings.assessment_time_coding_seconds
    return settings.assessment_time_prose_seconds


async def cleanup(factory: async_sessionmaker[AsyncSession], world: World) -> None:
    """Remove the world. The tenant cascade takes the job, the link, the
    conversation and everything hung off it; the profile is the candidate's."""
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("DELETE FROM credit_ledger WHERE tenant_id = :t"),
                    {"t": str(world.tenant)},
                )
                await s.execute(
                    text("DELETE FROM tenants WHERE id = :t"), {"t": str(world.tenant)}
                )
                await s.execute(
                    text("DELETE FROM profiles WHERE id = :p"), {"p": str(world.profile)}
                )


async def committed(
    factory: async_sessionmaker[AsyncSession], sql: str, **params: Any
) -> list[Any]:
    """Rows as a SECOND connection sees them, after the writer committed."""
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return list((await s.execute(text(sql), params)).all())


async def conversation_row(
    factory: async_sessionmaker[AsyncSession], world: World
) -> Any:
    rows = await committed(
        factory,
        "SELECT * FROM assessment_conversations WHERE id = :c",
        c=str(world.conversation),
    )
    return rows[0]


async def set_conversation(
    factory: async_sessionmaker[AsyncSession], world: World, **values: Any
) -> None:
    """Move the conversation's own columns, as a test's time machine."""
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text(f"UPDATE assessment_conversations SET {assignments} WHERE id = :cid"),
                    {**values, "cid": str(world.conversation)},
                )


async def add_pause(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    *,
    reason: str,
    started_ago: float,
    ended_ago: float | None = None,
    expires_in: float | None = None,
    ref_id: uuid.UUID | None = None,
) -> None:
    """A pause row as its owner would have written it, `started_ago` seconds
    back; open unless `ended_ago` is given."""
    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text(
                        "INSERT INTO assessment_pauses (id, tenant_id, conversation_id, "
                        "reason, started_at, ended_at, expires_at, ref_id) VALUES "
                        "(:id, :t, :c, :r, :s, :e, :x, :ref)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "t": str(world.tenant),
                        "c": str(world.conversation),
                        "r": reason,
                        "s": now - timedelta(seconds=started_ago),
                        "e": None if ended_ago is None else now - timedelta(seconds=ended_ago),
                        "x": None if expires_in is None else now + timedelta(seconds=expires_in),
                        "ref": None if ref_id is None else str(ref_id),
                    },
                )


async def messages(factory: async_sessionmaker[AsyncSession], world: World) -> list[Any]:
    """The transcript as a second connection reads it, oldest first."""
    return await committed(
        factory,
        "SELECT speaker, content, question_key, answer_label, evidence_gap "
        "FROM assessment_messages WHERE conversation_id = :c ORDER BY ordinal",
        c=str(world.conversation),
    )


def quiet_models(monkeypatch) -> None:
    """Stub every provider call a turn can make, at the attribute the engine
    reads. The question writer returns the row's own prompt and rubric, which
    is what a non-degraded write of an unchanged question looks like."""
    from app.services import agent_loop, answer_classification, interviewer, ppi_interview
    from app.services.assessment_formats import scoring as format_scoring

    async def _substantive(**kwargs):
        return answer_classification.Classification(
            label="substantive", confidence="high", reason="stubbed for an engine test",
            needs_rechallenge=False, scorable=True,
        )

    async def _written(*, row, **kwargs):
        return agent_loop.LoopResult(
            value={"question": row.prompt, "rubric": dict(row.rubric_json or {})},
            degraded=False, attempts=1,
        )

    async def _no_follow_up(**kwargs):
        return None

    async def _not_equivalent(session, **kwargs):
        return False

    monkeypatch.setattr(answer_classification, "classify", _substantive)
    monkeypatch.setattr(ppi_interview, "write_question", _written)
    monkeypatch.setattr(interviewer, "next_follow_up", _no_follow_up)
    monkeypatch.setattr(format_scoring, "semantically_equivalent", _not_equivalent)


def client(candidate: Any):
    """An HTTP client carrying the candidate's REAL session cookies. No
    dependency is overridden: the routes resolve who is asking exactly as they
    do for a browser."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, cookies=candidate.cookies())
