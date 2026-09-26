"""`api/emails._load_targets`: what it resolves, what it refuses, and how many
round trips it costs to find out.

WHY THIS FILE EXISTS
--------------------
`_load_targets` decides who receives a lifecycle email and who is left behind
with a reason the recruiter reads, and it had no test of its own. It was also
an N+1: one `session.get` for the link, one for the candidate and one for the
job, PER recipient, inside a loop, so a fifty-person send cost a hundred and
fifty round trips before the first draft was composed and the drafting path
then adds a model call per recipient on top. `api/outreach._resolve` was
repaired for exactly this shape and this is the same repair.

The query count is asserted directly rather than described in a comment,
because an N+1 that is fixed and then quietly reintroduced by somebody adding
one more lookup inside the loop produces no failure anywhere else: the output
is identical and only the clock moves. Counting statements is the only
assertion that can see it.

Every other test here pins a decision that must NOT have changed. A batching
rewrite that also changed which recipients are rejected, or the wording of a
rejection, would be a product change wearing a performance fix's clothes.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fx:
    """Two tenants, because the tenant check is half of what is under test."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.other_job_id = uuid.uuid4()
        #: Three ordinary recipients, so an assertion about ORDER has something
        #: to be wrong about and the query count has something to multiply.
        self.link_ids = [uuid.uuid4() for _ in range(3)]
        self.cand_ids = [uuid.uuid4() for _ in range(3)]
        #: A candidate with no address on file. Skipped, never failed.
        self.no_email_link_id = uuid.uuid4()
        self.no_email_cand_id = uuid.uuid4()
        #: A perfectly real application belonging to somebody else.
        self.foreign_link_id = uuid.uuid4()
        self.foreign_cand_id = uuid.uuid4()


async def _seed(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.candidate import JobCandidateLink

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for tenant_id in (fx.tenant_id, fx.other_tenant_id):
                    s.add(
                        Tenant(
                            id=tenant_id,
                            name=f"Em {tenant_id.hex[:6]}",
                            domain=f"{tenant_id}.em.test",
                        )
                    )
                await s.flush()
                for job_id, tenant_id in (
                    (fx.job_id, fx.tenant_id),
                    (fx.other_job_id, fx.other_tenant_id),
                ):
                    s.add(
                        Job(
                            id=job_id,
                            tenant_id=tenant_id,
                            title="Staff Data Engineer",
                            jd_json={},
                            jd_markdown="# Staff Data Engineer",
                            status=JobStatus.draft,
                            assessment_grade="non_managerial",
                        )
                    )
                for index, cand_id in enumerate(fx.cand_ids):
                    s.add(
                        Candidate(
                            id=cand_id,
                            email=f"c{cand_id.hex[:8]}@em.test",
                            full_name=f"Recipient {index}",
                            consent_databank=False,
                        )
                    )
                s.add(
                    Candidate(
                        id=fx.no_email_cand_id,
                        email=None,
                        full_name="Unreachable Person",
                        consent_databank=False,
                    )
                )
                s.add(
                    Candidate(
                        id=fx.foreign_cand_id,
                        email=f"c{fx.foreign_cand_id.hex[:8]}@em.test",
                        full_name="Another Customer's Candidate",
                        consent_databank=False,
                    )
                )
                await s.flush()
                for link_id, cand_id in zip(fx.link_ids, fx.cand_ids):
                    s.add(
                        JobCandidateLink(
                            id=link_id,
                            tenant_id=fx.tenant_id,
                            job_id=fx.job_id,
                            candidate_id=cand_id,
                            source=LinkSource.fresh,
                            status="applied",
                        )
                    )
                s.add(
                    JobCandidateLink(
                        id=fx.no_email_link_id,
                        tenant_id=fx.tenant_id,
                        job_id=fx.job_id,
                        candidate_id=fx.no_email_cand_id,
                        source=LinkSource.fresh,
                        status="applied",
                    )
                )
                s.add(
                    JobCandidateLink(
                        id=fx.foreign_link_id,
                        tenant_id=fx.other_tenant_id,
                        job_id=fx.other_job_id,
                        candidate_id=fx.foreign_cand_id,
                        source=LinkSource.fresh,
                        status="applied",
                    )
                )


async def _cleanup(factory, fx: _Fx) -> None:
    from sqlalchemy import text

    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("DELETE FROM job_candidate_links WHERE tenant_id = ANY(:t)"),
                    {"t": [str(fx.tenant_id), str(fx.other_tenant_id)]},
                )
                await s.execute(
                    text("DELETE FROM candidates WHERE id = ANY(:c)"),
                    {
                        "c": [str(i) for i in fx.cand_ids]
                        + [str(fx.no_email_cand_id), str(fx.foreign_cand_id)]
                    },
                )
                await s.execute(
                    text("DELETE FROM jobs WHERE tenant_id = ANY(:t)"),
                    {"t": [str(fx.tenant_id), str(fx.other_tenant_id)]},
                )
                await s.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:t)"),
                    {"t": [str(fx.tenant_id), str(fx.other_tenant_id)]},
                )


def _user(fx: _Fx):
    from app.api.deps import CurrentUser
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=fx.tenant_id, role=Role.recruiter)


@pytest.fixture
async def world():
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        yield engine, factory, fx
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


