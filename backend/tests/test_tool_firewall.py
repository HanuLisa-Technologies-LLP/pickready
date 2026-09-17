"""The tool firewall: what the policy engine refuses, and when it refuses it.

RPN-AI-UP-001 W3.2 and W3.3. The property under test is not "the decision is
correct" but "the decision happens FIRST". Every refusal here is asserted with a
handler that raises the moment it is entered, because a refusal that ran the
handler has already read the row it was refusing to show, and a permission
layer that reads first and refuses second is a permission layer only in the
sense that it prints a different message afterwards.

The cross-tenant case is asserted on the SHAPE of the refusal as well as on the
fact of it. RBAC 4 forbids one client from INFERRING another client's
resources, so the answer is a 404 that names nothing: a 403 would confirm that
the object exists, and an agent that can tell "forbidden" from "absent" is a
cross-tenant existence oracle with a prompt in front of it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict

from app.services import tools
from app.services.tools import errors, executor, permissions, policy, registry


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 1


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubled: int


class HandlerWasReached(AssertionError):
    """Raised by the trap handler. Its own type is the assertion.

    A counter incremented inside the handler would also work and would report
    the failure one line later, as a confusing equality mismatch. This reports
    it as what it is.
    """


async def _trap(payload: _In):
    raise HandlerWasReached("the policy engine let the call through")


@pytest.fixture
def sandbox(monkeypatch):
    """An empty registry and a two-agent matrix, restored afterwards.

    Registering throwaway tools into the real registry would make one test's
    fixtures visible to the audit tests at the bottom of this file, which is
    precisely the drift those exist to catch.
    """
    snapshot = registry._reset_for_tests()
    monkeypatch.setattr(
        permissions,
        "AGENT_TOOLS",
        {"tester": frozenset({"probe"}), "outsider": frozenset()},
    )
    monkeypatch.setitem(policy.TOOL_STAGES, "probe", frozenset({policy.STAGE_REPORTING}))
    try:
        yield
    finally:
        registry._restore_for_tests(snapshot)
        policy.TOOL_STAGES.pop("probe", None)


def _spec(handler=_trap, **overrides) -> registry.ToolSpec:
    defaults = dict(
        name="probe",
        handler=handler,
        input_model=_In,
        output_model=_Out,
        description="test tool",
        needs_session=False,
        max_attempts=1,
        timeout_seconds=0.5,
        deadline_seconds=2.0,
    )
    defaults.update(overrides)
    return registry.register(registry.ToolSpec(**defaults))


_TENANT_A = uuid.uuid4()
_TENANT_B = uuid.uuid4()


# ── The refusal happens before the handler ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_ungranted_agent_never_reaches_the_handler(sandbox) -> None:
    _spec()
    with pytest.raises(errors.ToolPermissionError):
        await executor.execute("probe", "outsider", {"value": 1})


@pytest.mark.asyncio
async def test_a_cross_tenant_call_never_reaches_the_handler(sandbox) -> None:
    """The critical one. The handler raises if entered; the refusal is a 404."""
    _spec()
    context = policy.ToolContext(
        tenant_id=_TENANT_A,
        objects=(policy.ToolObject(kind="job", object_id=uuid.uuid4(), tenant_id=_TENANT_B),),
    )
    with pytest.raises(errors.ToolScopeError) as caught:
        await executor.execute("probe", "tester", {"value": 1}, context=context)

    assert caught.value.http_status == 404
    assert caught.value.reason.startswith("cross_tenant_object")


@pytest.mark.asyncio
async def test_the_cross_tenant_refusal_carries_no_identifier(sandbox) -> None:
    """It says "no such object" and nothing more.

    An id in the message would confirm the id exists, which is the inference
    RBAC 4 forbids. The KIND survives, in `reason`, because a wiring defect and
    an injection are both easier to diagnose with it and neither reveals
    whether any particular row is there.
    """
    _spec()
    secret_id = uuid.uuid4()
    context = policy.ToolContext(
        tenant_id=_TENANT_A,
        objects=(policy.ToolObject(kind="job", object_id=secret_id, tenant_id=_TENANT_B),),
    )
    with pytest.raises(errors.ToolScopeError) as caught:
        await executor.execute("probe", "tester", {"value": 1}, context=context)

    assert str(secret_id) not in str(caught.value)
    assert str(_TENANT_B) not in str(caught.value)


@pytest.mark.asyncio
async def test_a_cross_tenant_refusal_is_never_a_403(sandbox) -> None:
    """403 and 404 are different answers to different questions, and only one
    of them keeps the two indistinguishable from outside."""
    _spec()
    context = policy.ToolContext(
        tenant_id=_TENANT_A,
        objects=(policy.ToolObject(kind="profile", object_id=uuid.uuid4(), tenant_id=_TENANT_B),),
    )
    with pytest.raises(errors.ToolPolicyError) as caught:
        await executor.execute("probe", "tester", {"value": 1}, context=context)
    assert caught.value.http_status != 403


@pytest.mark.asyncio
async def test_an_object_in_the_acting_tenant_is_allowed(sandbox) -> None:
    """The negative direction, without which the test above passes by refusing
    everything."""

    async def handler(payload: _In):
        return {"doubled": payload.value * 2}

    _spec(handler=handler)
    context = policy.ToolContext(
        tenant_id=_TENANT_A,
        objects=(policy.ToolObject(kind="job", object_id=uuid.uuid4(), tenant_id=_TENANT_A),),
    )
    result = await executor.execute("probe", "tester", {"value": 21}, context=context)
    assert result.value.doubled == 42


@pytest.mark.asyncio
async def test_an_object_owned_by_no_tenant_is_not_a_cross_tenant_call(sandbox) -> None:
    """A candidate spans tenants by design and carries a NULL tenant when they
    arrived through the databank. Treating NULL as a mismatch would refuse an
    agent access to the very person it was invoked for."""

    async def handler(payload: _In):
        return {"doubled": 2}

    _spec(handler=handler)
    context = policy.ToolContext(
        tenant_id=_TENANT_A,
        objects=(policy.ToolObject(kind="candidate", object_id=uuid.uuid4()),),
    )
    result = await executor.execute("probe", "tester", {"value": 1}, context=context)
    assert result.value.doubled == 2


# ── Workflow stage ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_called_outside_its_stage_never_reaches_the_handler(sandbox) -> None:
    _spec()
    context = policy.ToolContext(tenant_id=_TENANT_A, stage=policy.STAGE_JOB_SETUP)
    with pytest.raises(errors.ToolPolicyError) as caught:
        await executor.execute("probe", "tester", {"value": 1}, context=context)
    assert caught.value.reason == "tool_not_available_in_stage"


@pytest.mark.asyncio
async def test_a_tool_called_inside_its_stage_runs(sandbox) -> None:
    async def handler(payload: _In):
        return {"doubled": 4}

    _spec(handler=handler)
    context = policy.ToolContext(tenant_id=_TENANT_A, stage=policy.STAGE_REPORTING)
    result = await executor.execute("probe", "tester", {"value": 2}, context=context)
    assert result.value.doubled == 4


def test_a_tool_with_no_stage_entry_is_refused_in_every_stage() -> None:
    """Deny by default. "Unlisted means everywhere" is how a table like this
    stops meaning anything."""
    for stage in policy.STAGES:
        verdict = policy.evaluate(
            tool="never_declared",
            agent=permissions.AGENT_RANKING,
            risk=policy.RiskClass.READ,
            context=policy.ToolContext(tenant_id=_TENANT_A, stage=stage),
        )
        assert verdict.decision is policy.PolicyDecision.DENY


def test_an_unknown_stage_is_refused_rather_than_ignored() -> None:
    verdict = policy.evaluate(
        tool="extract_jd",
        agent=permissions.AGENT_RANKING,
        risk=policy.RiskClass.READ,
        context=policy.ToolContext(tenant_id=_TENANT_A, stage="whenever"),
    )
    assert verdict.reason == "unknown_workflow_stage"


# ── Risk class ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_irreversible_call_without_approval_never_reaches_the_handler(
    sandbox,
) -> None:
    """The adapter gate. An outbound effect issued speculatively and compensated
    afterwards is an email the candidate has already read."""
    _spec(risk=policy.RiskClass.WRITE_EXTERNAL)
    with pytest.raises(errors.ToolApprovalRequired):
        await executor.execute(
            "probe",
            "tester",
            {"value": 1},
            context=policy.ToolContext(stage=policy.STAGE_REPORTING),
        )


@pytest.mark.asyncio
async def test_a_current_human_approval_lets_an_irreversible_call_through(
    sandbox,
) -> None:
    async def handler(payload: _In):
        return {"doubled": 6}

    _spec(handler=handler, risk=policy.RiskClass.WRITE_EXTERNAL)
    context = policy.ToolContext(
        stage=policy.STAGE_REPORTING,
        approval=policy.ToolApproval(
            approved_by=uuid.uuid4(),
            tool="probe",
            granted_at=datetime.now(timezone.utc),
        ),
    )
    result = await executor.execute("probe", "tester", {"value": 3}, context=context)
    assert result.value.doubled == 6


@pytest.mark.asyncio
async def test_an_expired_approval_is_not_an_approval(sandbox) -> None:
    """An approval is permission for THIS call. One held open across a session
    would let a replayed invocation reuse a click made about something else."""
    _spec(risk=policy.RiskClass.WRITE_EXTERNAL)
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=policy.APPROVAL_MAX_AGE_SECONDS + 1
    )
    context = policy.ToolContext(
        stage=policy.STAGE_REPORTING,
        approval=policy.ToolApproval(
            approved_by=uuid.uuid4(), tool="probe", granted_at=stale
        ),
    )
    with pytest.raises(errors.ToolPolicyError) as caught:
        await executor.execute("probe", "tester", {"value": 1}, context=context)
    assert caught.value.reason == "approval_expired"


@pytest.mark.asyncio
async def test_an_approval_for_another_tool_does_not_transfer(sandbox) -> None:
    _spec(risk=policy.RiskClass.WRITE_EXTERNAL)
    context = policy.ToolContext(
        stage=policy.STAGE_REPORTING,
        approval=policy.ToolApproval(
            approved_by=uuid.uuid4(),
            tool="some_other_tool",
            granted_at=datetime.now(timezone.utc),
        ),
    )
    with pytest.raises(errors.ToolPolicyError) as caught:
        await executor.execute("probe", "tester", {"value": 1}, context=context)
    assert caught.value.reason == "approval_is_for_another_tool"


def test_a_policy_change_always_needs_a_human() -> None:
    """Mandatory human, and no tenant configuration can remove it: there is no
    row shape that expresses removal."""
    verdict = policy.evaluate(
        tool="extract_jd",
        agent=permissions.AGENT_RANKING,
        risk=policy.RiskClass.POLICY_CHANGE,
        context=policy.ToolContext(tenant_id=_TENANT_A),
    )
    assert verdict.decision is policy.PolicyDecision.REQUIRE_APPROVAL


def test_a_tenant_rule_can_only_raise_the_bar() -> None:
    """A tenant requiring approval for reads gets it; the baseline for reads is
    automatic, and there is no direction in which a rule makes something
    MORE automatic."""
    context = policy.ToolContext(
        tenant_id=_TENANT_A,
        tenant_approval_required=frozenset({policy.RiskClass.READ.value}),
    )
    verdict = policy.evaluate(
        tool="extract_jd",
        agent=permissions.AGENT_RANKING,
        risk=policy.RiskClass.READ,
        context=context,
    )
    assert verdict.decision is policy.PolicyDecision.REQUIRE_APPROVAL

    # And the same call for a tenant with no rule is automatic.
    baseline = policy.evaluate(
        tool="extract_jd",
        agent=permissions.AGENT_RANKING,
        risk=policy.RiskClass.READ,
        context=policy.ToolContext(tenant_id=_TENANT_A),
    )
    assert baseline.decision is policy.PolicyDecision.ALLOW


def test_the_approval_rules_table_cannot_express_less() -> None:
    """The shape IS the control. `agent_tool_approval_rules` has no boolean, so
    a tenant row can add a requirement and has no way to remove one."""
    from app.models import AgentToolApprovalRule

    columns = set(AgentToolApprovalRule.__table__.columns.keys())
    assert "requires_approval" not in columns
    assert not {column for column in columns if column.startswith("allow")}


@pytest.mark.asyncio
async def test_a_call_that_changes_something_refuses_when_the_rules_are_unreadable(
    sandbox,
) -> None:
    """A policy input that could not be read is not a policy decision.

    Without a session the tenant's configured requirements cannot be resolved,
    so a non-read call for a tenant is refused rather than run on the baseline.
    """
    _spec(risk=policy.RiskClass.WRITE_INTERNAL, needs_session=True)
    with pytest.raises(errors.ToolPolicyError) as caught:
        await executor.execute(
            "probe",
            "tester",
            {"value": 1},
            context=policy.ToolContext(tenant_id=_TENANT_A, stage=policy.STAGE_REPORTING),
        )
    assert caught.value.reason == "approval_rules_unresolvable"


# ── The tenant rule, end to end against the real table ───────────────────────
#
# Everything above hands `tenant_approval_required` to the pure function
# directly, which proves the RULE and not the WIRING. This is the half that
# proves a row written by a customer actually reaches the decision: the table
# exists, the executor reads it, and the read changes the answer. A control
# asserted only against its own inputs is the shape this codebase keeps finding
# green in isolation and dead in production.


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def tenant_with_a_read_approval_rule():
    """One tenant that has asked for a human on every read.

    A deliberately extreme rule, because it is the one whose effect is visible
    against a registry that is entirely reads.
    """
    from app.core.db import superadmin_scope
    from app.models import Tenant
    from sqlalchemy import text as sql

    engine, factory = await _factory_or_skip()
    tenant_id = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                session.add(
                    Tenant(
                        id=tenant_id,
                        name=f"Fw {tenant_id.hex[:6]}",
                        domain=f"{tenant_id}.fw.test",
                    )
                )
                await session.flush()
                await session.execute(
                    sql(
                        "INSERT INTO agent_tool_approval_rules "
                        "(id, tenant_id, risk_class) "
                        "VALUES (gen_random_uuid(), :t, 'read')"
                    ),
                    {"t": str(tenant_id)},
                )
    try:
        yield factory, tenant_id
    finally:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sql("DELETE FROM agent_tool_approval_rules WHERE tenant_id = :t"),
                        {"t": str(tenant_id)},
                    )
                    await session.execute(
                        sql("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
                    )
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_tenants_own_rule_reaches_the_decision(
    sandbox, tenant_with_a_read_approval_rule
) -> None:
    from app.core.db import tenant_scope

    factory, tenant_id = tenant_with_a_read_approval_rule
    _spec(needs_session=True)

    async with factory() as session:
        async with tenant_scope(session, tenant_id):
            with pytest.raises(errors.ToolApprovalRequired):
                await executor.execute(
                    "probe",
                    "tester",
                    {"value": 1},
                    session=session,
                    context=policy.ToolContext(
                        tenant_id=tenant_id, stage=policy.STAGE_REPORTING
                    ),
                )


@pytest.mark.asyncio
async def test_a_tenant_with_no_rule_takes_the_platform_baseline(
    sandbox, tenant_with_a_read_approval_rule
) -> None:
    """The negative direction. Without it the test above passes for a tenant
    whose rule was never read, because reads would be held for everybody."""
    from app.core.db import tenant_scope

    factory, _rule_holding_tenant = tenant_with_a_read_approval_rule

    async def handler(payload: _In, *, session=None):
        return {"doubled": 8}

    _spec(handler=handler, needs_session=True)

    from app.core.db import superadmin_scope
    from app.models import Tenant

    plain_tenant = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                session.add(
                    Tenant(
                        id=plain_tenant,
                        name=f"Fw {plain_tenant.hex[:6]}",
                        domain=f"{plain_tenant}.fw.test",
                    )
                )
    try:
        async with factory() as session:
            async with tenant_scope(session, plain_tenant):
                result = await executor.execute(
                    "probe",
                    "tester",
                    {"value": 4},
                    session=session,
                    context=policy.ToolContext(
                        tenant_id=plain_tenant, stage=policy.STAGE_REPORTING
                    ),
                )
        assert result.value.doubled == 8
    finally:
        from sqlalchemy import text as sql

        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sql("DELETE FROM tenants WHERE id = :t"),
                        {"t": str(plain_tenant)},
                    )


@pytest.mark.asyncio
async def test_a_tenant_rule_is_invisible_to_another_tenant(
    tenant_with_a_read_approval_rule,
) -> None:
    """RLS on the rules table. One customer's risk appetite is not another's
    configuration, and it is not readable as one either."""
    from app.core.db import tenant_scope
    from app.services.tools import approvals

    factory, tenant_id = tenant_with_a_read_approval_rule
    stranger = uuid.uuid4()

    async with factory() as session:
        async with tenant_scope(session, tenant_id):
            mine = await approvals.required_risk_classes(session, tenant_id)
        async with tenant_scope(session, stranger):
            theirs = await approvals.required_risk_classes(session, tenant_id)

    assert mine == frozenset({policy.RiskClass.READ.value})
    assert theirs == frozenset()


# ── Ordering, asserted directly on the pure function ─────────────────────────


def test_capability_is_decided_before_any_object_is_looked_at() -> None:
    """An agent that does not hold the tool is refused identically whether the
    object it named is its own, somebody else's, or invented."""
    mine = policy.evaluate(
        tool="extract_resume",
        agent=permissions.AGENT_EMAIL,
        risk=policy.RiskClass.READ,
        context=policy.ToolContext(
            tenant_id=_TENANT_A,
            objects=(policy.ToolObject("profile", uuid.uuid4(), _TENANT_A),),
        ),
    )
    theirs = policy.evaluate(
        tool="extract_resume",
        agent=permissions.AGENT_EMAIL,
        risk=policy.RiskClass.READ,
        context=policy.ToolContext(
            tenant_id=_TENANT_A,
            objects=(policy.ToolObject("profile", uuid.uuid4(), _TENANT_B),),
        ),
    )
    assert mine.reason == theirs.reason == "agent_does_not_hold_tool"
    assert mine.http_status == theirs.http_status == 403


