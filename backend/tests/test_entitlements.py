"""Access is not credit: `entitlements.restriction_reason` against a real ledger.

Owner spec 2026-09-29, sections 2.10 and 25. A company at zero still signs in
and reads everything; what the pool refuses is new work that will cost
credits, at the moment that work is committed. This file drives the ONE
question every gate asks, over real ledger rows, and pins:

* each action's answer at a funded, an empty and a short balance, and the
  exact sentence each refusal carries (a candidate's names no billing term);
* an invitation batch is asked about as a whole, at the job's role rate;
* a demonstration tenant is never refused;
* an unknown action RAISES rather than reading as "allowed";
* every API gate routes through it (the three route-facing call sites), so a
  refusal cannot be worded two ways.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.billing import EVENT_COMPLETED, ROLE_NON_STEM, ROLE_STEM, SUBUNITS_PER_CREDIT
from app.services import credits, entitlements

BACKEND = pathlib.Path(__file__).resolve().parents[1]


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no database reachable
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _tenant(session, *, demo: bool = False) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status, is_demo) "
            "VALUES (:id, :name, :domain, 'pending', :demo)"
        ),
        {
            "id": str(tenant_id),
            "name": f"Entitlement Test {tenant_id.hex[:8]}",
            "domain": f"{tenant_id.hex[:12]}.entitle.test",
            "demo": demo,
        },
    )
    return tenant_id


def test_an_unknown_action_raises() -> None:
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(entitlements.restriction_reason(None, uuid.uuid4(), "read_reports"))


def test_the_candidate_sentence_names_no_billing_term() -> None:
    sentence = entitlements.START_ASSESSMENT_DETAIL.lower()
    for word in ("credit", "balance", "billing", "subscription", "purchase"):
        assert word not in sentence, word


def test_the_shortfall_names_count_rate_balance_and_gap() -> None:
    from decimal import Decimal

    sentence = entitlements.shortfall_detail(
        count=3,
        role_classification=ROLE_STEM,
        required=Decimal("3.00"),
        balance=Decimal("2.00"),
    )
    assert "3 candidates" in sentence
    assert "STEM role" in sentence
    assert "3.00 credits (1.00 per assessment)" in sentence
    assert "2.00 credits, 1.00 short" in sentence
    assert "Nobody was invited" in sentence


@pytest.mark.asyncio
async def test_each_action_against_a_funded_an_empty_and_a_short_pool() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            tenant = await _tenant(session)

            # Empty: every action refused, each with its own sentence.
            reason = entitlements.restriction_reason
            assert await reason(session, tenant, entitlements.ACTION_CREATE_JOB) == (
                entitlements.CREATE_JOB_DETAIL
            )
            assert await reason(session, tenant, entitlements.ACTION_BILLABLE_AI_WORK) == (
                entitlements.BILLABLE_AI_WORK_DETAIL
            )
            assert await reason(
                session, tenant, entitlements.ACTION_START_ASSESSMENT,
                role_classification=ROLE_NON_STEM,
            ) == entitlements.START_ASSESSMENT_DETAIL
            refused = await reason(
                session, tenant, entitlements.ACTION_SEND_ASSESSMENT,
                role_classification=ROLE_NON_STEM, count=2,
            )
            assert refused is not None and "Nobody was invited" in refused

            # One credit covers either kind of completed assessment, but a
            # batch of two does not fit.
            await credits.grant(
                session, tenant_id=tenant, subunits=SUBUNITS_PER_CREDIT,
                idempotency_key=f"entitle-grant-{tenant}",
            )
            assert await reason(session, tenant, entitlements.ACTION_CREATE_JOB) is None
            assert await reason(session, tenant, entitlements.ACTION_BILLABLE_AI_WORK) is None
            assert await reason(
                session, tenant, entitlements.ACTION_START_ASSESSMENT,
                role_classification=ROLE_NON_STEM,
            ) is None
            assert await reason(
                session, tenant, entitlements.ACTION_START_ASSESSMENT,
                role_classification=ROLE_STEM,
            ) is None
            short = await reason(
                session, tenant, entitlements.ACTION_SEND_ASSESSMENT,
                role_classification=ROLE_NON_STEM, count=2,
            )
            assert short is not None
            assert "2 candidates" in short and "1.00 short" in short

            # Spent into the negative: the charge is never refused, the NEXT
            # piece of work is.
            await credits.consume(
                session, tenant_id=tenant, event_type=EVENT_COMPLETED,
                idempotency_key=f"entitle-done-{tenant}-a",
            )
            await credits.consume(
                session, tenant_id=tenant, event_type=EVENT_COMPLETED,
                idempotency_key=f"entitle-done-{tenant}-b",
            )
            assert await reason(session, tenant, entitlements.ACTION_CREATE_JOB) is not None

            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_demonstration_tenant_is_never_refused() -> None:
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            demo = await _tenant(session, demo=True)
            for action in sorted(entitlements.ACTIONS):
                assert await entitlements.restriction_reason(
                    session, demo, action, role_classification=ROLE_STEM, count=50
                ) is None, action
            await session.rollback()
    finally:
        await engine.dispose()


def _calls(path: pathlib.Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) == name or getattr(node.func, "id", None) == name)
    )


def test_every_api_gate_asks_the_one_question() -> None:
    """One wording per refusal (rule 5): the three request-facing gates ask
    `restriction_reason` and none of them asks the balance functions itself."""
    for relative in (
        "app/api/jobs.py",
        "app/api/assessment_conversation.py",
        "app/services/assessment_invitations.py",
    ):
        path = BACKEND / relative
        assert _calls(path, "restriction_reason") >= 1, relative
        for balance_question in ("has_positive_balance", "can_start_assessment"):
            assert _calls(path, balance_question) == 0, (relative, balance_question)
