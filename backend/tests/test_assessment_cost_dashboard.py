"""The cost dashboard is the OWNER's, and the boundary is the audience.

WHY THE AUDIENCE AND NOT A CAPABILITY
---------------------------------------
The obvious way to gate a dashboard in this product is
`require_capability(caps.VIEW_INTELLIGENCE_DASHBOARDS)`, and it is the wrong
one here by a wide margin: that capability is held by TENANT users, and this
payload carries a per-client cost breakdown. Gating on it would hand one
customer every other customer's spend, through a route that looked correctly
protected in a diff.

So `/admin/cost/assessments` sits behind `get_superadmin_db`, exactly like
`/admin/llm/stats`, and the refusal below is asserted for both of the
audiences that must never reach it. The 403 is raised before a session is
opened, so these cases need no database and run everywhere.

THE NUMBERS ARE OPERATIONAL AND THEY STAY ON THIS SIDE
--------------------------------------------------------
Rule 1 forbids a number reaching a client. Nothing on this payload is a score,
a grade or a candidate fact; it is dollars, tokens and call counts, and the
owner is not a client in that rule's sense. What makes that safe is structural
rather than editorial: this table has exactly one reader, and it is this route.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import admin as admin_api
from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG, AUDIENCE_OWNER
from app.config import llm_providers
from app.main import app
from app.models.enums import Role
from app.services import cost_telemetry

V1 = "/api/v1"
ROUTE = f"{V1}/admin/cost/assessments"


def _run(coro):
    """One fresh loop per call: an engine bound to a closed loop surfaces later
    as an unrelated timeout in a different test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _table_present() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                sa.text("SELECT 1 FROM assessment_cost_records LIMIT 0")
            )
        return True
    except Exception:  # noqa: BLE001 -- no database, or migration 0109 not applied
        return False
    finally:
        await engine.dispose()


# ── The audience boundary ────────────────────────────────────────────────────


def _as(principal: CurrentUser) -> Iterator[TestClient]:
    async def _current_user() -> CurrentUser:
        return principal

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize(
    "principal",
    [
        pytest.param(
            CurrentUser(
                user_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                role=Role.client,
                audience=AUDIENCE_ORG,
            ),
            id="an employer super admin",
        ),
        pytest.param(
            CurrentUser(
                user_id=uuid.uuid4(),
                tenant_id=None,
                role=Role.candidate,
                audience=AUDIENCE_CANDIDATE,
            ),
            id="a candidate",
        ),
        pytest.param(
            # The audience is right and the ROLE is not. Both halves of
            # `get_superadmin_db`'s check matter, and a test that only varied
            # the audience would pass against a gate that had lost one of them.
            CurrentUser(
                user_id=uuid.uuid4(),
                tenant_id=None,
                role=Role.bd,
                audience=AUDIENCE_OWNER,
            ),
            id="platform BD staff",
        ),
    ],
)
def test_the_cost_dashboard_refuses_everyone_but_the_owner(principal) -> None:
    for http in _as(principal):
        response = http.get(ROUTE)
    assert response.status_code == 403


# ── The month window ─────────────────────────────────────────────────────────


def test_the_window_is_half_open_and_utc() -> None:
    start, end = admin_api._month_window("2026-09")
    assert start == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_december_rolls_into_the_next_year() -> None:
    """The arithmetic that a `month + 1` would get wrong once a year."""
    start, end = admin_api._month_window("2026-12")
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_twelve_windows_tile_a_year_without_overlapping() -> None:
    previous_end = None
    for month in range(1, 13):
        start, end = admin_api._month_window(f"2026-{month:02d}")
        if previous_end is not None:
            assert start == previous_end
        assert end > start
        previous_end = end
    assert previous_end == datetime(2027, 1, 1, tzinfo=timezone.utc)


# ── The rollup, over real rows ───────────────────────────────────────────────


pytestmark_db = pytest.mark.skipif(
    not _run(_table_present()),
    reason="no database, or migration 0109_assessment_cost_records is not applied",
)


class _World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.job = uuid.uuid4()
        self.link = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.plan = uuid.uuid4()
        self.slug = f"tier-{self.plan.hex[:6]}"


