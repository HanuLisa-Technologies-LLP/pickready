"""Single flight: N concurrent identical calls produce ONE upstream call.

RPN-AI-UP-001 W4.2, and the two hard rules W4.3 attaches to the key builder the
coalescer and the derivation cache share.

The upstream is a real counting coroutine rather than a mock, and the assertion
is on how many times it RAN. "The factory was called once" asserted against a
mock is a statement about the mock; a counter incremented inside the awaited
body is a statement about the concurrency.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core import cache
from app.services import coalescing


@pytest.fixture(autouse=True)
def _clean():
    coalescing.reset()
    yield
    coalescing.reset()


# ── The saving ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ten_concurrent_identical_calls_run_the_work_once() -> None:
    ran = 0

    async def work() -> str:
        nonlocal ran
        ran += 1
        # Yields control, so every other caller reaches the flight table while
        # this one is genuinely in flight. Without a suspension point the test
        # would pass on a scheduler that never interleaved and prove nothing.
        await asyncio.sleep(0.01)
        return "one answer"

    key = coalescing.request_key(tenant_id="t-1", kind="jd", payload={"job": 1})
    results = await asyncio.gather(
        *(coalescing.single_flight(key, work) for _ in range(10))
    )

    assert ran == 1
    assert results == ["one answer"] * 10
    assert coalescing.stats() == {"calls": 10, "upstream": 1, "coalesced": 9}


@pytest.mark.asyncio
async def test_two_different_keys_do_not_share_a_flight() -> None:
    """The negative direction, and it is not a formality: a coalescer that
    ignored the key would pass the test above perfectly."""
    ran = 0

    async def work() -> int:
        nonlocal ran
        ran += 1
        await asyncio.sleep(0.01)
        return ran

    keys = [
        coalescing.request_key(tenant_id="t-1", kind="jd", payload={"job": index})
        for index in range(4)
    ]
    await asyncio.gather(*(coalescing.single_flight(key, work) for key in keys))
    assert ran == 4


@pytest.mark.asyncio
async def test_two_tenants_asking_the_same_question_do_not_share_an_answer() -> None:
    """The isolation the key builder exists for, asserted through the
    coalescer rather than only against the key string."""
    served: list[str] = []

    def factory(tenant: str):
        async def work() -> str:
            served.append(tenant)
            await asyncio.sleep(0.01)
            return f"answer for {tenant}"

        return work

    payload = {"job": "the same job id"}
    results = await asyncio.gather(
        *(
            coalescing.single_flight(
                coalescing.request_key(tenant_id=tenant, kind="jd", payload=payload),
                factory(tenant),
            )
            for tenant in ("t-1", "t-2")
        )
    )
    assert sorted(served) == ["t-1", "t-2"]
    assert results == ["answer for t-1", "answer for t-2"]


@pytest.mark.asyncio
async def test_a_completed_flight_does_not_serve_the_next_caller() -> None:
    """Coalescing is deduplication of CONCURRENT work only. Persistence across
    time is what the derivation cache is for, and conflating them would serve a
    stale answer to a caller that arrived a minute later."""
    ran = 0

    async def work() -> int:
        nonlocal ran
        ran += 1
        return ran

    key = coalescing.request_key(tenant_id="t-1", kind="jd", payload={})
    assert await coalescing.single_flight(key, work) == 1
    assert await coalescing.single_flight(key, work) == 2


# ── Failure travels, it does not degrade ─────────────────────────────────────


@pytest.mark.asyncio
async def test_every_follower_gets_the_leaders_exception() -> None:
    """A coalescer that swallowed the failure would hand N callers an empty
    value indistinguishable from a legitimately empty result, and every one of
    them would render it."""
    ran = 0

    async def work() -> str:
        nonlocal ran
        ran += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream is down")

    key = coalescing.request_key(tenant_id="t-1", kind="jd", payload={})
    results = await asyncio.gather(
        *(coalescing.single_flight(key, work) for _ in range(5)),
        return_exceptions=True,
    )
    assert ran == 1
    assert len(results) == 5
    for outcome in results:
        assert isinstance(outcome, RuntimeError)
        assert str(outcome) == "upstream is down"


@pytest.mark.asyncio
async def test_a_failed_flight_is_cleared_so_the_next_caller_retries() -> None:
    """A flight table that kept a failed entry would turn one outage into a
    permanent one for that key."""
    attempts = 0

    async def work() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first one fails")
        return "recovered"

    key = coalescing.request_key(tenant_id="t-1", kind="jd", payload={})
    with pytest.raises(RuntimeError):
        await coalescing.single_flight(key, work)
    assert await coalescing.single_flight(key, work) == "recovered"


# ── The key builder's two hard rules ─────────────────────────────────────────


def test_every_key_carries_the_tenant() -> None:
    key = coalescing.request_key(tenant_id="t-1", kind="claims", payload={"a": 1})
    assert "t-1" in key
    other = coalescing.request_key(tenant_id="t-2", kind="claims", payload={"a": 1})
    assert key != other


def test_a_missing_tenant_is_refused_rather_than_defaulted() -> None:
    """The failure this builder exists to make impossible. A default of "global"
    or an empty string would produce a perfectly working key that serves one
    tenant's derived data to another."""
    for absent in (None, "", "   "):
        with pytest.raises(coalescing.TenantScopeMissing):
            coalescing.request_key(tenant_id=absent, kind="claims", payload={})


