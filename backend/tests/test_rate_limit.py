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
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG, create_access_token
from app.models.enums import Role
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


def test_only_the_last_forwarded_address_is_trusted() -> None:
    """The address THIS deployment's own load balancer observed.

    THIS TEST PREVIOUSLY ASSERTED THE OPPOSITE, AND THE OPPOSITE WAS THE BUG.
    It read `test_only_the_first_forwarded_address_is_trusted`, with the
    reasoning that "the rest of X-Forwarded-For is caller-supplied". Half of
    that is right: the rest of the header IS caller-supplied. The half that was
    wrong is which end.

    An AWS ALB APPENDS the peer address it saw to whatever `X-Forwarded-For`
    arrived on the request. So on `evil, evil, <real client>` the leftmost
    entry is the attacker's invention and the rightmost is the only one this
    infrastructure vouches for. Trusting the first meant a rotating
    `X-Forwarded-For` header bought a fresh bucket on every request, and every
    rate limit in the product was one header away from unlimited -- with no
    backstop, because the WAF module is instantiated disabled.
    """
    request = _request({"x-forwarded-for": "10.0.0.1, 10.0.0.2, 203.0.113.7"})
    assert rate_limit.client_identifier(request) == "ip:203.0.113.7"

    # The spoof this is actually defending against: everything to the left of
    # the appended address is chosen by the caller and must not reach a bucket.
    spoofed = _request({"x-forwarded-for": "evil-1, evil-2, evil-3, 203.0.113.7"})
    assert rate_limit.client_identifier(spoofed) == "ip:203.0.113.7"

    # And a caller who sends no header at all still lands in the ALB's bucket
    # rather than one they picked.
    single = _request({"x-forwarded-for": "203.0.113.7"})
    assert rate_limit.client_identifier(single) == "ip:203.0.113.7"


def test_the_socket_address_is_used_when_there_is_no_proxy_header() -> None:
    assert rate_limit.client_identifier(_request()) == "ip:10.0.0.9"


# ── The authenticated subject ───────────────────────────────────────────────
#
# THESE TESTS REPLACE ONE THAT PASSED WHILE THE FEATURE WAS ENTIRELY DEAD.
# The previous version set `request.state.rate_limit_subject` by hand and
# asserted the branch that read it. Nothing in the application ever wrote that
# attribute, so every authenticated caller in production was limited by apparent
# address alone, and the test reported the opposite. A test that supplies the
# mechanism it is testing cannot fail when the mechanism is missing.
#
# So each of these builds a REAL signed token and puts it where a browser would,
# and the negative cases below are the ones that matter most: the subject is
# attacker-supplied unless the signature is checked, and an unverified `sub`
# would let anyone name a stranger's bucket and exhaust their allowance.


def _access_token(subject: str, audience: str = AUDIENCE_ORG) -> str:
    return create_access_token(subject, Role.recruiter.value, None, audience)


def test_an_authenticated_caller_is_limited_by_account_not_by_address() -> None:
    """Fairer and harder to escape: an office behind one NAT address is many
    people, and one account should not slip the limit by changing networks."""
    token = _access_token("user-123")
    request = _request(
        {"x-forwarded-for": "203.0.113.7", "cookie": f"pr_access={token}"}
    )
    assert rate_limit.client_identifier(request) == "user:user-123"


def test_a_bearer_token_is_read_the_same_way_as_the_cookie() -> None:
    token = _access_token("user-456")
    request = _request({"authorization": f"Bearer {token}"})
    assert rate_limit.client_identifier(request) == "user:user-456"


def test_a_candidate_token_also_gets_its_own_bucket() -> None:
    """All three audiences are real sessions. A candidate limited by address
    would share a bucket with everyone else on their office network."""
    token = _access_token("cand-1", audience=AUDIENCE_CANDIDATE)
    request = _request({"cookie": f"pr_access={token}"})
    assert rate_limit.client_identifier(request) == "user:cand-1"


def test_a_tampered_token_falls_back_to_the_address() -> None:
    """THE ONE THAT MATTERS. If the signature were not checked, anyone could
    forge `sub` and either escape their own limit or burn somebody else's."""
    forged = _access_token("victim-user") + "tampered"
    request = _request(
        {"x-forwarded-for": "203.0.113.7", "cookie": f"pr_access={forged}"}
    )
    assert rate_limit.client_identifier(request) == "ip:203.0.113.7"


def test_a_garbage_cookie_falls_back_to_the_address_and_does_not_raise() -> None:
    """This runs in front of unauthenticated endpoints too, so a malformed
    cookie must read as anonymous rather than 500 the sign-in path."""
    request = _request({"cookie": "pr_access=not-a-jwt-at-all"})
    assert rate_limit.client_identifier(request) == "ip:10.0.0.9"


def test_an_anonymous_caller_still_falls_back_to_the_address() -> None:
    assert rate_limit.client_identifier(_request()) == "ip:10.0.0.9"


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
    from app.api import assessment_conversation, auth

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

    invitation = limited_paths(assessment_conversation.router)
    session = limited_paths(auth.router)
    assert any("invitations" in path for path in invitation), (
        f"the public invitation resolver is not rate limited: {invitation}"
    )
    assert any("firebase/session" in path for path in session), (
        f"the session exchange is not rate limited: {session}"
    )