def test_tenant_scope_is_decided_before_the_stage_and_the_risk_class() -> None:
    """Otherwise a caller could tell "this agent cannot do that here" apart from
    "that object is not yours", which is the same oracle by a longer route."""
    verdict = policy.evaluate(
        tool="extract_assessment",
        agent=permissions.AGENT_PPI_REPORT,
        risk=policy.RiskClass.POLICY_CHANGE,
        context=policy.ToolContext(
            tenant_id=_TENANT_A,
            stage=policy.STAGE_JOB_SETUP,
            objects=(policy.ToolObject("link", uuid.uuid4(), _TENANT_B),),
        ),
    )
    assert verdict.http_status == 404


# ── The result cache is inside the tenant boundary ───────────────────────────


@pytest.fixture
def memory_cache(monkeypatch):
    """A real cache, in a dict, so the hit path is actually exercised.

    `core.cache` no-ops when Redis is unreachable, which is correct for a cache
    and useless for a test about one: every assertion below would pass against
    a cache that never stored anything, which is the shape of a test that
    cannot fail.
    """
    from app.core import cache as core_cache

    store: dict[str, object] = {}

    async def fake_get(cache_key: str):
        return store.get(cache_key)

    async def fake_set(cache_key: str, value, ttl: int = 0) -> bool:
        store[cache_key] = value
        return True

    async def fake_invalidate(*cache_keys: str) -> None:
        for cache_key in cache_keys:
            store.pop(cache_key, None)

    monkeypatch.setattr(core_cache, "get", fake_get)
    monkeypatch.setattr(core_cache, "set", fake_set)
    monkeypatch.setattr(core_cache, "invalidate", fake_invalidate)
    return store


