"""The golden end-to-end journey, against real Postgres (CONTRACT v4 item 1).

One customer, one job, one candidate, from an empty funded tenant to the
recruiter reading the executive profile, over the real routes, with exactly
three things faked at their boundaries: the model (at the router), the code
execution provider (`override_provider(FakeProvider())`) and speech to text
(at the transcription service). The sequence lives in `harness.golden_journey`
so this module and the harness scenario drive the same journey.

HOW IT DIFFERS FROM THE HARNESS SCENARIO
------------------------------------------
Both callers are REAL sessions: the staff user and the candidate are signed in
through `auth._issue_session`, the production minting path, and no dependency
is overridden, so a gate that passes here passes for a browser. And every gate
is judged the moment it is reached, from a SECOND CONNECTION, after the
request's transaction committed or rolled back (the 2026-09-20 lesson: a write
that answered 200 and vanished is invisible to the connection that made it).

A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED. Every gate asserts a row.

Supersedes `tests/test_end_to_end_journey.py`, which drove the cross-cutting
provenance half without the HTTP API while the routes were still moving.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import auth
from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, SESSION_HINT_COOKIE
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG
from app.main import app
from app.models.user import User
from harness import golden_journey as journey
from harness import world as harness_world
from harness.doubles.golden_model import GoldenModel


def _sessions() -> async_sessionmaker:
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect():
            return True
    except OSError:
        return False
    finally:
        await engine.dispose()


def _cookie(response: Response, name: str) -> str:
    for raw in response.headers.getlist("set-cookie"):
        if raw.startswith(name + "="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"_issue_session set no {name} cookie")


async def _signed_in(user_id: uuid.UUID, audience: str) -> dict[str, str]:
    """The three cookies a browser holds after signing in, minted by the
    production path for a user the world seeded."""
    factory = _sessions()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    response = Response()
    await auth._issue_session(response, user, audience)  # noqa: SLF001
    return {
        ACCESS_COOKIE: _cookie(response, ACCESS_COOKIE),
        REFRESH_COOKIE: _cookie(response, REFRESH_COOKIE),
        SESSION_HINT_COOKIE: "1",
    }


class _RealClient:
    """One TestClient for the whole journey (one lifespan), the principal
    switched by swapping the cookie jar, never by an override."""

    def __init__(self, http: TestClient, staff: dict[str, str], candidate: dict[str, str]):
        self._http = http
        self._staff = staff
        self._candidate = candidate
        self.bodies: list[tuple[str, str, Any]] = []

    def as_staff(self) -> None:
        self._http.cookies.clear()
        self._http.cookies.update(self._staff)

    def as_candidate(self) -> None:
        self._http.cookies.clear()
        self._http.cookies.update(self._candidate)

    def call(self, step: str, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        response = self._http.request(method, path, **kwargs)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text or None
        self.bodies.append((step, path, body))
        return response.status_code, body


# ── The gates, each judged from a second connection ─────────────────────────


async def _rows(sql: str, **params: Any) -> list[Any]:
    factory = _sessions()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return list((await session.execute(text(sql), params)).all())


async def _judge(name: str, state: journey.JourneyState) -> None:
    job = str(state.job)
    if name == "job_created":
        rows = await _rows("SELECT tenant_id, ratified_at FROM jobs WHERE id = :j", j=job)
        assert rows and rows[0].tenant_id == state.tenant and rows[0].ratified_at is None
    elif name == "jd_saved":
        rows = await _rows("SELECT jd_markdown FROM jobs WHERE id = :j", j=job)
        assert rows[0].jd_markdown.strip() == journey.JD_MARKDOWN.strip()
    elif name == "swot_saved":
        rows = await _rows(
            "SELECT weaknesses, version FROM job_swot_analyses WHERE job_id = :j", j=job
        )
        assert rows and rows[0].weaknesses == journey.SWOT["weaknesses"]
    elif name == "skills_drafted":
        rows = await _rows(
            "SELECT category, name FROM job_competencies WHERE job_id = :j AND is_active",
            j=job,
        )
        assert {row.category for row in rows} >= {"must_have", "behavioural"}
    elif name == "skills_saved":
        rows = await _rows(
            "SELECT framework_approved_at FROM jobs WHERE id = :j", j=job
        )
        assert rows[0].framework_approved_at is not None
        missing = await _rows(
            "SELECT name FROM job_competencies WHERE job_id = :j AND is_active "
            "AND (observable_evidence IS NULL OR btrim(observable_evidence) = '')",
            j=job,
        )
        assert not missing, f"saved skills without hidden context: {missing}"
    elif name == "job_published":
        rows = await _rows("SELECT ratified_at, posting_start_date FROM jobs WHERE id = :j", j=job)
        assert rows[0].ratified_at is not None and rows[0].posting_start_date is not None
    elif name == "applied":
        rows = await _rows(
            "SELECT job_id, candidate_id, status, validation_json FROM job_candidate_links "
            "WHERE id = :l",
            l=str(state.link),
        )
        assert rows and rows[0].candidate_id == state.candidate
        assert rows[0].status == "applied" and rows[0].validation_json
        assert not await _rows(
            "SELECT id FROM assessment_conversations WHERE job_candidate_link_id = :l",
            l=str(state.link),
        ), "applying created an assessment"
    elif name == "matched":
        rows = await _rows(
            "SELECT yukti_status, yukti_pre_score FROM job_candidate_links WHERE id = :l",
            l=str(state.link),
        )
        assert rows[0].yukti_status == "scored" and rows[0].yukti_pre_score is not None
    elif name == "invited":
        rows = await _rows(
            "SELECT id, status FROM assessment_conversations WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert len(rows) == 1 and rows[0].status == "active"
        state.conversation = rows[0].id
        link = await _rows("SELECT status FROM job_candidate_links WHERE id = :l", l=str(state.link))
        assert link[0].status == "assessment_invited"
    elif name == "questions_written":
        rows = await _rows(
            "SELECT question_type FROM candidate_questions WHERE job_candidate_link_id = :l",
            l=str(state.link),
        )
        assert rows, "no question was written for the invited candidate"
    else:
        raise AssertionError(f"no judge for gate {name!r}")


def test_the_golden_journey() -> None:
    if not asyncio.run(_reachable()):
        pytest.skip("no database reachable")
    sessions = harness_world.session_factory()
    world = asyncio.run(harness_world.build("golden_journey_ready", {}, sessions=sessions))
    reached: list[str] = []

    def gate(name: str, state: journey.JourneyState) -> None:
        asyncio.run(_judge(name, state))
        reached.append(name)

    model = GoldenModel()
    try:
        staff = asyncio.run(_signed_in(world.id("staff"), AUDIENCE_ORG))
        candidate = asyncio.run(_signed_in(world.id("candidate_user"), AUDIENCE_CANDIDATE))
        state = journey.JourneyState(
            tenant=world.id("tenant"), staff=world.id("staff"), candidate=world.id("candidate")
        )
        with TestClient(app) as http, model.installed(), journey.object_store():
            client = _RealClient(http, staff, candidate)
            journey.drive(client, state, gate)
        assert reached == list(journey.GATES)
        assert model.unscripted == [], f"a model call nobody scripted: {model.unscripted}"
    finally:
        asyncio.run(harness_world.teardown(world, sessions=sessions))
