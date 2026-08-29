"""The deployment health probe.

WHAT THIS ENDPOINT IS FOR
--------------------------
It is the ALB target group's health check and therefore the deploy gate. ECS
runs a rolling deployment with `deployment_circuit_breaker { rollback = true }`,
so a task that fails this check is not promoted and the deploy reverts. That
makes the question this endpoint asks the question that decides whether a
release ships.

WHY IT PROBES THE BROKER AS WELL AS THE DATABASE
-------------------------------------------------
It used to probe the database only, and the reason it now probes both is not
symmetry.

Publishing to Redis has NO timeout by default, so an unreachable broker does not
raise, it HANGS. That silently defeats every `try`/`except` wrapped around an
enqueue, because nothing is ever raised for the handler to catch. This platform
has already been burned by it: a management job found thirty files, hung on its
first `send_task`, and died at the 900-second ceiling having written nothing,
while the deploy that shipped it reported success.

A task whose broker is unreachable therefore accepts requests and then stops
partway through them, one at a time, with no error anywhere. That is exactly the
shape a health check exists to keep out of a target group, and a database-only
probe promotes it.

spec-doc6 §13.2 states the requirement directly: health checks "hit real
application health endpoints that verify database and cache connectivity, not a
static 200".

WHY IT DOES NOT REUSE `app.core.cache`
---------------------------------------
`cache._redis()` returns None when Redis is unreachable and latches
`_unavailable` so the next request does not retry. That is correct for the read
path, where a cache miss is cheaper than an error, and it is precisely wrong
here: a probe built on it would report a healthy task with no broker, which is
the failure it exists to detect. So this module opens its own connection, and it
is a fresh one each time on purpose. A pooled connection that was established
before the outage can survive one; establishing a new connection is the thing
that actually proves the broker is reachable now.

WHAT THIS COSTS, STATED RATHER THAN DISCOVERED
------------------------------------------------
A dependency outage now takes the tasks out of the target group rather than
leaving them serving broken requests. The load balancer's own configuration is
what keeps that proportionate: 30-second interval, 3 consecutive failures, so a
blip has to persist for about ninety seconds before a task is drained. A probe
that failed on the first missed packet would turn every transient into an
outage, which is the opposite trade.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings

router = APIRouter()

#: Bound on each dependency probe.
#:
#: MUST STAY BELOW THE LOAD BALANCER'S OWN TIMEOUT, which the Terraform sets to
#: 10 seconds (`infra/modules/alb`, `health_timeout_seconds`). The two probes run
#: concurrently, so the endpoint's worst case is one of these plus overhead
#: rather than the sum. If the probe could outlast the load balancer's patience,
#: a slow dependency and an unreachable one would look identical from outside:
#: both would simply time out, and the response body naming which one failed
#: would never be read.
PROBE_TIMEOUT_SECONDS = 3.0


async def _probe_database() -> None:
    """Resolve a pooled session and execute against it.

    `SELECT 1` rather than a table read: this must prove connectivity and
    credentials without depending on a migration having run, because the
    migration job is a separate one-shot ECS task and a health check that failed
    before it completed would deadlock the first deploy of an environment.
    """
    from app.core.db import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))


async def _probe_broker() -> None:
    """PING the Celery broker over a connection opened for this probe.

    Explicit socket timeouts on the client as well as the `wait_for` around it.
    The `wait_for` bounds the whole await; the socket timeouts bound the read
    itself, which matters because a TCP connection to a host that accepts and
    then never answers is the case that hangs without them, and it is the case
    this probe was added for.
    """
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(
        get_settings().redis_url,
        socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        socket_timeout=PROBE_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
    finally:
        # Always closed. A probe that leaked a connection every 30 seconds would
        # exhaust the broker's client limit in a day and take down the thing it
        # was checking on.
        await client.aclose()


async def probe_dependencies() -> dict:
    """Run every dependency probe and RAISE if any of them fails.

    Raising rather than returning a degraded body is the whole design. A 200
    carrying `{"database": "down"}` is a healthy response as far as a target
    group is concerned: the matcher reads the status code, the task stays in
    rotation, and the deploy promotes. The only way this endpoint can gate a
    deployment is by not returning 200.

    The two probes run CONCURRENTLY, so the endpoint's worst case is one timeout
    rather than their sum, and `gather` re-raises the first exception.
    """
    await asyncio.gather(
        asyncio.wait_for(_probe_database(), timeout=PROBE_TIMEOUT_SECONDS),
        asyncio.wait_for(_probe_broker(), timeout=PROBE_TIMEOUT_SECONDS),
    )
    return {"status": "ok", "database": "ok", "cache": "ok"}


@router.get("/health")
async def health() -> dict:
    return await probe_dependencies()
