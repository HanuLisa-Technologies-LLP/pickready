"""Talent Intelligence dashboards (2026-09-05 spec, sections 2-5).

Four layers, mirroring the split in the code:

* the registry: 18 dashboards in the right tiers, every widget defined, and
  the copy sweep (no em dash, no per-candidate score key);
* the health bands: threshold boundaries map to the right WORDS, pure;
* the metric arithmetic and no_data honesty, over inserted rows on the real
  migrated database (skips cleanly without it);
* the API: 403 without the capability, computed widgets and alerts with it.

The emit-never-raises contract is asserted here too, against a session whose
write path fails, because the alerts and metrics only stay honest if a
telemetry failure can never take a business write down with it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from app.services import intelligence_dashboards as dashboards
from app.services import intelligence_metrics as im
from app.services import telemetry_events
from tests.test_billing import _factory_or_skip, _tenant


# ── The registry (always runs) ──────────────────────────────────────────────

def test_exactly_eighteen_dashboards_in_the_four_tiers() -> None:
    """Section 2's taxonomy verbatim: 5 + 5 + 5 + 3, unique keys."""
    assert len(dashboards.DASHBOARDS) == 18
    per_tier: dict[str, int] = {}
    for dashboard in dashboards.DASHBOARDS:
        per_tier[dashboard.tier] = per_tier.get(dashboard.tier, 0) + 1
    assert per_tier == {"A": 5, "B": 5, "C": 5, "D": 3}
    keys = [dashboard.key for dashboard in dashboards.DASHBOARDS]
    assert len(keys) == len(set(keys))
    assert set(dashboards.TIER_TITLES) == {"A", "B", "C", "D"}


def test_every_widget_references_a_defined_metric() -> None:
    for dashboard in dashboards.DASHBOARDS:
        assert dashboard.widgets, dashboard.key
        for metric_id in dashboard.widgets:
            assert metric_id in im.METRICS, (
                f"dashboard {dashboard.key} references undefined metric "
                f"{metric_id}"
            )
        # Widgets are unique within a dashboard: the same tile twice is a
        # layout bug, not information.
        assert len(dashboard.widgets) == len(set(dashboard.widgets))


def test_every_metric_definition_is_complete_and_honest() -> None:
    """A metric either computes or states why it cannot; both carry the
    specification's threshold text; nothing is both."""
    for metric_id, definition in im.METRICS.items():
        assert definition.metric_id == metric_id
        assert definition.title and definition.formula and definition.thresholds
        assert definition.no_data_reason
        if definition.compute is None:
            # An uncomputable metric's reason must be substantive prose, not
            # the generic default.
            assert definition.no_data_reason != (
                "No data has been recorded for this metric yet."
            ), metric_id


def test_copy_sweep_no_em_dash_and_no_candidate_score_keys() -> None:
    """No em dash anywhere in the suite's copy, and no per-candidate score
    vocabulary: candidate quality never appears on these dashboards as a
    number, so nothing here may even NAME the internal score fields."""
    em_dash = chr(8212)
    forbidden = (
        "match_score", "ai_score", "assessment_score", "prescreen_score",
        "candidate score", "match percentage",
    )
    texts: list[str] = []
    for dashboard in dashboards.DASHBOARDS:
        texts += [dashboard.key, dashboard.title, dashboard.audience,
                  dashboard.description]
    for definition in im.METRICS.values():
        texts += [definition.title, definition.formula, definition.unit,
                  definition.thresholds, definition.no_data_reason,
                  definition.proxy_note or ""]
    texts += list(dashboards.TIER_TITLES.values())
    for text_value in texts:
        assert em_dash not in text_value, text_value
        lowered = text_value.lower()
        for term in forbidden:
            assert term not in lowered, f"{term!r} in {text_value!r}"


