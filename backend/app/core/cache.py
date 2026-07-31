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

from app.core.config import get_settings

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

_client: Any = None
_unavailable = False


def key(*parts: Any) -> str:
    """Build a namespaced cache key from its parts.

    Callers pass the tenant id as a part for anything tenant-scoped; see rule 2.
    """
    return ":".join([_PREFIX, *(str(part) for part in parts)])


def _redis():
    """Lazily-built async Redis client, or None if Redis is unreachable.

    `_unavailable` latches so a Redis outage costs one failed connection rather
    than one per request — the whole point is to never make things slower.
    """
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        try:
            import redis.asyncio as redis_asyncio

            _client = redis_asyncio.from_url(
                get_settings().redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        except Exception as exc:  # noqa: BLE001 — cache must never break a request
            log.warning("cache.unavailable %s", type(exc).__name__)
            _unavailable = True
            return None
    return _client


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
    """Release the connection pool at shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _client = None