async def _seed(session, world: _World) -> None:
    ids = {
        "tenant": str(world.tenant),
        "job": str(world.job),
        "link": str(world.link),
        "candidate": str(world.candidate),
        "plan": str(world.plan),
        "slug": world.slug,
    }
    await session.execute(
        sa.text(
            "INSERT INTO pricing_plans (id, slug, name, applications_per_month, "
            " price_inr, rate_per_application_inr) "
            "VALUES (:plan, :slug, 'Cost Test', 100, 10000, 100)"
        ),
        ids,
    )
    await session.execute(
        sa.text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status, "
            " current_plan_id) "
            "VALUES (:tenant, :name, :domain, 'pending', :plan)"
        ),
        {**ids, "name": f"Cost-{world.tenant.hex[:6]}",
         "domain": f"{world.tenant.hex[:10]}.cost.test"},
    )
    await session.execute(
        sa.text(
            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
            "VALUES (:job, :tenant, 'Data Engineer', CAST('{}' AS jsonb), 'draft')"
        ),
        ids,
    )
    await session.execute(
        sa.text(
            "INSERT INTO candidates (id, full_name, email, consent_databank) "
            "VALUES (:candidate, 'Ada Rao', :email, false)"
        ),
        {**ids, "email": f"{world.candidate.hex[:10]}@cost.test"},
    )
    await session.execute(
        sa.text(
            "INSERT INTO job_candidate_links "
            "(id, tenant_id, job_id, candidate_id, source, status) "
            "VALUES (:link, :tenant, :job, :candidate, 'fresh', 'applied')"
        ),
        ids,
    )


def _spend(tally: cost_telemetry.UsageTally, task_type: str, model: str) -> None:
    tally.add(
        cost_telemetry.UsageEvent(
            task_type=task_type,
            model=model,
            provider=llm_providers.PROVIDER,
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cached_prompt_tokens=250_000,
            had_usage=True,
        )
    )


