"""The pause record (`services/assessment_conversation/pauses.py`).

Two halves. The arithmetic the turn timer relies on (`paused_seconds`) is pure
and tested without a database: a union, clipped to the window, where an overlap
counts once, because a warning pause and a device pause can be open together
and the candidate must not get the same second back twice.

The record itself is tested against the real table, because its guarantees
live in the schema: one OPEN pause per reason per conversation (a partial
unique index), a cap that ends a pause whether or not anybody closes it, and
an expired row closed at the instant it stopped counting, never "now".
"""
from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.services.assessment_conversation import pauses
from app.services.assessment_conversation.pauses import Interval

T0 = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# ══════════════════════════════════════════════════════════════════════════
# THE ARITHMETIC
# ══════════════════════════════════════════════════════════════════════════


def test_no_pause_is_no_time() -> None:
    assert pauses.paused_seconds((), start=_at(0), end=_at(100)) == 0.0


def test_a_closed_pause_inside_the_window_counts_in_full() -> None:
    intervals = (Interval("warning", _at(10), _at(25)),)
    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 15.0


def test_a_pause_is_clipped_to_the_window() -> None:
    """A pause that began before the turn was shown, or runs past the moment
    asked about, counts only for the part inside."""
    intervals = (Interval("device_loss", _at(-30), _at(20)),)
    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 20.0
    intervals = (Interval("device_loss", _at(90), _at(200)),)
    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 10.0


def test_an_open_pause_runs_to_the_end_of_the_window() -> None:
    intervals = (Interval("transcription", _at(40), None),)
    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 60.0


def test_overlapping_pauses_count_once() -> None:
    """[10, 40], [30, 60] and [35, 45] cover [10, 60]: fifty seconds. Summing
    the intervals instead of taking their union gives seventy, and this test
    fails (mutation-checked)."""
    intervals = (
        Interval("warning", _at(10), _at(40)),
        Interval("device_loss", _at(30), _at(60)),
        Interval("transcription", _at(35), _at(45)),
    )
    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 50.0


def test_disjoint_pauses_add_up() -> None:
    intervals = (Interval("warning", _at(10), _at(20)), Interval("warning", _at(50), _at(55)))
    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 15.0


def test_an_empty_or_inverted_window_is_no_time() -> None:
    intervals = (Interval("warning", _at(0), None),)
    assert pauses.paused_seconds(intervals, start=_at(10), end=_at(10)) == 0.0
    assert pauses.paused_seconds(intervals, start=_at(10), end=_at(5)) == 0.0


def test_the_pause_module_imports_nothing_from_proctoring() -> None:
    """The conversation engine reads this module to time a turn. If it
    imported the proctoring package, every module that times a turn would
    acquire it, which `test_proctoring_scoring_isolation.py` exists to stop."""
    source = pathlib.Path(pauses.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not {name for name in imported if name.startswith("app.services.proctoring")}


def test_an_unknown_reason_is_refused_before_the_database_is_asked() -> None:
    import asyncio

    with pytest.raises(pauses.UnknownPauseReason):
        asyncio.run(pauses.count_pauses(None, None, "coffee"))


# ══════════════════════════════════════════════════════════════════════════
# THE RECORD
# ══════════════════════════════════════════════════════════════════════════


async def _conversation(factory, fx):
    from tests.test_proctoring_pipeline import _seed

    await _seed(factory, fx)


@pytest.mark.asyncio
async def test_opening_twice_is_one_pause_and_the_cap_ends_it() -> None:
    from app.core.db import superadmin_scope
    from tests.test_proctoring_pipeline import _Fx, _cleanup, _factory_or_skip

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _conversation(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    first = await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(0), max_seconds=30
                    )
                    again = await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(5), max_seconds=30
                    )
                    assert again.id == first.id, "a retried open stacked a second pause"
                    assert first.expires_at == _at(30)
                    assert await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(29)
                    ) is not None
                    # Past the cap the pause no longer stops the clock...
                    assert await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(31)
                    ) is None
                    # ...although nobody closed it: its owner may still act on it.
                    assert (
                        await pauses.unclosed_pause(s, fx.conv_id, pauses.PAUSE_WARNING)
                    ).id == first.id
                    intervals = await pauses.pauses_for_turn(
                        s, fx.conv_id, since=_at(0), until=_at(300)
                    )
                    assert intervals == (Interval("warning", _at(0), _at(30)),)

                    # The next open closes the expired row AT ITS CAP, then opens.
                    second = await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(100), max_seconds=30
                    )
                    assert second.id != first.id
                    await s.refresh(first)
                    assert first.ended_at == _at(30)
                    assert await pauses.count_pauses(s, fx.conv_id, pauses.PAUSE_WARNING) == 2
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_reasons_are_independent_and_a_ref_closes_only_its_own_pause() -> None:
    import uuid

    from app.core.db import superadmin_scope
    from tests.test_proctoring_pipeline import _Fx, _cleanup, _factory_or_skip

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _conversation(factory, fx)
        voice_a = uuid.uuid4()
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(0), ref_id=voice_a
                    )
                    await pauses.open_pause(s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(2))
                    assert await pauses.close_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(5), ref_id=uuid.uuid4()
                    ) is None, "an acknowledgement closed somebody else's pause"
                    closed = await pauses.close_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(9), ref_id=voice_a
                    )
                    assert closed is not None and closed.ended_at == _at(9)
                    assert await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(9)
                    ) is not None
                    assert await pauses.close_all_open(s, fx.conv_id, at=_at(20)) == 1
                    assert await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(21)
                    ) is None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_database_allows_one_open_pause_per_reason() -> None:
    """The partial unique index is the guarantee, not the service's check."""
    import uuid

    from sqlalchemy.exc import IntegrityError

    from app.core.db import superadmin_scope
    from app.models.assessment_pause import AssessmentPause
    from tests.test_proctoring_pipeline import _Fx, _cleanup, _factory_or_skip

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _conversation(factory, fx)
        async with factory() as s:
            with pytest.raises(IntegrityError):
                async with s.begin():
                    async with superadmin_scope(s):
                        for offset in (0, 1):
                            s.add(AssessmentPause(
                                id=uuid.uuid4(), tenant_id=fx.tenant_id,
                                conversation_id=fx.conv_id, reason="warning",
                                started_at=_at(offset),
                            ))
                            await s.flush()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