@pytest.mark.asyncio
async def test_one_tenants_cached_result_is_never_served_to_another(
    sandbox, memory_cache
) -> None:
    """The cache is read BEFORE the handler, and therefore before RLS.

    Which makes it the one step in this layer where an authorised call for
    tenant B can be answered with a value computed for tenant A: the policy
    engine has already said yes, and nothing downstream ever runs to disagree.
    The two calls here are identical in every declared way except the tenant,
    which is exactly the case a key without a tenant cannot tell apart.
    """
    seen: list[str] = []

    async def handler(payload: _In):
        seen.append("ran")
        return {"doubled": payload.value * 2}

    _spec(handler=handler, idempotent=True, cache_ttl_seconds=60)

    def scoped(tenant):
        return policy.ToolContext(tenant_id=tenant, stage=policy.STAGE_REPORTING)

    first = await executor.execute("probe", "tester", {"value": 2}, context=scoped(_TENANT_A))
    assert first.cached is False
    again = await executor.execute("probe", "tester", {"value": 2}, context=scoped(_TENANT_A))
    assert again.cached is True, "the same tenant asking twice must hit"

    other = await executor.execute("probe", "tester", {"value": 2}, context=scoped(_TENANT_B))
    assert other.cached is False, "tenant B was served tenant A's cached result"
    assert seen == ["ran", "ran"], "the second tenant's call never reached the handler"


