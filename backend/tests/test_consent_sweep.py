"""The consent sweep: the letters go out, and the erasure is GATED.

WHAT THIS TEST IS FOR
-----------------------
`test_consent_lifecycle.py` pins WHEN each threshold falls, with no database
and no clock. This pins what the sweep DOES about it, and the assertion that
matters most is a negative one: with `consent_auto_deletion_enabled` off, a
candidate who meets every deletion threshold is still there afterwards.

That gate is the only thing standing between a scheduled job and the permanent
erasure of real profiles, and it defaults off for a concrete reason: until
`last_engagement_at` has been recorded for longer than the inactivity window,
dormancy is computed from REGISTRATION, so a genuinely active candidate reads
as dormant. A test that only ran with the gate armed would prove the dangerous
half works and say nothing about whether it can be kept shut.

Skips cleanly with no database, runs for real inside the backend container.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


def _run(coro_factory):
    """Run one coroutine on its OWN loop, with its OWN engine.

    Two constraints collide here and the shape below is what satisfies both.

    First, `sweep_consent_lifecycle` is a worker entry point that calls
    `asyncio.run` itself, like every task in `workers/tasks.py`, so these tests
    have to be SYNC: an async test raises "asyncio.run() cannot be called from
    a running event loop".

    Second, AN ASYNC ENGINE CANNOT BE SHARED ACROSS `asyncio.run` CALLS. An
    asyncpg connection belongs to the loop that opened it, and the pool hands
    the same connection to the next call on a different loop, which fails with
    "cannot perform operation: another operation is in progress". That is the
    same per-loop binding trap `claude.md` records for the realtime hub, met
    here through a connection pool instead of a task.

    So `coro_factory` is a CALLABLE that builds its coroutine after the engine
    exists, and the engine is created and disposed inside this one loop.
    """
    async def _wrapped():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.config import get_settings

        engine = create_async_engine(get_settings().database_url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


def _skip_without_database() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    async def _probe():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect():
                return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await engine.dispose()

    if not asyncio.run(_probe()):
        pytest.skip("no database reachable, skipping consent sweep test")


class _World:
    """Four candidates, one per outcome, so a sweep that did the same thing to
    everybody could not pass. `fresh` is the control."""

    def __init__(self) -> None:
        self.fresh = uuid.uuid4()
        self.reminder_due = uuid.uuid4()
        self.warning_due = uuid.uuid4()
        self.deletion_due = uuid.uuid4()

    def all_ids(self):
        return (self.fresh, self.reminder_due, self.warning_due, self.deletion_due)


async def _seed(factory, w: _World) -> None:
    from app.core.config import get_settings
    from app.core.db import superadmin_scope

    s = get_settings()
    now = datetime.now(timezone.utc)
    overdue = now - timedelta(days=30 * s.consent_renewal_months + 1)
    grace = timedelta(days=s.consent_grace_days)

    # (id, created_at, engagement, reminder_sent, final_sent). Engagement is
    # NOW for every one of them, so the dormancy clock is deliberately out of
    # the way and each row differs only in its consent stage.
    rows = (
        (w.fresh, now, now, None, None),
        (w.reminder_due, overdue, now, None, None),
        (w.warning_due, overdue, now, now - grace, None),
        (w.deletion_due, overdue, now, now - grace * 2, now - grace),
    )
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for cid, created, engaged, reminded, warned in rows:
                    await session.execute(
                        text(
                            "INSERT INTO candidates (id, full_name, email, "
                            "consent_databank, created_at, last_engagement_at, "
                            "consent_reminder_sent_at, consent_final_warning_at) "
                            "VALUES (:i, 'Sweep Subject', :e, false, :c, :g, "
                            ":r, :w)"
                        ),
                        {"i": str(cid), "e": f"{cid}@sweep.test", "c": created,
                         "g": engaged, "r": reminded, "w": warned},
                    )


async def _cleanup(factory, w: _World) -> None:
    from app.core.db import superadmin_scope

    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for cid in w.all_ids():
                    await session.execute(
                        text("DELETE FROM candidates WHERE id = :i"),
                        {"i": str(cid)},
                    )


async def _row(factory, cid):
    from app.core.db import superadmin_scope

    async with factory() as session:
        async with superadmin_scope(session):
            return (
                await session.execute(
                    text(
                        "SELECT consent_reminder_sent_at, consent_final_warning_at "
                        "FROM candidates WHERE id = :i"
                    ),
                    {"i": str(cid)},
                )
            ).first()


def test_the_sweep_writes_letters_and_erases_NOBODY_while_unarmed(
    monkeypatch,
) -> None:
    """The negative assertion is the point of this file.

    The `deletion_due` candidate meets a threshold. With the gate off it must
    still exist afterwards.
    """
    from app.core.config import get_settings
    from app.workers import tasks

    _skip_without_database()
    w = _World()

    sent: list[tuple] = []
    monkeypatch.setattr(
        tasks, "dispatch", lambda name, args=None, **kw: sent.append((name, args))
    )
    # Explicit rather than relying on the default, so this test states the
    # posture it is testing instead of inheriting it.
    monkeypatch.setattr(get_settings(), "consent_auto_deletion_enabled", False)

    try:
        _run(lambda f: _seed(f, w))
        tasks.sweep_consent_lifecycle()

        # The two letters went out, to the right people, once each.
        templates = [args[2] for _, args in sent]
        assert templates.count("consent_renewal_reminder") == 1
        assert templates.count("consent_final_warning") == 1
        addressed = {args[1] for _, args in sent}
        assert f"{w.reminder_due}@sweep.test" in addressed
        assert f"{w.warning_due}@sweep.test" in addressed

        # And the stamps landed, which is what stops a second letter.
        assert _run(lambda f: _row(f, w.reminder_due))[0] is not None
        assert _run(lambda f: _row(f, w.warning_due))[1] is not None

        # The control was not written to at all.
        assert f"{w.fresh}@sweep.test" not in addressed
        assert _run(lambda f: _row(f, w.fresh)) == (None, None)

        # THE ASSERTION THIS FILE EXISTS FOR.
        assert _run(lambda f: _row(f, w.deletion_due)) is not None, (
            "a candidate was ERASED while consent_auto_deletion_enabled was "
            "off. That gate is the only thing between a scheduled job and the "
            "permanent deletion of real profiles."
        )
    finally:
        _run(lambda f: _cleanup(f, w))
        

def test_the_armed_sweep_erases_the_right_person_and_only_that_person(
    monkeypatch,
) -> None:
    """The dangerous path, which the other tests deliberately never take.

    Everything else here runs UNARMED, which proves the gate holds and proves
    nothing about what happens when somebody opens it. This arms it once, in a
    test, so the erasure is exercised before it is ever exercised by a
    scheduler against real people.

    Two assertions, and the second is the one that would catch a catastrophe:
    the candidate past the threshold is gone, and the three who are not are
    all still there. A sweep that erased everybody would satisfy the first on
    its own.
    """
    from app.core.config import get_settings
    from app.workers import tasks

    _skip_without_database()
    w = _World()

    monkeypatch.setattr(tasks, "dispatch", lambda name, args=None, **kw: None)
    monkeypatch.setattr(get_settings(), "consent_auto_deletion_enabled", True)

    try:
        _run(lambda f: _seed(f, w))
        tasks.sweep_consent_lifecycle()

        assert _run(lambda f: _row(f, w.deletion_due)) is None, (
            "the armed sweep did not erase a candidate past the deletion "
            "threshold, so the feature does not work when it is turned on"
        )
        for survivor, label in (
            (w.fresh, "fresh"),
            (w.reminder_due, "reminder_due"),
            (w.warning_due, "warning_due"),
        ):
            assert _run(lambda f, s=survivor: _row(f, s)) is not None, (
                f"the armed sweep erased the {label} candidate, who is not "
                "past any deletion threshold"
            )
    finally:
        _run(lambda f: _cleanup(f, w))


def test_a_second_sweep_does_not_write_to_the_same_person_twice(
    monkeypatch,
) -> None:
    """The stamp is what makes the sweep idempotent.

    It runs daily. Without the stamp gating the stage, everybody overdue would
    receive a reminder every single day until they answered, which is the
    failure mode that turns a compliance feature into a complaint.
    """
    from app.core.config import get_settings
    from app.workers import tasks

    _skip_without_database()
    w = _World()

    sent: list[tuple] = []
    monkeypatch.setattr(
        tasks, "dispatch", lambda name, args=None, **kw: sent.append((name, args))
    )
    monkeypatch.setattr(get_settings(), "consent_auto_deletion_enabled", False)

    try:
        _run(lambda f: _seed(f, w))
        tasks.sweep_consent_lifecycle()
        after_first = len(sent)
        tasks.sweep_consent_lifecycle()

        assert len(sent) == after_first, (
            "the second sweep wrote to somebody again. Every overdue "
            f"candidate would then be written to daily: {sent[after_first:]}"
        )
    finally:
        _run(lambda f: _cleanup(f, w))
        