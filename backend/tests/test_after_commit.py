"""`dispatch_after_commit` hands work off only once the transaction is durable.

The defect class this pins (PLAN-p3 3.1, the audit's "dispatch before commit"
finding): a handler wrote a row, dispatched a task about it, and committed
later in `get_tenant_db`. The task could start before the row was visible, and
a request that raised after the dispatch rolled the row back while the task
was already on its way.

What is asserted here, against a real database:

- Nothing is dispatched before `commit()`, and when the dispatch happens a
  SECOND connection can already read the row the request wrote. Replacing
  `dispatch_after_commit` with a bare `dispatch` fails the first test, because
  the recorder then holds the run before the commit.
- A rollback dispatches nothing, and a LATER commit on the same session does
  not resurrect the discarded dispatch. The same for a session closed without
  committing.
- A failing invoke after the commit is logged and never raised out of
  `commit()`: the write is durable by then, and raising would 500 a request
  that succeeded.
- Everything that can fail for a programming reason fails INSIDE the request:
  an unknown task name and a non-JSON argument raise at the call and register
  nothing.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import after_commit
from app.core.config import get_settings
from app.models.tenant import AuditLog
from app.services.audit import AUTH_OTP_FAILED, audit
from app.workers import dispatch as dispatch_mod
from app.workers.registry import UnknownTask

TASK = "pickready.parse_resume"


async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable, skipping the after-commit tests")
    return engine


async def _write_marker(session, marker: str) -> None:
    await audit(
        session,
        tenant_id=None,
        actor_user_id=None,
        action=AUTH_OTP_FAILED,
        target_type="after_commit_test",
        target_id=None,
        metadata={"marker": marker},
    )


async def _marker_visible(marker: str) -> bool:
    """Read from a brand new engine: a second connection, outside the
    transaction under test."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine)() as other:
            rows = (
                await other.execute(
                    select(AuditLog.metadata_json).where(
                        AuditLog.action == AUTH_OTP_FAILED,
                        AuditLog.target_type == "after_commit_test",
                    )
                )
            ).scalars().all()
            return any((row or {}).get("marker") == marker for row in rows)
    finally:
        await engine.dispose()


def _visible_from_another_thread(marker: str) -> bool:
    """The commit hook runs synchronously inside `commit()`, on the running
    loop, so the second connection is opened on a fresh loop in a thread."""
    seen: list[bool] = []

    def _run() -> None:
        seen.append(asyncio.run(_marker_visible(marker)))

    worker = threading.Thread(target=_run)
    worker.start()
    worker.join(timeout=30)
    assert seen, "the second-connection probe did not finish"
    return seen[0]


async def test_dispatch_waits_for_the_commit_and_the_row_is_visible_when_it_runs(
    monkeypatch,
) -> None:
    engine = await _db_or_skip()
    marker = f"after-commit-{uuid.uuid4()}"
    visible_at_dispatch: list[bool] = []
    real_send = dispatch_mod._send

    def _probing_send(spec, run_id, args, kwargs):
        visible_at_dispatch.append(_visible_from_another_thread(marker))
        return real_send(spec, run_id, args, kwargs)

    monkeypatch.setattr(dispatch_mod, "_send", _probing_send)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await _write_marker(session, marker)
            handle = dispatch_mod.dispatch_after_commit(
                session, TASK, args=["profile-1"]
            )
            assert handle.name == TASK
            assert handle.id
            assert dispatch_mod.recorded() == []
            assert after_commit.pending_labels(session) == [
                f"dispatch task={TASK} run_id={handle.id}"
            ]

            await session.commit()

            runs = dispatch_mod.recorded()
            assert [(r.id, r.name, r.args) for r in runs] == [
                (handle.id, TASK, ("profile-1",))
            ]
            assert visible_at_dispatch == [True]
            assert after_commit.pending_labels(session) == []

            # A second commit on the same session does not dispatch again.
            await _write_marker(session, f"{marker}-second")
            await session.commit()
            assert len(dispatch_mod.recorded()) == 1
    finally:
        await engine.dispose()


async def test_a_rollback_dispatches_nothing_now_or_on_a_later_commit() -> None:
    engine = await _db_or_skip()
    marker = f"after-commit-rollback-{uuid.uuid4()}"
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await _write_marker(session, marker)
            dispatch_mod.dispatch_after_commit(session, TASK, args=["profile-2"])
            await session.rollback()
            assert dispatch_mod.recorded() == []
            assert after_commit.pending_labels(session) == []

            await _write_marker(session, f"{marker}-later")
            await session.commit()
            assert dispatch_mod.recorded() == []
        assert not await _marker_visible(marker)
    finally:
        await engine.dispose()


async def test_a_session_closed_without_committing_dispatches_nothing() -> None:
    engine = await _db_or_skip()
    marker = f"after-commit-close-{uuid.uuid4()}"
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        session = factory()
        await _write_marker(session, marker)
        dispatch_mod.dispatch_after_commit(session, TASK, args=["profile-3"])
        await session.close()
        assert dispatch_mod.recorded() == []
        assert after_commit.pending_labels(session) == []
    finally:
        await engine.dispose()


async def test_a_failed_invoke_after_commit_is_logged_and_never_raised(
    monkeypatch, caplog
) -> None:
    engine = await _db_or_skip()
    marker = f"after-commit-fail-{uuid.uuid4()}"

    def _refuse(spec, run_id, args, kwargs):
        raise dispatch_mod.DispatchError(f"could not dispatch {spec.name}")

    monkeypatch.setattr(dispatch_mod, "_send", _refuse)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await _write_marker(session, marker)
            handle = dispatch_mod.dispatch_after_commit(
                session, TASK, args=["profile-4"]
            )
            with caplog.at_level(logging.ERROR, logger="app.core.after_commit"):
                await session.commit()
        failures = [
            record
            for record in caplog.records
            if record.getMessage().startswith("after_commit.callback_failed")
        ]
        assert len(failures) == 1
        assert handle.id in failures[0].getMessage()
        assert TASK in failures[0].getMessage()
        assert failures[0].exc_info is not None
        # The write survived: the failure was after the durable commit.
        assert await _marker_visible(marker)
    finally:
        await engine.dispose()


async def test_programming_errors_raise_inside_the_request_and_register_nothing() -> None:
    engine = await _db_or_skip()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            with pytest.raises(UnknownTask):
                dispatch_mod.dispatch_after_commit(
                    session, "pickready.no_such_task", args=[]
                )
            with pytest.raises(TypeError):
                dispatch_mod.dispatch_after_commit(session, TASK, args=[object()])
            assert after_commit.pending_labels(session) == []
            await session.commit()
            assert dispatch_mod.recorded() == []
    finally:
        await engine.dispose()


def test_on_commit_refuses_something_that_is_not_a_session() -> None:
    with pytest.raises(TypeError):
        after_commit.on_commit(object(), lambda: None, label="not-a-session")
