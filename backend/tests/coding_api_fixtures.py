"""Shared fixtures for the coding API tests (Phase 4 WP-4C).

Built on `test_coding_tables`' `world` (a tenant, a job, one competency, a
candidate, an application and an ACTIVE conversation), plus what the
candidate routes need before they answer: the job's assessment open, a
portal user LINKED to the candidate record, and an active proctoring
session. The HTTP clients override exactly the dependencies production
builds, including the candidate BYPASS scope, so a cross-candidate case is
refused by the handler rather than by a tenant filter that production does
not have.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_current_user,
    get_tenant_db,
)
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from tests.test_coding_tables import _World

CONV = "/api/v2/assessments/conversations"


@dataclass
class Candidate:
    world: _World
    user_id: uuid.UUID
    proctoring_id: uuid.UUID

    @property
    def principal(self) -> CurrentUser:
        return CurrentUser(
            user_id=self.user_id, tenant_id=None, role=Role.candidate, audience=AUDIENCE_CANDIDATE
        )


async def _exec(factory, sql: str, **params) -> None:
    async with factory() as session:
        async with superadmin_scope(session):
            await session.execute(text(sql), params)
            await session.commit()


async def sign_in(factory, candidate_id: uuid.UUID) -> uuid.UUID:
    """A portal user linked to `candidate_id` (Phase 6: `candidates.user_id`
    is the only thing the candidate routes resolve through)."""
    user_id = uuid.uuid4()
    await _exec(
        factory,
        "INSERT INTO users (id, tenant_id, email, full_name, role, status) "
        "VALUES (:u, NULL, :e, 'Coding Subject', 'candidate', 'active')",
        u=user_id,
        e=f"{user_id}@coding-api.test",
    )
    await _exec(factory, "UPDATE candidates SET user_id = :u WHERE id = :c", u=user_id, c=candidate_id)
    return user_id


@pytest.fixture(autouse=True)
async def _fresh_rate_windows() -> AsyncIterator[None]:
    """Empty the coding routes' Redis windows around every test.

    A test client carries no session cookie, so the limiter keys these
    requests by address and every test shares one window; left alone, one
    test's presses would spend the next test's allowance."""
    from app.core import cache

    async def _clear() -> None:
        client = cache._redis()  # noqa: SLF001 - the limiter's own client
        if client is None:
            return
        for bucket in ("coding_run", "coding_run_poll", "coding_submission_state"):
            async for key in client.scan_iter(match=f"ratelimit:{bucket}:*"):
                await client.delete(key)

    await _clear()
    yield
    await _clear()


@pytest.fixture
async def candidate(factory, world) -> AsyncIterator[Candidate]:
    await _exec(factory, "UPDATE jobs SET assessment_status = 'ready_for_candidates' WHERE id = :j", j=world.job)
    # `world` writes the link with a source the ORM enum does not carry; the
    # candidate routes LOAD the link, so it gets a real one.
    await _exec(factory, "UPDATE job_candidate_links SET source = 'fresh' WHERE id = :l", l=world.link)
    # The coding question is ON SCREEN: the server's turn clock has a turn open
    # on it with the coding allocation, the state `respond` itself leaves after
    # opening a turn. A Run is refused on a turn with no clock (p4-4c hunk 2),
    # so a world with no open turn could not exercise the Run routes at all.
    await _exec(
        factory,
        "UPDATE assessment_conversations SET turn_seq = 1, prompt_shown_at = now(), "
        "turn_allocation_seconds = :a WHERE id = :c",
        a=get_settings().assessment_time_coding_seconds,
        c=world.conversation,
    )
    user_id = await sign_in(factory, world.candidate)
    proctoring_id = uuid.uuid4()
    await _exec(
        factory,
        "INSERT INTO proctoring_sessions (id, tenant_id, conversation_id, job_candidate_link_id, "
        "candidate_id, job_id, consented_at, started_at, outcome) "
        "VALUES (:p, :t, :c, :l, :cand, :j, now(), now(), 'active')",
        p=proctoring_id,
        t=world.tenant,
        c=world.conversation,
        l=world.link,
        cand=world.candidate,
        j=world.job,
    )
    try:
        yield Candidate(world=world, user_id=user_id, proctoring_id=proctoring_id)
    finally:
        await _exec(factory, "DELETE FROM users WHERE id = :u", u=user_id)


@asynccontextmanager
async def candidate_client(factory, holder: dict) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client signed in as `holder["principal"]` on the candidate
    audience, over the production bypass-scope session shape."""

    async def _current_candidate() -> CurrentUser:
        return holder["principal"]

    async def _candidate_db():
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_candidate] = _current_candidate
    app.dependency_overrides[get_candidate_db] = _candidate_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@asynccontextmanager
async def staff_client(factory, world: _World) -> AsyncIterator[httpx.AsyncClient]:
    """A Client Super Admin of `world`'s tenant, over the RLS tenant session."""
    principal = CurrentUser(user_id=uuid.uuid4(), tenant_id=world.tenant, role=Role.client, audience=AUDIENCE_ORG)

    async def _current_user() -> CurrentUser:
        return principal

    async def _tenant_db():
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def write_final_answer(
    factory, w: _World, question_id: uuid.UUID, code: str, *, language: str = "python", ordinal: int = 1
) -> uuid.UUID:
    """What `respond`'s structured branch writes for a coding answer: the
    agent's line, the candidate's line and the `assessment_answers` row.
    Returns the answer id."""
    agent_id, message_id, answer_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        async with superadmin_scope(session):
            for mid, speaker, o, body in (
                (agent_id, "agent", ordinal, "Write the program described above."),
                (message_id, "candidate", ordinal + 1, f"Language: {language}\n{code}"),
            ):
                await session.execute(
                    text(
                        "INSERT INTO assessment_messages (id, tenant_id, conversation_id, ordinal, speaker, "
                        "domain, question_key, content) VALUES (:m, :t, :c, :o, :s, 'must_have', :k, :body)"
                    ),
                    {"m": mid, "t": w.tenant, "c": w.conversation, "o": o, "s": speaker,
                     "k": str(question_id), "body": body},
                )
            await session.execute(
                text(
                    "INSERT INTO assessment_answers (id, tenant_id, conversation_id, question_id, message_id, "
                    "question_type, answer_json, submitted_at) VALUES (:a, :t, :c, :q, :m, 'coding', "
                    "CAST(:body AS jsonb), :now)"
                ),
                {"a": answer_id, "t": w.tenant, "c": w.conversation, "q": question_id, "m": message_id,
                 "body": json.dumps({"language": language, "code": code}), "now": datetime.now(timezone.utc)},
            )
            await session.commit()
    return answer_id
