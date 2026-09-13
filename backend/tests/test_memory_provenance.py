"""A learning belongs to one tenant, and it cannot be written without saying so.

RPN-AI-UP-001 W3.5. Before this, `agent_learnings` had no tenant column at all.
A learning is extracted from a run whose prompt carried a real candidate's
answers and a real client's job description, and every learning was retrievable
by every later run for every customer: the single place in this architecture
where one tenant's untrusted input could reach another tenant's decision.

THE DECISION UNDER TEST IS PER-TENANT SCOPING, NOT APPROVAL BEFORE PROMOTION.
`app/services/memory/provenance.py` carries the reasoning. What matters here is
that it is enforced in three independent places, and each one is asserted
separately because each fails differently:

  * the WRITE cannot happen without provenance, because `Provenance` refuses to
    exist without a tenant and `record_failure` has no other parameter shape;
  * the READ filters on the tenant, so tenant B's query returns tenant A's row
    never;
  * ROW LEVEL SECURITY refuses it underneath, so a query somebody writes wrong
    in a year still cannot cross.

The third is the one worth having. The first two are code, and code is edited.

The existing containment is asserted too, in both directions: nothing below
`MIN_OBSERVATIONS` is ever applied, and a hint is prompt text that changes no
gate. Those rules are older and stronger than anything added here, and a change
that quietly relaxed one while adding tenant scoping would look like an
improvement in the diff.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.memory import experience, procedural, provenance, semantic, working
from app.services.memory.provenance import Provenance, ProvenanceError

_AGENT = "ranking"
_TASK = "prescreen"
_PATTERN = "remark_word_count"
_FIX = "state the word range explicitly in the instruction"


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


class _World:
    """Two tenants, minted per test so nothing leaks between them."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()


async def _seed_tenants(factory, world: _World) -> None:
    from app.core.db import superadmin_scope
    from app.models import Tenant

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for tenant_id, label in (
                    (world.tenant_a, "A"),
                    (world.tenant_b, "B"),
                ):
                    session.add(
                        Tenant(
                            id=tenant_id,
                            name=f"Prov {label} {tenant_id.hex[:6]}",
                            domain=f"{tenant_id}.prov.test",
                        )
                    )


async def _cleanup(factory, world: _World) -> None:
    from app.core.db import superadmin_scope

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for tenant_id in (world.tenant_a, world.tenant_b):
                    await session.execute(
                        text("DELETE FROM agent_learnings WHERE tenant_id = :t"),
                        {"t": str(tenant_id)},
                    )
                    await session.execute(
                        text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
                    )


@pytest.fixture
async def world():
    engine, factory = await _factory_or_skip()
    fixture = _World()
    await _seed_tenants(factory, fixture)
    try:
        yield factory, fixture
    finally:
        await _cleanup(factory, fixture)
        await engine.dispose()


def _prov(tenant_id: uuid.UUID, **overrides) -> Provenance:
    defaults = dict(
        tenant_id=tenant_id,
        source="verification.prescreen",
        source_version="v3",
        trust_level=provenance.TRUST_PLATFORM,
    )
    defaults.update(overrides)
    return Provenance(**defaults)


async def _establish(factory, tenant_id: uuid.UUID, **overrides) -> None:
    """Write one learning past `MIN_OBSERVATIONS`, with successes to match.

    Below the floor nothing is retrieved at all, so a retrieval test that
    recorded a single observation would pass for the wrong reason.
    """
    from app.core.db import superadmin_scope

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for _ in range(experience.MIN_OBSERVATIONS):
                    await experience.record_failure(
                        session,
                        agent_type=_AGENT,
                        task_type=_TASK,
                        pattern=_PATTERN,
                        fix=_FIX,
                        provenance=_prov(tenant_id, **overrides),
                    )
                    await experience.record_success(
                        session,
                        agent_type=_AGENT,
                        task_type=_TASK,
                        pattern=_PATTERN,
                        tenant_id=tenant_id,
                    )


# ── A learning without provenance cannot be written ──────────────────────────


