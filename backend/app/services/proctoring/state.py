"""The shared proctoring state, in Redis (proctoring-spec-doc.md section 9).

WHY THIS IS IN REDIS AND NOT ON THE ROW
---------------------------------------
"Warning counts are authoritative on the server (Redis), never trusted from the
client. The client requests a warning; the server decides." Two API tasks can
receive two batches from the same browser in the same second, and a counter
read from a row and written back is two counters. `INCR` is one. The row's
`warnings_used` is a MIRROR written after the increment so a report can be
generated from the database alone; it is never the thing that decides.

WHY IT FAILS LOUD
-----------------
`core/cache` and `services/rate_limit` fail OPEN when Redis is unreachable,
and they argue for it: a cache miss is cheaper than an outage. That argument
does not hold here. A warning counter that quietly answers "zero" during a
Redis outage lets every candidate in the outage window take the assessment
unwarned and unterminated, and the report generated afterwards reads as
clean. `StateUnavailable` is raised instead and the API answers 503, so the
browser retries the batch and nothing is silently forgiven.

WHY THE CLIENT IS PER EVENT LOOP
--------------------------------
An `asyncio` Redis connection belongs to the loop that opened it. The API
process runs one loop for its lifetime; a Celery task runs `asyncio.run` per
task and so opens a new loop every time, and pytest-asyncio does the same per
test. A module-level client shared across those loops fails on the second one
with a closed-loop error that reads as a Redis outage. The client is therefore
rebuilt whenever the running loop changes, which costs one connection per
loop and never a wrong answer.

KEYS carry the `pickready:proctoring:` prefix, which is deliberately not
`core/cache`'s versioned namespace: this is not a cache and must never be
invalidated by a cache version bump.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "StateUnavailable",
    "KEY_PREFIX",
    "warnings_used",
    "seed_warnings",
    "increment_warning",
    "in_cooldown",
    "start_cooldown",
    "claim_once",
    "bump_consecutive",
    "reset_consecutive",
    "count_in_minute",
    "clear_session",
]

KEY_PREFIX = "pickready:proctoring"

#: Every key expires. A session's shared state has no meaning once the session
#: is over, and a key with no expiry would accumulate one entry per assessment
#: forever. One day is the ceiling `schemas/proctoring.EventIn` already places
#: on any single event's duration, and no assessment runs that long.
SESSION_KEY_TTL_SECONDS = 24 * 60 * 60
#: The fixed rate-limit window, in seconds. The limit itself is
#: `catalog.CLIENT_RATE_LIMIT_PER_MINUTE`; this is only the unit it is stated in.
_ONE_MINUTE_SECONDS = 60
#: Socket bounds. A Redis that accepts and never answers must fail the batch,
#: not hold the request open for the load balancer to kill.
_SOCKET_TIMEOUT_SECONDS = 2


class StateUnavailable(RuntimeError):
    """Redis could not answer. The API turns this into a 503 so the client
    retries, because a proctoring decision that was not made must not read as a
    decision that nothing happened."""


_client: Any = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _redis():
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        import redis.asyncio as redis_asyncio

        _client = redis_asyncio.from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )
        _client_loop = loop
    return _client


async def _call(operation: str, coroutine_factory):
    """Run one Redis operation, converting any transport failure into
    `StateUnavailable`. Named so the log line says which decision was lost."""
    from redis.exceptions import RedisError

    try:
        return await coroutine_factory()
    except (OSError, RedisError, asyncio.TimeoutError) as exc:
        logger.error("proctoring.state.unavailable op=%s err=%s", operation, type(exc).__name__)
        raise StateUnavailable(f"proctoring state unavailable during {operation}") from exc


def _key(kind: str, session_id: uuid.UUID, *rest: str) -> str:
    return ":".join([KEY_PREFIX, kind, str(session_id), *rest])


# ── The shared warning counter ───────────────────────────────────────────────


async def warnings_used(session_id: uuid.UUID) -> int:
    client = _redis()
    raw = await _call("warnings.get", lambda: client.get(_key("warnings", session_id)))
    return int(raw) if raw is not None else 0


async def seed_warnings(session_id: uuid.UUID, mirrored: int) -> int:
    """Re-seed the counter from the row's mirror if Redis has forgotten it.

    `SET NX`, so a live counter is never overwritten by the mirror, and a
    counter lost to a Redis restart resumes from the last value the row saw
    rather than from zero. Returns the value now in force.
    """
    client = _redis()
    key = _key("warnings", session_id)
    await _call(
        "warnings.seed",
        lambda: client.set(key, int(mirrored), nx=True, ex=SESSION_KEY_TTL_SECONDS),
    )
    raw = await _call("warnings.get", lambda: client.get(key))
    return int(raw) if raw is not None else int(mirrored)


async def increment_warning(session_id: uuid.UUID) -> int:
    """Atomically take the next warning number."""
    client = _redis()
    key = _key("warnings", session_id)
    value = await _call("warnings.incr", lambda: client.incr(key))
    await _call("warnings.expire", lambda: client.expire(key, SESSION_KEY_TTL_SECONDS))
    return int(value)


# ── Cooldowns and once-per-session markers (section 4.2) ─────────────────────


async def in_cooldown(session_id: uuid.UUID, cooldown_key: str) -> bool:
    client = _redis()
    return bool(
        await _call(
            "cooldown.exists",
            lambda: client.exists(_key("cooldown", session_id, cooldown_key)),
        )
    )


async def start_cooldown(session_id: uuid.UUID, cooldown_key: str, seconds: int) -> None:
    client = _redis()
    await _call(
        "cooldown.set",
        lambda: client.set(_key("cooldown", session_id, cooldown_key), "1", ex=max(1, int(seconds))),
    )


async def claim_once(session_id: uuid.UUID, marker: str) -> bool:
    """True the FIRST time a marker is claimed for a session, False after.

    `SET NX` makes the claim atomic, so two batches racing on "more than one
    display" cannot both take the warning.
    """
    client = _redis()
    claimed = await _call(
        "once.claim",
        lambda: client.set(_key("once", session_id, marker), "1", nx=True, ex=SESSION_KEY_TTL_SECONDS),
    )
    return bool(claimed)


# ── Consecutive-evidence counters (sections 3.3 and 3.4) ─────────────────────


async def bump_consecutive(session_id: uuid.UUID, name: str) -> int:
    """One more consecutive piece of evidence under `name`; returns the run."""
    client = _redis()
    key = _key("consecutive", session_id, name)
    value = await _call("consecutive.incr", lambda: client.incr(key))
    await _call("consecutive.expire", lambda: client.expire(key, SESSION_KEY_TTL_SECONDS))
    return int(value)


async def reset_consecutive(session_id: uuid.UUID, name: str) -> None:
    client = _redis()
    await _call("consecutive.reset", lambda: client.delete(_key("consecutive", session_id, name)))


# ── The abuse ceiling ────────────────────────────────────────────────────────


async def count_in_minute(session_id: uuid.UUID, now: datetime, events: int) -> int:
    """Add `events` to this minute's fixed window and return the new total.

    The same INCR-plus-EXPIRE window `services/rate_limit` uses, keyed by the
    session rather than the caller: the ceiling is about one browser's event
    stream, and a candidate on a shared office address is not the abuser.
    """
    client = _redis()
    minute = int(now.timestamp()) // _ONE_MINUTE_SECONDS
    key = _key("rate", session_id, str(minute))
    total = await _call("rate.incrby", lambda: client.incrby(key, int(events)))
    await _call("rate.expire", lambda: client.expire(key, _ONE_MINUTE_SECONDS))
    return int(total)


async def clear_session(session_id: uuid.UUID) -> None:
    """Drop every key for a session once it has ended.

    Not required for correctness (everything expires), but a terminated session
    whose counters linger would let a resumed request read stale state, and a
    test that shares a Redis with other agents should leave nothing behind.
    """
    client = _redis()
    pattern = f"{KEY_PREFIX}:*:{session_id}*"
    keys = await _call("clear.scan", lambda: _scan_all(client, pattern))
    if keys:
        await _call("clear.delete", lambda: client.delete(*keys))


async def _scan_all(client, pattern: str) -> list[str]:
    found: list[str] = []
    cursor = 0
    while True:
        cursor, batch = await client.scan(cursor=cursor, match=pattern)
        found.extend(batch)
        if cursor == 0:
            return found
