"""Telemetry event store + metric engine foundation (Master Directive Part 2).

Three layers, mirroring the split in the code:

* the threshold helper and event-code registry, pure and always runnable;
* `telemetry_events.emit`'s never-raise contract, on a real database;
* the metric formulas end to end over inserted pipeline rows.

Same convention as test_billing.py / test_stem_credit_rates.py: arithmetic
always runs; database tests skip cleanly without the containerised database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import metrics, telemetry_events
from tests.test_billing import _factory_or_skip, _tenant


# ── Registry and thresholds (always run) ────────────────────────────────────

def test_event_code_registry_matches_part2_section_5_1() -> None:
    """All ten section 5.1 codes exist, partitioned into wired vs pending,
    and every one fits the varchar(30) column."""
    expected = {
        "EV_REQ_CREATED", "EV_CALIB_SENT", "EV_CALIB_APPROVED",
        "EV_PROFILE_SUBMIT", "EV_HM_DECISION", "EV_INT_COMPLETED",
        "EV_SCORECARD_SUB", "EV_OFFER_EXTENDED", "EV_OFFER_DECISION",
        "EV_ONBOARD_JOIN",
    }
    assert telemetry_events.EVENT_CODES == expected
    assert (
        telemetry_events.WIRED_EVENT_CODES | telemetry_events.PENDING_EVENT_CODES
        == expected
    )
    assert not (
        telemetry_events.WIRED_EVENT_CODES & telemetry_events.PENDING_EVENT_CODES
    )
    assert all(len(code) <= 30 for code in expected)
    # The four milestones this codebase actually has today.
    assert telemetry_events.WIRED_EVENT_CODES == {
        "EV_REQ_CREATED", "EV_PROFILE_SUBMIT", "EV_HM_DECISION",
        "EV_INT_COMPLETED",
    }


def test_health_bands_prl_boundaries() -> None:
    """Section 3.1 PRL: Green < 24.0h, Amber 24.0-48.0h, Red > 48.0h."""
    green, amber = metrics.PRL_GREEN_HOURS, metrics.PRL_AMBER_HOURS
    assert (green, amber) == (24.0, 48.0)
    assert metrics.health(23.9, green, amber) == "green"
    assert metrics.health(24.0, green, amber) == "amber"   # falls TO the band
    assert metrics.health(36.0, green, amber) == "amber"
    assert metrics.health(48.0, green, amber) == "amber"   # inclusive top
    assert metrics.health(48.1, green, amber) == "red"


def test_health_bands_sla_boundaries() -> None:
    """Section 3.1 SLA_PR: Green >= 90.0%, Amber 75.0-89.9%, Red < 75.0%."""
    green, amber = metrics.SLA_GREEN_PCT, metrics.SLA_AMBER_PCT
    assert (green, amber) == (90.0, 75.0)
    assert metrics.health(90.0, green, amber) == "green"   # inclusive
    assert metrics.health(89.9, green, amber) == "amber"
    assert metrics.health(75.0, green, amber) == "amber"   # inclusive
    assert metrics.health(74.9, green, amber) == "red"


def test_health_bands_csr_boundaries() -> None:
    """Section 3.2 CSR_aging: Green < 10.0%, Amber 10.0-20.0%, Red > 20.0%."""
    green, amber = metrics.CSR_GREEN_PCT, metrics.CSR_AMBER_PCT
    assert (green, amber) == (10.0, 20.0)
    assert metrics.health(9.9, green, amber) == "green"
    assert metrics.health(10.0, green, amber) == "amber"
    assert metrics.health(20.0, green, amber) == "amber"
    assert metrics.health(20.1, green, amber) == "red"


def test_health_bands_aisp_and_ttf() -> None:
    """Section 3.3: AISP >= 80 green, 65-79.9 amber, < 65 red; TTF < 35 days
    green, 35-50 amber, > 50 red."""
    assert metrics.health(80.0, metrics.AISP_GREEN_PCT, metrics.AISP_AMBER_PCT) == "green"
    assert metrics.health(79.9, metrics.AISP_GREEN_PCT, metrics.AISP_AMBER_PCT) == "amber"
    assert metrics.health(64.9, metrics.AISP_GREEN_PCT, metrics.AISP_AMBER_PCT) == "red"
    assert metrics.health(34.9, metrics.TTF_GREEN_DAYS, metrics.TTF_AMBER_DAYS) == "green"
    assert metrics.health(50.0, metrics.TTF_GREEN_DAYS, metrics.TTF_AMBER_DAYS) == "amber"
    assert metrics.health(50.1, metrics.TTF_GREEN_DAYS, metrics.TTF_AMBER_DAYS) == "red"


# ── emit(): never-raise contract (containerised database) ───────────────────

@pytest.mark.asyncio
async def test_emit_writes_a_row_and_swallows_failures() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            job_id = uuid.uuid4()

            assert await telemetry_events.emit(
                session,
                tenant_id=tenant_id,
                event_code=telemetry_events.EV_REQ_CREATED,
                job_id=job_id,
                correlation_id="job-test",
                payload={"title": "Data Engineer", "grade": "non_managerial"},
            )
            row = (
                await session.execute(
                    text(
                        "SELECT event_code, job_id, correlation_id, payload, "
                        "occurred_at FROM telemetry_events "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).mappings().one()
            assert row["event_code"] == "EV_REQ_CREATED"
            assert str(row["job_id"]) == str(job_id)
            assert row["correlation_id"] == "job-test"
            assert row["payload"]["title"] == "Data Engineer"
            assert row["occurred_at"] is not None  # server-stamped

            # A bogus tenant violates the FK. emit must swallow it, and the
            # SAVEPOINT must leave the outer transaction fully usable.
            assert not await telemetry_events.emit(
                session,
                tenant_id=uuid.uuid4(),
                event_code=telemetry_events.EV_HM_DECISION,
            )
            # An unknown code is refused up front, also without raising.
            assert not await telemetry_events.emit(
                session, tenant_id=tenant_id, event_code="EV_NOT_A_THING"
            )
            # The outer transaction was not poisoned: reads and writes still
            # work in the same transaction after the failed insert.
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            assert await telemetry_events.emit(
                session,
                tenant_id=tenant_id,
                event_code=telemetry_events.EV_PROFILE_SUBMIT,
                payload={"source": "applied"},
            )
            count = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM telemetry_events "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).scalar_one()
            assert count == 2
            await session.rollback()
    finally:
        await engine.dispose()


# ── Metric formulas end to end (containerised database) ─────────────────────

@pytest.mark.asyncio
async def test_csr_and_prl_over_real_pipeline_rows() -> None:
    """Insert a tenant with a small pipeline and check CSR / PRL / SLA
    against hand-computed values."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            from app.models.candidate import (
                Candidate, JobCandidateLink, PipelineStatusEntry,
            )
            from app.models.enums import JobStatus, LinkSource, PipelineStatus
            from app.models.job import Job

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            now = datetime.now(timezone.utc)

            job = Job(
                tenant_id=tenant_id, title="Backend Engineer", jd_json={},
                status=JobStatus.draft, created_at=now - timedelta(days=20),
            )
            session.add(job)
            await session.flush()

            def _candidate(tag: str) -> Candidate:
                return Candidate(
                    tenant_id=tenant_id, email=f"{tag}-{uuid.uuid4().hex[:8]}@t.invalid"
                )

            def _link(candidate: Candidate, status: str, *, created: datetime,
                      updated: datetime) -> JobCandidateLink:
                return JobCandidateLink(
                    tenant_id=tenant_id, job_id=job.id,
                    candidate_id=candidate.id, source=LinkSource.fresh,
                    status=status, created_at=created, status_updated_at=updated,
                )

            cands = [_candidate(tag) for tag in ("stale", "fresh", "gone")]
            session.add_all(cands)
            await session.flush()

            # Active, sitting undecided in `applied` (3-day stage threshold)
            # for 10 days: STALE, and counts against SLA compliance.
            stale = _link(
                cands[0], "applied",
                created=now - timedelta(days=10),
                updated=now - timedelta(days=10),
            )
            # Active, moved to `shortlisted` an hour ago: not stale (2-day
            # threshold), decided 10 hours after creation.
            fresh = _link(
                cands[1], "shortlisted",
                created=now - timedelta(hours=11),
                updated=now - timedelta(hours=1),
            )
            # Terminal: excluded from the active pipeline entirely.
            gone = _link(
                cands[2], "rejected",
                created=now - timedelta(days=30),
                updated=now - timedelta(days=29),
            )
            session.add_all([stale, fresh, gone])
            await session.flush()
            session.add(
                PipelineStatusEntry(
                    tenant_id=tenant_id, job_candidate_link_id=fresh.id,
                    status=PipelineStatus.shortlisted,
                    at=fresh.created_at + timedelta(hours=10),
                )
            )
            session.add(
                PipelineStatusEntry(
                    tenant_id=tenant_id, job_candidate_link_id=gone.id,
                    status=PipelineStatus.rejected,
                    at=gone.created_at + timedelta(hours=48),
                )
            )
            await session.flush()

            # CSR: 2 active (stale + fresh), 1 stale -> 50.0% -> red.
            # The whole-tenant query would sweep other tests' leftovers, so
            # pin `now`; rows from this test are the only ones this tenant has.
            csr = await metrics.candidate_stagnation_rate(
                session, tenant_id, now=now
            )
            assert csr["value"] == 50.0
            assert csr["status"] == "red"
            assert csr["inputs"] == {
                "stale_candidates": 1,
                "active_pipeline": 2,
                "stage_thresholds_days": dict(metrics.STAGE_STALE_THRESHOLD_DAYS),
            }

            # PRL: decisions exist for `fresh` (10h) and `gone` (48h) ->
            # mean 29.0h -> amber. `stale` is undecided so contributes no
            # latency but stays in the SLA denominator.
            prl = await metrics.profile_review_latency(session, tenant_id)
            assert prl["value"] == 29.0
            assert prl["status"] == "amber"
            assert prl["inputs"]["profiles_reviewed"] == 2
            assert prl["inputs"]["profiles_submitted"] == 3

            # SLA_PR at the 24h target: only `fresh` decided within 24h of
            # submission, over 3 submitted -> 33.33% -> red.
            sla = await metrics.profile_review_sla(session, tenant_id)
            assert sla["value"] == 33.33
            assert sla["status"] == "red"
            assert sla["inputs"]["within_sla"] == 1

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_aisp_counts_only_decided_databank_links() -> None:
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
            job = Job(
                tenant_id=tenant_id, title="Platform Engineer", jd_json={},
                status=JobStatus.draft,
            )
            session.add(job)
            await session.flush()

            statuses = [
                ("shortlisted", LinkSource.databank),   # accepted
                ("interview_scheduled", LinkSource.databank),  # accepted
                ("rejected", LinkSource.databank),      # rejected
                ("applied", LinkSource.databank),       # undecided, excluded
                ("rejected", LinkSource.fresh),         # not AI-matched
            ]
            for status, source in statuses:
                candidate = Candidate(
                    tenant_id=tenant_id,
                    email=f"aisp-{uuid.uuid4().hex[:10]}@t.invalid",
                )
                session.add(candidate)
                await session.flush()
                session.add(
                    JobCandidateLink(
                        tenant_id=tenant_id, job_id=job.id,
                        candidate_id=candidate.id, source=source, status=status,
                    )
                )
            await session.flush()

            aisp = await metrics.sourcing_precision(session, tenant_id)
            # 2 accepted / 3 decided = 66.67% -> amber (65.0-79.9 band).
            assert aisp["value"] == 66.67
            assert aisp["status"] == "amber"
            assert aisp["inputs"]["ai_profiles_presented"] == 4
            assert aisp["inputs"]["accepted"] == 2
            assert aisp["inputs"]["rejected"] == 1
            assert aisp["inputs"]["undecided"] == 1
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_tenant_reports_no_data_not_a_verdict() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            result = await metrics.overview(session, tenant_id)
            for name, metric in result["metrics"].items():
                assert metric["value"] is None, name
                assert metric["status"] is None, name
            # The blocked metrics are named, not silently absent.
            assert "join_realization_rate" in result["unavailable"]
            assert "selectivity_ratio" in result["unavailable"]
            await session.rollback()
    finally:
        await engine.dispose()