def test_provenance_cannot_exist_without_a_tenant() -> None:
    """The write is impossible rather than merely discouraged: there is no call
    shape that produces an unattributed row."""
    with pytest.raises(ProvenanceError):
        Provenance(tenant_id=None, source="verification.prescreen")


def test_provenance_cannot_exist_without_a_source() -> None:
    """`source` is the handle a revocation grabs. A row that cannot name what
    produced it can only be removed by emptying the table."""
    with pytest.raises(ProvenanceError):
        Provenance(tenant_id=uuid.uuid4(), source="   ")


def test_an_unknown_trust_level_is_refused_rather_than_accepted() -> None:
    with pytest.raises(ProvenanceError):
        Provenance(
            tenant_id=uuid.uuid4(), source="somewhere", trust_level="quite_trustworthy"
        )


def test_the_default_trust_level_is_the_least_trusting_one() -> None:
    """A default that assumed platform provenance would make an unlabelled
    learning look like the safest kind there is."""
    origin = Provenance(tenant_id=uuid.uuid4(), source="somewhere")
    assert origin.trust_level == provenance.TRUST_CANDIDATE_AUTHORED
    assert (
        provenance.TRUST_RANK[origin.trust_level]
        == max(provenance.TRUST_RANK.values())
    )


def test_record_failure_has_no_call_shape_without_provenance() -> None:
    """Asserted on the signature, because the point is that a caller cannot
    omit it, not that omitting it fails at runtime somewhere later."""
    import inspect

    parameter = inspect.signature(experience.record_failure).parameters["provenance"]
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_a_learning_row_carries_all_seven_provenance_facts(world) -> None:
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(
        factory,
        fixture.tenant_a,
        created_by=None,
        evidence_ids=("chunk-1", "chunk-2"),
    )

    async with factory() as session:
        async with superadmin_scope(session):
            row = (
                await session.execute(
                    text(
                        "SELECT tenant_id, source, source_version, created_by, "
                        "trust_level, evidence_ids, revalidate_after "
                        "FROM agent_learnings WHERE tenant_id = :t"
                    ),
                    {"t": str(fixture.tenant_a)},
                )
            ).first()

    assert row is not None
    assert str(row.tenant_id) == str(fixture.tenant_a)
    assert row.source == "verification.prescreen"
    assert row.source_version == "v3"
    assert row.trust_level == provenance.TRUST_PLATFORM
    assert row.evidence_ids == ["chunk-1", "chunk-2"]
    assert row.revalidate_after > datetime.now(timezone.utc)


# ── A learning from tenant A is not retrieved for tenant B ───────────────────


@pytest.mark.asyncio
async def test_a_learning_from_one_tenant_is_never_a_hint_for_another(world) -> None:
    """THE CONTROL. Same agent, same task, same pattern, different customer."""
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)

    async with factory() as session:
        async with superadmin_scope(session):
            mine = await experience.hints_for(
                session, agent_type=_AGENT, task_type=_TASK, tenant_id=fixture.tenant_a
            )
            theirs = await experience.hints_for(
                session, agent_type=_AGENT, task_type=_TASK, tenant_id=fixture.tenant_b
            )

    assert mine == [_FIX]
    assert theirs == []


@pytest.mark.asyncio
async def test_row_level_security_refuses_the_cross_tenant_read_underneath(
    world,
) -> None:
    """The filter in the query is code, and code is edited. This is the half
    that still holds when somebody writes the next query wrong."""
    from app.core.db import tenant_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)

    async with factory() as session:
        async with tenant_scope(session, fixture.tenant_b):
            visible = (
                await session.execute(
                    text("SELECT count(*) FROM agent_learnings WHERE tenant_id = :t"),
                    {"t": str(fixture.tenant_a)},
                )
            ).scalar_one()
    assert visible == 0

    async with factory() as session:
        async with tenant_scope(session, fixture.tenant_a):
            own = (
                await session.execute(
                    text("SELECT count(*) FROM agent_learnings")
                )
            ).scalar_one()
    assert own == 1


