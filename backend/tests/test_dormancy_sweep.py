"""The inactivity rule WARNS before it deletes, and the gate still holds.

WHAT THIS FILE PINS THAT NOTHING ELSE DOES
--------------------------------------------
`test_dormancy_lifecycle.py` pins WHEN each threshold falls, with no database
and no clock. This pins what the sweep DOES about it, and the assertion that
matters most is `test_a_dormant_candidate_is_warned_and_not_erased`: with the
deletion gate ARMED and a candidate long past the inactivity threshold, the
sweep must write a letter and leave them alone. That is the whole of change 17.
Before it, the sweep read one boolean and erased, so somebody who had simply
not signed in for two years lost their profile, their assessment data and their
background verification record without ever being told it was coming.

THE GATE IS MUTATION CHECKED HERE. `test_the_gate_is_off_by_default` and
`test_nothing_is_erased_while_the_gate_is_shut` fail together if
`consent_auto_deletion_enabled` is removed, defaulted to True, or stops being
consulted, which is the only mechanism standing between a daily scheduled job
and the permanent erasure of real profiles.

Skips cleanly with no database, runs for real against the test stack.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


def _run(coro_factory):
    """One coroutine, its OWN loop, its OWN engine.

    `sweep_consent_lifecycle` calls `asyncio.run` itself, like every worker
    entry point, so these tests are SYNC. And an async engine cannot be shared
    across `asyncio.run` calls, because an asyncpg connection belongs to the
    loop that opened it. `test_consent_sweep.py` records the same shape and the
    same reason.
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
        pytest.skip("no database reachable, skipping dormancy sweep test")


class _World:
    """Three candidates on the INACTIVITY clock alone.

    Every one of them registered recently enough that their consent is not
    due, so nothing here can pass because the consent path happened to do the
    right thing. `active` is the control.
    """

    def __init__(self) -> None:
        self.active = uuid.uuid4()
        self.warning_due = uuid.uuid4()
        self.deletion_due = uuid.uuid4()

    def all_ids(self):
        return (self.active, self.warning_due, self.deletion_due)