def test_availability_counts_match_compute_paths() -> None:
    computable_ids = {
        metric_id
        for metric_id, definition in im.METRICS.items()
        if definition.compute is not None
    }
    # The seven measurable today; a change here is a product change, not noise.
    assert computable_ids == {
        "PRL", "SLA_PR", "SR_INT", "CSR_AGING", "JRR", "AISP", "TTF",
    }
    for dashboard in dashboards.DASHBOARDS:
        measurable, total = dashboards.dashboard_availability(dashboard)
        assert total == len(dashboard.widgets)
        assert measurable == sum(
            1 for metric_id in dashboard.widgets if metric_id in computable_ids
        )


# ── Health bands at the boundaries (always runs) ────────────────────────────

def test_prl_and_sla_words_at_the_boundaries() -> None:
    assert im.health_for("PRL", 23.9) == "green"
    assert im.health_for("PRL", 24.0) == "amber"
    assert im.health_for("PRL", 48.0) == "amber"
    assert im.health_for("PRL", 48.1) == "red"
    assert im.health_for("SLA_PR", 90.0) == "green"
    assert im.health_for("SLA_PR", 89.9) == "amber"
    assert im.health_for("SLA_PR", 75.0) == "amber"
    assert im.health_for("SLA_PR", 74.9) == "red"


def test_selectivity_ratio_band_is_a_range_not_a_direction() -> None:
    """Section 3.1: green 2.5 to 4.0, red over 6.0 OR under 1.5; the two
    unassigned gaps read amber."""
    assert im.health_for("SR_INT", 2.5) == "green"
    assert im.health_for("SR_INT", 4.0) == "green"
    assert im.health_for("SR_INT", 4.5) == "amber"
    assert im.health_for("SR_INT", 6.0) == "amber"
    assert im.health_for("SR_INT", 6.1) == "red"
    assert im.health_for("SR_INT", 1.49) == "red"
    assert im.health_for("SR_INT", 2.0) == "amber"


def test_remaining_two_threshold_bands() -> None:
    cases = [
        ("SSL", 11.9, 12.0, 24.0, 24.1),
        ("T_CAL", 23.9, 24.0, 48.0, 48.1),
        ("COV", 24999.0, 25000.0, 60000.0, 60001.0),
        ("TTF", 34.9, 35.0, 50.0, 50.1),
    ]
    for metric_id, green_v, amber_low, amber_high, red_v in cases:
        assert im.health_for(metric_id, green_v) == "green", metric_id
        assert im.health_for(metric_id, amber_low) == "amber", metric_id
        assert im.health_for(metric_id, amber_high) == "amber", metric_id
        assert im.health_for(metric_id, red_v) == "red", metric_id
    higher_better = [
        ("RRI", 0.75, 0.74, 0.50, 0.49),
        ("QOH", 85.0, 84.9, 70.0, 69.9),
        ("JRR", 90.0, 89.9, 75.0, 74.9),
        ("AISP", 80.0, 79.9, 65.0, 64.9),
        ("CNPS", 60.0, 59.9, 30.0, 29.9),
    ]
    for metric_id, green_v, amber_high, amber_low, red_v in higher_better:
        assert im.health_for(metric_id, green_v) == "green", metric_id
        assert im.health_for(metric_id, amber_high) == "amber", metric_id
        assert im.health_for(metric_id, amber_low) == "amber", metric_id
        assert im.health_for(metric_id, red_v) == "red", metric_id


def test_sdr_only_a_stable_jd_is_green() -> None:
    assert im.health_for("SDR", 0.0) == "green"
    assert im.health_for("SDR", 0.5) == "amber"
    assert im.health_for("SDR", 15.0) == "amber"
    assert im.health_for("SDR", 15.1) == "red"


def test_ebdi_band_is_symmetric_around_calibrated() -> None:
    assert im.health_for("EBDI", 0.85) == "green"
    assert im.health_for("EBDI", 1.15) == "green"
    assert im.health_for("EBDI", 0.84) == "amber"
    assert im.health_for("EBDI", 0.70) == "amber"
    assert im.health_for("EBDI", 1.16) == "amber"
    assert im.health_for("EBDI", 1.30) == "amber"
    assert im.health_for("EBDI", 0.69) == "red"
    assert im.health_for("EBDI", 1.31) == "red"


