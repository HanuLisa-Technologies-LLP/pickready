"""Rate limiting: what it stops, and the two ways it must never fail.

The direction of each failure is the whole design, so both are asserted:

  * **Fails OPEN when Redis is down.** A limiter that fails closed turns a
    cache blip into a total outage of the candidate portal, for a mechanism
    whose only job is to slow down abuse. Nothing downstream is unsafe because
    a counter did not run: an over-limit request was authorized anyway.
  * **Never authorizes anything.** It is an abuse control. `require_capability`
    and the RLS boundary decide who may do what, and they do it whether or not
    this module works.

And the evasion the identifier has to resist: `X-Forwarded-For` is
caller-supplied past its first entry, so a limiter that reads the whole header
is a limiter anyone can escape by appending to it.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core import cache
from app.services import rate_limit


class _FakeRedis:
    """Enough of Redis to drive the fixed-window counter."""

    def __init__(self, *, explode: bool = False, forget_ttl: bool = False) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.explode = explode
        self.forget_ttl = forget_ttl
        self.expire_calls = 0

    async def incr(self, key: str) -> int:
        if self.explode:
            raise ConnectionError("redis is down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, window: int) -> bool:
        self.expire_calls += 1
        self.expiries[key] = window
        return True

    async def ttl(self, key: str) -> int:
        if self.forget_ttl:
            return -1
        return self.expiries.get(key, -1)


@pytest.fixture
def redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    return fake


# ── The counter ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_requests_are_allowed_up_to_the_limit_and_refused_after(redis) -> None:
    for expected_remaining in (2, 1, 0):
        decision = await rate_limit.check("bucket", "ip:1.2.3.4", limit=3, window=60)
        assert decision.allowed
        assert decision.remaining == expected_remaining

    refused = await rate_limit.check("bucket", "ip:1.2.3.4", limit=3, window=60)
    assert not refused.allowed
    assert refused.remaining == 0
    # A caller told to back off needs to know for how long.
    assert refused.retry_after > 0


@pytest.mark.asyncio
async def test_the_window_is_only_set_once(redis) -> None:
    """A busy caller must not be able to push the window forward forever.

    If EXPIRE ran on every request, a client sending steadily would keep
    resetting the TTL and the counter would never roll over -- so the limit
    would be permanent rather than per-window.
    """
    for _ in range(5):
        await rate_limit.check("bucket", "ip:1.2.3.4", limit=10, window=60)
    assert redis.expire_calls == 1


@pytest.mark.asyncio
async def test_a_key_with_no_expiry_is_repaired_rather_than_limiting_forever(
    monkeypatch,
) -> None:
    """Seen when a process dies between INCR and EXPIRE: the key exists, has no
    TTL, and that caller is rate limited for the lifetime of the Redis."""
    fake = _FakeRedis(forget_ttl=True)
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    await rate_limit.check("bucket", "ip:1.2.3.4", limit=10, window=60)
    await rate_limit.check("bucket", "ip:1.2.3.4", limit=10, window=60)
    assert fake.expire_calls >= 2, "a TTL-less key was left to limit forever"


@pytest.mark.asyncio
async def test_buckets_and_callers_are_counted_separately(redis) -> None:
    await rate_limit.check("a", "ip:1", limit=1, window=60)
    # A different caller in the same bucket is unaffected.
    assert (await rate_limit.check("a", "ip:2", limit=1, window=60)).allowed
    # And the same caller in a different bucket.
    assert (await rate_limit.check("b", "ip:1", limit=1, window=60)).allowed
    # But the first pair is now over.
    assert not (await rate_limit.check("a", "ip:1", limit=1, window=60)).allowed


# ── The failure direction ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_being_absent_allows_the_request(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: None)
    decision = await rate_limit.check("bucket", "ip:1.2.3.4", limit=1, window=60)
    assert decision.allowed, "a missing Redis must never refuse a request"


@pytest.mark.asyncio
async def test_redis_raising_allows_the_request(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: _FakeRedis(explode=True))
    for _ in range(10):
        decision = await rate_limit.check("bucket", "ip:1.2.3.4", limit=1, window=60)
        assert decision.allowed, "a broken Redis must never refuse a request"


# ── Who gets limited ────────────────────────────────────────────────────────

def _request(headers: dict[str, str] | None = None, client=("10.0.0.9", 1234)):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "client": client,
        "query_string": b"",
    }
    return Request(scope)


def test_only_the_first_forwarded_address_is_trusted() -> None:
    """The rest of X-Forwarded-For is caller-supplied. A limiter that reads the
    whole header can be escaped by appending to it."""
    request = _request({"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"})
    assert rate_limit.client_identifier(request) == "ip:203.0.113.7"

    spoofed = _request({"x-forwarded-for": "203.0.113.7, evil-1, evil-2, evil-3"})
    assert rate_limit.client_identifier(spoofed) == "ip:203.0.113.7"


def test_the_socket_address_is_used_when_there_is_no_proxy_header() -> None:
    assert rate_limit.client_identifier(_request()) == "ip:10.0.0.9"


def test_an_authenticated_caller_is_limited_by_account_not_by_address() -> None:
    """Fairer and harder to escape: an office behind one NAT address is many
    people, and one account should not slip the limit by changing networks."""
    request = _request({"x-forwarded-for": "203.0.113.7"})
    request.state.rate_limit_subject = "user-123"
    assert rate_limit.client_identifier(request) == "user:user-123"


# ── The dependency, end to end ──────────────────────────────────────────────

def test_the_dependency_answers_429_with_a_real_cause_and_retry_after(redis) -> None:
    app = FastAPI()

    @app.get(
        "/thing",
        dependencies=[__import__("fastapi").Depends(
            rate_limit.rate_limit("thing", limit=2, window=60)
        )],
    )
    async def _thing() -> dict:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/thing").status_code == 200
    assert client.get("/thing").status_code == 200

    refused = client.get("/thing")
    assert refused.status_code == 429
    # Not "Request failed (429)". Section 1: every error surfaces its cause.
    detail = refused.json()["detail"]
    assert "2 per 60 seconds" in detail
    assert "try again in" in detail
    assert refused.headers["Retry-After"].isdigit()
    assert refused.headers["X-RateLimit-Limit"] == "2"


def test_the_dependency_lets_everything_through_when_redis_is_gone(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: None)
    app = FastAPI()

    @app.get(
        "/thing",
        dependencies=[__import__("fastapi").Depends(
            rate_limit.rate_limit("thing", limit=1, window=60)
        )],
    )
    async def _thing() -> dict:
        return {"ok": True}

    client = TestClient(app)
    for _ in range(5):
        assert client.get("/thing").status_code == 200


# ── It is applied where it matters ──────────────────────────────────────────

def test_the_public_endpoints_that_do_real_work_are_limited() -> None:
    """A limiter nobody applied is a limiter that does nothing.

    Both of these are reachable with no session and do real work per call: one
    resolves an emailed token against the database, the other verifies a
    Firebase token over the network and mints a session.
    """
    # The ROUTERS, not `app.routes`: this FastAPI version keeps included
    # routers as lazy wrappers, so the app's route list is not flattened and a
    # scan of it silently finds nothing -- which would have made this test pass
    # by measuring an empty set.
    from app.api import assessments, auth

    def limited_paths(router) -> set[str]:
        found = set()
        for route in router.routes:
            for dependency in getattr(route, "dependencies", []):
                call = getattr(dependency, "dependency", None) or getattr(
                    dependency, "call", None
                )
                if "rate_limit" in getattr(call, "__qualname__", ""):
                    found.add(route.path)
        return found

    invitation = limited_paths(assessments.router)
    session = limited_paths(auth.router)
    assert any("invitations" in path for path in invitation), (
        f"the public invitation resolver is not rate limited: {invitation}"
    )
    assert any("firebase/session" in path for path in session), (
        f"the session exchange is not rate limited: {session}"
    )
