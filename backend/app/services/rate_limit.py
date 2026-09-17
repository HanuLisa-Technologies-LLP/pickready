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
already a hard dependency (the cache and the proctoring warning counter), so a shared counter costs
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
from jwt import PyJWTError

from app.core import cache
from app.core.security import (
    AUDIENCE_CANDIDATE,
    AUDIENCE_ORG,
    AUDIENCE_OWNER,
    decode_token,
)

logger = logging.getLogger(__name__)

__all__ = ["Decision", "check", "rate_limit", "client_identifier"]


@dataclass(frozen=True)
class Decision:
    """The outcome, including the numbers a caller needs to back off politely."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


# Both places `app.api.deps._extract_token` looks. Duplicated as a constant
# rather than imported because `app.api.deps` imports this module's siblings and
# a services module must not depend on the API layer.
_ACCESS_COOKIE = "pr_access"

# Every audience an access token can legitimately carry. PyJWT accepts a list
# and matches any one of them, which is the same call `deps._decode_or_401`
# makes for the two internal audiences.
_ALL_AUDIENCES = [AUDIENCE_OWNER, AUDIENCE_ORG, AUDIENCE_CANDIDATE]


def _verified_subject(request: Request) -> str | None:
    """The authenticated user id, or None for anyone this cannot prove.

    Never raises. This runs in front of every rate-limited endpoint including
    the unauthenticated ones, so a malformed cookie must read as "anonymous"
    rather than as a 500 on the sign-in path.
    """
    token = request.cookies.get(_ACCESS_COOKIE)
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[len("Bearer ") :]
    if not token:
        return None
    try:
        payload = decode_token(token, audience=_ALL_AUDIENCES)
    except PyJWTError:
        return None
    # A refresh token presented here is not a session and must not share a
    # bucket with one.
    if payload.get("type") != "access":
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


def client_identifier(request: Request) -> str:
    """Who is being limited.

    An authenticated caller is limited by USER, which is both fairer and more
    useful: an office behind one NAT address is many people, and a single
    account abusing an endpoint should not be able to escape by changing
    networks.

    Anonymous callers fall back to the client address, because the socket
    address is the load balancer and would put every visitor in one bucket.

    THE LAST ENTRY, NOT THE FIRST, AND THE DIRECTION IS THE WHOLE CONTROL.
    ---------------------------------------------------------------------
    This read the FIRST entry, with a docstring arguing that the rest of the
    header is caller-supplied. That is true and it is backwards. An AWS ALB
    APPENDS the address it observed to whatever `X-Forwarded-For` arrived, so
    the first entry is precisely the attacker-supplied part and the last is the
    one the load balancer saw. Reading the first meant
    `X-Forwarded-For: <random>` on every request bought a fresh bucket, and
    every limit in the product was one header away from unlimited.

    WHY THE SUBJECT IS DECODED HERE RATHER THAN READ OFF `request.state`.
    ---------------------------------------------------------------------
    This used to read `request.state.rate_limit_subject`, and NOTHING EVER SET
    IT, so the per-user branch was dead code and every authenticated caller was
    limited by apparent IP alone. That is the fairness hole the docstring above
    describes: one account could escape any limit by changing networks.

    Setting it from the auth dependency does not work, and the reason is
    ordering. `rate_limit` is attached as a ROUTE-LEVEL dependency
    (`dependencies=[Depends(rate_limit(...))]`), and FastAPI solves those
    BEFORE the endpoint signature's own dependencies, so `get_current_user` has
    not run yet when this is called. Anything written from there would arrive
    one dependency too late, on every request, silently.

    So the subject is resolved here, from the token, and it is VERIFIED. An
    unverified `sub` would be worse than no subject at all: the claim is
    attacker-controlled, so anyone could name somebody else's bucket and
    exhaust a stranger's allowance. A signature check is one HMAC and is
    cheaper than the Redis round trip that follows it.

    Any failure at all falls back to the address bucket. A request with no
    token, an expired token or a refresh token is exactly the anonymous case.
    """
    token_subject = _verified_subject(request)
    if token_subject:
        return f"user:{token_subject}"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # The LAST entry: what this deployment's own load balancer observed.
        # Everything to its left arrived from outside and is caller-controlled.
        observed = forwarded.split(",")[-1].strip()
        if observed:
            return f"ip:{observed}"
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
