"""Run a callback when, and only when, a session's transaction commits.

WHY THIS EXISTS
---------------
A request handler that writes a row and then dispatches a task about that row
has a race and a lie in it. The race: the task can start, and read, before the
request's transaction commits, so it finds nothing and fails or no-ops. The
lie: if the request then raises, the transaction rolls back and the task has
already been sent about a row that was never stored. Both have happened here
(PLAN-p3 3.1, the audit's "dispatch before commit" finding).

`on_commit` holds the callback on the session and runs it from SQLAlchemy's
own `after_commit` event, the one hook that means what it says. It is the
generalisation of `services/realtime.publish_after_commit`, whose docstring
records why FastAPI's `BackgroundTasks` cannot give the same guarantee: they
run inside the dependency exit stack, BEFORE `get_tenant_db` commits.

THE THREE RULES
---------------
- A rolled-back transaction runs NOTHING, and a later commit on the same
  session cannot fire a callback registered before the rollback. The list is
  cleared whenever the outermost transaction ends without committing: a
  rollback, whether or not a DBAPI rollback happened, and a `close()` of a
  session that never committed.
- A callback that raises is LOGGED, never raised out of `commit()`. The event
  runs inside `commit()`, so whatever it raises the caller raises, AFTER the
  write is durable: a request that stored its row would answer 500 and the
  client would retry a success. The 2026-09-16 section of CLAUDE.md records
  the same failure in the realtime hub. Callers therefore do every check that
  can fail for a programming reason (an unknown task name, an argument that is
  not JSON) BEFORE registering, inside the request, where raising is correct.
- Callbacks run in registration order, once each.

DOCUMENTED LIMIT
----------------
A callback registered inside a SAVEPOINT (`begin_nested`) that later rolls
back still fires on the outer commit: SQLAlchemy reports a savepoint rollback
as a nested transaction ending, not as the transaction the callback belongs
to. No call site registers inside a savepoint; register after it has been
released instead.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

logger = logging.getLogger(__name__)

_CALLBACKS_KEY = "after_commit_callbacks"
_INSTALLED_KEY = "after_commit_listeners_installed"


def _sync_session(session: Any) -> Session:
    """The ORM `Session` behind an `AsyncSession`, or the session itself."""
    sync = getattr(session, "sync_session", session)
    if not isinstance(sync, Session):
        raise TypeError(
            f"on_commit needs a SQLAlchemy session, got {type(session).__name__}"
        )
    return sync


def _pending(sync: Session) -> list[tuple[str, Callable[[], None]]]:
    return sync.info.setdefault(_CALLBACKS_KEY, [])


def _run_callbacks(sync: Session) -> None:
    """`after_commit`: drain the list, run each callback, never raise."""
    callbacks = list(sync.info.get(_CALLBACKS_KEY) or ())
    sync.info[_CALLBACKS_KEY] = []
    for label, callback in callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001 -- inside commit(); see module docstring
            logger.exception("after_commit.callback_failed label=%s", label)


def _discard_on_outer_end(sync: Session, transaction: SessionTransaction) -> None:
    """`after_transaction_end`: forget whatever is still pending when the
    OUTERMOST transaction ends.

    After a commit the list is already empty, because `after_commit` fires
    first and drains it. Anything left here belongs to a transaction that
    rolled back or was closed without committing, and must never run on a
    later commit of the same session. A savepoint ending is not the outermost
    transaction ending (see the module limit)."""
    if transaction.parent is not None:
        return
    dropped = sync.info.get(_CALLBACKS_KEY) or []
    if dropped:
        logger.info(
            "after_commit.discarded_uncommitted labels=%s",
            ",".join(label for label, _ in dropped),
        )
    sync.info[_CALLBACKS_KEY] = []


def _install(sync: Session) -> None:
    if sync.info.get(_INSTALLED_KEY):
        return
    event.listen(sync, "after_commit", _run_callbacks)
    event.listen(sync, "after_transaction_end", _discard_on_outer_end)
    sync.info[_INSTALLED_KEY] = True


def on_commit(session: Any, callback: Callable[[], None], *, label: str) -> None:
    """Run `callback()` after `session`'s current transaction commits.

    `label` names the callback in the log line written when it fails or is
    discarded, so an operator can see WHICH deferred work did not happen.
    """
    sync = _sync_session(session)
    _install(sync)
    _pending(sync).append((label, callback))


def pending_labels(session: Any) -> list[str]:
    """The labels waiting on the next commit. For tests and diagnostics."""
    sync = _sync_session(session)
    return [label for label, _ in sync.info.get(_CALLBACKS_KEY) or ()]
