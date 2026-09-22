"""The fault layer, tested at the seams it claims to inject at.

WHAT THIS FILE IS TRYING TO CATCH
----------------------------------
Two failures, and they are not the same one.

The first is a fault that does not reverse. A fault that leaves a patch behind
turns the NEXT scenario's result into a statement about this one, and the
symptom is a failure somewhere unrelated that passes when run alone. Every
reversal assertion below compares by IDENTITY, `is`, against the object that was
there before, because a fault that restored an equal-but-different object would
pass an equality check and still have replaced something another test was
holding a reference to.

The second, and the more dangerous, is a fault that is DECORATIVE. An injector
that patches a product function to return a canned failure proves that the
fallback runs when a mock says so, which is a statement about the mock. So the
model faults below are asserted through `llm_router.invoke_llm` itself: the
response is serialized onto a real `httpx` request, `raise_for_status` really
raises it, and what is checked is the classification the REAL `classify_failure`
reached. `"rate_limit (429)"` in the router's own error text is a fact about the
product; "the patch was called" would not be.

WHY THE ROUTER TESTS PASS A TINY BUDGET
-----------------------------------------
`total_budget=_TIGHT_BUDGET` rather than the default. The 429 fixture carries
`retry-after: 3` and the router honours it, so a default budget would spend
three real seconds per rate-limit assertion waiting out a header. The tight
budget makes the router refuse the wait as exceeding what it has left, which is
the documented behaviour on that path and is also what keeps this file fast.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import llm_providers
from app.core import cache
from app.services import embeddings, llm_router, object_storage
from app.services.llm_router import LLMUnavailableError, _RouterKey
from app.services.reliability import vendor_contract
from app.workers import dispatch, registry
from harness import faults
from harness.doubles import clock as clock_module
from harness.doubles.redis import InMemoryRedis, RedisDoubleError
from harness.doubles.vendor import FixtureMissing, load_fixture

#: Short enough that the router refuses the 429 fixture's retry-after rather
#: than sleeping on it. See the module docstring.
_TIGHT_BUDGET = 0.2

#: One task type per model tier, so a fixture is chosen the way the responder
#: chooses it: from the model named in the request.
_PROSE_TASK = "report_synthesis"


@pytest.fixture(autouse=True)
def _clean_fault_registry():
    """No test may start or finish with a fault applied.

    Asserted on the way IN as well as out, so a leak is attributed to the test
    that caused it rather than to the next one to run.
    """
    faults.assert_clean()
    yield
    faults.assert_clean()


@pytest.fixture
def router_key(monkeypatch):
    """A resolvable credential, the way `tests/test_router_recovery.py` does it.

    The fault layer deliberately does not supply one: `key_for_model` answering
    None makes the router refuse before it builds a request, so without this the
    transport is never reached and a seam test would pass while proving nothing.
    """
    monkeypatch.setattr(
        llm_router,
        "key_for_model",
        lambda model: _RouterKey(api_key="k-harness", fingerprint="fp-harness"),
    )
    llm_router.reset_provider_stats()
    vendor_contract.reset_first_use()
    yield
    llm_router.reset_provider_stats()
    vendor_contract.reset_first_use()


def _messages(text: str = "Summarise the evidence.") -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


# ── Every fault applies, and puts back exactly what it found ─────────────────

#: One valid invocation of every registered fault. Kept as data so the sweep
#: below covers the registry rather than a list somebody remembered to extend,
#: and so a new fault with no entry here fails the coverage assertion.
_SAMPLES: tuple[faults.FaultSpec, ...] = (
    faults.FaultSpec("model_failure", {"kind": "rate_limited"}),
    faults.FaultSpec("embedding_failure", {"kind": "credential"}),
    faults.FaultSpec("redis_down", {}),
    faults.FaultSpec("redis_slow", {"seconds": 0.0}),
    faults.FaultSpec("object_store_failure", {"kind": "server_error"}),
    faults.FaultSpec("dispatch_failure", {}),
    faults.FaultSpec("clock_at", {"when": "2026-03-01T00:00:00+00:00"}),
    faults.FaultSpec("clock_advance", {"seconds": 3600.0}),
)


def test_the_sample_table_covers_every_registered_fault() -> None:
    """A fault nobody exercises is a fault nobody has reversed.

    This is the assertion that makes the sweep below meaningful: without it,
    adding a ninth fault and forgetting its sample would leave the reversal
    check quietly covering eight.
    """
    assert {spec.name for spec in _SAMPLES} == set(faults.registered())


def _seam_snapshot() -> dict[str, object]:
    """Every object a fault in this registry may replace."""
    snapshot: dict[str, object] = {
        "httpx.AsyncClient": httpx.AsyncClient,
        "cache._redis": cache._redis,
        "object_storage._client": object_storage._client,
        "dispatch._recorded": dispatch._recorded,
    }
    for seam in faults.CLOCK_SEAMS:
        module = importlib.import_module(seam.module)
        snapshot[f"{seam.module}.{seam.attribute}"] = getattr(module, seam.attribute)
    return snapshot


@pytest.mark.parametrize("spec", _SAMPLES, ids=[s.name for s in _SAMPLES])
def test_a_fault_applies_and_restores_the_exact_prior_object(
    spec: faults.FaultSpec,
) -> None:
    before = _seam_snapshot()

    with faults.apply([spec]) as applied:
        assert applied == (spec,)
        assert faults.active_faults() == (spec,)
        during = _seam_snapshot()

    after = _seam_snapshot()

    changed = [name for name in before if before[name] is not during[name]]
    assert changed, (
        f"{spec.name} claimed to inject a fault and replaced nothing. A fault "
        f"that patches no seam degrades nothing, and every scenario using it "
        f"would report silent survival as a pass."
    )
    for name, original in before.items():
        assert after[name] is original, f"{spec.name} did not restore {name}"
    assert faults.active_faults() == ()


def test_the_vendor_faults_restore_the_breaker_and_the_first_use_memo() -> None:
    """Product state a served response changes must not outlive the fault.

    A credential failure trips the breaker on its first occurrence and a tripped
    breaker refuses every later call in the process, so leaving it tripped would
    fail the next scenario for a reason that scenario had nothing to do with.
    """
    llm_router.reset_provider_stats()
    vendor_contract.reset_first_use()
    key = _RouterKey(api_key="k-x", fingerprint="fp-restore")
    llm_router._record_failure(key, terminal=True)

    tripped_before = llm_router._is_cooling_down(key)
    assert tripped_before is True

    with faults.model_failure("server_error"):
        llm_router._record_failure(
            _RouterKey(api_key="k-y", fingerprint="fp-inner"), terminal=True
        )
        vendor_contract._checked.add("harness/inner-path")

    assert llm_router._is_cooling_down(key) is True
    assert "fp-inner" not in llm_router._breakers
    assert "harness/inner-path" not in vendor_contract._checked

    llm_router.reset_provider_stats()


# ── Leaks and unwinding ─────────────────────────────────────────────────────


def test_assert_clean_catches_a_leaked_fault() -> None:
    """Entered by hand and not left, which is how a leak actually happens."""
    leaked = faults.redis_down()
    leaked.__enter__()
    try:
        with pytest.raises(faults.FaultLeak) as caught:
            faults.assert_clean()
        assert "redis_down" in str(caught.value)
    finally:
        leaked.__exit__(None, None, None)

    faults.assert_clean()


def test_composed_faults_unwind_in_reverse_order_when_the_body_raises() -> None:
    """The failure path is the one that matters.

    A body that raises is the normal case in a harness, not the exception: a
    scenario asserting a degradation frequently does it by letting the product
    raise. If an exception skipped an unwind, every fault would leak on exactly
    the runs that found something.
    """
    before = _seam_snapshot()

    class Detonated(RuntimeError):
        pass

    with pytest.raises(Detonated):
        with faults.apply(
            [
                faults.FaultSpec("redis_down", {}),
                faults.FaultSpec("dispatch_failure", {}),
                faults.FaultSpec("model_failure", {"kind": "unavailable"}),
            ]
        ):
            assert len(faults.active_faults()) == 3
            raise Detonated("the scenario found something")

    after = _seam_snapshot()
    for name, original in before.items():
        assert after[name] is original, f"{name} was not restored after a raise"
    faults.assert_clean()


@pytest.mark.asyncio
async def test_an_inner_fault_on_one_seam_wins_and_the_outer_one_survives_it() -> None:
    """Two faults on the same host: innermost answers, and the outer comes back.

    Asserted through a real request rather than by reading the route list, so
    what is checked is which response the transport actually served.
    """
    url = llm_providers.OPENAI_CHAT_COMPLETIONS_URL

    with faults.model_failure("server_error"):
        async with httpx.AsyncClient() as client:
            assert (await client.post(url, json={})).status_code == 500

        with faults.model_failure("rate_limited"):
            async with httpx.AsyncClient() as client:
                assert (await client.post(url, json={})).status_code == 429

        async with httpx.AsyncClient() as client:
            assert (await client.post(url, json={})).status_code == 500


@pytest.mark.asyncio
async def test_an_unrouted_host_is_not_intercepted() -> None:
    """A fault is a statement about ONE vendor.

    A transport that refused everything would turn a model fault into a network
    embargo, and a scenario that happened to call anything else would fail for a
    reason the fault does not describe. The assertion is that the request is
    attempted for real and fails on DNS, not that it was answered from a
    fixture: reaching a fixture would mean the route matched.
    """
    with faults.model_failure("credential"):
        async with httpx.AsyncClient(timeout=2.0) as client:
            with pytest.raises(httpx.HTTPError) as caught:
                await client.get("https://harness-unrouted.invalid/probe")
    assert not isinstance(caught.value, httpx.HTTPStatusError)


# ── The seam is real: the product's own classifier says so ──────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("rate_limited", "rate_limit (429)"),
        ("server_error", "provider_error (500)"),
        ("unavailable", "provider_error (503)"),
        ("credential", "credential (401)"),
        ("timeout", "timeout (ReadTimeout)"),
        ("malformed", "unclassified (VendorContractViolation)"),
    ],
)
@pytest.mark.asyncio
async def test_the_real_router_classifies_each_injected_model_failure(
    router_key, kind: str, expected: str
) -> None:
    """The test that proves the seam rather than the injector.

    `expected` is the string `_attempt` composes from `classify_failure` and the
    status, so an assertion on it is an assertion about the product's own
    classification table. If a fault were injected by patching `_call_openai`,
    none of this code would run and the same test would pass.
    """
    with faults.model_failure(kind):
        with pytest.raises(LLMUnavailableError) as caught:
            await llm_router.invoke_llm(
                _PROSE_TASK,
                _messages(),
                timeout=2.0,
                total_budget=_TIGHT_BUDGET,
            )
    assert expected in str(caught.value)


@pytest.mark.asyncio
async def test_a_credential_failure_trips_the_breaker_on_the_first_occurrence(
    router_key,
) -> None:
    """The documented asymmetry: no amount of waiting fixes a revoked key.

    Checked INSIDE the fault, because the fault restores the breaker on the way
    out. That ordering is the point: a scenario gets to assert on the
    consequence, and the next scenario does not inherit it.
    """
    key = _RouterKey(api_key="k-harness", fingerprint="fp-harness")
    with faults.model_failure("credential"):
        assert llm_router._is_cooling_down(key) is False
        with pytest.raises(LLMUnavailableError):
            await llm_router.invoke_llm(
                _PROSE_TASK, _messages(), timeout=2.0, total_budget=_TIGHT_BUDGET
            )
        assert llm_router._is_cooling_down(key) is True


@pytest.mark.asyncio
async def test_a_partial_response_is_accepted_and_arrives_truncated(
    router_key,
) -> None:
    """Truncation is NOT a failure class, and that is the finding.

    `finish_reason` of "length" is outside `REFUSAL_FINISH_REASONS`, so the
    router accepts it and the caller receives a short answer with nothing
    marking it as cut. This fault exists so a scenario can ask what each
    generative caller does with one, and the assertion here is that the fault
    reproduces the condition faithfully rather than that the product handles it.
    """
    authored = str(
        load_fixture("openai/chat_completion_terra_reasoning.json").body["choices"][0][
            "message"
        ]["content"]
    )

    with faults.model_failure("partial"):
        answer = await llm_router.invoke_llm(
            _PROSE_TASK, _messages(), timeout=2.0, total_budget=2.0
        )

    assert answer
    assert len(answer) < len(authored)
    assert authored.startswith(answer)


@pytest.mark.asyncio
async def test_an_injected_embedding_failure_reaches_the_real_client(
    monkeypatch,
) -> None:
    """With a key configured, `embed` opens a real request and raises its own
    typed error naming the exception class and never the body.

    The key has to be supplied here. Without `VOYAGE_CONTEXT_4` the module
    returns deterministic pseudo-random vectors outside production and never
    opens a connection, so the fault would have nothing to intercept and this
    test would pass against a code path nobody injected into. That branch is
    the one that hid a non-existent model id for a whole phase.
    """
    monkeypatch.setattr(
        embeddings,
        "get_settings",
        lambda: type(
            "S", (), {"voyage_context_4": "k-harness", "is_production": False}
        )(),
    )

    with faults.embedding_failure("credential"):
        with pytest.raises(embeddings.EmbeddingError) as caught:
            await embeddings.embed(["a resume line"])

    assert "HTTPStatusError" in str(caught.value)


def test_an_embedding_fault_with_no_fixture_is_refused_rather_than_invented() -> None:
    """The gap is loud, and it names the file that would close it.

    PROVENANCE.md records why there is no Voyage 500 envelope: nothing in this
    codebase parses a Voyage error body, so none was authored. A harness that
    filled one in would be authoring a vendor contract nobody agreed to.
    """
    with pytest.raises(FixtureMissing) as caught:
        with faults.embedding_failure("server_error"):
            pass

    assert "voyage/error_500_server_error.json" in str(caught.value)
    faults.assert_clean()


# ── Object store ────────────────────────────────────────────────────────────


def test_object_store_server_error_surfaces_as_the_products_own_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(object_storage, "_bucket_name", lambda: "harness-bucket")

    with faults.object_store_failure("server_error"):
        with pytest.raises(object_storage.ObjectStorageError) as caught:
            object_storage.get_bytes("some/key")

    assert "InternalError" in str(caught.value)


def test_object_store_missing_key_reads_as_absent_and_not_as_a_fault(
    monkeypatch,
) -> None:
    """A 404 on HEAD is "not there for us", which `exists` answers False to.

    The distinction matters for the project-intake deletion contract, which may
    only record an original as deleted after a HEAD confirms it is gone: a
    missing key that raised would be indistinguishable from a bucket outage, and
    the rollback would be recorded as a success.
    """
    monkeypatch.setattr(object_storage, "_bucket_name", lambda: "harness-bucket")

    with faults.object_store_failure("missing_key"):
        assert object_storage.exists("some/key") is False
        with pytest.raises(object_storage.ObjectStorageError):
            object_storage.get_bytes("some/key")


# ── Dispatch ────────────────────────────────────────────────────────────────


def test_dispatch_failure_raises_after_the_task_name_has_been_resolved() -> None:
    """The handoff fails; everything before it still runs.

    Asserted two ways, because only the pair distinguishes this fault from a
    patched-out `dispatch()`: a REGISTERED name raises `DispatchError` and
    records nothing, and an UNREGISTERED name still raises the registry's own
    refusal, which means `resolve()` ran before the fault could reach the
    handoff.
    """
    name = registry.names()[0]
    dispatch.clear_recorded()

    with faults.dispatch_failure():
        with pytest.raises(dispatch.DispatchError):
            dispatch.dispatch(name, args=[1])
        assert dispatch.recorded_names() == []

        with pytest.raises(KeyError):
            dispatch.dispatch("pickready.no_such_task_exists")

    handle = dispatch.dispatch(name, args=[1])
    assert handle.name == name
    assert dispatch.recorded_names() == [name]


def test_dispatch_failure_keeps_dispatches_recorded_before_it_was_applied() -> None:
    """Evidence a scenario may still be asserting on is not discarded."""
    name = registry.names()[0]
    dispatch.clear_recorded()
    dispatch.dispatch(name)

    with faults.dispatch_failure():
        assert dispatch.recorded_names() == [name]

    assert dispatch.recorded_names() == [name]


# ── The clock ───────────────────────────────────────────────────────────────


def test_every_named_clock_seam_exists_and_is_callable() -> None:
    """The drift guard.

    A seam that is renamed or removed leaves `clock_at` binding nothing at that
    module while still reporting that it froze the clock, which is a scenario
    reading the real date and saying otherwise. Checked here rather than at
    apply time so the failure names the rename rather than the scenario.
    """
    for seam in faults.CLOCK_SEAMS:
        module = importlib.import_module(seam.module)
        assert hasattr(module, seam.attribute), f"{seam.module}.{seam.attribute}"
        assert callable(getattr(module, seam.attribute))


def test_clock_at_freezes_every_seam_and_honours_an_explicit_instant() -> None:
    from app.services import bd_leads, swot_analysis

    frozen = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)
    caller_supplied = datetime(2019, 5, 5, tzinfo=timezone.utc)

    with faults.clock_at(frozen):
        assert swot_analysis._now() == frozen
        # A caller passing an instant is passing DATA, not asking the clock.
        assert bd_leads._now(caller_supplied) == caller_supplied
        assert bd_leads._now() == frozen
        active = faults.active_clock()
        assert active is not None and active.instant == frozen

    assert faults.active_clock() is None
    assert swot_analysis._now() != frozen


def test_clock_advance_composes_with_clock_at_and_leaves_it_where_it_was() -> None:
    from app.services import swot_analysis

    frozen = datetime(2026, 3, 1, tzinfo=timezone.utc)

    with faults.clock_at(frozen):
        assert swot_analysis._now() == frozen
        with faults.clock_advance(3600):
            assert swot_analysis._now() == frozen + timedelta(hours=1)
        assert swot_analysis._now() == frozen


def test_the_clock_double_keeps_monotonic_time_monotonic() -> None:
    """`set` moves wall time and leaves the monotonic counter alone.

    `llm_router` computes its wall clock budget as `time.monotonic() + budget`,
    so a monotonic reading that moved backwards with a date would make every
    deadline unreachable: a clock fault would arrive as a router outage and the
    scenario would report the wrong cause.
    """
    clock = clock_module.Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    started = clock.monotonic()

    clock.set(datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert clock.monotonic() == started
    assert clock.now().year == 2020

    clock.advance(timedelta(seconds=30))
    assert clock.monotonic() == pytest.approx(started + 30)

    with pytest.raises(ValueError):
        clock.advance(-1)


# ── Descriptors and replay ──────────────────────────────────────────────────


def test_a_descriptor_round_trips_through_json() -> None:
    """The manifest contract: what is recorded is what a replay applies."""
    spec = faults.FaultSpec("model_failure", {"kind": "rate_limited"})
    restored = faults.FaultSpec.from_dict(json.loads(json.dumps(spec.as_dict())))
    assert restored == spec

    with faults.apply([restored.as_dict()]) as applied:
        assert applied == (spec,)


def test_a_parameter_that_will_not_serialize_is_refused_at_construction() -> None:
    """Found when the fault is declared, not when the artifacts are flushed.

    A manifest that cannot be written is a run that cannot be replayed, and
    discovering that at flush time means discovering it after the interesting
    failure has already happened.
    """
    with pytest.raises(TypeError):
        faults.FaultSpec("clock_at", {"when": datetime.now(timezone.utc)})


def test_an_unknown_fault_name_is_refused() -> None:
    with pytest.raises(faults.UnknownFault):
        faults.FaultSpec("network_partition", {})


def test_a_parsed_scenario_request_is_applied_without_a_translation_layer() -> None:
    """`scenario.FaultRequest` goes straight into `apply`.

    The two types are separate on purpose, because `scenario.py` is parse only
    and must not import this registry. What must NOT be separate is the path a
    parameter travels: a translation layer between the parser and the injector
    is where a parameter gets dropped in silence, and the scenario would then
    run under conditions its own file does not describe.
    """
    from harness.scenario import FaultRequest

    request = FaultRequest("model_failure", {"kind": "unavailable"})

    with faults.apply([request]) as applied:
        assert applied == (faults.FaultSpec("model_failure", {"kind": "unavailable"}),)
        assert faults.active_faults()[0].as_dict() == {
            "fault": "model_failure",
            "params": {"kind": "unavailable"},
        }


def test_something_that_is_not_a_fault_at_all_is_refused() -> None:
    with pytest.raises(TypeError):
        with faults.apply(["redis_down"]):
            pass
    faults.assert_clean()


# ── The Redis double ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_redis_double_refuses_a_lua_script_it_was_not_taught() -> None:
    """Guessing is the one thing it must not do.

    A session store that answers a rotation with the wrong token is exactly the
    failure the rotation grace window exists to prevent, and it would surface as
    an intermittent logout nobody can reproduce.
    """
    double = InMemoryRedis()
    with pytest.raises(RedisDoubleError) as caught:
        await double.eval("return redis.call('GET', KEYS[1])", 1, "k")
    assert "auth_sessions" in str(caught.value)


@pytest.mark.asyncio
async def test_the_double_runs_the_products_own_session_scripts(monkeypatch) -> None:
    """`auth_sessions` driven end to end against the double.

    The scripts are recognised by their exact text, so this also pins the
    emulation to the product's current Lua: an edit to either script makes the
    double refuse, and this test fails naming it rather than the emulation
    silently outliving the script it was derived from.
    """
    from app.services import auth_sessions

    double = InMemoryRedis()
    monkeypatch.setattr(cache, "_redis", lambda: double)

    user = uuid.uuid4()
    sid = auth_sessions.new_id()
    await auth_sessions.create(sid, user, "jti-1", "token-1")

    assert await auth_sessions.validate(sid, user) is True
    assert await auth_sessions.validate(sid, uuid.uuid4()) is False

    rotated = await auth_sessions.rotate(sid, user, "jti-1", "jti-2", "token-2")
    assert rotated == "token-2"

    # A racing tab presenting the PRIOR jti inside the grace window is handed
    # the winner's token rather than being logged out.
    assert await auth_sessions.rotate(sid, user, "jti-1", "jti-3", "token-3") == (
        "token-2"
    )
    # A jti that was never current and is not the graced one gets nothing.
    assert await auth_sessions.rotate(sid, user, "jti-9", "jti-4", "token-4") is None

    await auth_sessions.revoke_all(user)
    assert await auth_sessions.validate(sid, user) is False


@pytest.mark.asyncio
async def test_the_double_drives_the_real_fixed_window_rate_limiter(
    monkeypatch,
) -> None:
    """`rate_limit` against the double, including the expiry it depends on.

    INCRBY creating a missing key at zero and NOT resetting an existing TTL are
    both load bearing here: a double that re-armed the deadline on every
    increment would make a window that never closes look correct.
    """
    from app.services import rate_limit

    clock = clock_module.Clock()
    double = InMemoryRedis(clock)
    monkeypatch.setattr(cache, "_redis", lambda: double)

    bucket = f"harness:{uuid.uuid4()}"
    for _ in range(3):
        assert (await rate_limit.check(bucket, "ip:1.2.3.4", limit=3, window=60)).allowed
    assert not (
        await rate_limit.check(bucket, "ip:1.2.3.4", limit=3, window=60)
    ).allowed

    clock.advance(61)
    assert (await rate_limit.check(bucket, "ip:1.2.3.4", limit=3, window=60)).allowed


@pytest.mark.asyncio
async def test_redis_down_makes_the_session_store_refuse_rather_than_fail_open(
    monkeypatch,
) -> None:
    """The degradation each caller declared, under one fault.

    The cache answers a MISS, because a cache that can take the API down is
    worse than no cache. The session store answers 503, because an unavailable
    session store that fell open would turn revocation into an allow. A fault
    that produced the same behaviour in both would be hiding the difference the
    scenario exists to check.
    """
    from fastapi import HTTPException

    from app.services import auth_sessions

    with faults.redis_down():
        assert await cache.get(cache.key("harness", "probe")) is None
        assert await cache.set(cache.key("harness", "probe"), {"a": 1}) is False

        with pytest.raises(HTTPException) as caught:
            await auth_sessions.validate("sid", uuid.uuid4())
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_redis_slow_adds_latency_and_changes_no_answer(monkeypatch) -> None:
    """Latency alone. A slow client that also changed answers would be two
    faults under one name, and a scenario could not say which one the product
    reacted to."""
    double = InMemoryRedis()
    monkeypatch.setattr(cache, "_redis", lambda: double)

    delay = 0.05
    probe = cache.key("harness", "slow")
    await cache.set(probe, {"value": 7}, ttl=60)

    with faults.redis_slow(delay):
        started = asyncio.get_running_loop().time()
        assert await cache.get(probe) == {"value": 7}
        elapsed = asyncio.get_running_loop().time() - started

    assert elapsed >= delay
    assert await cache.get(probe) == {"value": 7}


@pytest.mark.asyncio
async def test_the_double_and_a_real_redis_agree_on_the_commands_the_product_uses() -> None:
    """Parity, against the suite's own Redis when one is reachable.

    The double's whole value is that its answers can be substituted for Redis's,
    and the only way to know that is to ask both the same questions. Skipped
    rather than faked when the stack is not up: a parity test with one side
    missing asserts nothing, which is the same argument that deleted the SWOT
    intake parity assertion when its module went.
    """
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from app.core.config import get_settings

    client = redis_asyncio.from_url(
        get_settings().redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 -- the skip reason names it
        await client.aclose()
        pytest.skip(f"no Redis at the configured url: {type(exc).__name__}")

    double = InMemoryRedis()
    prefix = f"harness:parity:{uuid.uuid4()}"
    hash_key = f"{prefix}:h"
    set_key = f"{prefix}:s"
    counter = f"{prefix}:c"
    string = f"{prefix}:v"

    async def both(command: str, *args: object, **kwargs: object) -> tuple[object, object]:
        real = await getattr(client, command)(*args, **kwargs)
        fake = await getattr(double, command)(*args, **kwargs)
        return real, fake

    try:
        checks: list[tuple[str, object, object]] = []

        for command, args, kwargs in [
            ("set", (string, "one"), {}),
            ("get", (string,), {}),
            ("set", (string, "two"), {"nx": True}),
            ("get", (string,), {}),
            ("incr", (counter,), {}),
            ("incrby", (counter, 4), {}),
            ("get", (counter,), {}),
            ("expire", (counter, 60), {}),
            ("exists", (counter,), {}),
            ("hset", (hash_key,), {"mapping": {"user": "u1", "jti": "j1"}}),
            ("hget", (hash_key, "user"), {}),
            ("hget", (hash_key, "absent"), {}),
            ("sadd", (set_key, "a", "b"), {}),
            ("smembers", (set_key,), {}),
            ("srem", (set_key, "a"), {}),
            ("smembers", (set_key,), {}),
            ("delete", (string,), {}),
            ("get", (string,), {}),
            ("exists", (string,), {}),
        ]:
            real, fake = await both(command, *args, **kwargs)
            checks.append((f"{command}{args}{kwargs}", real, fake))

        for label, real, fake in checks:
            assert real == fake, f"{label}: real Redis {real!r}, double {fake!r}"

        # TTL is compared as a BAND rather than a value: the real server's clock
        # runs while this test does, so an equality assertion here would be a
        # race with nothing to do with fidelity.
        real_ttl = await client.ttl(counter)
        fake_ttl = await double.ttl(counter)
        assert 55 <= real_ttl <= 60
        assert 55 <= fake_ttl <= 60
        assert await client.ttl(f"{prefix}:never-written") == await double.ttl(
            f"{prefix}:never-written"
        )
    finally:
        await client.delete(hash_key, set_key, counter, string)
        await client.aclose()
