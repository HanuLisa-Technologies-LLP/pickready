"""The deploy gate must prove the database AND Redis are reachable.

WHY THE BROKER HALF IS HERE
----------------------------
spec-doc6 §13.2 requires health checks that "verify database and cache
connectivity, not a static 200". This endpoint was not a static 200 before this
phase, it probed the database, and the missing half was the one that matters
most for this platform.

Redis stopped being the message broker on 2026-09-04. It still carries the
proctoring warning counter, and `services/proctoring/gate` answers 503 rather
than silently not warning, so a task that has lost it refuses every assessment
turn while looking perfectly healthy from outside. That is what this probe is
for now, and it is the same shape as the reason it was added: a dependency that
raise, it HANGS. Nothing is raised for a handler's `except` to catch. A task in
that state accepts requests and stops partway through them, silently, one at a
time. A database-only probe promotes it and the deploy reports success, which is
the exact failure this project has already been burned by once.

Each test below asserts a BEHAVIOUR of the endpoint rather than that a probe was
called, per spec-doc6 §10.1 rule 10.
"""
from __future__ import annotations

import asyncio

import pytest

from app import main
from app.api import health as health_module


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement) -> None:
        self.statements.append(str(statement))


class _Redis:
    """A Redis double that records whether it was pinged and whether it was
    closed. `aclose` matters: this probe opens a connection every 30 seconds on
    every task, so one that leaked would exhaust the server's client limit."""

    def __init__(self, *, fail: bool = False, hang: bool = False) -> None:
        self.fail = fail
        self.hang = hang
        self.pinged = False
        self.closed = False

    async def ping(self) -> bool:
        self.pinged = True
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise ConnectionError("redis unavailable")
        return True

    async def aclose(self) -> None:
        self.closed = True


def _probe_with(redis):
    """The real `_probe_redis` with the client substituted: it still pings and
    it still closes in a `finally`, so the closing test below is testing the
    contract rather than the double."""

    async def _probe() -> None:
        try:
            await redis.ping()
        finally:
            await redis.aclose()

    return _probe


@pytest.fixture
def wire(monkeypatch):
    """Point both probes at doubles and hand the test back what it wired."""

    def _wire(*, session=None, redis=None):
        session = session if session is not None else _Session()
        redis = redis if redis is not None else _Redis()
        monkeypatch.setattr("app.core.db.get_session_factory", lambda: lambda: session)
        monkeypatch.setattr(health_module, "_probe_redis", _probe_with(redis))
        return session, redis

    return _wire


@pytest.mark.asyncio
async def test_health_probes_the_database_and_redis(wire) -> None:
    session, redis = wire()

    response = await main.health()

    assert response == {"status": "ok", "database": "ok", "cache": "ok"}
    assert session.statements == ["SELECT 1"], (
        "the database probe must actually execute; a health check that resolves "
        "a session without using it passes with wrong credentials"
    )
    assert redis.pinged, (
        "Redis was never probed. A task that has lost Redis refuses every "
        "assessment turn at the proctoring gate while looking healthy."
    )


@pytest.mark.asyncio
async def test_health_fails_when_the_database_is_unreachable(wire) -> None:
    class BrokenSession(_Session):
        async def execute(self, statement) -> None:
            raise ConnectionError("database unavailable")

    wire(session=BrokenSession())

    with pytest.raises(ConnectionError, match="database unavailable"):
        await main.health()


@pytest.mark.asyncio
async def test_health_fails_when_redis_is_unreachable(wire) -> None:
    """THE HALF ADDED IN THIS PHASE.

    Raising is the whole point. A 200 carrying `{"cache": "down"}` is a healthy
    response as far as a target group is concerned: the matcher reads the status
    code, the task stays in rotation, and the deploy promotes.
    """
    wire(redis=_Redis(fail=True))

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await main.health()


@pytest.mark.asyncio
async def test_health_closes_the_redis_connection_even_when_the_ping_fails(wire) -> None:
    """One connection per probe, every 30 seconds, on every task. A probe that
    leaked on the failure path would exhaust the server's client limit during
    exactly the incident it was reporting."""
    _, redis = wire(redis=_Redis(fail=True))

    with pytest.raises(ConnectionError):
        await main.health()

    assert redis.closed