def test_an_interval_is_open_until_its_end_and_a_scheduled_end_is_still_open() -> None:
    interval = Interval("transcription", _at(10), _at(40))
    assert not interval.open_at(_at(9))
    assert interval.open_at(_at(10))
    assert interval.open_at(_at(39))
    assert not interval.open_at(_at(40)), "a pause stops counting AT its end"
    assert Interval("warning", _at(0), None).open_at(_at(10_000))


@pytest.mark.asyncio
async def test_a_close_in_the_future_is_scheduled_and_an_earlier_close_brings_it_forward() -> None:
    """The failed-transcription window: the voice task schedules the end, the
    candidate's acknowledgement brings it forward, and nothing pushes an end
    later. The scheduled pause still stops the clock until it ends."""
    import uuid

    from app.core.db import superadmin_scope
    from tests.test_proctoring_pipeline import _Fx, _cleanup, _factory_or_skip

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _conversation(factory, fx)
        voice = uuid.uuid4()
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(0), ref_id=voice
                    )
                    scheduled = await pauses.close_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(60), ref_id=voice
                    )
                    assert scheduled is not None and scheduled.ended_at == _at(60)
                    # Still stopping the clock at 30, even though it has an end.
                    current = await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(30)
                    )
                    assert current is not None and current.id == scheduled.id
                    intervals = await pauses.pauses_for_turn(s, fx.conv_id, since=_at(0))
                    assert intervals == (Interval("transcription", _at(0), _at(60)),)
                    assert intervals[0].open_at(_at(30))
                    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(30)) == 30.0

                    # The acknowledgement at 40 brings it forward...
                    ack = await pauses.close_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(40), ref_id=voice
                    )
                    assert ack is not None and ack.ended_at == _at(40)
                    # ...and a later close cannot push it back out.
                    assert await pauses.close_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(90), ref_id=voice
                    ) is None
                    assert await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_TRANSCRIPTION, at=_at(45)
                    ) is None
                    assert await pauses.pauses_for_turn(s, fx.conv_id, since=_at(50)) == ()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_open_by_a_different_reference_supersedes_and_the_clock_loses_nothing() -> None:
    """A second warning while the first is still on screen: the first pause
    ends where the second begins, the second carries its own cap, and a retry
    of the second is still one pause."""
    import uuid

    from app.core.db import superadmin_scope
    from tests.test_proctoring_pipeline import _Fx, _cleanup, _factory_or_skip

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _conversation(factory, fx)
        first_ref, second_ref = uuid.uuid4(), uuid.uuid4()
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    first = await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(0),
                        ref_id=first_ref, max_seconds=30,
                    )
                    second = await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(10),
                        ref_id=second_ref, max_seconds=30,
                    )
                    assert second.id != first.id
                    await s.refresh(first)
                    assert first.ended_at == _at(10)
                    assert second.expires_at == _at(40)
                    retried = await pauses.open_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=_at(12),
                        ref_id=second_ref, max_seconds=30,
                    )
                    assert retried.id == second.id
                    intervals = await pauses.pauses_for_turn(
                        s, fx.conv_id, since=_at(0), until=_at(100)
                    )
                    assert pauses.paused_seconds(intervals, start=_at(0), end=_at(100)) == 40.0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