@pytest.mark.asyncio
async def test_a_call_that_declares_no_tenant_is_not_cached_at_all(
    sandbox, memory_cache
) -> None:
    """`UNSCOPED` gets no cache rather than a shared one.

    A bucket keyed on "nobody said" is the same cross-tenant serving one line
    down, in a shape nobody would read as one. Declining to memoise costs the
    second call its handler and nothing else.
    """
    calls: list[str] = []

    async def handler(payload: _In):
        calls.append("ran")
        return {"doubled": 4}

    _spec(handler=handler, idempotent=True, cache_ttl_seconds=60)
    unscoped = policy.ToolContext(stage=policy.STAGE_REPORTING)

    for _ in range(2):
        result = await executor.execute("probe", "tester", {"value": 2}, context=unscoped)
        assert result.cached is False

    assert calls == ["ran", "ran"]
    assert memory_cache == {}, "an unscoped call wrote a cache entry somebody can hit"


# ── Audit over the REAL registry ─────────────────────────────────────────────


def test_every_registered_tool_declares_a_stage_set() -> None:
    """A tool with no entry is refused in every declared stage, which is the
    safe direction and a silent one. This is the loud half."""
    missing = sorted(tools.names() - set(policy.TOOL_STAGES))
    assert not missing, f"no TOOL_STAGES entry for: {missing}"