def test_the_assessment_transcript_can_never_be_cached_or_coalesced() -> None:
    """W4.3 says this rule must survive the workstream that adds the cache. It
    survives by being unreachable rather than by being remembered."""
    assert "extract_assessment" in coalescing.NEVER_SHARED
    for kind in sorted(coalescing.NEVER_SHARED):
        with pytest.raises(coalescing.NeverShared):
            coalescing.request_key(tenant_id="t-1", kind=kind, payload={})


def test_the_same_request_spelled_two_ways_produces_one_key() -> None:
    """Keyed on canonical json, so a reordered dict does not quietly halve the
    hit rate."""
    left = coalescing.request_key(
        tenant_id="t-1", kind="claims", payload={"a": 1, "b": 2}
    )
    right = coalescing.request_key(
        tenant_id="t-1", kind="claims", payload={"b": 2, "a": 1}
    )
    assert left == right


# ── The derivation cache ─────────────────────────────────────────────────────


@pytest.fixture()
def _memory_cache(monkeypatch):
    """An in-memory stand-in for Redis, so these tests measure the wrapper.

    Substituted at `app.core.cache`'s own functions rather than at a client,
    because what is under test is that `cached_derivation` reads through and
    single flights, not how Redis serializes.
    """
    store: dict[str, object] = {}

    async def _get(key: str):
        return store.get(key)

    async def _set(key: str, value, ttl: int = 60) -> bool:
        store[key] = value
        return True

    monkeypatch.setattr(cache, "get", _get)
    monkeypatch.setattr(cache, "set", _set)
    return store


@pytest.mark.asyncio
async def test_a_derivation_is_computed_once_and_then_read_from_the_cache(
    _memory_cache,
) -> None:
    computed = 0

    async def compute() -> dict[str, int]:
        nonlocal computed
        computed += 1
        await asyncio.sleep(0.01)
        return {"claims": computed}

    async def call():
        return await coalescing.cached_derivation(
            tenant_id="t-1",
            kind="resume_claims",
            payload={"profile": "p-1"},
            compute=compute,
            ttl=60,
        )

    concurrent = await asyncio.gather(*(call() for _ in range(6)))
    assert computed == 1
    assert concurrent == [{"claims": 1}] * 6

    # A later caller reads the stored value rather than recomputing.
    assert await call() == {"claims": 1}
    assert computed == 1


@pytest.mark.asyncio
async def test_the_derivation_cache_stores_under_a_tenant_scoped_key(
    _memory_cache,
) -> None:
    async def compute() -> str:
        return "derived"

    await coalescing.cached_derivation(
        tenant_id="t-9",
        kind="resume_claims",
        payload={"profile": "p-1"},
        compute=compute,
        ttl=60,
    )
    assert len(_memory_cache) == 1
    assert all("t-9" in key for key in _memory_cache)
