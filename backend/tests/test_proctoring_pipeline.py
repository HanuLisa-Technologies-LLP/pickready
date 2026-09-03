"""The event pipeline end to end (spec section 11's integration list).

    "Full event pipeline: client emission, ingestion, storage, report
     generation. Session termination flows for every Path A trigger. Warning
     policy branching at the third warning. Report appears correctly in the
     Executive Profile."

These run against the real database and the real Redis, because the three
things they are about are only true there: the warning counter is an atomic
INCR, the rows carry CHECK constraints the ORM cannot see, and the gate the
assessment API calls reads a committed row. A fake for any of those would
test the fake.

The Executive Profile of the specification is the PRISM Report, so the last
group asserts the report reaches `FunctionalReportOut.proctoring` through the
same loader the route uses.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models.proctoring import (
    OUTCOME_ABANDONED,
    OUTCOME_ACTIVE,
    OUTCOME_COMPLETED,
    OUTCOME_TECHNICAL_FAILURE,
    OUTCOME_TERMINATED_INTEGRITY,
    OUTCOME_TERMINATED_WARNINGS,
    POLICY_CONTINUE_AND_NOTE,
    POLICY_TERMINATE,
)
from app.schemas.proctoring import EventBatchIn, EventIn
from app.services.proctoring import catalog, ingestion, state
from app.services.proctoring import report as proctoring_report
from app.services.proctoring.config import get_config

CONFIG = get_config()
MS = 1000


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
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.conv_id = uuid.uuid4()
        self.ps_id = uuid.uuid4()
        self.enqueued: list[str] = []

    def enqueue(self, link_id: str) -> None:
        """Stands in for `celery_app.send_task`, so the tests can assert the
        report was ordered without a broker."""
        self.enqueued.append(link_id)


async def _seed(
    factory,
    fx: _Fx,
    *,
    policy: str = POLICY_CONTINUE_AND_NOTE,
    warnings_used: int = 0,
    completed: bool = False,
    heartbeat_at: datetime | None = None,
) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant, User
    from app.models.assessment import AssessmentConversation
    from app.models.candidate import JobCandidateLink
    from app.models.enums import Role, UserStatus
    from app.models.proctoring import ProctoringSession

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name=f"Proc {fx.tenant_id.hex[:6]}",
                             domain=f"{fx.tenant_id}.proc.test"))
                s.add(User(id=fx.user_id, email=f"u{fx.user_id.hex[:8]}@proc.test",
                           role=Role.candidate, tenant_id=None,
                           full_name="Proctored Candidate", status=UserStatus.active))
                await s.flush()
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Platform Engineer",
                          jd_json={}, status=JobStatus.ratified, ratified_at=now,
                          assessment_status="ready_for_candidates",
                          assessment_grade="non_managerial",
                          proctoring_warning_policy=policy))
                s.add(Candidate(id=fx.cand_id, email=f"c{fx.cand_id.hex[:8]}@proc.test",
                                user_id=fx.user_id, full_name="Proctored Candidate",
                                consent_databank=False))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id, candidate_id=fx.cand_id,
                                       source=LinkSource.fresh, status="applied"))
                await s.flush()
                s.add(AssessmentConversation(
                    id=fx.conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    status="completed" if completed else "active",
                    next_question_index=0, started_at=now,
                    invitation_sent_at=now,
                    completed_at=now if completed else None,
                ))
                await s.flush()
                s.add(ProctoringSession(
                    id=fx.ps_id, tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                    job_candidate_link_id=fx.link_id, candidate_id=fx.cand_id,
                    job_id=fx.job_id, consented_at=now, started_at=now,
                    outcome=OUTCOME_ACTIVE, warnings_used=warnings_used,
                    face_descriptor_baseline=[0.1] * 128,
                    device_context={}, system_check={},
                    last_heartbeat_at=heartbeat_at or now,
                ))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    await state.clear_session(fx.ps_id)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})
                await s.execute(text("DELETE FROM users WHERE id = :u"),
                                {"u": str(fx.user_id)})


def _batch(*events: tuple[str, int | None]) -> EventBatchIn:
    now = datetime.now(timezone.utc)
    return EventBatchIn(
        events=[
            EventIn(event_type=kind, occurred_at=now, duration_ms=duration)
            for kind, duration in events
        ]
    )


async def _load(session, fx: _Fx):
    from app.models.proctoring import ProctoringSession

    return await session.get(ProctoringSession, fx.ps_id)


async def _events(session, fx: _Fx) -> list:
    from app.models.proctoring import ProctoringEvent

    return list(
        (
            await session.execute(
                select(ProctoringEvent)
                .where(ProctoringEvent.proctoring_session_id == fx.ps_id)
                .order_by(ProctoringEvent.occurred_at, ProctoringEvent.created_at)
            )
        ).scalars().all()
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. INGESTION AND STORAGE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_logged_event_is_stored_and_warns_nobody() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("LOW_LIGHT", 60_000)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    assert result.accepted == 1
                    assert result.warning is None
                    assert result.termination is None
                    assert result.warnings_used == 0
                    rows = await _events(s, fx)
                    assert [r.event_type for r in rows] == ["LOW_LIGHT"]
                    assert rows[0].path == catalog.PATH_C
                    assert rows[0].warning_issued is False
        assert fx.enqueued == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_first_warning_is_issued_and_mirrored_onto_the_row() -> None:
    """The counter is authoritative in Redis; the row is the mirror a report
    is generated from. They must agree after every batch."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("WINDOW_FOCUS_LOST", 30_000)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    assert result.warning is not None
                    assert result.warning.number == 1
                    assert result.warning.final is False
                    assert "first of three warnings" in result.warning.message
                    assert ps.warnings_used == 1
                    assert await state.warnings_used(fx.ps_id) == 1
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_one_warning_is_issued_per_batch() -> None:
    """A batch is a few seconds of one browser's life. Warning three times in
    one round trip would leave a candidate no chance to act on the first."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(
                            ("WINDOW_FOCUS_LOST", 30_000),
                            ("DEVICE_DETECTED_PHONE", 5_000),
                            ("SECOND_PERSON_DETECTED", 5_000),
                        ),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    assert result.warnings_used == 1
                    assert result.accepted == 3
                    rows = await _events(s, fx)
                    assert sum(1 for r in rows if r.warning_issued) == 1
                    deferred = [
                        r for r in rows
                        if r.metadata_json.get(ingestion.NOTE_KEY)
                        == ingestion.NOTE_BATCH_ALREADY_WARNED
                    ]
                    assert len(deferred) == 2
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_repeat_inside_its_cooldown_is_stored_without_warning_again() -> None:
    """Section 4.2: without the cooldown "a candidate whose phone is sitting
    on the desk would burn all three warnings in six seconds"."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    first = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("DEVICE_DETECTED_PHONE", 5_000)),
                        now=now, enqueue=fx.enqueue,
                    )
                    assert first.warning is not None
                    second = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("DEVICE_DETECTED_PHONE", 5_000)),
                        now=now + timedelta(seconds=1), enqueue=fx.enqueue,
                    )
                    assert second.warning is None
                    assert second.warnings_used == 1
                    rows = await _events(s, fx)
                    assert rows[-1].metadata_json[ingestion.NOTE_KEY] == (
                        ingestion.NOTE_WITHIN_COOLDOWN
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_once_per_session_event_warns_once_and_is_noted_after() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    first = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("MULTIPLE_DISPLAYS_DETECTED", None)),
                        now=now, enqueue=fx.enqueue,
                    )
                    assert first.warning is not None
                    later = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("MULTIPLE_DISPLAYS_DETECTED", None)),
                        now=now + timedelta(minutes=5), enqueue=fx.enqueue,
                    )
                    assert later.warning is None
                    rows = await _events(s, fx)
                    assert rows[-1].path == catalog.PATH_C
                    assert rows[-1].metadata_json[ingestion.NOTE_KEY] == (
                        ingestion.NOTE_ALREADY_REPORTED
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_counter_is_shared_across_different_path_b_types() -> None:
    """Section 4.0's deliberate design: a tab switch, a phone and a second
    person together use all three warnings."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    numbers = []
                    for index, kind in enumerate(
                        ("WINDOW_FOCUS_LOST", "DEVICE_DETECTED_PHONE", "SECOND_PERSON_DETECTED")
                    ):
                        result = await ingestion.ingest(
                            s, ps, POLICY_CONTINUE_AND_NOTE, _batch((kind, 30_000)),
                            now=now + timedelta(minutes=index * 5), enqueue=fx.enqueue,
                        )
                        if result.warning is not None:
                            numbers.append(result.warning.number)
                    assert numbers == [1, 2, 3]
                    assert ps.warnings_used == CONFIG.max_warnings
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_over_limit_batch_is_refused_rather_than_stored() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await state.count_in_minute(
                        fx.ps_id, now, catalog.CLIENT_RATE_LIMIT_PER_MINUTE
                    )
                    with pytest.raises(ingestion.RateLimited):
                        await ingestion.ingest(
                            s, ps, POLICY_CONTINUE_AND_NOTE,
                            _batch(("LOW_LIGHT", 1_000)),
                            now=now, enqueue=fx.enqueue,
                        )
                    assert await _events(s, fx) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_event_for_an_ended_session_is_refused() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    ps.outcome = OUTCOME_COMPLETED
                    await s.flush()
                    with pytest.raises(ingestion.SessionEnded):
                        await ingestion.ingest(
                            s, ps, POLICY_CONTINUE_AND_NOTE,
                            _batch(("LOW_LIGHT", 1_000)),
                            now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                        )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
# 2. TERMINATION, FOR EVERY PATH A TRIGGER
# ══════════════════════════════════════════════════════════════════════════


#: Every client-emittable Path A trigger, with a duration that clears its own
#: rule so the server does not downgrade it. `IDENTITY_MISMATCH` is server
#: derived and has its own test below.
PATH_A_TRIGGERS = [
    ("CAMERA_OBSTRUCTED", CONFIG.obstruction_seconds * MS),
    ("FACE_ABSENT_EXTENDED", CONFIG.face_absent_extended_seconds * MS),
    ("CAMERA_PERMISSION_LOST", None),
    ("MIC_PERMISSION_LOST", None),
    ("CAMERA_STREAM_FAILED", CONFIG.camera_recovery_seconds * MS),
    ("INTEGRITY_CHECK_FAILED", CONFIG.integrity_failure_termination_seconds * MS),
]


@pytest.mark.parametrize("trigger,duration", PATH_A_TRIGGERS)
@pytest.mark.asyncio
async def test_every_path_a_trigger_ends_the_session_and_the_conversation(
    trigger: str, duration: int | None
) -> None:
    """Section 4.1: end the session immediately, save the answers, tell the
    candidate plainly, record the reason. The conversation is marked
    terminated so `gate.require_active` refuses the next turn."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE, _batch((trigger, duration)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    assert result.termination is not None
                    assert result.termination.reason_code == trigger
                    assert "Your assessment has ended" in result.termination.message
                    assert ps.outcome in (
                        OUTCOME_TERMINATED_INTEGRITY, OUTCOME_TECHNICAL_FAILURE
                    )
                    assert ps.ended_at is not None
                    assert ps.termination_reason == trigger
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    assert conversation.status == ingestion.CONVERSATION_TERMINATED
                    assert conversation.completed_at is None
                    rows = await _events(s, fx)
                    assert rows[-1].event_type == trigger
                    assert rows[-1].path == catalog.PATH_A
        # The PRISM Report is written from the answers saved so far.
        assert fx.enqueued == [str(fx.link_id)]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_terminated_conversation_is_refused_by_the_gate() -> None:
    """The gate is what the assessment API calls, so this is the property
    that actually stops the next answer being accepted."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from fastapi import HTTPException

        from app.core.db import superadmin_scope
        from app.models.assessment import AssessmentConversation
        from app.services.proctoring import gate

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    conversation = await s.get(AssessmentConversation, fx.conv_id)
                    assert await gate.require_active(s, conversation) is not None
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("CAMERA_PERMISSION_LOST", None)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    with pytest.raises(HTTPException) as caught:
                        await gate.require_active(s, conversation)
                    assert caught.value.status_code == 409
                    assert "has ended" in caught.value.detail
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_identity_mismatch_does_not_end_an_assessment() -> None:
    """Section 3.3: "a single bad reading from lighting or angle must not end
    someone's assessment"."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("IDENTITY_CHECK_MISMATCH", None)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    assert result.termination is None
                    assert ps.outcome == OUTCOME_ACTIVE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_consecutive_identity_mismatches_terminate() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    for index in range(CONFIG.identity_consecutive_mismatches):
                        result = await ingestion.ingest(
                            s, ps, POLICY_CONTINUE_AND_NOTE,
                            _batch(("IDENTITY_CHECK_MISMATCH", None)),
                            now=now + timedelta(seconds=30 * index), enqueue=fx.enqueue,
                        )
                    assert result.termination is not None
                    assert result.termination.reason_code == "IDENTITY_MISMATCH"
                    assert ps.outcome == OUTCOME_TERMINATED_INTEGRITY
                    kinds = [r.event_type for r in await _events(s, fx)]
                    assert "IDENTITY_MISMATCH" in kinds
                    assert kinds.count("IDENTITY_CHECK_MISMATCH") == (
                        CONFIG.identity_consecutive_mismatches
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_matching_heartbeat_resets_the_mismatch_run() -> None:
    """Consecutive means consecutive. A mismatch, a good check, a mismatch is
    two isolated readings and not a confirmed different person."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("IDENTITY_CHECK_MISMATCH", None)),
                        now=now, enqueue=fx.enqueue,
                    )
                    await ingestion.heartbeat(
                        s, ps, identity_matched=True,
                        monitoring={"camera": True, "microphone": True,
                                    "models": True, "handlers": True},
                        now=now + timedelta(seconds=10),
                    )
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("IDENTITY_CHECK_MISMATCH", None)),
                        now=now + timedelta(seconds=40), enqueue=fx.enqueue,
                    )
                    assert result.termination is None
                    assert ps.outcome == OUTCOME_ACTIVE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_distance_inside_the_threshold_does_not_count_as_a_mismatch() -> None:
    """The browser reports the distance it measured and the server holds it to
    the shared threshold, so a browser running a different rule cannot end an
    assessment."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope
        from app.services.proctoring.identity import MISMATCH_DISTANCE_KEY

        now = datetime.now(timezone.utc)
        close = CONFIG.face_distance_threshold / 2
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    for index in range(CONFIG.identity_consecutive_mismatches + 1):
                        result = await ingestion.ingest(
                            s, ps, POLICY_CONTINUE_AND_NOTE,
                            EventBatchIn(events=[
                                EventIn(
                                    event_type="IDENTITY_CHECK_MISMATCH",
                                    occurred_at=now + timedelta(seconds=30 * index),
                                    metadata={MISMATCH_DISTANCE_KEY: close},
                                )
                            ]),
                            now=now + timedelta(seconds=30 * index), enqueue=fx.enqueue,
                        )
                    assert result.termination is None
                    assert ps.outcome == OUTCOME_ACTIVE
                    rows = await _events(s, fx)
                    assert rows[-1].metadata_json[ingestion.NOTE_KEY] == (
                        ingestion.NOTE_WITHIN_DISTANCE
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
# 3. THE THIRD WARNING, BOTH POLICY BRANCHES
# ══════════════════════════════════════════════════════════════════════════


async def _third_warning(fx: _Fx, factory, policy: str):
    """Drive a session to its third warning under `policy` and return the
    result of the batch that produced it, with the session row."""
    from app.core.db import superadmin_scope

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                ps = await _load(s, fx)
                result = None
                for index, kind in enumerate(
                    ("WINDOW_FOCUS_LOST", "DEVICE_DETECTED_PHONE", "SECOND_PERSON_DETECTED")
                ):
                    result = await ingestion.ingest(
                        s, ps, policy, _batch((kind, 30_000)),
                        now=now + timedelta(minutes=index * 5), enqueue=fx.enqueue,
                    )
                return result, ps.outcome, ps.warnings_used


@pytest.mark.asyncio
async def test_the_third_warning_stops_the_assessment_under_the_terminate_policy() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, policy=POLICY_TERMINATE)
        result, outcome, warnings = await _third_warning(fx, factory, POLICY_TERMINATE)
        assert result.termination is not None
        assert "warning limit for this role was reached" in result.termination.message
        assert outcome == OUTCOME_TERMINATED_WARNINGS
        assert warnings == CONFIG.max_warnings
        assert fx.enqueued == [str(fx.link_id)]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_third_warning_lets_them_finish_under_the_continue_policy() -> None:
    """Section 6's default. The candidate continues; the report says the limit
    was crossed."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, policy=POLICY_CONTINUE_AND_NOTE)
        result, outcome, warnings = await _third_warning(fx, factory, POLICY_CONTINUE_AND_NOTE)
        assert result.termination is None
        assert result.warning is not None
        assert result.warning.final is True
        assert "noted in your report" in result.warning.message
        assert outcome == OUTCOME_ACTIVE
        assert warnings == CONFIG.max_warnings
        assert fx.enqueued == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_after_the_limit_later_events_are_recorded_without_warning() -> None:
    """Under continue-and-note the session runs on, and the report has to be
    able to say the thing kept happening."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, policy=POLICY_CONTINUE_AND_NOTE)
        await _third_warning(fx, factory, POLICY_CONTINUE_AND_NOTE)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("FULLSCREEN_EXITED", 20_000)),
                        now=datetime.now(timezone.utc) + timedelta(minutes=30),
                        enqueue=fx.enqueue,
                    )
                    assert result.warning is None
                    assert result.termination is None
                    assert result.warnings_used == CONFIG.max_warnings
                    rows = await _events(s, fx)
                    assert rows[-1].event_type == "FULLSCREEN_EXITED"
                    assert rows[-1].warning_issued is False
                    assert rows[-1].metadata_json[ingestion.NOTE_KEY] == (
                        ingestion.NOTE_NO_WARNING_LEFT
                    )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_counter_survives_a_redis_restart_through_the_row_mirror() -> None:
    """The row is the mirror, and re-seeding from it is what stops a candidate
    getting three fresh warnings because a cache blinked."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, warnings_used=2)
        await state.clear_session(fx.ps_id)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    result = await ingestion.ingest(
                        s, ps, POLICY_TERMINATE,
                        _batch(("SECOND_PERSON_DETECTED", 30_000)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    assert result.warnings_used == CONFIG.max_warnings
                    assert result.termination is not None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
# 4. HEARTBEAT AND MONITORING GAPS
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_heartbeat_gap_is_recorded_as_a_monitoring_interruption() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    later = ps.last_heartbeat_at + timedelta(
                        seconds=CONFIG.heartbeat_gap_seconds + 30
                    )
                    out = await ingestion.heartbeat(
                        s, ps, identity_matched=None,
                        monitoring={"camera": True, "microphone": True,
                                    "models": True, "handlers": True},
                        now=later,
                    )
                    assert out.status == OUTCOME_ACTIVE
                    assert ps.last_heartbeat_at == later
                    rows = await _events(s, fx)
                    assert [r.event_type for r in rows] == ["MONITORING_INTERRUPTED"]
                    assert rows[0].duration_ms >= CONFIG.heartbeat_gap_seconds * MS
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_ordinary_heartbeat_records_nothing() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await ingestion.heartbeat(
                        s, ps, identity_matched=True,
                        monitoring={"camera": True, "microphone": True,
                                    "models": True, "handlers": True},
                        now=ps.last_heartbeat_at + timedelta(
                            seconds=CONFIG.heartbeat_interval_seconds
                        ),
                    )
                    assert await _events(s, fx) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_heartbeat_on_an_ended_session_returns_the_termination() -> None:
    """The browser that missed the terminating response is exactly the one
    still sending heartbeats, so this answers rather than refusing."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("CAMERA_PERMISSION_LOST", None)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    out = await ingestion.heartbeat(
                        s, ps, identity_matched=None,
                        monitoring={"camera": False, "microphone": True,
                                    "models": True, "handlers": True},
                        now=datetime.now(timezone.utc),
                    )
                    assert out.termination is not None
                    assert out.termination.reason_code == "CAMERA_PERMISSION_LOST"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