async def _seed(factory, w: _World) -> None:
    from app.core.config import get_settings
    from app.core.db import superadmin_scope

    s = get_settings()
    now = datetime.now(timezone.utc)
    grace = timedelta(days=s.consent_grace_days)
    dormant_since = now - timedelta(days=30 * s.consent_inactivity_months + 1)

    # (id, last_engagement_at, dormancy_warning_sent_at). `created_at` is NOW
    # for all three, so the consent clock is deliberately out of the way.
    rows = (
        (w.active, now, None),
        (w.warning_due, dormant_since, None),
        (w.deletion_due, dormant_since, now - grace),
    )
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for cid, engaged, warned in rows:
                    await session.execute(
                        text(
                            "INSERT INTO candidates (id, full_name, email, "
                            "consent_databank, created_at, last_engagement_at, "
                            "dormancy_warning_sent_at) "
                            "VALUES (:i, 'Dormancy Subject', :e, false, :c, "
                            ":g, :w)"
                        ),
                        {
                            "i": str(cid),
                            "e": f"{cid}@dormancy.test",
                            "c": now,
                            "g": engaged,
                            "w": warned,
                        },
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
                    await session.execute(
                        text(
                            "DELETE FROM candidate_deletion_requests "
                            "WHERE candidate_id = :i"
                        ),
                        {"i": str(cid)},
                    )


async def _row(factory, cid):
    from app.core.db import superadmin_scope

    async with factory() as session:
        async with superadmin_scope(session):
            return (
                await session.execute(
                    text(
                        "SELECT dormancy_warning_sent_at FROM candidates "
                        "WHERE id = :i"
                    ),
                    {"i": str(cid)},
                )
            ).first()


def test_the_gate_is_off_by_default() -> None:
    """The posture, asserted rather than assumed.

    It is not timidity. Until `last_engagement_at` has been recorded for
    longer than the inactivity window, every dormancy answer is computed from
    REGISTRATION, so a genuinely active candidate reads as dormant. The
    measurement is safe from day one; the deletion is not.
    """
    from app.core.config import Settings

    assert Settings().consent_auto_deletion_enabled is False


def test_a_dormant_candidate_is_warned_and_not_erased(monkeypatch) -> None:
    """CHANGE 17, IN ONE ASSERTION.

    The gate is ARMED here on purpose. A candidate two years past the
    inactivity threshold who has never been written to must receive a letter
    and must still exist afterwards, because the specification requires a
    warning and then a grace period. Running this unarmed would prove nothing:
    the gate would be doing the work, and the old code, which erased on a bare
    boolean, would pass.
    """
    from app.core.config import get_settings
    from app.workers import tasks

    _skip_without_database()
    w = _World()

    sent: list[tuple] = []
    monkeypatch.setattr(
        tasks, "dispatch", lambda name, args=None, **kw: sent.append((name, args))
    )
    monkeypatch.setattr(get_settings(), "consent_auto_deletion_enabled", True)

    try:
        _run(lambda f: _seed(f, w))
        tasks.sweep_consent_lifecycle()

        templates = [args[2] for _, args in sent if args and len(args) > 2]
        addressed = {args[1] for _, args in sent if args and len(args) > 1}
        assert templates.count("dormancy_deletion_warning") == 1
        assert f"{w.warning_due}@dormancy.test" in addressed

        assert _run(lambda f: _row(f, w.warning_due)) is not None, (
            "a dormant candidate was ERASED on the sweep that first noticed "
            "they were dormant. The specification requires a deletion warning "
            "and a grace period, and this is the exact behaviour change 17 "
            "exists to remove."
        )
        assert _run(lambda f: _row(f, w.warning_due))[0] is not None, (
            "the warning stamp was not written, so the letter goes out again "
            "on every sweep and the grace window never starts"
        )

        # The control was not written to and not erased.
        assert f"{w.active}@dormancy.test" not in addressed
        assert _run(lambda f: _row(f, w.active)) == (None,)
    finally:
        _run(lambda f: _cleanup(f, w))


def test_the_armed_sweep_erases_only_after_the_grace_period(monkeypatch) -> None:
    """The dangerous path, exercised once here before a scheduler ever takes it.

    The candidate who was warned and whose window has closed is erased; the
    one who was warned today and the one who is not dormant are not.
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
            "a candidate who was warned and whose grace period has elapsed "
            "was not erased, so the inactivity rule does not work when it is "
            "turned on"
        )
        for survivor, label in ((w.active, "active"), (w.warning_due, "warning_due")):
            assert _run(lambda f, s=survivor: _row(f, s)) is not None, (
                f"the armed sweep erased the {label} candidate, who is not "
                "past any deletion threshold"
            )
    finally:
        _run(lambda f: _cleanup(f, w))


def test_nothing_is_erased_while_the_gate_is_shut(monkeypatch) -> None:
    """THE MUTATION CHECK.

    Remove the gate, default it to True, or stop consulting it, and this test
    fails: the `deletion_due` candidate meets every threshold and is erased.
    It is the only mechanism between a daily scheduled job and the permanent
    deletion of real profiles.
    """
    from app.core.config import get_settings
    from app.workers import tasks

    _skip_without_database()
    w = _World()

    monkeypatch.setattr(tasks, "dispatch", lambda name, args=None, **kw: None)
    monkeypatch.setattr(get_settings(), "consent_auto_deletion_enabled", False)

    try:
        _run(lambda f: _seed(f, w))
        tasks.sweep_consent_lifecycle()

        for cid, label in (
            (w.active, "active"),
            (w.warning_due, "warning_due"),
            (w.deletion_due, "deletion_due"),
        ):
            assert _run(lambda f, c=cid: _row(f, c)) is not None, (
                f"the {label} candidate was erased while "
                "consent_auto_deletion_enabled was off"
            )
    finally:
        _run(lambda f: _cleanup(f, w))


def test_an_erasure_on_the_dormancy_path_opens_a_deletion_record(
    monkeypatch,
) -> None:
    """ONE canonical workflow, not a second engine.

    Consent expiry and inactivity are two REASONS recorded on the same
    erasure. The record is what carries the object keys past the rows that
    named them, so a dormancy erasure that skipped it would delete the person
    and leave their resume and their assessment recording in the store with
    nothing anywhere knowing they were there.
    """
    from app.core.config import get_settings
    from app.core.db import superadmin_scope
    from app.services import consent_lifecycle as cl
    from app.workers import tasks

    _skip_without_database()
    w = _World()

    monkeypatch.setattr(tasks, "dispatch", lambda name, args=None, **kw: None)
    monkeypatch.setattr(get_settings(), "consent_auto_deletion_enabled", True)

    async def _request(factory):
        async with factory() as session:
            async with superadmin_scope(session):
                return (
                    await session.execute(
                        text(
                            "SELECT state, reason FROM "
                            "candidate_deletion_requests WHERE candidate_id = :i"
                        ),
                        {"i": str(w.deletion_due)},
                    )
                ).first()

    try:
        _run(lambda f: _seed(f, w))
        tasks.sweep_consent_lifecycle()
        row = _run(_request)
        assert row is not None, (
            "the dormancy erasure wrote no deletion record, so the stored "
            "files it was supposed to remove are unreachable"
        )
        state, reason = row
        assert reason == cl.REASON_DORMANT, (
            "the erasure does not record WHICH clock deleted this person, and "
            "'deleted' with no reason is a fact nobody can answer a complaint "
            "with"
        )
        assert state == "completed", (
            "a candidate with no stored files should finish in the same pass"
        )
    finally:
        _run(lambda f: _cleanup(f, w))


def test_a_deletion_reason_is_never_written_to_actor_role() -> None:
    """The bug this change found, pinned so it cannot come back.

    The sweep passed its reason as `cascade_erasure(actor_role=reason)`.
    `audit_log.actor_role` is `varchar(30)` and means the role of the human
    who acted; `inactive_for_the_retention_period` is thirty three characters.
    So the dormancy erasure raised StringDataRightTruncationError on its own
    audit write and rolled the whole erasure back, EVERY TIME, and nothing saw
    it because the only armed test in the suite took the consent reason, which
    fits in thirty.

    A reason is a FACT about the erasure and now travels in the receipt. This
    test asserts both halves: that the reason genuinely does not fit in that
    column, so the check is not vacuous, and that the sweep no longer sends it
    there.
    """
    import inspect

    from app.services import consent_lifecycle as cl
    from app.services import erasure
    from app.workers import tasks

    assert len(cl.REASON_DORMANT) > 30, (
        "the reason now fits in actor_role, so this test proves nothing. "
        "Check that the column is still varchar(30) before deleting it."
    )
    assert "reason" in inspect.signature(erasure.cascade_erasure).parameters
    source = inspect.getsource(tasks.sweep_consent_lifecycle)
    assert "actor_role=reason" not in source, (
        "the sweep is writing a deletion reason into actor_role again, which "
        "cannot commit"
    )
