"""STEM-aware credit deduction (Master Directive Parts 3 and 5).

The acceptance checklist rows this file owns, verbatim from Part 5 §10 and
Part 3 §10:

  * Non-STEM completed report: exactly 1.0 credit deducted
  * STEM completed report: exactly 1.5 credits deducted
  * Non-STEM partial (3 partials): 0.33 credits each... i.e. 1/3 credit per
    partial at the non-STEM base, 0.50 at the STEM base
  * Account with 1.2 credits: STEM assessment blocked at start, non-STEM
    assessment allowed
  * Account with 0.9 credits: both blocked
  * A completed assessment LOCKS the job's classification (Part 3 Rule 5)

Same convention as test_billing.py: arithmetic always runs; ledger tests skip
cleanly without the containerised database.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.billing import (
    CONSUMPTION_SUBUNITS,
    EVENT_COMPLETED,
    EVENT_INCOMPLETE,
    EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW,
    ROLE_NON_STEM,
    ROLE_STEM,
    STEM_CONSUMPTION_SUBUNITS,
    SUBUNITS_PER_CREDIT,
    consumption_subunits,
)
from app.services import credits
from tests.test_billing import _factory_or_skip, _tenant


# ── Arithmetic (always runs) ────────────────────────────────────────────────

def test_stem_rates_match_the_directive() -> None:
    """Part 5 §2.1's table, restated independently of the constants file."""
    assert STEM_CONSUMPTION_SUBUNITS[EVENT_COMPLETED] == 90       # 1.5 credits
    assert STEM_CONSUMPTION_SUBUNITS[EVENT_INCOMPLETE] == 30      # 0.50 credits
    # Unfilled / no-response is FLAT for either role type (§2.1: "Either").
    assert STEM_CONSUMPTION_SUBUNITS[EVENT_NO_SHOW] == CONSUMPTION_SUBUNITS[EVENT_NO_SHOW]
    assert (
        STEM_CONSUMPTION_SUBUNITS[EVENT_OLD_PROFILE_REVIEW]
        == CONSUMPTION_SUBUNITS[EVENT_OLD_PROFILE_REVIEW]
    )
    # The display arithmetic is exact: two STEM reports are exactly 3 credits.
    assert credits.credits_from_subunits(90) == Decimal("1.50")
    assert credits.credits_from_subunits(2 * 90) == Decimal("3.00")
    assert credits.credits_from_subunits(30) == Decimal("0.50")


def test_rate_routing_reads_the_classification() -> None:
    """Part 5 Rule 9: the Job record's flag picks the table; NULL and unknown
    values bill at the commercially-safe non-STEM rate."""
    assert consumption_subunits(EVENT_COMPLETED, ROLE_STEM) == 90
    assert consumption_subunits(EVENT_COMPLETED, ROLE_NON_STEM) == 60
    assert consumption_subunits(EVENT_COMPLETED, None) == 60
    assert consumption_subunits(EVENT_COMPLETED, "garbage") == 60
    assert consumption_subunits(EVENT_INCOMPLETE, ROLE_STEM) == 30
    assert consumption_subunits(EVENT_INCOMPLETE, None) == 20
    # A non-billable event is None in both tables, mirroring the old lookup.
    assert consumption_subunits("grant", ROLE_STEM) is None
    assert consumption_subunits("grant", None) is None


# ── Ledger behaviour (containerised database) ───────────────────────────────

