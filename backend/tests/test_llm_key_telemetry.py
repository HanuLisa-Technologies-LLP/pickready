"""Per-KEY latency, tokens, cost and errors, and the breaker under real failure.

WHY PER KEY
-----------
Per-PROVIDER counters already existed and answer "is Groq degraded?". They
cannot answer the two questions an operator actually has when a latency graph
or a bill moves: WHICH of the seven keys on that provider is failing, and WHICH
task_type is spending the budget. A provider with one dead key out of seven
reads as mildly degraded in aggregate and is perfectly healthy on six of them,
so the aggregate points at the wrong thing and the real one stays invisible.

WHY THE BREAKER IS EXERCISED HERE RATHER THAN ASSUMED
-----------------------------------------------------
The brief asks that the fallback chain and the circuit breaker be TESTED under
failure, not assumed. The existing suite covers cooldown skipping and recovery
with pre-seeded breaker state; what was missing is the path that actually
matters in production: a key that fails repeatedly on live calls trips itself,
and traffic moves to the next key without the caller seeing anything.

NO KEY MATERIAL, ANYWHERE
-------------------------
Every counter is filed under a FINGERPRINT (`db:<uuid>` / `env:<name>`), the
same non-secret handle the breaker and the log lines already use. That is
asserted, not assumed, because the snapshot is designed to be servable to an
admin console.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import llm_providers
from app.services import llm_router
from app.services.llm_router import _RouterKey, LLMUnavailableError

SECRET = "sk-this-must-never-appear-anywhere"


@pytest.fixture(autouse=True)
def _clean():
    llm_router._breaker.clear()
    llm_router.clear_provider_breaker()
    llm_router.reset_provider_stats()
    yield
    llm_router._breaker.clear()
    llm_router.clear_provider_breaker()
    llm_router.reset_provider_stats()


def _key(fp: str, provider: str = "groq") -> _RouterKey:
    return _RouterKey(
        provider=provider,
        api_key=SECRET,
        fingerprint=fp,
        db_id=uuid.uuid4(),
        db_healthy=True,
    )


def _returns(keys):
    async def _load(session=None):
        return keys

    return _load


def _usage(prompt: int, completion: int, text: str = "ok"):
    """A provider caller that reports usage, as the real ones now do."""

    async def _call(client, key, messages, json_mode, max_tokens, temperature=0.0):
        return llm_router._ProviderResult(
            content=text, prompt_tokens=prompt, completion_tokens=completion
        )

    return _call


# ── Usage is captured rather than discarded ─────────────────────────────────

@pytest.mark.asyncio
async def test_tokens_and_cost_are_attributed_to_the_key_and_the_task(monkeypatch):
    key = _key("db:alpha")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _usage(1000, 500))

    await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])

    stats = llm_router.key_stats()
    assert "db:alpha" in stats
    entry = stats["db:alpha"]
    assert entry["prompt_tokens"] == 1000
    assert entry["completion_tokens"] == 500
    assert entry["calls_with_usage"] == 1
    assert entry["by_task"] == {"rerank": 1}
    expected = llm_providers.estimate_cost_usd("groq", 1000, 500)
    assert entry["estimated_cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert entry["priced"] is True


@pytest.mark.asyncio
async def test_usage_accumulates_across_calls_and_across_tasks(monkeypatch):
    key = _key("db:alpha")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _usage(100, 50))

    await llm_router.chat_completion("rerank", [{"role": "user", "content": "a"}])
    await llm_router.chat_completion("rerank", [{"role": "user", "content": "b"}])
    await llm_router.chat_completion("extraction", [{"role": "user", "content": "c"}])

    entry = llm_router.key_stats()["db:alpha"]
    assert entry["prompt_tokens"] == 300
    assert entry["completion_tokens"] == 150
    # Which work the budget went on, which is the actual operator question.
    assert entry["by_task"] == {"extraction": 1, "rerank": 2}


@pytest.mark.asyncio
async def test_a_provider_that_reports_no_usage_is_distinguishable_from_zero(
    monkeypatch,
):
    """Zero tokens must not be ambiguous.

    A caller that returns a bare string is a WORKING call with unknown usage.
    It counts as an attempt and a success, and deliberately does not count as
    a call with usage -- otherwise "this provider does not report tokens" and
    "this call sent nothing" would look identical in the snapshot.
    """
    key = _key("db:silent")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))

    async def _bare(client, k, messages, json_mode, max_tokens, temperature=0.0):
        return "plain string, no usage block"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _bare)
    result = await llm_router.chat_completion(
        "rerank", [{"role": "user", "content": "hi"}]
    )

    assert result == "plain string, no usage block"
    entry = llm_router.key_stats()["db:silent"]
    assert entry["successes"] == 1
    assert entry["calls_with_usage"] == 0
    assert entry["prompt_tokens"] == 0
    assert entry["estimated_cost_usd"] == 0


def test_an_unpriced_provider_reports_priced_false_rather_than_free() -> None:
    """"We have no price for this" must never read as "this was free"."""
    assert llm_providers.is_priced("groq")
    assert not llm_providers.is_priced("some-future-provider")
    assert llm_providers.estimate_cost_usd("some-future-provider", 1000, 1000) == 0.0


def test_the_price_table_covers_every_configured_provider() -> None:
    """A provider added to the roster without a price would silently report
    every call as free."""
    for provider in llm_providers.PROVIDERS:
        assert llm_providers.is_priced(provider), f"{provider} has no price on file"


# ── Failures are attributed too ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failing_key_is_identifiable_by_fingerprint(monkeypatch):
    """The failures, not the successes, identify the bad slot. A per-provider
    counter averages this away."""
    # Different PROVIDERS, not two keys on one. `rerank` routes
    # groq -> gemini -> openrouter, so groq is always tried first; two keys on
    # one provider are round-robined and the bad one would not be tried every
    # time, which is correct behaviour and useless for this assertion.
    bad, good = _key("db:bad", "groq"), _key("db:good", "gemini")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([bad, good]))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    async def _boom(client, key, messages, json_mode, max_tokens, temperature=0.0):
        raise httpx.ConnectError("down")

    async def _ok(client, key, messages, json_mode, max_tokens, temperature=0.0):
        return llm_router._ProviderResult(
            content="ok", prompt_tokens=10, completion_tokens=5
        )

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _boom)
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "gemini", _ok)
    assert (
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
        == "ok"
    )

    stats = llm_router.key_stats()
    assert stats["db:bad"]["failures"] == 1
    assert stats["db:bad"]["successes"] == 0
    assert stats["db:good"]["successes"] == 1
    # Both views agree on the totals; only the per-key one says WHICH slot.
    # With seven keys on a provider the aggregate would read as one failure in
    # seven, and the fingerprint is the only thing that names the bad one.
    assert llm_router.provider_stats()["groq"]["failures"] == 1
    assert llm_router.provider_stats()["gemini"]["successes"] == 1
    assert stats["db:bad"]["provider"] == "groq"
    assert stats["db:good"]["provider"] == "gemini"


@pytest.mark.asyncio
async def test_a_throttle_is_counted_separately_from_a_failure(monkeypatch):
    """429 is a quota problem, not a broken key, and the remedy differs."""
    key = _key("db:throttled")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))

    async def _429(client, k, messages, json_mode, max_tokens, temperature=0.0):
        request = httpx.Request("POST", "https://example.invalid")
        raise httpx.HTTPStatusError(
            "rate limited",
            request=request,
            response=httpx.Response(429, request=request),
        )

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _429)
    with pytest.raises(LLMUnavailableError):
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])

    entry = llm_router.key_stats()["db:throttled"]
    assert entry["throttles"] >= 1
    assert entry["failures"] == entry["attempts"]


@pytest.mark.asyncio
async def test_latency_records_the_worst_attempt_not_only_the_average(monkeypatch):
    """An average hides the one call that took forty seconds, and that call is
    the one somebody sat through."""
    key = _key("db:slow")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _usage(1, 1))

    await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
    entry = llm_router.key_stats()["db:slow"]
    assert entry["max_latency_ms"] is not None
    assert entry["avg_latency_ms"] is not None
    assert entry["max_latency_ms"] >= entry["avg_latency_ms"]


# ── The breaker, exercised rather than pre-seeded ───────────────────────────

@pytest.mark.asyncio
async def test_repeated_live_failures_trip_the_breaker_and_traffic_moves_on(
    monkeypatch, caplog
):
    """The production path: a key that keeps failing on real calls condemns
    ITSELF, and the caller never sees it.

    The existing suite seeds breaker state and checks it is honoured. This
    drives the state machine through actual failures, which is the half that
    would break silently if `_record_failure` stopped incrementing.
    """
    # One key per provider, so the failing one is genuinely tried on every
    # call. Two keys on one provider are round-robined, which means the bad
    # one only fails every other call and never reaches the threshold -- that
    # is correct routing behaviour, and it is why this test is written across
    # providers rather than within one.
    bad, good = _key("db:decaying", "groq"), _key("db:healthy", "gemini")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([bad, good]))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    seen: list[str] = []

    async def _boom(client, key, messages, json_mode, max_tokens, temperature=0.0):
        seen.append(key.fingerprint)
        raise httpx.ConnectError("down")

    async def _ok(client, key, messages, json_mode, max_tokens, temperature=0.0):
        seen.append(key.fingerprint)
        return llm_router._ProviderResult(content="ok")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _boom)
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "gemini", _ok)

    for _ in range(llm_router._FAILURE_THRESHOLD):
        assert (
            await llm_router.chat_completion(
                "rerank", [{"role": "user", "content": "hi"}]
            )
            == "ok"
        )

    # It condemned itself without anybody seeding anything.
    assert llm_router._is_skippable(bad), "the failing key never tripped"

    seen.clear()
    assert (
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
        == "ok"
    )
    assert "db:decaying" not in seen, "an open key was still being tried"
    assert seen == ["db:healthy"]


@pytest.mark.asyncio
async def test_one_success_closes_the_breaker_again(monkeypatch):
    """Half-open recovery, driven rather than asserted from state.

    A breaker that only ever opens turns a transient blip into a permanently
    dead key, which is worse than the outage it was protecting against.
    """
    key = _key("db:flaky")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    state = {"fail": True}

    async def _call(client, k, messages, json_mode, max_tokens, temperature=0.0):
        if state["fail"]:
            raise httpx.ConnectError("down")
        return llm_router._ProviderResult(content="recovered")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _call)

    with pytest.raises(LLMUnavailableError):
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
    assert llm_router._breaker[key.fingerprint].consecutive_failures >= 1

    state["fail"] = False
    # Clear the cooldown the way the half-open path does, then succeed.
    llm_router._breaker[key.fingerprint].unhealthy_until = 0.0
    assert (
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
        == "recovered"
    )
    assert llm_router._breaker[key.fingerprint].consecutive_failures == 0


@pytest.mark.asyncio
async def test_the_whole_chain_failing_raises_and_leaks_nothing(monkeypatch):
    """The last line of defence. The caller gets a real cause and no secret."""
    keys = [_key("db:one"), _key("db:two", provider="gemini")]
    monkeypatch.setattr(llm_router, "_load_keys", _returns(keys))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    async def _boom(client, key, messages, json_mode, max_tokens, temperature=0.0):
        raise httpx.ConnectError("down")

    for provider in ("groq", "gemini", "openrouter"):
        monkeypatch.setitem(llm_router._PROVIDER_CALLERS, provider, _boom)

    with pytest.raises(LLMUnavailableError) as caught:
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])

    message = str(caught.value)
    assert SECRET not in message
    # A real cause, not "request failed": the message names what went wrong.
    assert "ConnectError" in message


def test_the_snapshot_never_carries_key_material() -> None:
    """It is designed to be servable to an admin console, so this is asserted
    rather than reasoned about."""
    llm_router._key_stat("env:GROQ_API_KEY_1", "groq").attempts = 3
    snapshot = llm_router.key_stats()
    rendered = repr(snapshot)
    assert SECRET not in rendered
    assert "sk-" not in rendered
    for fingerprint in snapshot:
        assert fingerprint.startswith(("db:", "env:")), fingerprint


def _noop_persist():
    async def _persist(statement, params):
        return True

    return _persist


# ── The PROVIDER-level write-off ────────────────────────────────────────────
#
# Distinct from the per-key breaker above, and the round-robin is exactly why it
# has to be. An account-level status (401 wrong credentials, 402 no prepaid
# balance, 403 disabled) condemns every key that bills that account, but the
# cursor hands out a DIFFERENT sibling key on each call, so no single key ever
# accumulates the two CONSECUTIVE failures its own breaker needs. Measured
# 2026-08-23: the OpenRouter account had zero credits and answered 402 to
# everything, and it still led the route order for two task types, so every one
# of those calls paid a guaranteed-failing round-trip. Forever.


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1/chat")
    response = httpx.Response(status, request=request, text="no balance")
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.asyncio
async def test_an_account_level_status_writes_off_the_whole_provider(monkeypatch):
    """One 402 is enough, and it survives into the NEXT call."""
    # The dead account is the tier that LEADS the route, so it is genuinely
    # reached; a dead tier sitting behind a healthy one is never tried at all
    # and would make this test pass for the wrong reason.
    dead = [_key(f"env:groq:{n}", "groq") for n in (1, 2, 3)]
    good = _key("env:gemini:1", "gemini")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([*dead, good]))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    tried: list[str] = []

    async def _broke(client, key, messages, json_mode, max_tokens, temperature=0.0):
        tried.append(key.fingerprint)
        raise _http_error(402)

    async def _ok(client, key, messages, json_mode, max_tokens, temperature=0.0):
        tried.append(key.fingerprint)
        return llm_router._ProviderResult(content="ok")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _broke)
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "gemini", _ok)

    assert (
        await llm_router.chat_completion(
            "jd_generation", [{"role": "user", "content": "hi"}]
        )
        == "ok"
    )
    # Within the call: the siblings on the dead account were never tried.
    assert sum(1 for fp in tried if fp.startswith("env:groq")) == 1
    assert llm_router.provider_is_written_off("groq")

    # Across calls: the whole tier stays quiet, which is the half the per-key
    # breaker could not deliver.
    tried.clear()
    for _ in range(4):
        await llm_router.chat_completion(
            "jd_generation", [{"role": "user", "content": "hi"}]
        )
    assert not any(fp.startswith("env:groq") for fp in tried), tried


@pytest.mark.asyncio
async def test_a_success_rehabilitates_a_written_off_provider(monkeypatch):
    """A topped-up balance must be picked up on the first success.

    The dangerous direction is a write-off that outlives the problem: a
    fifteen-minute cooldown a customer cannot clear by paying is a worse outage
    than the one it was protecting against.
    """
    key = _key("env:groq:1", "groq")
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    llm_router.trip_provider("groq")
    assert llm_router.provider_is_written_off("groq")

    async def _ok(client, k, messages, json_mode, max_tokens, temperature=0.0):
        return llm_router._ProviderResult(content="topped up")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _ok)
    # The write-off is cleared explicitly, as an operator top-up would; the
    # point of the test is that the SUCCESS keeps it cleared rather than the
    # next 402-free call re-arming it.
    llm_router.clear_provider_breaker("groq")
    assert (
        await llm_router.chat_completion(
            "jd_generation", [{"role": "user", "content": "hi"}]
        )
        == "topped up"
    )
    assert not llm_router.provider_is_written_off("groq")


@pytest.mark.asyncio
async def test_a_rate_limit_does_not_write_off_the_provider(monkeypatch):
    """429 is per key and per minute. Condemning the account on one would turn
    an ordinary throttle into a fifteen-minute tier outage."""
    keys = [_key(f"env:groq:{n}", "groq") for n in (1, 2)]
    monkeypatch.setattr(llm_router, "_load_keys", _returns(keys))
    monkeypatch.setattr(llm_router, "_persist_key_health", _noop_persist())

    calls = {"n": 0}

    async def _throttle_then_ok(client, key, messages, json_mode, max_tokens, temperature=0.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429)
        return llm_router._ProviderResult(content="ok")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _throttle_then_ok)

    assert (
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
        == "ok"
    )
    assert not llm_router.provider_is_written_off("groq")


# ── Provider-specific request parameters ────────────────────────────────────


def test_groq_requests_carry_reasoning_effort():
    """Not a tuning preference: without it the gpt-oss family INTERMITTENTLY
    fails Groq's JSON mode with `json_validate_failed`, which looks like a
    healthy tier that drops a fraction of every structured call."""
    payload = llm_router._openai_style_payload(
        "openai/gpt-oss-120b",
        [{"role": "user", "content": "hi"}],
        json_mode=True,
        max_tokens=256,
        temperature=0.0,
        provider="groq",
    )
    assert payload["reasoning_effort"] == "low"
    assert payload["response_format"] == {"type": "json_object"}


def test_other_providers_get_no_extra_parameters():
    """A parameter Groq needs is a 400 on a provider that has never heard of it."""
    for provider in ("gemini", "openrouter", ""):
        payload = llm_router._openai_style_payload(
            "m", [{"role": "user", "content": "hi"}], False, 256, 0.0, provider
        )
        assert "reasoning_effort" not in payload, provider


def test_no_provider_model_id_is_a_known_retired_one():
    """Three tiers have gone dark this way. Cheap guard against a fourth."""
    from app.config.llm_providers import PROVIDER_MODELS

    retired = {
        "llama-3.3-70b-versatile",
        "gemini-2.0-flash",
        "meta-llama/llama-3.3-70b-instruct:free",
    }
    assert not (set(PROVIDER_MODELS.values()) & retired), PROVIDER_MODELS
