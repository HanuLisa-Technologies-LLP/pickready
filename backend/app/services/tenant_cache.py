"""Tenant-scoped cache facade over the ONE Redis client, `app.core.cache`.

WHY THIS MODULE IS NOW FOUR DELEGATIONS AND NOTHING ELSE
---------------------------------------------------------
It used to build its own `redis.asyncio` client, with NO
`socket_connect_timeout` and NO `socket_timeout`, and to open and close a fresh
one on EVERY call. That is the hot path: `rbac._permission_rows` reads through
here, and `require_capability` calls it on essentially every authorized route
in the product.

The `except Exception` guards it carried could not save it. They catch a raised
error; they cannot catch a HANG, and an unreachable Redis does not raise, it
blocks. With no socket timeout a network partition between the API task and
ElastiCache would have stalled every RBAC-gated request in the product until
the kernel gave up on the connection, and `/health` would have kept answering,
because the same class of failure is what the broker publish timeout taught
this repository once already (claude.md, 2026-08-05).

So this is a "one implementation per concept" repair, not a patch: there were
two Redis cache layers, and the one missing the guard was the one on the hot
path. `app/core/cache.py` already had all three properties this needed -- one
second connect and read timeouts, a lazily-built module-level client so a
request does not pay for a handshake, and an `_unavailable` latch so an outage
costs one failed connection rather than one per request. This module keeps its
own NAME and its own KEY FORMAT (`pickready:tenant:<id>:...`, spelled by its
callers and swept by `tests/test_cache_tenant_keying.py`) and owns no client.

Behaviour on the happy path and on a genuine Redis outage is unchanged: a read
returns None, a write is dropped, and the caller falls through to Postgres.
"""
from __future__ import annotations

from typing import Any

from app.core import cache


async def get_json(key: str) -> Any | None:
    """Read a JSON value, or None on a miss, a decode failure or an outage."""
    return await cache.get(key)


async def set_json(key: str, value: Any, *, ttl: int = 120) -> None:
    """Write a JSON value with a TTL. A failed write is a dropped cache entry."""
    await cache.set(key, value, ttl)


async def delete(key: str) -> None:
    """Drop one key. Called by the write path, never by the read path."""
    await cache.invalidate(key)


async def delete_pattern(pattern: str) -> None:
    """Drop every key matching a glob. See `cache.invalidate_pattern`."""
    await cache.invalidate_pattern(pattern)
