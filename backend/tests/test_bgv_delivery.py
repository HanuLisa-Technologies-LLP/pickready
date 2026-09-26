"""The three-day clock, and the two definitions of it that must not disagree.

WHAT THIS PINS
----------------
`bgv_delivery.clock_start` is Python and `REMINDER_DUE_WHERE` is SQL, and they
answer the same question: when did the employer's three days begin. Two
implementations of one rule is the failure mode `services/tiers.py` demonstrated
at product scale (747 of 1075 rows carrying a label the correct scale disagreed
with), so the SQL predicate is run against REAL ROWS here and its answer is
compared against the Python function's, case by case.

The cases are chosen so that a naive `first_sent_at` predicate, which is what
the sweep used before, produces a DIFFERENT answer on at least one of them: a
verification delivered late is not yet due, and a bounced one is never due at
all. A parity test that both implementations pass by accident pins nothing.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import bgv_delivery, bgv_form

TTL = 3


def _run(coro):
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


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                sa.text("SELECT delivery_status FROM bgv_verifications LIMIT 0")
            )
        return True
    except Exception:  # noqa: BLE001 -- no database, or migration 0114 unapplied
        return False
    finally:
        await engine.dispose()


# ── The pure half ────────────────────────────────────────────────────────────


def test_the_clock_starts_at_delivery_when_there_is_one():
    sent = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    delivered = datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc)
    assert bgv_delivery.clock_start(delivered, sent) == delivered


def test_the_clock_falls_back_to_the_send_and_says_so():
    """No delivery event is a REAL state, not a reason to wait for ever.

    Under the smtp transport Gmail reports no per-message delivery at all, so a
    link keyed strictly on `delivered_at` would never expire: a standing
    credential in a third party's mailbox, which is the thing the TTL exists
    to prevent.
    """
    sent = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    assert bgv_delivery.clock_start(None, sent) == sent
    assert bgv_delivery.link_expires_at(None, sent) == bgv_form.expires_at(sent)


def test_an_unsent_verification_has_no_live_link():
    assert bgv_delivery.link_expires_at(None, None) is None
    assert bgv_delivery.link_expired(None, None) is True


def test_the_window_is_measured_from_delivery_not_from_send():
    """The case the old behaviour got wrong, stated as an assertion.

    Sent on day 0, delivered on day 2. On day 4 the send-based clock has
    already expired the link while the employer has had it for two days.
    """
    sent = datetime(2026, 9, 1, tzinfo=timezone.utc)
    delivered = sent + timedelta(days=2)
    day_four = sent + timedelta(days=4)
    assert bgv_delivery.link_expired(delivered, sent, now=day_four) is False
    assert bgv_delivery.link_expired(None, sent, now=day_four) is True


# ── The SQL half, against real rows ──────────────────────────────────────────


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.email = f"clock-{self.candidate.hex[:10]}@bgvclock.test"
        #: (label, first_sent_at offset days, delivered_at offset or None,
        #:  delivery_status, expected to be chased)
        self.cases: list[tuple[str, int, int | None, str, bool]] = [
            ("delivered four days ago", -6, -4, "delivered", True),
            ("delivered yesterday", -6, -1, "delivered", False),
            ("sent six days ago, never confirmed", -6, None, "sent", True),
            ("sent yesterday, never confirmed", -1, None, "sent", False),
            ("bounced six days ago", -6, None, "bounced", False),
        ]
        self.verifications = [uuid.uuid4() for _ in self.cases]
        #: ONE EMPLOYMENT PER CASE. `uq_bgv_verification_employer` is on
        #: (tenant, employment), which is the constraint that makes two
        #: recruiters produce one verification: five rows for one employer in
        #: one tenant is a state the schema correctly refuses.
        self.employments = [uuid.uuid4() for _ in self.cases]


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database with migration 0114 -- skipping BGV clock test")
    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        now = datetime.now(timezone.utc)
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"BGV-CLOCK-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.bgvclock.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidates (id, full_name, email, "
                            " consent_databank, employment_background, "
                            " created_at) "
                            "VALUES (:id, 'Anitha Rao', :email, false, "
                            " 'experienced', now())"
                        ),
                        {"id": str(w.candidate), "email": w.email},
                    )
                    for employment in w.employments:
                        await session.execute(
                            sa.text(
                                "INSERT INTO candidate_employments (id, "
                                " candidate_id, employer_name, designation, "
                                " started_on, ended_on, hr_name, hr_email, "
                                " created_at) "
                                "VALUES (:id, :cid, 'Northwind Systems', "
                                " 'Engineer', DATE '2021-01-04', "
                                " DATE '2024-03-29', 'R Menon', :hr, now())"
                            ),
                            {
                                "id": str(employment),
                                "cid": str(w.candidate),
                                "hr": f"hr-{employment.hex[:8]}@northwind.example",
                            },
                        )
                    for vid, employment, (
                        _,
                        sent_days,
                        delivered_days,
                        state,
                        _due,
                    ) in zip(w.verifications, w.employments, w.cases):
                        await session.execute(
                            sa.text(
                                "INSERT INTO bgv_verifications (id, tenant_id, "
                                " candidate_id, candidate_employment_id, status, "
                                " first_sent_at, delivered_at, delivery_status, "
                                " created_at) "
                                "VALUES (:id, :tid, :cid, :eid, 'pending', "
                                " :sent, :delivered, :state, now())"
                            ),
                            {
                                "id": str(vid),
                                "tid": str(w.tenant),
                                "cid": str(w.candidate),
                                "eid": str(employment),
                                "sent": now + timedelta(days=sent_days),
                                "delivered": (
                                    None
                                    if delivered_days is None
                                    else now + timedelta(days=delivered_days)
                                ),
                                "state": state,
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = :id"),
                        {"id": str(w.candidate)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(w.tenant)},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


def test_the_sweep_predicate_agrees_with_the_python_clock(world: World):
    """One rule, two languages, compared over rows that separate them.

    A send-based predicate would chase "delivered yesterday" (sent six days
    ago) and would chase the bounced row. Both are in the fixture precisely so
    that this assertion cannot pass by accident.
    """

    async def _ask() -> set[str]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = (
                        await session.execute(
                            sa.text(bgv_delivery.reminder_due_sql()),
                            {"days": TTL},
                        )
                    ).mappings().all()
        return {str(row["id"]) for row in rows}

    chased = _run(_ask())
    for vid, (label, _sent, _delivered, _state, expected) in zip(
        world.verifications, world.cases
    ):
        assert (str(vid) in chased) is expected, label


def test_a_bounced_request_is_never_chased(world: World):
    """The half that protects the candidate from the product's own mistake.

    Chasing somebody about an employer who never received anything sends them
    to argue with an innocent HR team about an email that does not exist, and
    the bounce path has already written to them asking for a correction.
    """

    async def _ask() -> set[str]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = (
                        await session.execute(
                            sa.text(bgv_delivery.reminder_due_sql()),
                            {"days": TTL},
                        )
                    ).mappings().all()
        return {str(row["id"]) for row in rows}

    bounced_id = str(world.verifications[4])
    assert bounced_id not in _run(_ask())


def test_marking_delivered_is_written_once_and_never_demotes_a_bounce(world: World):
    """SNS delivers at least once, and SES reports per RECIPIENT.

    So a redelivered delivery event must not restart the employer's three days,
    and a delivery for a second recipient must not erase a bounce a candidate
    has already been asked to fix.
    """
    verification = world.verifications[2]
    bounced = world.verifications[4]
    first = datetime.now(timezone.utc)
    second = first + timedelta(hours=6)

    async def _act() -> tuple[datetime, str]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await bgv_delivery.mark_delivered(
                        session, verification_id=verification, at=first
                    )
                    await bgv_delivery.mark_delivered(
                        session, verification_id=verification, at=second
                    )
                    await bgv_delivery.mark_delivered(
                        session, verification_id=bounced, at=second
                    )
        # READ BACK FROM A SECOND CONNECTION, after the commit. An assertion on
        # the in-session object cannot see a write that rolled back.
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    stamped = (
                        await session.execute(
                            sa.text(
                                "SELECT delivered_at FROM bgv_verifications "
                                "WHERE id = :id"
                            ),
                            {"id": str(verification)},
                        )
                    ).scalar()
                    bounced_state = (
                        await session.execute(
                            sa.text(
                                "SELECT delivery_status FROM bgv_verifications "
                                "WHERE id = :id"
                            ),
                            {"id": str(bounced)},
                        )
                    ).scalar()
        return stamped, bounced_state

    stamped, bounced_state = _run(_act())
    assert abs((stamped - first).total_seconds()) < 1
    assert bounced_state == "bounced"


def test_the_effective_address_is_the_correction_when_one_exists(world: World):
    """`candidate_employments` is never rewritten; the newest correction wins."""

    async def _act() -> tuple[str, str, str]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    declared = await bgv_delivery.effective_hr_email(
                        session, world.employments[0]
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO bgv_contact_corrections (id, "
                            " candidate_id, candidate_employment_id, "
                            " previous_hr_email, hr_email, reason, created_at) "
                            "VALUES (gen_random_uuid(), :cid, :eid, :old, "
                            " 'people@northwind.example', 'bounced', now())"
                        ),
                        {
                            "cid": str(world.candidate),
                            "eid": str(world.employments[0]),
                            "old": declared,
                        },
                    )
                    corrected = await bgv_delivery.effective_hr_email(
                        session, world.employments[0]
                    )
                    row_still = (
                        await session.execute(
                            sa.text(
                                "SELECT hr_email FROM candidate_employments "
                                "WHERE id = :eid"
                            ),
                            {"eid": str(world.employments[0])},
                        )
                    ).scalar()
        return declared, corrected, row_still

    declared, corrected, row_still = _run(_act())
    assert corrected == "people@northwind.example"
    # THE CLAIM IS UNTOUCHED. This is the whole reason the correction is a row
    # beside the declaration rather than an UPDATE of it.
    assert row_still == declared

    # AND THE SWEEP AGREES. The chase letter masks the HR address, so a
    # candidate who has already corrected one must not be shown a mask of the
    # address they replaced: `reminder_due_sql` resolves the same correction
    # the Python helper does.
    async def _chase_address() -> str | None:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = (
                        await session.execute(
                            sa.text(bgv_delivery.reminder_due_sql()),
                            {"days": TTL},
                        )
                    ).mappings().all()
        for row in rows:
            if str(row["id"]) == str(world.verifications[0]):
                return row["hr_email"]
        return None

    assert _run(_chase_address()) == "people@northwind.example"
