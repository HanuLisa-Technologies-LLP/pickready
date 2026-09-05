"""What a dispatched task run is doing, for the two screens that poll it.

WHY THIS EXISTS
---------------
Celery's result backend answered two live questions in this product: the
matching run's stage list on the job page, and the per-recipient delivery state
in the outreach modal. Both polled `AsyncResult`, which read the backend Redis
that the broker also used. Dropping Celery drops that backend, so the same two
questions need the same answer from somewhere.

WHY REDIS AND NOT A TABLE
-------------------------
This is transient run telemetry with a natural expiry, not a record anybody
audits. A table would need a migration, an RLS policy and a purge sweep to hold
data whose entire useful life is the few minutes a recruiter has the page open.
Redis is already a hard dependency for the cache, the rate limiter and the
proctoring warning counter, and Celery's backend was Redis too, so this is the
same durability promise the product already made rather than a weaker one.

WHY AN UNKNOWN ID READS AS PENDING
----------------------------------
An id that is not in Redis is indistinguishable from one that was dispatched a
moment ago and has not been picked up. Celery behaved exactly this way, and the
alternative is worse: reporting "unknown" would make the job page stop polling
a run that is about to start. The honest consequence is documented rather than
hidden -- a run whose status expired reads as pending forever, which is why the
TTL is generous relative to the longest task.

The permanent record of what happened is elsewhere and unaffected: `email_log`
for delivery, `audit_logs` for the trigger, and the rows the task itself wrote.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: Terminal and non-terminal states. The names match Celery's so the response
#: schemas, the frontend union and the existing tests keep their vocabulary.
STATE_PENDING = "PENDING"
STATE_PROGRESS = "PROGRESS"
STATE_SUCCESS = "SUCCESS"
STATE_FAILURE = "FAILURE"

TERMINAL_STATES = frozenset({STATE_SUCCESS, STATE_FAILURE})

#: Six hours: longer than any run this product performs, plus the time a
#: recruiter might leave the page open afterwards. Generous on purpose, because
#: an expired record is indistinguishable from one that was never written and
#: therefore reads as PENDING for ever (see the docstring).
TTL_SECONDS = 6 * 3600

_PREFIX = "pickready:taskrun:v1"

_client: Any = None
_unavailable = False


def _key(run_id: str) -> str:
    return f"{_PREFIX}:{run_id}"


def _redis():
    """Lazily-built async client, or None when Redis is unreachable.

    `_unavailable` latches, for the same reason `core.cache` latches: a Redis
    outage should cost one failed connection, not one per publish, and a
    matching run publishes a payload on every stage transition.
    """
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        try:
            import redis.asyncio as redis_asyncio

            _client = redis_asyncio.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        except Exception:  # noqa: BLE001 -- see the docstring
            logger.warning("taskrun.status_unavailable", exc_info=True)
            _unavailable = True
            return None
    return _client


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    state: str
    #: The task's own payload: progress stages while running, the return value
    #: on success. Never an exception object -- see `record_failure`.
    payload: dict[str, Any]
    #: Present only on FAILURE. The exception CLASS NAME, never its message:
    #: a message can quote a row, and this is read by a recruiter's browser.
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.state in TERMINAL_STATES


def unknown(run_id: str) -> RunStatus:
    return RunStatus(run_id=run_id, state=STATE_PENDING, payload={})


async def read(run_id: str) -> RunStatus:
    client = _redis()
    if client is None:
        return unknown(run_id)
    try:
        raw = await client.get(_key(run_id))
    except Exception:  # noqa: BLE001
        logger.debug("taskrun.read_failed run_id=%s", run_id, exc_info=True)
        return unknown(run_id)
    if not raw:
        return unknown(run_id)
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("taskrun.read_corrupt run_id=%s", run_id)
        return unknown(run_id)
    return RunStatus(
        run_id=run_id,
        state=str(data.get("state") or STATE_PENDING),
        payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
        error=data.get("error"),
    )


async def write(
    run_id: str,
    state: str,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record a run's state. Never raises into the work it describes.

    A status display that can fail the task it is reporting on is a strictly
    worse trade than a display that goes blank, which is the same rule
    `matching_progress.Progress` already states for its own publish callback.
    """
    client = _redis()
    if client is None:
        return
    body = json.dumps(
        {"state": state, "payload": payload or {}, "error": error},
        default=str,
    )
    try:
        await client.set(_key(run_id), body, ex=TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("taskrun.write_failed run_id=%s", run_id, exc_info=True)


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None
