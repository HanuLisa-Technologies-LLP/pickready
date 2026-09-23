"""Single flight, and the request key both it and the derivation cache use.

RPN-AI-UP-001 W4.2 and W4.3.

WHAT COALESCING IS FOR, AND WHY IT IS NOT HEDGING
--------------------------------------------------
Two recruiters open the same job page in the same second; a matching run fans
out over fifty candidates who share one job description; a dashboard is polled
by three tabs. In each case identical work is already in flight, and the second
caller's correct answer is the first caller's awaitable rather than a second
upstream call.

W4.2 also says, explicitly, what NOT to build here: request hedging. Hedging
doubles cost on the hedged fraction, is only safe for read-only generation, and
interacts badly with this platform's standing rule that a deadline must PREDICT
(`elapsed + longest_attempt_so_far >= deadline`): a hedge would have to be
budgeted INSIDE that inequality rather than on top of it, or the router would be
advertising a bound it does not have. Coalescing has no cost multiplier at all,
which is why it is the one that got built.

IN PROCESS, NOT DISTRIBUTED, AND THAT IS THE WHOLE DESIGN
-----------------------------------------------------------
There is no lock in Redis here. `core.cache.get_or_set` already records why: a
distributed lock costs a round trip on every hit and a stuck lock takes the
endpoint down. What this module removes is the narrower and much more common
waste, N concurrent calls inside ONE process, and it removes it with a plain
dict of asyncio tasks that cannot outlive the process holding it.

THE TENANT IS IN THE KEY, STRUCTURALLY
----------------------------------------
`request_key` REFUSES a falsy tenant id rather than defaulting one. W4.3 names
this as the place multi-tenant isolation most often disappears, and names the
mechanism: it disappears later, when somebody adds a cache for a performance fix
and keys it on the entity id alone. A key builder that cannot be called without
a tenant is the only version of that rule that survives the next contributor.
`tests/test_cache_tenant_keying.py` sweeps the tree for the ones that came
before it.

AND `extract_assessment` IS NEVER SHARED
------------------------------------------
`NEVER_SHARED` is checked by the key builder, so neither the cache nor the
coalescer can be pointed at a live assessment transcript. That rule predates
this module (`tools/implementations.py` declares the tool non-idempotent for the
same reason) and W4.3 says in as many words that it must survive: a conversation
grows between two reads by design, and an agent scoring a transcript two answers
stale is scoring the wrong assessment. Two callers reading it in the same
millisecond is not an exception, because "the same millisecond" is a property of
the scheduler rather than of the data.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, TypeVar

from app.core import cache

logger = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "NEVER_SHARED",
    "NeverShared",
    "TenantScopeMissing",
    "request_key",
    "single_flight",
    "cached_derivation",
    "stats",
    "reset",
]


#: Work whose result must never be shared between two callers, however close
#: together they arrive. Keyed by the KIND a caller passes, and both spellings
#: are listed because the tool and the transcript it reads are named differently
#: in the two places a caller could plausibly reach for one.
NEVER_SHARED: frozenset[str] = frozenset(
    {"extract_assessment", "assessment_transcript"}
)


class TenantScopeMissing(ValueError):
    """A key was requested without a tenant. Refused rather than defaulted."""


class NeverShared(ValueError):
    """A kind on the `NEVER_SHARED` list was offered to the cache or coalescer."""


def request_key(*, tenant_id: Any, kind: str, payload: Any) -> str:
    """A stable key over (tenant, kind, payload). The only key builder here.

    The digest is over CANONICAL json with sorted keys, so two callers that
    spell the same request differently -- a UUID object and its string, a field
    left to its default -- share one entry rather than quietly halving the hit
    rate. That is the same argument `tools/executor._cache_key` already makes
    for keying on the validated model.

    Raises rather than substituting anything. A missing tenant is the failure
    this builder exists to make impossible, and a `NEVER_SHARED` kind is a rule
    that is worth more than the call site that broke it.
    """
    if kind in NEVER_SHARED:
        raise NeverShared(
            f"{kind!r} is never cached and never coalesced: a live "
            f"conversation grows between two reads by design, and an agent "
            f"scoring a transcript two answers stale is scoring the wrong "
            f"assessment"
        )
    scope = "" if tenant_id is None else str(tenant_id).strip()
    if not scope:
        raise TenantScopeMissing(
            f"a cache or coalescing key for {kind!r} needs a tenant id; a key "
            f"without one serves one tenant's derived data to another, which "
            f"is the most common way multi-tenant isolation disappears"
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return cache.key("derived", scope, kind, digest)


# ── The flight table ─────────────────────────────────────────────────────────

#: key -> (the loop the task belongs to, the task). The loop is stored because
#: an entry left behind by a previous event loop -- which is every test that
#: shares this process -- must never be awaited from a new one; awaiting a task
#: from a dead loop raises something nobody could diagnose from the traceback.
_inflight: dict[str, tuple[asyncio.AbstractEventLoop, "asyncio.Task[Any]"]] = {}

_stats = {"calls": 0, "upstream": 0, "coalesced": 0}


def stats() -> dict[str, int]:
    """Counters for the admin health surface. `coalesced` is the saving.

    Reported rather than logged per call: the operator question is "how much
    duplicate work is this removing", which is a ratio over time and not an
    event.
    """
    return dict(_stats)


def reset() -> None:
    """Drop every counter and every recorded flight. Test and operator hook."""
    _inflight.clear()
    for name in _stats:
        _stats[name] = 0


def _discard(key: str, task: "asyncio.Task[Any]") -> None:
    current = _inflight.get(key)
    if current is not None and current[1] is task:
        del _inflight[key]


async def single_flight(key: str, factory: Callable[[], Awaitable[T]]) -> T:
    """Run `factory` once for `key`, however many callers ask concurrently.

    Followers await the LEADER'S awaitable, so they get the leader's result and,
    just as importantly, the leader's exception. Nothing here degrades: a
    coalescer that swallowed the failure would hand N callers an empty value
    indistinguishable from a legitimately empty result, which is the split
    `services/tools.execute` and `llm_router` both keep on purpose.

    The entry is cleared when the TASK finishes rather than when this caller
    returns. The leader can be cancelled while followers are still waiting, and
    a `finally` here would then either delete a flight the followers are still
    joined to or leave a completed one in the table for ever.

    This is deduplication of CONCURRENT work only; persistence across time is
    what `cached_derivation` is for, and the two are separate because a value
    can be worth sharing for one millisecond and not for one minute.
    """
    _stats["calls"] += 1
    loop = asyncio.get_running_loop()
    existing = _inflight.get(key)
    if existing is not None:
        owner, running = existing
        if owner is loop and not running.done():
            _stats["coalesced"] += 1
            logger.debug("coalescing.joined key=%s", key)
            return await asyncio.shield(running)

    _stats["upstream"] += 1
    task = asyncio.ensure_future(factory())
    _inflight[key] = (loop, task)
    task.add_done_callback(lambda finished: _discard(key, finished))
    return await asyncio.shield(task)


async def cached_derivation(
    *,
    tenant_id: Any,
    kind: str,
    payload: Any,
    compute: Callable[[], Awaitable[T]],
    ttl: int,
) -> T:
    """Read a DERIVED representation through the cache, single flighted.

    W4.3's rule about WHAT may be stored is in the name: the derived
    representation, never the API response. A resume's extracted claims, a
    document's reduced pack, a resolved entity, an embedding. Storing the raw
    completion instead would tie the cache to the vendor's response shape and
    would make a prompt change silently serve answers written by the old one.

    Composed on top of `cache.get_or_set` rather than reimplementing it. That
    function deliberately does not single flight, and says so; this wrapper adds
    exactly the missing property, in process, without the distributed lock its
    docstring rejects.
    """
    key = request_key(tenant_id=tenant_id, kind=kind, payload=payload)
    return await single_flight(key, lambda: cache.get_or_set(key, compute, ttl=ttl))