def test_no_stage_table_entry_names_a_tool_that_does_not_exist() -> None:
    stale = sorted(set(policy.TOOL_STAGES) - tools.names())
    assert not stale, f"TOOL_STAGES names unregistered tools: {stale}"


def test_every_stage_named_in_the_table_is_a_real_stage() -> None:
    for tool, stages in policy.TOOL_STAGES.items():
        unknown = stages - set(policy.STAGES)
        assert not unknown, f"{tool} names unknown stages: {sorted(unknown)}"


def test_every_shipped_tool_is_a_read() -> None:
    """Today the whole registry is bounded reads, and that is worth asserting
    rather than assuming: the first tool that is not one must be a deliberate
    change to this line as well as to its own spec."""
    for spec in registry.specs():
        assert spec.risk is policy.RiskClass.READ, spec.name


def test_no_registered_tool_runs_an_irreversible_effect_automatically() -> None:
    """The rule that will still matter after the first write tool lands."""
    for spec in registry.specs():
        if spec.risk in policy.REQUIRES_HUMAN_APPROVAL:
            verdict = policy.evaluate(
                tool=spec.name,
                agent=next(iter(permissions.agents_holding(spec.name))),
                risk=spec.risk,
                context=policy.ToolContext(tenant_id=_TENANT_A),
            )
            assert verdict.decision is policy.PolicyDecision.REQUIRE_APPROVAL