def test_metrics_without_bands_refuse_to_band() -> None:
    with pytest.raises(ValueError):
        im.health_for("SCEI", 1.0)
    with pytest.raises(KeyError):
        im.health_for("NOT_A_METRIC", 1.0)


# ── emit() never raises even when the write path fails (pure) ───────────────

@pytest.mark.asyncio
async def test_emit_swallows_a_failing_session() -> None:
    """A session whose SAVEPOINT machinery blows up must cost the telemetry
    row and nothing else: emit returns False and raises nothing."""

    class ExplodingSession:
        def begin_nested(self):
            raise RuntimeError("database is on fire")

    ok = await telemetry_events.emit(
        ExplodingSession(),  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        event_code=telemetry_events.EV_OFFER_EXTENDED,
    )
    assert ok is False


# ── Metric arithmetic over the real database (skips without it) ─────────────

async def _seed_world(session, tenant_id, now):
    """One job, four applications at known timestamps.

    Timeline (all pipeline history under the tenant's own rows):
    * link A: applied 100h ago, decided (shortlisted) 10h later, then
      interview_completed 80h ago, offer_extended 70h ago, joined 24h ago.
    * link B: applied 90h ago, decided (rejected) 60h later.
    * link C: applied 30h ago, still undecided.
    * link D: applied 80h ago, first decision (interview_completed) 8h
      later, offer_extended 70h ago, never joined.
    """
    from app.models.candidate import (
        Candidate, JobCandidateLink, PipelineStatusEntry,
    )
    from app.models.enums import JobStatus, LinkSource, PipelineStatus
    from app.models.job import Job

    job = Job(
        tenant_id=tenant_id, title="Platform Engineer", jd_json={},
        status=JobStatus.draft, created_at=now - timedelta(days=40),
    )
    session.add(job)
    await session.flush()

    def _candidate(tag: str) -> Candidate:
        return Candidate(
            tenant_id=tenant_id, email=f"{tag}-{uuid.uuid4().hex[:8]}@t.invalid"
        )

    cands = [_candidate(tag) for tag in ("a", "b", "c", "d")]
    session.add_all(cands)
    await session.flush()

    def _link(candidate, status, created):
        return JobCandidateLink(
            tenant_id=tenant_id, job_id=job.id, candidate_id=candidate.id,
            source=LinkSource.fresh, status=status,
            created_at=created, status_updated_at=created,
        )

    link_a = _link(cands[0], "joined", now - timedelta(hours=100))
    link_b = _link(cands[1], "rejected", now - timedelta(hours=90))
    link_c = _link(cands[2], "applied", now - timedelta(hours=30))
    link_d = _link(cands[3], "offer_extended", now - timedelta(hours=80))
    session.add_all([link_a, link_b, link_c, link_d])
    await session.flush()

    history = [
        (link_a, PipelineStatus.shortlisted, now - timedelta(hours=90)),
        (link_a, PipelineStatus.interview_completed, now - timedelta(hours=80)),
        (link_a, PipelineStatus.offer_extended, now - timedelta(hours=70)),
        (link_a, PipelineStatus.joined, now - timedelta(hours=24)),
        (link_b, PipelineStatus.rejected, now - timedelta(hours=30)),
        (link_d, PipelineStatus.interview_completed, now - timedelta(hours=72)),
        (link_d, PipelineStatus.offer_extended, now - timedelta(hours=70)),
    ]
    for link, status, at in history:
        session.add(
            PipelineStatusEntry(
                tenant_id=tenant_id, job_candidate_link_id=link.id,
                status=status, at=at,
            )
        )
    await session.flush()
    return job