@pytest.mark.asyncio
async def test_two_tenants_hitting_one_pattern_keep_separate_counters(world) -> None:
    """The unique key includes the tenant. Without it, ON CONFLICT makes one
    customer's observation increment another's counter, which is a cross-tenant
    write wearing a counter's clothes."""
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)
    await _establish(factory, fixture.tenant_b)

    async with factory() as session:
        async with superadmin_scope(session):
            rows = (
                await session.execute(
                    text(
                        "SELECT tenant_id, observations FROM agent_learnings "
                        "WHERE tenant_id IN (:a, :b)"
                    ),
                    {"a": str(fixture.tenant_a), "b": str(fixture.tenant_b)},
                )
            ).all()

    assert len(rows) == 2
    assert {row.observations for row in rows} == {experience.MIN_OBSERVATIONS}


# ── Revocation ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoking_a_source_stops_its_learnings_being_applied(world) -> None:
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                revoked = await experience.revoke_learnings_from_source(
                    session,
                    tenant_id=fixture.tenant_a,
                    source="verification.prescreen",
                )
    assert revoked == 1

    async with factory() as session:
        async with superadmin_scope(session):
            hints = await experience.hints_for(
                session, agent_type=_AGENT, task_type=_TASK, tenant_id=fixture.tenant_a
            )
            still_there = (
                await session.execute(
                    text(
                        "SELECT observations, is_active FROM agent_learnings "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": str(fixture.tenant_a)},
                )
            ).first()

    assert hints == []
    # Deactivated, never deleted. The observation count is the evidence of what
    # happened, and a revocation issued in error must be recoverable.
    assert still_there is not None
    assert still_there.observations == experience.MIN_OBSERVATIONS
    assert still_there.is_active is False


@pytest.mark.asyncio
async def test_a_revocation_cannot_reach_another_tenants_learnings(world) -> None:
    """A revocation is a change to how one customer's future generations
    behave. A call that crossed tenants would be the influence channel this
    design removed, running backwards."""
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)
    await _establish(factory, fixture.tenant_b)

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                revoked = await experience.revoke_learnings_from_source(
                    session,
                    tenant_id=fixture.tenant_a,
                    source="verification.prescreen",
                )
    assert revoked == 1

    async with factory() as session:
        async with superadmin_scope(session):
            theirs = await experience.hints_for(
                session, agent_type=_AGENT, task_type=_TASK, tenant_id=fixture.tenant_b
            )
    assert theirs == [_FIX]


@pytest.mark.asyncio
async def test_a_revocation_can_be_narrowed_to_one_source_version(world) -> None:
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                revoked = await experience.revoke_learnings_from_source(
                    session,
                    tenant_id=fixture.tenant_a,
                    source="verification.prescreen",
                    source_version="a-version-that-was-never-used",
                )
    assert revoked == 0


# ── Staleness: the consequence of the trust level ────────────────────────────


@pytest.mark.asyncio
async def test_a_learning_past_its_revalidation_date_is_not_applied(world) -> None:
    """`revalidate_after` is what makes the trust level mean something. Without
    it the column is a label, and a label that changes nothing is a comment in
    a table."""
    from app.core.db import superadmin_scope

    factory, fixture = world
    await _establish(factory, fixture.tenant_a)

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE agent_learnings SET revalidate_after = :past "
                        "WHERE tenant_id = :t"
                    ),
                    {
                        "past": datetime.now(timezone.utc) - timedelta(days=1),
                        "t": str(fixture.tenant_a),
                    },
                )

    async with factory() as session:
        async with superadmin_scope(session):
            hints = await experience.hints_for(
                session, agent_type=_AGENT, task_type=_TASK, tenant_id=fixture.tenant_a
            )
    assert hints == []


def test_candidate_authored_text_earns_the_shortest_life() -> None:
    """It is the only level whose words an outsider chooses, so a poisoned
    learning has a bounded life even if nobody ever notices it."""
    days = provenance.REVALIDATION_DAYS
    assert days[provenance.TRUST_CANDIDATE_AUTHORED] < days[provenance.TRUST_TENANT_AUTHORED]
    assert days[provenance.TRUST_TENANT_AUTHORED] < days[provenance.TRUST_PLATFORM]


