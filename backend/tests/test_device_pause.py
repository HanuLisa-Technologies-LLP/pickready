"""Camera or microphone loss: a pause, a grace, a limit (master prompt, Phase 3).

    "Camera or microphone loss triggers a pause, an explicit warning and a
     2-minute grace. At most 2 pauses per session; a 3rd failure, or no
     recovery within 2 minutes, ends the session."

Against the real database and the real Redis, like the rest of the pipeline
tests, because every property here is a property of committed rows: the pause
IS an `assessment_pauses` row, the count IS the number of those rows, and an
ended session must still be ended when a SECOND connection reads it after the
request's transaction commits (a termination raised rather than returned would
roll itself back, and only a second connection can see that).

Every time here is the SERVER's `now`, passed in; nothing sleeps.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.assessment_pause import AssessmentPause
from app.models.proctoring import (
    OUTCOME_ACTIVE,
    OUTCOME_TECHNICAL_FAILURE,
    POLICY_CONTINUE_AND_NOTE,
    ProctoringEvent,
    ProctoringSession,
)
from app.schemas.proctoring import EventBatchIn, EventIn
from app.services.assessment_conversation import pauses
from app.services.proctoring import catalog, gate, ingestion
from app.services.proctoring.config import get_config
from app.workers import dispatch as dispatch_mod

from tests.test_proctoring_pipeline import (  # one harness, not two
    _Fx,
    _cleanup,
    _events,
    _factory_or_skip,
    _load,
    _seed,
)

CONFIG = get_config()
MS = 1000
GRACE = timedelta(seconds=CONFIG.device_grace_seconds)
T0 = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def _loss(kind: str = "CAMERA_STREAM_FAILED", duration_ms: int | None = None, at=T0) -> EventBatchIn:
    return EventBatchIn(events=[EventIn(event_type=kind, occurred_at=at, duration_ms=duration_ms)])


def _recovered(at=T0) -> EventBatchIn:
    return EventBatchIn(events=[EventIn(event_type="DEVICE_RECOVERED", occurred_at=at)])


async def _device_pauses(session, fx: _Fx) -> list[AssessmentPause]:
    return list(
        (
            await session.execute(
                select(AssessmentPause)
                .where(
                    AssessmentPause.conversation_id == fx.conv_id,
                    AssessmentPause.reason == pauses.PAUSE_DEVICE_LOSS,
                )
                .order_by(AssessmentPause.started_at)
            )
        ).scalars().all()
    )


async def _committed_session(factory, fx: _Fx) -> ProctoringSession:
    """The session row as a SECOND connection reads it after the commit."""
    from app.core.db import superadmin_scope

    async with factory() as other:
        async with other.begin():
            async with superadmin_scope(other):
                return await other.get(ProctoringSession, fx.ps_id)


async def _ingest(factory, fx: _Fx, batch: EventBatchIn, now: datetime):
    """One request: its own session and transaction, committed at the end."""
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                ps = await _load(s, fx)
                return await ingestion.ingest(
                    s, ps, POLICY_CONTINUE_AND_NOTE, batch, now=now, enqueue=fx.enqueue
                )


@pytest.mark.asyncio
async def test_a_loss_pauses_the_assessment_and_answering_is_refused() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        out = await _ingest(factory, fx, _loss(), T0)
        assert out.termination is None
        assert out.warning is None, "a pause is not a warning and uses none"
        assert out.warnings_used == 0
        assert out.pause is not None and out.pause.paused is True
        assert out.pause.pauses_used == 1
        assert out.pause.max_pauses == CONFIG.device_max_pauses
        assert out.pause.grace_deadline_at == T0 + GRACE
        assert "camera" in out.pause.message and "paused" in out.pause.message

        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    rows = await _device_pauses(s, fx)
                    assert len(rows) == 1 and rows[0].ended_at is None
                    assert rows[0].started_at == T0
                    assert rows[0].expires_at == T0 + GRACE
                    events = await _events(s, fx)
                    assert [(e.event_type, e.path) for e in events] == [
                        ("CAMERA_STREAM_FAILED", catalog.PATH_P)
                    ]
                    assert rows[0].ref_id == events[0].id
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    # The start route still opens the page (it shows the pause)...
                    assert await gate.require_active(s, conversation) is not None
                    # ...but no answer is taken while paused.
                    with pytest.raises(HTTPException) as caught:
                        await gate.require_answerable(s, conversation)
                    assert caught.value.status_code == 409
                    assert caught.value.detail == gate.PAUSED_DETAIL
        assert (await _committed_session(factory, fx)).outcome == OUTCOME_ACTIVE
        assert fx.enqueued == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_recovery_inside_the_grace_resumes_and_the_clock_excludes_the_pause() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss(), T0)
        back = T0 + timedelta(seconds=40)
        out = await _ingest(factory, fx, _recovered(back), back)
        assert out.termination is None
        assert out.pause.paused is False and out.pause.pauses_used == 1

        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    rows = await _device_pauses(s, fx)
                    assert rows[0].ended_at == back
                    recovered = [e for e in await _events(s, fx) if e.event_type == "DEVICE_RECOVERED"]
                    assert recovered[0].metadata_json[ingestion.NOTE_KEY] == ingestion.NOTE_RESUMED
                    assert recovered[0].duration_ms == 40 * MS
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    assert await gate.require_answerable(s, conversation) is not None
                    # The turn timer's arithmetic: a turn shown a minute before
                    # the loss and read two minutes after it lost exactly the
                    # forty paused seconds.
                    shown = T0 - timedelta(minutes=1)
                    until = T0 + timedelta(minutes=2)
                    intervals = await pauses.pauses_for_turn(
                        s, fx.conv_id, since=shown, until=until
                    )
                    assert pauses.paused_seconds(intervals, start=shown, end=until) == 40.0
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_recovery_exactly_at_the_end_of_the_grace_still_resumes() -> None:
    """The boundary is inclusive: a tie goes to the candidate."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss(), T0)
        out = await _ingest(factory, fx, _recovered(T0 + GRACE), T0 + GRACE)
        assert out.termination is None
        assert (await _committed_session(factory, fx)).outcome == OUTCOME_ACTIVE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_recovery_after_the_grace_ends_the_session_as_a_technical_failure() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss(), T0)
        late = T0 + GRACE + timedelta(seconds=1)
        out = await _ingest(factory, fx, _recovered(late), late)
        assert out.termination is not None
        assert out.termination.reason_code == "DEVICE_RECOVERY_TIMED_OUT"
        assert "camera or microphone" in out.termination.message
        assert out.pause is None

        committed = await _committed_session(factory, fx)
        assert committed.outcome == OUTCOME_TECHNICAL_FAILURE
        assert committed.termination_reason == "DEVICE_RECOVERY_TIMED_OUT"
        assert committed.ended_at == late
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ending = [e for e in await _events(s, fx) if e.path == catalog.PATH_A]
                    assert [e.event_type for e in ending] == ["DEVICE_RECOVERY_TIMED_OUT"]
                    # Dated at the instant the grace ran out, not when the
                    # server happened to hear about it.
                    assert ending[0].occurred_at == T0 + GRACE
                    rows = await _device_pauses(s, fx)
                    assert rows[0].ended_at is not None, "an ended session leaves no open pause"
        assert fx.enqueued == [str(fx.link_id)]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_heartbeat_after_the_grace_ends_the_session_and_it_stays_ended() -> None:
    """A candidate who never fixes the camera sends no recovery. The next
    heartbeat settles it, and the ending is COMMITTED: a second connection
    reads it, which would fail if the heartbeat raised instead of returning."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss(), T0)
        from app.core.db import superadmin_scope

        late = T0 + GRACE + timedelta(seconds=5)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    out = await ingestion.heartbeat(
                        s, ps, identity_matched=None, monitoring={}, now=late,
                        enqueue=fx.enqueue,
                    )
        assert out.termination is not None
        assert out.termination.reason_code == "DEVICE_RECOVERY_TIMED_OUT"
        committed = await _committed_session(factory, fx)
        assert committed.outcome == OUTCOME_TECHNICAL_FAILURE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_third_loss_ends_the_session_and_the_second_does_not() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        moment = T0
        for number in range(CONFIG.device_max_pauses):
            out = await _ingest(factory, fx, _loss("MIC_PERMISSION_LOST", at=moment), moment)
            assert out.termination is None, f"pause {number + 1} ended the session"
            assert out.pause.pauses_used == number + 1
            moment += timedelta(seconds=30)
            out = await _ingest(factory, fx, _recovered(moment), moment)
            assert out.termination is None
            moment += timedelta(minutes=5)
        out = await _ingest(factory, fx, _loss("MIC_PERMISSION_LOST", at=moment), moment)
        assert out.termination is not None
        assert out.termination.reason_code == "DEVICE_PAUSE_LIMIT_EXCEEDED"
        committed = await _committed_session(factory, fx)
        assert committed.outcome == OUTCOME_TECHNICAL_FAILURE
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    assert len(await _device_pauses(s, fx)) == CONFIG.device_max_pauses
                    losses = [e for e in await _events(s, fx) if e.path == catalog.PATH_P]
                    assert losses[-1].metadata_json[ingestion.NOTE_KEY] == ingestion.NOTE_PAUSE_LIMIT
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_second_device_failing_during_a_pause_is_the_same_pause() -> None:
    """The microphone dropping while the camera is being fixed is one
    interruption: it must not spend the candidate's second pause."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss("CAMERA_PERMISSION_LOST"), T0)
        later = T0 + timedelta(seconds=20)
        out = await _ingest(factory, fx, _loss("MIC_STREAM_FAILED", at=later), later)
        assert out.pause.paused is True and out.pause.pauses_used == 1
        # The deadline did not move: the grace runs from the first loss.
        assert out.pause.grace_deadline_at == T0 + GRACE
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    assert len(await _device_pauses(s, fx)) == 1
                    notes = [
                        e.metadata_json[ingestion.NOTE_KEY]
                        for e in await _events(s, fx) if e.path == catalog.PATH_P
                    ]
                    assert notes == [ingestion.NOTE_PAUSE_OPENED, ingestion.NOTE_ALREADY_PAUSED]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_flicker_under_the_glitch_window_pauses_nothing() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        flicker = CONFIG.device_glitch_seconds * MS - 1
        out = await _ingest(factory, fx, _loss(duration_ms=flicker), T0)
        assert out.pause.paused is False and out.pause.pauses_used == 0
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    assert await _device_pauses(s, fx) == []
                    events = await _events(s, fx)
                    assert [(e.event_type, e.path) for e in events] == [
                        ("CAMERA_STREAM_INTERRUPTED", catalog.PATH_C)
                    ]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_loss_reported_after_it_ended_spends_a_pause_and_credits_the_clock() -> None:
    """A batch held while the browser was offline arrives with the loss's
    measured duration. It still counts as a pause, and the clock is credited
    for the time the device was really gone, dated back from the server's
    own receipt, not from the browser's clock."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        heard = T0 + timedelta(minutes=3)
        out = await _ingest(factory, fx, _loss(duration_ms=30 * MS, at=T0), heard)
        assert out.termination is None
        assert out.pause.paused is False and out.pause.pauses_used == 1
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    rows = await _device_pauses(s, fx)
                    assert rows[0].started_at == heard - timedelta(seconds=30)
                    assert rows[0].ended_at == heard
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_loss_reported_afterwards_but_longer_than_the_grace_ends_the_session() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        too_long = int(GRACE.total_seconds() * MS) + MS
        heard = T0 + timedelta(minutes=5)
        out = await _ingest(factory, fx, _loss(duration_ms=too_long), heard)
        assert out.termination is not None
        assert out.termination.reason_code == "DEVICE_RECOVERY_TIMED_OUT"
        assert (await _committed_session(factory, fx)).outcome == OUTCOME_TECHNICAL_FAILURE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_sweep_ends_an_abandoned_pause() -> None:
    """The hourly reconciler's call for a browser that closed while paused:
    `enforce_pause` on a fresh session, exactly as the task makes it."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss(), T0)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    before = await ingestion.enforce_pause(
                        s, ps, now=T0 + GRACE, enqueue=fx.enqueue
                    )
                    assert before is None, "the grace is inclusive"
                    ended = await ingestion.enforce_pause(
                        s, ps, now=T0 + timedelta(hours=1), enqueue=fx.enqueue
                    )
                    assert ended is not None
                    assert ended.reason_code == "DEVICE_RECOVERY_TIMED_OUT"
                    # A second sweep over an ended session does nothing.
                    assert await ingestion.enforce_pause(
                        s, ps, now=T0 + timedelta(hours=2), enqueue=fx.enqueue
                    ) is None
        assert (await _committed_session(factory, fx)).outcome == OUTCOME_TECHNICAL_FAILURE
        assert fx.enqueued == [str(fx.link_id)]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_ending_orders_the_report_only_after_the_commit() -> None:
    """With no injected enqueue, the scoring task is handed to the dispatcher
    from `after_commit`: nothing is recorded while the transaction is open,
    and a rolled-back ending dispatches nothing at all."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _ingest(factory, fx, _loss(), T0)
        from app.core.db import superadmin_scope

        late = T0 + GRACE + timedelta(minutes=1)
        dispatch_mod.clear_recorded()
        async with factory() as s:
            await s.begin()
            async with superadmin_scope(s):
                ps = await _load(s, fx)
                assert await ingestion.enforce_pause(s, ps, now=late) is not None
                assert dispatch_mod.recorded_names() == []
            await s.rollback()
        assert dispatch_mod.recorded_names() == [], "a rolled-back ending dispatched"
        assert (await _committed_session(factory, fx)).outcome == OUTCOME_ACTIVE

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    assert await ingestion.enforce_pause(s, ps, now=late) is not None
                    assert dispatch_mod.recorded_names() == []
        assert dispatch_mod.recorded_names() == ["pickready.run_functional_assessment"]
        assert dispatch_mod.recorded()[0].args == (str(fx.link_id),)
    finally:
        dispatch_mod.clear_recorded()
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_warning_pauses_the_clock_until_acknowledged_and_never_longer_than_the_cap() -> None:
    """PLAN-p3 3.7: the time a candidate spends reading a warning is not taken
    from their answer, and an acknowledgement that never comes cannot stop the
    clock for ever."""
    from app.core.config import get_settings

    cap = timedelta(seconds=get_settings().assessment_warning_pause_max_seconds)
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        batch = EventBatchIn(events=[
            EventIn(event_type="WINDOW_FOCUS_LOST", occurred_at=T0, duration_ms=30 * MS)
        ])
        out = await _ingest(factory, fx, batch, T0)
        assert out.warning is not None
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    open_row = await pauses.current_pause(
                        s, fx.conv_id, pauses.PAUSE_WARNING, at=T0 + timedelta(seconds=1)
                    )
                    assert open_row is not None
                    assert open_row.expires_at == T0 + cap
                    warned = (
                        await s.execute(
                            select(ProctoringEvent).where(
                                ProctoringEvent.proctoring_session_id == fx.ps_id,
                                ProctoringEvent.warning_issued.is_(True),
                            )
                        )
                    ).scalars().one()
                    assert open_row.ref_id == warned.id
                    ack = T0 + timedelta(seconds=8)
                    assert await ingestion.acknowledge_warning(s, ps, now=ack) is True
                    assert await ingestion.acknowledge_warning(s, ps, now=ack) is False
                    intervals = await pauses.pauses_for_turn(
                        s, fx.conv_id, since=T0, until=T0 + timedelta(minutes=1)
                    )
                    assert pauses.paused_seconds(
                        intervals, start=T0, end=T0 + timedelta(minutes=1)
                    ) == 8.0

        # A second warning nobody acknowledges counts for the cap and no more.
        later = T0 + timedelta(minutes=10)
        batch = EventBatchIn(events=[
            EventIn(event_type="FULLSCREEN_EXITED", occurred_at=later, duration_ms=30 * MS)
        ])
        out = await _ingest(factory, fx, batch, later)
        assert out.warning is not None and out.warning.number == 2
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    end = later + timedelta(minutes=5)
                    intervals = await pauses.pauses_for_turn(s, fx.conv_id, since=later, until=end)
                    assert pauses.paused_seconds(intervals, start=later, end=end) == cap.total_seconds()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_candidate_is_resolved_through_the_one_resolver_and_nobody_else_gets_in() -> None:
    """`api/proctoring` resolves the signed-in person by
    `candidate_identity.resolve_candidate_id` (Phase 6). A different signed-in
    user is refused with a 404 that confirms nothing."""
    import uuid

    from app.api import proctoring as api
    from app.api.deps import CurrentUser
    from app.core.db import superadmin_scope
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        owner = CurrentUser(
            user_id=fx.user_id, tenant_id=None, role=Role.candidate, audience=AUDIENCE_CANDIDATE
        )
        stranger = CurrentUser(
            user_id=uuid.uuid4(), tenant_id=None, role=Role.candidate, audience=AUDIENCE_CANDIDATE
        )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ack = await api.acknowledge_warning(fx.ps_id, user=owner, session=s)
                    assert ack.resumed is False
                    with pytest.raises(HTTPException) as caught:
                        await api.acknowledge_warning(fx.ps_id, user=stranger, session=s)
                    assert caught.value.status_code == 404
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