@pytest.mark.asyncio
async def test_metric_arithmetic_over_seeded_rows() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            now = datetime.now(timezone.utc)
            await _seed_world(session, tenant_id, now)

            # PRL: first decisions are A at 10h, B at 60h, D at 8h
            # -> mean 26.0h -> amber. C is undecided so contributes no
            # latency but stays in the SLA denominator.
            prl = await im.compute_metric(session, tenant_id, "PRL")
            assert prl["value"] == 26.0
            assert prl["status"] == "amber"
            assert prl["status_reason"] is None

            # SLA_PR: A (10h) and D (8h) decided within the 24h window;
            # B decided at 60h; C undecided. 2 of 4 -> 50.0% -> red.
            sla = await im.compute_metric(session, tenant_id, "SLA_PR")
            assert sla["value"] == 50.0
            assert sla["status"] == "red"

            # SR_INT: interviewed distinct = A, D (2); offers = A, D (2)
            # -> 1.0 -> red (premature hiring band, under 1.5).
            sr = await im.compute_metric(session, tenant_id, "SR_INT")
            assert sr["value"] == 1.0
            assert sr["status"] == "red"
            assert sr["inputs"] == {
                "candidates_interviewed": 2, "offers_extended": 2,
            }

            # JRR: joined = 1 (A) over offers = 2 -> 50.0% -> red.
            jrr = await im.compute_metric(session, tenant_id, "JRR")
            assert jrr["value"] == 50.0
            assert jrr["status"] == "red"

            # TTF: one filled job, opened 40 days ago, joined 24h ago
            # -> 39.0 days -> amber.
            ttf = await im.compute_metric(session, tenant_id, "TTF")
            assert ttf["value"] == 39.0
            assert ttf["status"] == "amber"
            assert ttf["inputs"] == {"jobs": 1, "jobs_filled": 1}

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_empty_tenant_reads_no_data_never_zero() -> None:
    """The claude.md rule this suite exists to defend: an unmeasurable figure
    is reported as no_data with a reason, never as 0.0."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            for metric_id in im.METRICS:
                result = await im.compute_metric(session, tenant_id, metric_id)
                assert result["value"] is None, metric_id
                assert result["status"] == "no_data", metric_id
                assert result["status_reason"], metric_id
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_alerts_fire_on_the_section_4_ladder() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            from app.models.candidate import Candidate, JobCandidateLink
            from app.models.enums import JobStatus, LinkSource
            from app.models.job import Job

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            now = datetime.now(timezone.utc)
            job = Job(
                tenant_id=tenant_id, title="Ops Analyst", jd_json={},
                status=JobStatus.draft, created_at=now - timedelta(days=5),
            )
            session.add(job)
            await session.flush()
            ages_hours = [20, 30, 60]  # one per rung of the ladder
            for age in ages_hours:
                candidate = Candidate(
                    tenant_id=tenant_id,
                    email=f"alert-{uuid.uuid4().hex[:8]}@t.invalid",
                )
                session.add(candidate)
                await session.flush()
                session.add(
                    JobCandidateLink(
                        tenant_id=tenant_id, job_id=job.id,
                        candidate_id=candidate.id, source=LinkSource.fresh,
                        status="applied",
                        created_at=now - timedelta(hours=age),
                        status_updated_at=now - timedelta(hours=age),
                    )
                )
            await session.flush()

            alerts = await dashboards.compute_alerts(session, tenant_id, now=now)
            by_id = {alert["id"]: alert for alert in alerts}
            assert by_id["hm-review-escalation"]["severity"] == "red"
            assert "1" in by_id["hm-review-escalation"]["detail"]
            assert by_id["hm-review-alert"]["severity"] == "red"
            assert by_id["hm-review-nudge"]["severity"] == "amber"
            # Every link_path is relative, same rule as the candidate feed.
            for alert in alerts:
                if alert["link_path"] is not None:
                    assert alert["link_path"].startswith("/")
            # Reds sort before ambers.
            severities = [alert["severity"] for alert in alerts]
            assert severities == sorted(
                severities, key=lambda word: {"red": 0, "amber": 1}[word]
            )
            await session.rollback()
    finally:
        await engine.dispose()


# ── The API surface: capability enforcement and computed reads ──────────────

@pytest.fixture
def caller() -> Iterator["_Caller"]:
    """A TestClient with an injected principal, on the real migrated database.

    Same shape as tests/dashboard_world.py's harness: real routing, real
    require_capability against real role_permissions rows (migration 0082's
    seed is exactly what the 403 case exercises), and only WHO is calling is
    injected.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.api.deps import CurrentUser, get_current_user, get_tenant_db
    from app.core.config import get_settings
    from app.core.db import tenant_scope
    from app.core.security import AUDIENCE_ORG
    from app.main import app

    if not _db_reachable():
        pytest.skip("no database reachable -- skipping intelligence API tests")

    factory = async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )

    class _Caller:
        def __init__(self) -> None:
            self.principal: CurrentUser | None = None
            self.http: TestClient | None = None

        def as_role(self, role) -> None:
            self.principal = CurrentUser(
                user_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                role=role, audience=AUDIENCE_ORG,
            )

    state = _Caller()

    async def _current_user() -> CurrentUser:
        assert state.principal is not None
        return state.principal

    async def _tenant_db():
        assert state.principal is not None
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, state.principal.tenant_id):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            state.http = http
            yield state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture(autouse=True)