@pytest.mark.asyncio
async def test_stem_and_non_stem_reports_deduct_their_exact_rates() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            await credits.grant(
                session, tenant_id=tenant_id, subunits=5 * SUBUNITS_PER_CREDIT,
                idempotency_key=f"stem-test-grant:{tenant_id}",
            )

            assert await credits.consume(
                session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                idempotency_key=f"stem-test-stem:{tenant_id}",
                role_classification=ROLE_STEM,
            )
            balance = await credits.balance_subunits(session, tenant_id)
            assert credits.credits_from_subunits(balance) == Decimal("3.50")

            assert await credits.consume(
                session, tenant_id=tenant_id, event_type=EVENT_COMPLETED,
                idempotency_key=f"stem-test-nonstem:{tenant_id}",
                role_classification=ROLE_NON_STEM,
            )
            balance = await credits.balance_subunits(session, tenant_id)
            assert credits.credits_from_subunits(balance) == Decimal("2.50")

            # Part 3 §5.2: every deduction row carries the classification it
            # was billed at, for the audit trail.
            rows = (
                await session.execute(
                    text(
                        "SELECT metadata_json->>'role_classification' AS rc "
                        "FROM credit_ledger WHERE tenant_id = :tid "
                        "AND subunits_delta < 0 ORDER BY created_at"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).scalars().all()
            assert rows == ["STEM", "NON_STEM"]
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_point_two_credits_starts_non_stem_and_refuses_stem() -> None:
    """Part 5 §2.3 and both §10 checklists, on a real ledger."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            await credits.grant(
                session, tenant_id=tenant_id, subunits=72,  # 1.20 credits
                idempotency_key=f"gate-test-grant:{tenant_id}",
            )

            allowed, required, balance = await credits.can_start_assessment(
                session, tenant_id, role_classification=ROLE_STEM
            )
            assert not allowed
            assert required == Decimal("1.50")
            assert balance == Decimal("1.20")

            allowed, required, _ = await credits.can_start_assessment(
                session, tenant_id, role_classification=ROLE_NON_STEM
            )
            assert allowed
            assert required == Decimal("1.00")

            # 0.9 credits: both blocked. Exactly 1.0: non-STEM allowed, STEM
            # blocked (Part 5 §10, boundary rows).
            tenant_2 = await _tenant(session)
            await credits.grant(
                session, tenant_id=tenant_2, subunits=54,  # 0.90 credits
                idempotency_key=f"gate-test-grant2:{tenant_2}",
            )
            for role in (ROLE_STEM, ROLE_NON_STEM):
                allowed, _, _ = await credits.can_start_assessment(
                    session, tenant_2, role_classification=role
                )
                assert not allowed

            tenant_3 = await _tenant(session)
            await credits.grant(
                session, tenant_id=tenant_3, subunits=60,  # exactly 1.00
                idempotency_key=f"gate-test-grant3:{tenant_3}",
            )
            allowed, _, _ = await credits.can_start_assessment(
                session, tenant_3, role_classification=ROLE_NON_STEM
            )
            assert allowed
            allowed, _, _ = await credits.can_start_assessment(
                session, tenant_3, role_classification=ROLE_STEM
            )
            assert not allowed
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_completed_assessment_charges_stem_rate_and_locks_the_job() -> None:
    """Part 3 Rule 5 end to end: `charge_completed` reads the job's
    classification, bills 1.5, and stamps `classification_locked`."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            from sqlalchemy import text

            from app.models.assessment import AssessmentConversation
            from app.models.candidate import Candidate, JobCandidateLink
            from app.models.enums import JobStatus, LinkSource
            from app.models.job import Job
            from app.services import credit_reconciliation

            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            tenant_id = await _tenant(session)
            await credits.grant(
                session, tenant_id=tenant_id, subunits=5 * SUBUNITS_PER_CREDIT,
                idempotency_key=f"lock-test-grant:{tenant_id}",
            )

            job = Job(
                tenant_id=tenant_id, title="ML Engineer", jd_json={},
                status=JobStatus.draft, role_classification="STEM",
                credit_cost_per_report=Decimal("1.5"),
            )
            candidate = Candidate(tenant_id=tenant_id, email="lock@test.invalid")
            session.add_all([job, candidate])
            await session.flush()
            link = JobCandidateLink(
                tenant_id=tenant_id, job_id=job.id, candidate_id=candidate.id,
                source=LinkSource.fresh,
            )
            session.add(link)
            await session.flush()
            conversation = AssessmentConversation(
                tenant_id=tenant_id, job_id=job.id,
                job_candidate_link_id=link.id, grade="non_managerial",
            )
            session.add(conversation)
            await session.flush()

            assert await credit_reconciliation.charge_completed(
                session, conversation_id=conversation.id, tenant_id=tenant_id,
                job_candidate_link_id=link.id,
            )
            balance = await credits.balance_subunits(session, tenant_id)
            assert credits.credits_from_subunits(balance) == Decimal("3.50")

            locked = (
                await session.execute(
                    text("SELECT classification_locked FROM jobs WHERE id = :jid"),
                    {"jid": str(job.id)},
                )
            ).scalar_one()
            assert locked is True
            await session.rollback()
    finally:
        await engine.dispose()


# ── Two-tier warning alerts (Part 5 §4) ─────────────────────────────────────

def test_warning_tiers_use_the_fixed_absolute_thresholds() -> None:
    """§4.1: 20 and 10 credits, fixed system values."""
    def summary(balance_credits, granted=50, unlimited=False):
        return credits.BalanceSummary(
            balance_subunits=int(balance_credits * SUBUNITS_PER_CREDIT),
            granted_subunits=granted * SUBUNITS_PER_CREDIT,
            consumed_subunits=0, month_by_event={}, rollover_subunits=0,
            in_deficit=False, unlimited=unlimited,
        )

    assert summary(21).warning_level == 0
    assert summary(20).warning_level == 1      # falls TO the threshold
    assert summary(11).warning_level == 1
    assert summary(10).warning_level == 2
    assert summary(1).warning_level == 2
    assert summary(10, unlimited=True).warning_level == 0
    assert summary(0, granted=0).warning_level == 0  # never-granted account
    # low_balance keeps its name for existing consumers, on the new rule.
    assert summary(20).low_balance is True
    assert summary(21).low_balance is False


def test_estimate_matches_the_directive_worked_example() -> None:
    """Part 3 §7.4: 20 credits at 1.5 credits per STEM report covers
    approximately 13 more assessments. Rounded DOWN (§4.2)."""
    twenty = 20 * SUBUNITS_PER_CREDIT
    assert credits.estimated_assessments_remaining(twenty, Decimal("1.5")) == 13
    assert credits.estimated_assessments_remaining(twenty, Decimal("1.2")) == 16
    assert credits.estimated_assessments_remaining(twenty, Decimal("1.0")) == 20
    assert credits.estimated_assessments_remaining(0, Decimal("1.2")) == 0
    assert credits.estimated_assessments_remaining(-60, Decimal("1.2")) == 0
