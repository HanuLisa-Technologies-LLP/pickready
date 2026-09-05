"""The New Candidates section (workflow section 32), against a real database.

Everything here is SQL rather than a pure function, so the assertions run
against Postgres. That is deliberate: the two properties most worth pinning are
three-valued-logic behaviours, and both of them read as correct in Python and
are wrong in SQL.

  * `l.created_at > (SELECT MAX(...))` over an empty set is NULL, not FALSE.
    In a WHERE clause that filters the row out, which is what makes "before the
    first round, nobody is new" fall out of the comparison rather than out of a
    special case.
  * `NOT (NULL)` is NULL. Writing the complementary filter as `NOT (...)`
    instead of `NOT COALESCE(..., FALSE)` empties the whole table on any job
    that has never invited anybody, which is most jobs most of the time.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services import job_candidates as jc

NOW = datetime.now(timezone.utc)


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _World:
    """One tenant, one job, and links whose arrival times we control."""

    def __init__(self, session) -> None:
        self.session = session
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()

    async def build(self) -> None:
        await self.session.execute(
            text("SELECT set_config('app.bypass_rls','on',false)")
        )
        await self.session.execute(
            text(
                "INSERT INTO tenants (id, name, domain, status) "
                "VALUES (:id, :name, :domain, 'active')"
            ),
            {
                "id": str(self.tenant_id),
                "name": f"New Candidates {self.tenant_id.hex[:8]}",
                "domain": f"nc-{self.tenant_id.hex[:8]}.invalid",
            },
        )
        await self.session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                "                  assessment_grade, posting_start_date, created_at) "
                "VALUES (:id, :tenant, 'Backend Engineer', '{}'::jsonb, 'draft', "
                "        'non_managerial', :start, :start)"
            ),
            {
                "id": str(self.job_id),
                "tenant": str(self.tenant_id),
                "start": NOW - timedelta(days=20),
            },
        )

    async def add_link(self, *, applied_days_ago: int) -> uuid.UUID:
        candidate_id, link_id = uuid.uuid4(), uuid.uuid4()
        applied = NOW - timedelta(days=applied_days_ago)
        await self.session.execute(
            text(
                "INSERT INTO candidates (id, tenant_id, email, full_name, created_at) "
                "VALUES (:id, :tenant, :email, :name, :at)"
            ),
            {
                "id": str(candidate_id),
                "tenant": str(self.tenant_id),
                "email": f"{candidate_id.hex[:12]}@example.invalid",
                "name": f"Candidate {candidate_id.hex[:6]}",
                "at": applied,
            },
        )
        await self.session.execute(
            text(
                "INSERT INTO job_candidate_links "
                "  (id, tenant_id, job_id, candidate_id, source, status, "
                "   created_at) "
                "VALUES (:id, :tenant, :job, :candidate, 'applied', 'applied', "
                "        :at)"
            ),
            {
                "id": str(link_id),
                "tenant": str(self.tenant_id),
                "job": str(self.job_id),
                "candidate": str(candidate_id),
                "at": applied,
            },
        )
        return link_id

    async def invite(self, link_id: uuid.UUID, *, days_ago: int) -> None:
        """Record an assessment round: this is what makes earlier arrivals old."""
        await self.session.execute(
            text(
                "INSERT INTO assessment_conversations "
                "  (id, tenant_id, job_id, job_candidate_link_id, grade, "
                "   invitation_sent_at, created_at) "
                "VALUES (:id, :tenant, :job, :link, 'non_managerial', :at, :at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant": str(self.tenant_id),
                "job": str(self.job_id),
                "link": str(link_id),
                "at": NOW - timedelta(days=days_ago),
            },
        )

    async def page(self, **kwargs):
        return await jc.ranked_candidates(
            self.session, self.job_id, "non_managerial", **kwargs
        )


async def _run(body):
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            world = _World(session)
            await world.build()
            await body(world)
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_before_the_first_round_nobody_is_new() -> None:
    """A job whose first batch has not gone out has ONE pool.

    Marking all of it New would make the section noise on the day the recruiter
    most needs the main list, and it would hide nobody -- everyone is already
    on the list they are reading.
    """

    async def body(world: _World) -> None:
        await world.add_link(applied_days_ago=10)
        await world.add_link(applied_days_ago=2)
        page = await world.page()
        assert page.total == 2
        assert page.new_candidate_count == 0
        assert all(row["is_new_candidate"] is False for row in page.rows)

    await _run(body)


@pytest.mark.asyncio
async def test_someone_who_applied_after_the_round_is_new() -> None:
    async def body(world: _World) -> None:
        considered = await world.add_link(applied_days_ago=10)
        await world.invite(considered, days_ago=5)
        latecomer = await world.add_link(applied_days_ago=1)

        page = await world.page()
        assert page.total == 2
        assert page.new_candidate_count == 1
        flags = {row["link_id"]: row["is_new_candidate"] for row in page.rows}
        assert flags[latecomer] is True
        assert flags[considered] is False

    await _run(body)


@pytest.mark.asyncio
async def test_the_boundary_is_the_MOST_RECENT_round() -> None:
    """Two rounds: only arrivals after the SECOND one are still new.

    `MAX(invitation_sent_at)`, not the first invitation. Someone who applied
    between the two rounds WAS in front of the team for the second one.
    """

    async def body(world: _World) -> None:
        first = await world.add_link(applied_days_ago=20)
        await world.invite(first, days_ago=15)
        between = await world.add_link(applied_days_ago=12)
        await world.invite(between, days_ago=8)
        after = await world.add_link(applied_days_ago=3)

        page = await world.page()
        flags = {row["link_id"]: row["is_new_candidate"] for row in page.rows}
        assert flags[after] is True
        assert flags[between] is False
        assert flags[first] is False
        assert page.new_candidate_count == 1

    await _run(body)


@pytest.mark.asyncio
async def test_an_invitation_that_was_never_sent_starts_no_round() -> None:
    """A conversation row IS the invitation, but only once it is SENT.

    `invitation_sent_at` is nullable, and a row written before the mail goes
    out would otherwise silently close the round for everybody who applied
    afterwards. This is the "a timestamp is not evidence that work happened"
    rule applied in the other direction: the ABSENCE of the timestamp is the
    evidence that nothing happened.
    """

    async def body(world: _World) -> None:
        link = await world.add_link(applied_days_ago=10)
        await world.session.execute(
            text(
                "INSERT INTO assessment_conversations "
                "  (id, tenant_id, job_id, job_candidate_link_id, grade, created_at) "
                "VALUES (:id, :tenant, :job, :link, 'non_managerial', now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant": str(world.tenant_id),
                "job": str(world.job_id),
                "link": str(link),
            },
        )
        await world.add_link(applied_days_ago=1)
        page = await world.page()
        assert page.new_candidate_count == 0

    await _run(body)


@pytest.mark.asyncio
async def test_the_filters_partition_the_table() -> None:
    """`new` and `considered` together are the whole list, with no overlap.

    The complementary filter is `NOT COALESCE(..., FALSE)` rather than
    `NOT (...)`, because `NOT NULL` is NULL and would drop every row on a job
    that has never invited anybody -- which is most jobs most of the time.
    """

    async def body(world: _World) -> None:
        considered = await world.add_link(applied_days_ago=10)
        await world.invite(considered, days_ago=5)
        latecomer = await world.add_link(applied_days_ago=1)

        everyone = await world.page()
        new = await world.page(arrival_filter=jc.ARRIVAL_NEW)
        rest = await world.page(arrival_filter=jc.ARRIVAL_CONSIDERED)

        assert new.total + rest.total == everyone.total
        assert [row["link_id"] for row in new.rows] == [latecomer]
        assert [row["link_id"] for row in rest.rows] == [considered]

    await _run(body)


@pytest.mark.asyncio
async def test_the_considered_filter_returns_everyone_before_the_first_round() -> None:
    """The three-valued-logic case, stated on its own because it is the bug."""

    async def body(world: _World) -> None:
        await world.add_link(applied_days_ago=10)
        await world.add_link(applied_days_ago=2)
        rest = await world.page(arrival_filter=jc.ARRIVAL_CONSIDERED)
        assert rest.total == 2
        assert len(rest.rows) == 2

    await _run(body)


@pytest.mark.asyncio
async def test_the_count_ignores_the_page_filter() -> None:
    """It answers "who is waiting OUTSIDE the list you are looking at".

    Narrowed by the same filter it would read 0 on the main list, which is the
    one place the recruiter needs to be told the supplement exists.
    """

    async def body(world: _World) -> None:
        considered = await world.add_link(applied_days_ago=10)
        await world.invite(considered, days_ago=5)
        await world.add_link(applied_days_ago=2)
        await world.add_link(applied_days_ago=1)

        rest = await world.page(arrival_filter=jc.ARRIVAL_CONSIDERED)
        assert rest.total == 1
        assert rest.new_candidate_count == 2

    await _run(body)


@pytest.mark.asyncio
async def test_a_new_candidate_is_labelled_never_hidden() -> None:
    """Same promise `profile_age` makes: the flag is presentation only.

    With no filter the table returns everybody, new and considered together,
    ranked identically. Nothing about being new moves a score or a position.
    """

    async def body(world: _World) -> None:
        considered = await world.add_link(applied_days_ago=10)
        await world.invite(considered, days_ago=5)
        latecomer = await world.add_link(applied_days_ago=1)

        page = await world.page()
        assert {row["link_id"] for row in page.rows} == {considered, latecomer}

    await _run(body)


def test_the_arrival_filter_cannot_be_injected() -> None:
    """It reaches a SQL string rather than a bind parameter, like `profile_age`."""
    assert set(jc.ARRIVALS) == {"new", "considered"}
    assert "'; DROP TABLE jobs; --" not in jc.ARRIVALS