#: The three tables `_load_targets` reads. The counter looks only at these
#: because the session scope itself issues `SELECT set_config(...)` and a fresh
#: pool connection issues its own dialect probes, and neither is a round trip
#: this function chose to make. Counting them would make the assertion depend
#: on connection pooling rather than on the code under test.
_READ_TABLES = ("FROM JOB_CANDIDATE_LINKS", "FROM CANDIDATES", "FROM JOBS")


class _StatementCounter:
    """Counts the reads of those tables on the engine while a block runs.

    Hung off the engine rather than the session because that is where every
    round trip is actually issued, including the ones the identity map would
    have hidden from a counter wrapped around `session.execute`.
    """

    def __init__(self, engine) -> None:
        self._sync = engine.sync_engine
        self.selects: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        upper = statement.upper()
        if upper.lstrip().startswith("SELECT") and any(
            table in upper for table in _READ_TABLES
        ):
            self.selects.append(statement)

    def __enter__(self) -> "_StatementCounter":
        event.listen(self._sync, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self._sync, "before_cursor_execute", self._record)


async def _load(factory, fx: _Fx, link_ids):
    from app.api.emails import _load_targets
    from app.core.db import tenant_scope

    async with factory() as s:
        async with tenant_scope(s, fx.tenant_id):
            return await _load_targets(s, _user(fx), list(link_ids))


@pytest.mark.asyncio
async def test_the_cost_does_not_grow_with_the_number_of_recipients(world) -> None:
    """The fix itself. Three statements for three recipients, and for five.

    The count is compared between two sizes rather than pinned to a literal, so
    a legitimate extra read added once outside the loop does not fail this,
    and a read added INSIDE the loop cannot pass it.
    """
    engine, factory, fx = world

    with _StatementCounter(engine) as small:
        targets, _ = await _load(factory, fx, fx.link_ids[:1])
    assert len(targets) == 1

    with _StatementCounter(engine) as large:
        targets, skipped = await _load(
            factory, fx, fx.link_ids + [fx.no_email_link_id, fx.foreign_link_id]
        )
    assert len(targets) == 3 and len(skipped) == 2

    assert len(large.selects) == len(small.selects), (
        "the number of queries moved with the number of recipients: "
        f"{len(small.selects)} for one, {len(large.selects)} for five.\n"
        + "\n".join(large.selects)
    )


@pytest.mark.asyncio
async def test_the_targets_come_back_in_the_order_they_were_asked_for(world) -> None:
    """The drafting loop renders in this order and the recruiter reads it.

    A dict's iteration order is not the caller's order, and a batched rewrite
    that returned rows in whatever order Postgres handed them back would
    shuffle the modal without failing anything else.
    """
    _, factory, fx = world
    asked = list(reversed(fx.link_ids))
    targets, skipped = await _load(factory, fx, asked)
    assert [link.id for link, _, _ in targets] == asked
    assert skipped == []


@pytest.mark.asyncio
async def test_a_repeated_link_id_is_still_resolved_twice(world) -> None:
    """Unchanged behaviour, pinned because deduplicating is the obvious
    shortcut and it would silently drop a draft the caller asked for."""
    _, factory, fx = world
    first = fx.link_ids[0]
    targets, _ = await _load(factory, fx, [first, first])
    assert [link.id for link, _, _ in targets] == [first, first]


@pytest.mark.asyncio
async def test_another_tenants_application_is_skipped_as_not_found(world) -> None:
    """The refusal names nothing and admits nothing.

    RLS is the boundary and the explicit tenant check is defence in depth; what
    this pins is that the batched read cannot widen either one, and that the
    wording stays the same as the wording for an id that does not exist at all.
    A recruiter who could tell those two apart would have an existence oracle
    over another customer's applications.
    """
    _, factory, fx = world
    unknown = uuid.uuid4()
    targets, skipped = await _load(factory, fx, [fx.foreign_link_id, unknown])
    assert targets == []
    assert [entry["reason"] for entry in skipped] == [
        "Application not found",
        "Application not found",
    ]
    assert {entry["link_id"] for entry in skipped} == {
        str(fx.foreign_link_id),
        str(unknown),
    }
    # Nothing about the other tenant's candidate travels with the refusal.
    assert all("candidate_id" not in entry for entry in skipped)


@pytest.mark.asyncio
async def test_a_candidate_with_no_address_is_skipped_by_name(world) -> None:
    """Skipped, not failed, and identified, so the recruiter can go and fix it.

    The rest of the batch must still go out: that is the whole reason this
    function returns two lists instead of raising.
    """
    _, factory, fx = world
    targets, skipped = await _load(
        factory, fx, [fx.link_ids[0], fx.no_email_link_id, fx.link_ids[1]]
    )
    assert [link.id for link, _, _ in targets] == [fx.link_ids[0], fx.link_ids[1]]
    assert len(skipped) == 1
    assert skipped[0]["candidate_id"] == str(fx.no_email_cand_id)
    assert skipped[0]["name"] == "Unreachable Person"
    assert skipped[0]["reason"] == "No email address on file for this candidate"


@pytest.mark.asyncio
async def test_an_empty_selection_issues_no_queries_at_all(world) -> None:
    """An empty `IN ()` is a query that cannot match and still costs a trip."""
    engine, factory, fx = world
    with _StatementCounter(engine) as counter:
        targets, skipped = await _load(factory, fx, [])
    assert targets == [] and skipped == []
    assert counter.selects == []