@pytestmark_db
def test_two_flushes_accumulate_onto_one_application_and_the_owner_reads_them() -> None:
    """The whole path, from two separate pieces of spend to the rollup.

    TWO flushes, not one, because that is the real shape: a conversation turn
    today and a scoring run when the candidate finishes, in different processes.
    A test that flushed once would pass against a writer that OVERWROTE rather
    than accumulated, which is the defect most likely to be introduced here.

    Read back from a SECOND SESSION after the commit, for the reason
    `test_audit_single_insert_api` records: an assertion on the object the
    writer is holding cannot see a write that answered and then vanished.
    """
    world = _World()
    sessions = _sessions()

    async def _write() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await _seed(session, world)
                    tier = await cost_telemetry.pricing_tier_for(
                        session, world.tenant
                    )
                    assert tier == world.slug

                    turn = cost_telemetry.UsageTally()
                    _spend(turn, "conversation_turn", llm_providers.MODEL_LUNA)
                    assert await cost_telemetry.flush(
                        session,
                        scope=cost_telemetry.AssessmentScope(
                            tenant_id=world.tenant,
                            job_id=world.job,
                            link_id=world.link,
                        ),
                        tally=turn,
                        conversation_turns=1,
                        questions_asked=3,
                        pricing_tier=tier,
                    )

                    scoring = cost_telemetry.UsageTally()
                    _spend(scoring, "report_synthesis", llm_providers.MODEL_TERRA)
                    assert await cost_telemetry.flush(
                        session,
                        scope=cost_telemetry.AssessmentScope(
                            tenant_id=world.tenant,
                            job_id=world.job,
                            link_id=world.link,
                        ),
                        tally=scoring,
                        questions_asked=3,
                        pricing_tier="a-later-plan-that-must-not-overwrite",
                    )

    async def _read() -> dict:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    row = (
                        await session.execute(
                            sa.text(
                                "SELECT input_tokens, cached_input_tokens, calls, "
                                " calls_reporting_cache, conversation_turns, "
                                " questions_asked, estimated_cost_usd, "
                                " synthesis_cost_usd, pricing_tier, cost_basis, "
                                " models_json, pricing_json "
                                "FROM assessment_cost_records "
                                "WHERE job_candidate_link_id = :link"
                            ),
                            {"link": str(world.link)},
                        )
                    ).mappings().one()
                    summary = await cost_telemetry.owner_cost_summary(
                        session,
                        window_start=datetime.now(timezone.utc) - timedelta(hours=1),
                        window_end=datetime.now(timezone.utc) + timedelta(hours=1),
                        usd_to_inr=88.0,
                        alert_threshold_inr=150.0,
                    )
                    return {"row": dict(row), "summary": summary}

    async def _cleanup() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :tenant"),
                        {"tenant": str(world.tenant)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = :candidate"),
                        {"candidate": str(world.candidate)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM pricing_plans WHERE id = :plan"),
                        {"plan": str(world.plan)},
                    )

    _run(_write())
    try:
        result = _run(_read())
    finally:
        _run(_cleanup())

    row = result["row"]
    # Two calls of a million prompt tokens each, accumulated rather than
    # replaced.
    assert row["input_tokens"] == 2_000_000
    assert row["cached_input_tokens"] == 500_000
    assert row["calls"] == 2
    assert row["calls_reporting_cache"] == 2
    assert row["conversation_turns"] == 1
    assert row["questions_asked"] == 3
    # Luna at 1.00 + Terra at 3.00 per million prompt tokens, no cache discount.
    assert float(row["estimated_cost_usd"]) == pytest.approx(4.00)
    # Siddhi's own line is the Terra call alone.
    assert float(row["synthesis_cost_usd"]) == pytest.approx(3.00)
    # Stamped once, on the first piece of spend, and never restamped.
    assert row["pricing_tier"] == world.slug
    assert row["cost_basis"] == "estimated"
    assert set(row["models_json"]) == {
        llm_providers.MODEL_LUNA,
        llm_providers.MODEL_TERRA,
    }
    assert row["pricing_json"]["rates"][llm_providers.MODEL_TERRA]["prompt"] == 3.00

    summary = result["summary"]
    mine = [
        entry
        for entry in summary["by_client"]
        if entry["tenant_id"] == str(world.tenant)
    ]
    assert len(mine) == 1
    assert mine[0]["assessments"] == 1
    assert mine[0]["total_spend"]["usd"] == pytest.approx(4.00)
    assert mine[0]["total_spend"]["inr"] == pytest.approx(352.00)
    tiers = {entry["pricing_tier"] for entry in summary["by_pricing_tier"]}
    assert world.slug in tiers
    # The saving from prompt caching is counted and explicitly NOT priced.
    assert summary["prompt_cache"]["saving"]["status"] == "unavailable"
    assert summary["media_cost"]["status"] == "unavailable"


@pytestmark_db
def test_an_empty_month_reports_unavailable_rather_than_zero() -> None:
    """A month with no assessments did not cost nothing; it was not measured.

    And the Rs threshold flag must then answer `null`, not `false`: a month
    nobody measured has not been shown to be under the line, and a green tick
    on it is exactly the reading `release_gate` refuses to produce.
    """
    sessions = _sessions()

    async def _empty_window() -> dict:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    # A window in the past that no row can fall in.
                    return await cost_telemetry.owner_cost_summary(
                        session,
                        window_start=datetime(2001, 1, 1, tzinfo=timezone.utc),
                        window_end=datetime(2001, 2, 1, tzinfo=timezone.utc),
                        usd_to_inr=88.0,
                        alert_threshold_inr=150.0,
                    )

    summary = _run(_empty_window())
    assert summary["assessments"] == 0
    average = summary["average_per_assessment"]
    assert average["status"] == "unavailable"
    assert "value" not in average and "usd" not in average and "inr" not in average
    assert summary["alert"]["exceeded"] is None
    assert summary["alert"]["threshold_inr"] == 150.0


@pytestmark_db
def test_an_average_over_the_threshold_is_flagged() -> None:
    """The other direction, so the flag is not vacuously never true."""
    high = cost_telemetry._average(
        total_usd=10.0, assessments=2, rate=88.0, what="assessments"
    )
    assert high["inr"] == pytest.approx(440.0)
    assert cost_telemetry._alert(high, 150.0)["exceeded"] is True

    low = cost_telemetry._average(
        total_usd=1.0, assessments=2, rate=88.0, what="assessments"
    )
    assert cost_telemetry._alert(low, 150.0)["exceeded"] is False