# 5. REPORT GENERATION AND ITS PLACE IN THE PRISM REPORT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_report_is_generated_once_and_only_after_the_session_ends() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    with pytest.raises(proctoring_report.ReportNotReady):
                        await proctoring_report.generate(s, ps)
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("CAMERA_OBSTRUCTED", CONFIG.obstruction_seconds * MS)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    first = await proctoring_report.generate(s, ps)
                    second = await proctoring_report.generate(s, ps)
                    assert first.id == second.id
                    assert first.report_content["outcome"]
                    assert "camera was covered" in first.report_content["outcome"].lower()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_generated_report_describes_what_actually_happened() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx, policy=POLICY_CONTINUE_AND_NOTE)
        from app.core.db import superadmin_scope

        now = datetime.now(timezone.utc)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("DEVICE_DETECTED_PHONE", 30_000)),
                        now=now, enqueue=fx.enqueue,
                    )
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("BLOCKED_ACTION_ATTEMPTED", None)),
                        now=now + timedelta(minutes=2), enqueue=fx.enqueue,
                    )
                    ps.outcome = OUTCOME_COMPLETED
                    ps.ended_at = now + timedelta(minutes=30)
                    await s.flush()
                    row = await proctoring_report.generate(s, ps)
                    content = row.report_content
                    assert content["candidate"] == "Proctored Candidate"
                    assert "Platform Engineer" in content["assessment"]
                    camera = " ".join(content["findings"]["camera"])
                    assert "phone was visible" in camera
                    screen = " ".join(content["findings"]["screen_browser"])
                    assert "attempted these once" in screen
                    assert "warned once" in content["outcome"]
                    assert len(content["activity_log"]) == 2
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_report_reaches_the_prism_payload_through_the_loader() -> None:
    """Section 11: "Report appears correctly in the Executive Profile." The
    Executive Profile is the PRISM Report, and `load_report_out` is what
    `api/assessments.get_report` attaches."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    assert await proctoring_report.load_report_out(s, fx.link_id) is None
                    await ingestion.ingest(
                        s, ps, POLICY_CONTINUE_AND_NOTE,
                        _batch(("MIC_PERMISSION_LOST", None)),
                        now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                    )
                    await proctoring_report.generate(s, ps)
                    out = await proctoring_report.load_report_out(s, fx.link_id)
                    assert out is not None
                    assert out.candidate == "Proctored Candidate"
                    assert out.closing
                    assert out.generated_at is not None
                    from app.services.siddhi import numbers

                    assert not numbers.scan(out, path="proctoring")
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_application_with_no_proctoring_session_has_no_report() -> None:
    """`FunctionalReportOut.proctoring` is None rather than an error: a report
    written before proctoring existed still has to open."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with superadmin_scope(s):
                assert await proctoring_report.load_report_out(s, uuid.uuid4()) is None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_abandoned_session_is_settled_and_reported() -> None:
    """The reconciler's rule, exercised through the same `end_session` the
    task calls: a candidate who closed the tab leaves an active session that
    nothing else will ever close."""
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        stale = datetime.now(timezone.utc) - timedelta(days=30)
        await _seed(factory, fx, heartbeat_at=stale)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    ps = await _load(s, fx)
                    await ingestion.end_session(
                        s, ps, outcome=OUTCOME_ABANDONED, reason_code=None,
                        now=datetime.now(timezone.utc),
                    )
                    assert ps.outcome == OUTCOME_ABANDONED
                    row = await proctoring_report.generate(s, ps)
                    assert "not completed" in row.report_content["outcome"]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