def _no_permission_cache(monkeypatch):
    """Resolution runs against the real role_permissions rows, never a Redis
    row left by an earlier run (same double as dashboard_world)."""
    from app.services import tenant_cache

    async def _miss(key):  # noqa: ANN001
        return None

    async def _noop(key, value, *, ttl=120):  # noqa: ANN001
        return None

    monkeypatch.setattr(tenant_cache, "get_json", _miss)
    monkeypatch.setattr(tenant_cache, "set_json", _noop)


def _db_reachable() -> bool:
    import asyncio

    async def _probe() -> bool:
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import get_settings

        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect():
                return True
        except Exception:  # noqa: BLE001 -- the skip message carries the answer
            return False
        finally:
            await engine.dispose()

    return asyncio.run(_probe())


def test_capability_gates_every_intelligence_route(caller) -> None:
    from app.models.enums import Role

    # The Interview Manager is deliberately NOT granted the capability
    # (migration 0082 seeds an explicit False row).
    caller.as_role(Role.interview_manager)
    for path in (
        "/api/v1/intelligence/dashboards",
        "/api/v1/intelligence/dashboards/hm-sla-command-center",
        "/api/v1/intelligence/alerts",
    ):
        response = caller.http.get(path)
        assert response.status_code == 403, path
        assert "view_intelligence_dashboards" in response.json()["detail"]


def test_dashboard_reads_for_a_granted_role(caller) -> None:
    from app.models.enums import Role

    caller.as_role(Role.client)

    index = caller.http.get("/api/v1/intelligence/dashboards")
    assert index.status_code == 200
    tiers = index.json()["tiers"]
    assert [tier["tier"] for tier in tiers] == ["A", "B", "C", "D"]
    assert sum(len(tier["dashboards"]) for tier in tiers) == 18

    detail = caller.http.get(
        "/api/v1/intelligence/dashboards/hm-sla-command-center"
    )
    assert detail.status_code == 200
    body = detail.json()
    assert [w["metric_id"] for w in body["widgets"]] == ["PRL", "SLA_PR", "SSL"]
    # A fresh random tenant has nothing: every widget is honest no_data.
    for widget in body["widgets"]:
        assert widget["status"] == "no_data"
        assert widget["value"] is None
        assert widget["status_reason"]

    assert caller.http.get("/api/v1/intelligence/dashboards/nope").status_code == 404

    alerts = caller.http.get("/api/v1/intelligence/alerts")
    assert alerts.status_code == 200
    assert alerts.json()["alerts"] == []
