"""`last_engagement_at` is actually written, and by more than one door.

NOTHING ASSERTED THIS BEFORE. The stamp feeds the clock that erases dormant
profiles, and the only thing that wrote it was a private helper inside
`api/portal`, reached only through `_candidate_for_user`. There was no test
that it was written at all, on any path, so the failure mode was silent in both
directions: a stamp that stopped being written would have erased active people,
and a stamp written too eagerly would have made the inactivity rule
unreachable.

THE SPECIFICATION'S OWN RULE NEEDED A SECOND DOOR. A job-matching email the
candidate merely RECEIVES counts as engagement, and a worker cannot call a
helper defined inside a request module, so that half of the rule was not
implementable under the old shape. `record_engagement_by_id` is that door, and
its debounce is IN THE STATEMENT so two workers cannot both decide the stamp is
due and both write.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import engagement

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


@dataclass
class _Row:
    last_engagement_at: datetime | None = None


def test_a_candidate_who_has_never_been_seen_is_stamped() -> None:
    """NULL is due. Treating it as recent would leave the first engagement
    unwritten and measure dormancy from registration for ever."""
    row = _Row()
    assert engagement.record_engagement(row, now=NOW) is True
    assert row.last_engagement_at == NOW


def test_the_debounce_holds_for_a_day_and_no_longer() -> None:
    """One UPDATE per day, on a clock measured in months.

    Asserted at the boundary in both directions, because a debounce that is
    one comparison out either adds a write to every page load or stops
    recording a candidate who visits daily.
    """
    recent = _Row(last_engagement_at=NOW - timedelta(hours=23))
    assert engagement.record_engagement(recent, now=NOW) is False
    assert recent.last_engagement_at == NOW - timedelta(hours=23)

    stale = _Row(last_engagement_at=NOW - timedelta(days=1))
    assert engagement.record_engagement(stale, now=NOW) is True
    assert stale.last_engagement_at == NOW


def test_the_portal_chokepoint_still_records_it() -> None:
    """The behaviour the move must not have changed.

    `api/portal._record_engagement` is what every authenticated candidate
    route resolves through, and it now delegates. If it ever stops calling the
    service, every signed-in candidate silently stops counting as active.
    """
    from app.api import portal

    row = _Row()
    portal._record_engagement(row)  # type: ignore[arg-type]
    assert row.last_engagement_at is not None


async def _factory_or_skip():
    engine = create_async_engine(
        get_settings().database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping engagement stamp test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_a_worker_can_record_engagement_by_id() -> None:
    """The door the specification's job-matching rule needs.

    Read back from a SECOND session, because a value still sitting in the
    writing session cannot show that anything was committed.
    """
    engine, factory = await _factory_or_skip()
    subject = uuid.uuid4()
    bystander = uuid.uuid4()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                for candidate in (subject, bystander):
                    await session.execute(
                        text(
                            "INSERT INTO candidates (id, full_name, email, "
                            "consent_databank) VALUES (:c, 'Test Person', :e, "
                            "false)"
                        ),
                        {"c": str(candidate), "e": f"{candidate}@engage.test"},
                    )
                await session.commit()

                first = await engagement.record_engagement_by_id(
                    session, subject, now=NOW
                )
                # Same day: the statement's own predicate refuses, so two
                # workers arriving together cannot both write.
                second = await engagement.record_engagement_by_id(
                    session, subject, now=NOW + timedelta(hours=1)
                )
                third = await engagement.record_engagement_by_id(
                    session, subject, now=NOW + timedelta(days=2)
                )
                await session.commit()

        assert (first, second, third) == (True, False, True)

        async with factory() as session:
            async with superadmin_scope(session):
                stamped = (
                    await session.execute(
                        text(
                            "SELECT last_engagement_at FROM candidates "
                            "WHERE id = :c"
                        ),
                        {"c": str(subject)},
                    )
                ).scalar_one()
                untouched = (
                    await session.execute(
                        text(
                            "SELECT last_engagement_at FROM candidates "
                            "WHERE id = :c"
                        ),
                        {"c": str(bystander)},
                    )
                ).scalar_one()

        assert stamped == NOW + timedelta(days=2)
        assert untouched is None, (
            "recording one candidate's engagement reached another candidate, "
            "which would make the inactivity rule inapplicable to everybody"
        )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                for candidate in (subject, bystander):
                    await session.execute(
                        text("DELETE FROM candidates WHERE id = :c"),
                        {"c": str(candidate)},
                    )
                await session.commit()
        await engine.dispose()