def test_an_unknown_trust_level_gets_no_invented_deadline() -> None:
    with pytest.raises(ProvenanceError):
        provenance.revalidate_after("fairly_reliable")


# ── The old containment, unchanged and asserted in both directions ───────────


@pytest.mark.asyncio
async def test_nothing_below_the_observation_floor_is_ever_applied(world) -> None:
    """A pattern seen once is an anecdote."""
    from app.core.db import superadmin_scope

    factory, fixture = world
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await experience.record_failure(
                    session,
                    agent_type=_AGENT,
                    task_type=_TASK,
                    pattern=_PATTERN,
                    fix=_FIX,
                    provenance=_prov(fixture.tenant_a),
                )

    async with factory() as session:
        async with superadmin_scope(session):
            hints = await experience.hints_for(
                session, agent_type=_AGENT, task_type=_TASK, tenant_id=fixture.tenant_a
            )
    assert hints == []


def test_a_learning_is_text_and_carries_no_gate() -> None:
    """A hint cannot relax a word range, skip a verifier or lower a threshold,
    because what it is is a string. Asserted on the SHAPE: a field named like a
    threshold or a switch would be the first step towards one."""
    fields = set(experience.Learning.__dataclass_fields__)
    forbidden = {
        name
        for name in fields
        if any(
            marker in name
            for marker in ("threshold", "min_", "max_", "skip", "disable", "override")
        )
    }
    assert not forbidden, forbidden
    assert isinstance(
        experience.Learning(
            failure_pattern=_PATTERN,
            applied_fix=_FIX,
            observations=experience.MIN_OBSERVATIONS,
            successes=experience.MIN_OBSERVATIONS,
        ).applied_fix,
        str,
    )


# ── The other memory layers ──────────────────────────────────────────────────


def test_a_semantic_cache_key_cannot_be_built_without_a_tenant() -> None:
    """A cache is a place a value can be read from by a caller that never proved
    it may see it."""
    tenant = uuid.uuid4()
    entity = uuid.uuid4()
    key = semantic.key_for(tenant, "job", entity)
    assert str(tenant) in key
    other = semantic.key_for(uuid.uuid4(), "job", entity)
    assert key != other


def test_working_memory_refuses_a_value_produced_for_another_tenant() -> None:
    """Working memory dies with the run, so it cannot leak across tenants over
    time. It can still be MIXED within one run, and that is a raise."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    memory = working.WorkingMemory(tenant_id=tenant_a)
    with pytest.raises(ValueError):
        memory.put(
            "jd",
            {"title": "Staff Engineer"},
            stage="extract_jd",
            provenance=Provenance(tenant_id=tenant_b, source="extract_jd"),
        )
    # The check runs before the assignment, so a rejected write leaves nothing
    # behind for a later stage to read.
    assert "jd" not in memory.values


def test_working_memory_records_the_source_beside_the_stage() -> None:
    """Two different questions with two different answers: where in the run it
    happened, and what the value was derived from."""
    tenant = uuid.uuid4()
    memory = working.WorkingMemory(tenant_id=tenant)
    memory.put(
        "jd",
        {"title": "Staff Engineer"},
        stage="extract_jd",
        provenance=Provenance(
            tenant_id=tenant, source="prompt", source_version="jd_generation:abc123"
        ),
    )
    assert memory.provenance()["jd"] == "extract_jd"
    assert memory.sources()["jd"] == "prompt@jd_generation:abc123"


def test_a_prompt_supplies_a_source_version_that_identifies_its_output() -> None:
    """When a prompt version turns out to have taught the wrong lesson,
    everything it produced is identifiable by exactly this string."""
    version = procedural.source_version("jd_generation")
    assert version.startswith("jd_generation:")
    assert version == procedural.source_version("jd_generation")
