"""Redis-backed cache for hot, rarely-changing reads (latency pass §6).

Three rules keep this from becoming a source of bugs rather than speed:

1. **A cache miss and a dead Redis look identical to the caller.** Every
   operation is wrapped so a connection error degrades to "not cached" instead
   of failing the request. A cache that can take the API down is worse than no
   cache at all.

2. **Nothing tenant-scoped is cached without the tenant in the key.** A shared
   key across tenants is a data leak wearing a performance costume, so
   `key()` takes the parts and joins them rather than letting callers hand-roll
   a string they might forget to scope.

3. **Only DERIVED or SLOW-CHANGING data.** A job description, a company
   profile, a price list. Never a balance, never a permission, never anything
   a user just wrote and expects to see back — those must be read through.

The namespace carries a version. Bumping `_VERSION` invalidates everything at
once, which is the escape hatch when a payload shape changes and stale entries
would deserialize into the wrong thing.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.core.redis_loop import LoopBoundRedis

log = logging.getLogger(__name__)

_VERSION = "v1"
_PREFIX = f"pickready:{_VERSION}"

# TTLs, in seconds. Chosen per the latency brief's "5-60 minutes" guidance and
# by how badly a stale read would read to a user.
TTL_JOB_DESCRIPTION = 600      # 10 min — edited occasionally, read constantly
TTL_COMPANY_PROFILE = 900      # 15 min
TTL_PRICING_PLANS = 3600       # 1 hour — changes on a migration, not on a click
TTL_CANDIDATE_PROFILE = 3600   # 1 hour — static between the candidate's edits
TTL_SHORT = 60

#: The one loop-bound client for the cache, the rate limiter and the session
#: store (all three reach it through `_redis()`, which is also the seam the
#: harness and the tests substitute).
_CLIENT = LoopBoundRedis(name="cache", socket_timeout=1, connect_timeout=1)


def key(*parts: Any) -> str:
    """Build a namespaced cache key from its parts.

    Callers pass the tenant id as a part for anything tenant-scoped; see rule 2.
    """
    return ":".join([_PREFIX, *(str(part) for part in parts)])


def _redis():
    """The async Redis client for the running loop, or None if none can be built.

    REBUILT ON A NEW EVENT LOOP, the rule the realtime hub was taught on
    2026-09-16: a redis-py pool's connections belong to the loop that opened
    them. `core/redis_loop.LoopBoundRedis` is the one implementation of that
    rule, shared with `workers/status` and the web-search breaker; a build
    failure is logged at warning and latched for the current loop only.

    Kept as a function rather than exposing the object, because it is the seam
    the harness's `redis_down` fault and a dozen tests substitute.
    """
    return _CLIENT.client()


async def get(cache_key: str) -> Any | None:
    client = _redis()
    if client is None:
        return None
    try:
        raw = await client.get(cache_key)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache.get_failed key=%s err=%s", cache_key, type(exc).__name__)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # A value written by an older payload shape. Treat as a miss rather
        # than handing the caller something it cannot read.
        return None


async def set(cache_key: str, value: Any, ttl: int = TTL_SHORT) -> bool:
    client = _redis()
    if client is None:
        return False
    try:
        await client.set(cache_key, json.dumps(value, default=str), ex=ttl)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("cache.set_failed key=%s err=%s", cache_key, type(exc).__name__)
        return False


async def invalidate(*cache_keys: str) -> None:
    """Drop specific keys. Called by the WRITE path, never by the read path.

    Invalidating on write is what makes a 10-minute TTL acceptable on something
    a recruiter edits: they see their own change immediately, and everyone else
    sees it within the TTL at worst.
    """
    client = _redis()
    if client is None or not cache_keys:
        return
    try:
        await client.delete(*cache_keys)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache.invalidate_failed err=%s", type(exc).__name__)


#: Upper bound on the keys one `invalidate_pattern` sweep will visit.
#:
#: `SCAN` is cursor based and returns a partial batch per round trip, so a
#: pattern matching a large keyspace costs an unbounded number of them. The
#: only caller is the RBAC role-permission flush, whose keyspace is
#: (tenants x roles) and is nowhere near this, so the bound is a ceiling on a
#: pathological pattern rather than a limit the product ever reaches. Stopping
#: is safe for this data: every entry carries a TTL, so an unswept key expires
#: on its own rather than serving a stale row for ever.
_PATTERN_SCAN_LIMIT = 10_000
_PATTERN_SCAN_BATCH = 100


async def invalidate_pattern(pattern: str) -> None:
    """Drop every key matching a glob, e.g. `pickready:tenant:*:role_permissions:*`.

    Provenance: `services/tenant_cache.delete_pattern` before the 2026-09-17
    consolidation; it is the flush `rbac.invalidate_role_permissions` uses when
    a global capability row changes and every tenant's copy has to go.

    SCAN rather than KEYS, because KEYS blocks the Redis event loop for the
    whole keyspace and this is called from a request handler.
    """
    client = _redis()
    if client is None:
        return
    seen = 0
    try:
        async for cache_key in client.scan_iter(
            match=pattern, count=_PATTERN_SCAN_BATCH
        ):
            await client.delete(cache_key)
            seen += 1
            if seen >= _PATTERN_SCAN_LIMIT:
                log.warning(
                    "cache.invalidate_pattern_truncated pattern=%s visited=%s",
                    pattern,
                    seen,
                )
                return
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "cache.invalidate_pattern_failed pattern=%s err=%s",
            pattern,
            type(exc).__name__,
        )


async def get_or_set(
    cache_key: str, loader: Callable[[], Awaitable[Any]], ttl: int = TTL_SHORT
) -> Any:
    """Read through the cache, computing on a miss.

    Deliberately NOT a lock-protected single-flight: a stampede on a miss costs
    a few duplicate computations, whereas a distributed lock costs a round trip
    on every hit and a stuck lock takes the endpoint down. The wrong trade for
    the data this module is allowed to hold.
    """
    cached = await get(cache_key)
    if cached is not None:
        return cached
    value = await loader()
    if value is not None:
        await set(cache_key, value, ttl)
    return value


async def close() -> None:
    """Release the connection pool at shutdown. Never raises; failures are logged."""
    await _CLIENT.aclose()
