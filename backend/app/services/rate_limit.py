"""Shared-counter rate limiting for the endpoints an anonymous caller can reach.

WHAT THIS PROTECTS AND WHAT IT DOES NOT
---------------------------------------
This is an ABUSE control, not an authorization control. Nothing here decides
who may do what; `require_capability` and the RLS boundary do that, and they do
it whether or not this module is working. What this stops is one caller
consuming a shared resource faster than the product can afford: the assessment
invitation resolver and the public apply page both do real database work for an
unauthenticated visitor, and answering an assessment turn costs a model call.

WHY IT FAILS OPEN
-----------------
If Redis is unreachable, every request is ALLOWED. That is a deliberate
trade and the direction matters:

  * failing closed turns a cache blip into a total outage of the candidate
    portal, for a mechanism whose entire job is to slow down abuse; and
  * the thing being protected is cost and capacity, not correctness. Nothing
    downstream is unsafe because a limiter did not run -- an over-limit
    request would have been authorized anyway.

The same reasoning `core/cache` already uses, stated here because a limiter is
the one place where "fail open" looks careless and is not.

WHY REDIS AND NOT A PROCESS DICTIONARY
--------------------------------------
ECS runs several tasks. A per-process counter divides the real limit
by the instance count and, worse, moves with autoscaling -- so the limit a
caller actually experiences depends on how busy the service is. Redis is
already a hard dependency (the Celery broker), so a shared counter costs
nothing new.

THE ALGORITHM IS A FIXED WINDOW, ON PURPOSE
-------------------------------------------
INCR plus EXPIRE. A sliding window is more accurate at the boundary and needs
either a sorted set per caller or a Lua script; for "stop one IP hammering a
public endpoint" the extra accuracy buys nothing, and the simpler thing is the
one that stays correct under a Redis version bump.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core import cache

logger = logging.getLogger(__name__)

__all__ = ["Decision", "check", "rate_limit", "client_identifier"]


@dataclass(frozen=True)
class Decision:
    """The outcome, including the numbers a caller needs to back off politely."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


def client_identifier(request: Request) -> str:
    """Who is being limited.

    An authenticated caller is limited by USER, which is both fairer and more
    useful: an office behind one NAT address is many people, and a single
    account abusing an endpoint should not be able to escape by changing
    networks.

    Anonymous callers fall back to the client address. Behind the ALB the real
    address is the first entry of `X-Forwarded-For`; the socket address is the
    load balancer and would put every visitor in one bucket. Only the FIRST
    entry is trusted, because the rest of that header is caller-supplied and a
    limiter that reads it is a limiter anyone can evade.
    """
    token_subject = getattr(request.state, "rate_limit_subject", None)
    if token_subject:
        return f"user:{token_subject}"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return f"ip:{first}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


async def check(bucket: str, identifier: str, *, limit: int, window: int) -> Decision:
    """Count one request against `bucket` for `identifier`.

    Allowed-by-default when Redis cannot answer (see the module docstring).
    """
    client = cache._redis()  # noqa: SLF001 - one lazily-built client for the process
    if client is None:
        return Decision(True, limit, limit, 0)

    key = f"ratelimit:{bucket}:{identifier}"
    try:
        count = await client.incr(key)
        if count == 1:
            # Only the first request in a window sets the expiry, so a busy
            # caller cannot keep pushing the window forward and never reset.
            await client.expire(key, window)
            ttl = window
        else:
            ttl = await client.ttl(key)
            if ttl is None or ttl < 0:
                # A key with no expiry would limit that caller forever. Seen
                # when a process dies between INCR and EXPIRE.
                await client.expire(key, window)
                ttl = window
    except Exception as exc:  # noqa: BLE001 - never break a request over a counter
        logger.debug("rate_limit.unavailable bucket=%s err=%s", bucket, type(exc).__name__)
        return Decision(True, limit, limit, 0)

    remaining = max(0, limit - count)
    if count > limit:
        logger.warning(
            "rate_limit.exceeded bucket=%s identifier=%s count=%d limit=%d",
            bucket, identifier, count, limit,
        )
        return Decision(False, limit, 0, int(ttl))
    return Decision(True, limit, remaining, int(ttl))


def rate_limit(bucket: str, *, limit: int, window: int):
    """FastAPI dependency factory.

        @router.post("/things", dependencies=[Depends(rate_limit("things", limit=30, window=60))])

    Answers 429 with a real cause and a `Retry-After` header, because
    "Request failed (429)" tells a caller nothing about when to come back.
    """

    async def _dependency(request: Request) -> None:
        decision = await check(
            bucket, client_identifier(request), limit=limit, window=window
        )
        if decision.allowed:
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many requests. This endpoint allows {limit} per "
                f"{window} seconds; try again in {decision.retry_after} seconds."
            ),
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    return _dependency