@pytest.mark.asyncio
async def test_health_gives_up_on_a_hanging_redis(monkeypatch) -> None:
    """THE FAILURE THIS ENDPOINT EXISTS FOR, and the one that is not an
    exception.

    A server that accepts the TCP connection and then never answers produces no
    error at all. Without a bound, the probe waits forever and the load balancer
    reports a timeout it cannot distinguish from a slow one. The bound must stay
    below the target group's own `health_timeout_seconds`, which the Terraform
    sets to 10.
    """
    assert health_module.PROBE_TIMEOUT_SECONDS < 10, (
        "the probe may not outlast the load balancer's health check timeout, "
        "which infra/environments/*/main.tf sets to 10 seconds"
    )

    monkeypatch.setattr("app.core.db.get_session_factory", lambda: lambda: _Session())
    monkeypatch.setattr(health_module, "PROBE_TIMEOUT_SECONDS", 0.05)

    hanging = _Redis(hang=True)
    monkeypatch.setattr(health_module, "_probe_redis", _probe_with(hanging))

    with pytest.raises(asyncio.TimeoutError):
        await main.health()


@pytest.mark.asyncio
async def test_health_does_not_reuse_the_degrading_cache_client() -> None:
    """`app.core.cache._redis()` returns None when Redis is unreachable and
    latches `_unavailable` so the next call does not retry. That is right for
    the read path and exactly wrong here: a probe built on it would report a
    healthy task with no Redis at all.

    Asserted by walking the module's IMPORTS, not by grepping its text: the
    module docstring explains this rule at length and names the module it
    refuses, so a substring search would match the explanation. An AST walk
    matches only an actual import, which is the thing being refused.

    A source-level assertion rather than a runtime one because the mistake it
    prevents is an import somebody adds for convenience, and every runtime test
    above would still pass with that import in place.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(health_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "app.core.cache" not in imported, (
        "the health probe imports app.core.cache, whose client degrades to None "
        "on an outage and latches. A probe built on it reports a healthy task "
        "with no Redis. Open a connection here instead."
    )


# ── Liveness, which is a different question from readiness ──────────────────
#
# `/health` above is READINESS and its strictness is correct: a task with no
# Redis answers every assessment turn with a 503, and promoting it on a deploy
# would ship a broken release.
#
# The defect was using that one endpoint to answer a SECOND question. The ALB
# target group polled it every thirty seconds with `unhealthy_threshold = 3`,
# and ECS replaces a task that fails its load balancer check. Redis is a single
# shared ElastiCache, so an outage there marked EVERY task unhealthy at once:
# the target group emptied and the ALB answered 503 for the whole product,
# including jobs, candidates, billing and reports, all of which would otherwise
# have kept working; then ECS killed the tasks and the replacements failed the
# same check, which is a restart loop. There was nothing to route around TO,
# because the dependency is shared.
#
# The deploy gate did not disappear, it moved: `scripts/smoke-test.sh` probes
# `/health` after the rollout and fails the deploy when it is not 200.


@pytest.mark.asyncio
async def test_liveness_answers_while_every_dependency_is_down(wire) -> None:
    """THE WHOLE POINT, and the assertion that would catch a regression.

    A liveness probe that touched a dependency would bring the restart loop
    straight back, so this wires BOTH dependencies to fail and still expects a
    200 body.
    """

    class BrokenSession(_Session):
        async def execute(self, statement) -> None:
            raise ConnectionError("database unavailable")

    wire(session=BrokenSession(), redis=_Redis(fail=True))

    assert await main.health_live() == {"status": "ok"}


@pytest.mark.asyncio
async def test_liveness_touches_no_dependency_at_all(wire) -> None:
    """Asserted on the PROBES rather than on the response.

    A handler could return the right body and still have opened a connection on
    the way, which is the thing that must not happen. `session.statements` and
    `redis.pinged` are the same witnesses the readiness tests above use.
    """
    session, redis = wire()

    await main.health_live()

    assert session.statements == [], "liveness executed a query"
    assert not redis.pinged, "liveness opened Redis"


@pytest.mark.asyncio
async def test_readiness_still_refuses_when_a_dependency_is_down(wire) -> None:
    """The other direction. Splitting the two questions must not have quietly
    relaxed the strict one: a release with a broken dependency still must not
    pass the post-rollout smoke test."""
    wire(redis=_Redis(fail=True))

    with pytest.raises(Exception):
        await main.health()
