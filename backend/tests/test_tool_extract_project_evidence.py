"""`extract_project_evidence`: derived project evidence, through the tool layer.

PLAN-p5 decision P5-D10. Project evidence stays out of the chunk index and
crosses the tool boundary instead, so Vaada reads it under the same permission,
stage, validation, timeout and tenant-keyed cache as every other read. Pinned
here:

  * ONLY the interviewer holds it, and only in the assessment stage. A scorer
    that could read a candidate's projects would grade the projects rather than
    the answers;
  * compensation-shaped KEYS are stripped at any depth before a block is built.
    The stack's `languages` map renders its KEYS as the "Observed stack" line,
    so an unstripped `expected_ctc` key would reach a question-writing prompt
    as though it were a technology;
  * the cached result is keyed on the tenant the call was made for, so two
    employers assessing the same candidate never share an entry.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.tools import executor, permissions, policy, registry, schemas
from app.services.tools.errors import ToolPermissionError, ToolPolicyError
from tests import rag_world


def test_only_the_interviewer_holds_the_tool() -> None:
    assert permissions.agents_holding("extract_project_evidence") == frozenset(
        {permissions.AGENT_INTERVIEWER}
    )


def test_it_is_available_in_the_assessment_stage_only() -> None:
    assert policy.TOOL_STAGES["extract_project_evidence"] == frozenset(
        {policy.STAGE_ASSESSMENT}
    )


def test_it_is_a_cached_read() -> None:
    spec = registry.get("extract_project_evidence")
    assert spec is not None
    assert spec.risk is policy.RiskClass.READ
    assert spec.idempotent is True
    assert spec.cache_ttl_seconds > 0


def test_the_cache_key_carries_the_tenant() -> None:
    payload = schemas.ProjectEvidenceRequest(candidate_id=uuid.uuid4())
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    key_a = executor._cache_key("extract_project_evidence", payload, tenant_a)
    key_b = executor._cache_key("extract_project_evidence", payload, tenant_b)
    assert str(tenant_a) in key_a
    assert key_a != key_b


def test_compensation_keys_are_stripped_at_any_depth() -> None:
    from app.services.tools import implementations as impl

    stripped = impl._strip_compensation(
        {
            "technology_stack": {
                "languages": {"Python": 12, "expected_ctc": 1},
                "technologies": ["Kafka", "payments gateway"],
            },
            "salary_band": "L5",
            "units": [{"statement": "built it", "budget_owner": "me"}],
        }
    )
    assert stripped == {
        "technology_stack": {
            "languages": {"Python": 12},
            # VALUES are never matched: "payments" is a stack, not a salary.
            "technologies": ["Kafka", "payments gateway"],
        },
        "units": [{"statement": "built it"}],
    }


@pytest.fixture
async def world():
    engine, factory = await rag_world.factory_or_skip()
    fx = rag_world.World()
    await rag_world.seed(factory, fx)
    try:
        yield factory, fx
    finally:
        await rag_world.cleanup(factory, fx)
        await engine.dispose()


async def _add_project(factory, fx) -> None:
    from app.core.db import superadmin_scope
    from app.models.project import CandidateProject

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(CandidateProject(
                    candidate_id=fx.candidate_id,
                    name="Ingestion replay tool",
                    description="Replays a Kafka topic into a staging cluster.",
                    submission_kind="repository",
                    status="processed",
                    evidence_json={
                        "technology_stack": {
                            "languages": {"Python": 40, "expected_ctc": 1},
                            "technologies": ["Kafka"],
                        },
                        "potential_gaps": ["No load test is included."],
                        "salary_expectation": "4,00,000",
                    },
                    ai_interpretation_json={
                        "synthesis": "A working replay tool with a clear CLI.",
                        "compensation_note": "asked for a raise",
                    },
                ))


def _context(fx, *, stage=policy.STAGE_ASSESSMENT, tenant=None):
    tenant_id = tenant or fx.tenant_id
    return policy.ToolContext(
        tenant_id=tenant_id,
        stage=stage,
        objects=(policy.ToolObject("application", fx.link_id, fx.tenant_id),),
    )


async def test_the_interviewer_reads_the_block_without_compensation(world) -> None:
    from app.core.db import superadmin_scope

    factory, fx = world
    await _add_project(factory, fx)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                result = await executor.execute(
                    "extract_project_evidence",
                    permissions.AGENT_INTERVIEWER,
                    {"candidate_id": str(fx.candidate_id)},
                    session=s,
                    context=_context(fx),
                )
    text = result.value.text
    assert "Ingestion replay tool" in text
    assert "Python" in text and "Kafka" in text
    assert "No load test is included." in text
    for leaked in ("expected_ctc", "4,00,000", "salary", "raise"):
        assert leaked not in text, leaked


async def test_the_scorer_and_the_report_writer_are_refused_before_any_read(
    world,
) -> None:
    from app.core.db import superadmin_scope

    factory, fx = world
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for agent in (permissions.AGENT_SCORING, permissions.AGENT_PPI_REPORT):
                    with pytest.raises(ToolPermissionError):
                        await executor.execute(
                            "extract_project_evidence",
                            agent,
                            {"candidate_id": str(fx.candidate_id)},
                            session=s,
                            context=_context(fx),
                        )


async def test_the_reporting_stage_is_refused(world) -> None:
    from app.core.db import superadmin_scope

    factory, fx = world
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                with pytest.raises(ToolPolicyError) as refused:
                    await executor.execute(
                        "extract_project_evidence",
                        permissions.AGENT_INTERVIEWER,
                        {"candidate_id": str(fx.candidate_id)},
                        session=s,
                        context=_context(fx, stage=policy.STAGE_REPORTING),
                    )
    assert refused.value.reason == "tool_not_available_in_stage"


async def test_an_application_in_another_tenant_is_refused_as_absent(world) -> None:
    from app.core.db import superadmin_scope
    from app.services.tools.errors import ToolScopeError

    factory, fx = world
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                with pytest.raises(ToolScopeError):
                    await executor.execute(
                        "extract_project_evidence",
                        permissions.AGENT_INTERVIEWER,
                        {"candidate_id": str(fx.candidate_id)},
                        session=s,
                        context=_context(fx, tenant=uuid.uuid4()),
                    )
