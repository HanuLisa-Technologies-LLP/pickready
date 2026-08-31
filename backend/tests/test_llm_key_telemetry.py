"""Cost, latency and health telemetry, and the boundary it must never cross.

WHY THIS FILE STILL EXISTS AFTER THE VENDOR CONSOLIDATION
-----------------------------------------------------------
Its original question was "which of the seven keys on this provider is failing",
and with one credential that question is gone. Two of its questions are not, and
they are the ones that were always about safety rather than about routing:

  * NO KEY MATERIAL CROSSES THE TELEMETRY BOUNDARY. `/admin/llm/stats` is an
    owner-only endpoint whose whole job is to render counters, and a counter
    filed under a raw key would put credentials into a JSON response and a
    browser's history. Everything is filed under a fingerprint.
  * A ZERO IS NEVER AMBIGUOUS. A response that omits usage must be
    distinguishable from a call that genuinely consumed nothing, or an operator
    reading "0 tokens" cannot tell "nothing was sent" from "nothing was
    reported".

The third question is new, and it is the one the consolidation created: with two
models at a threefold price difference, "which MODEL is spending the budget" is
now the breakdown the old per-provider table used to give.

The vendor changed on 2026-08-31 and none of the three questions moved. What did
move is that there are now TWO credentials rather than one, so the per-key
breakdown is once again an axis that varies -- and it is still filed by
fingerprint, never by key material.
"""
from __future__ import annotations

import httpx
import pytest

from app.config import llm_providers
from app.services import llm_router
from app.services.llm_router import _RouterKey


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm_router.reset_provider_stats()
    monkeypatch.setattr(
        llm_router,
        "key_for_model",
        lambda model: _RouterKey(
            api_key="sk-do-not-log-me", fingerprint="fp-alpha"
        ),
    )
    yield
    llm_router.reset_provider_stats()


def _usage(prompt: int, completion: int, text: str = "ok"):
    async def _call(client, key, model, messages, json_mode, max_tokens, temperature):
        return llm_router._Result(
            content=text,
            prompt_tokens=prompt,
            completion_tokens=completion,
            had_usage=True,
        )

    return _call


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    return httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(status, request=request)
    )


# ── Attribution ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tokens_and_cost_are_attributed_to_the_model_and_the_credential(
    monkeypatch,
) -> None:
    monkeypatch.setattr(llm_router, "_call_openai", _usage(1000, 200))
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])

    key_entry = llm_router.key_stats()["fp-alpha"]
    assert key_entry["prompt_tokens"] == 1000
    assert key_entry["completion_tokens"] == 200
    assert key_entry["model"] == llm_providers.MODEL_LUNA
    assert key_entry["estimated_cost_usd"] > 0

    model_entry = llm_router.model_stats()[llm_providers.MODEL_LUNA]
    assert model_entry["prompt_tokens"] == 1000


@pytest.mark.asyncio
async def test_usage_accumulates_across_calls_and_models(monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "_call_openai", _usage(100, 50))
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "a"}])
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "b"}])
    await llm_router.invoke_llm("report_synthesis", [{"role": "user", "content": "c"}])

    models = llm_router.model_stats()
    assert models[llm_providers.MODEL_LUNA]["prompt_tokens"] == 200
    assert models[llm_providers.MODEL_TERRA]["prompt_tokens"] == 100
    # The credential served all three: it is the axis that no longer varies.
    assert llm_router.key_stats()["fp-alpha"]["prompt_tokens"] == 300


@pytest.mark.asyncio
async def test_a_response_with_no_usage_is_distinguishable_from_zero(
    monkeypatch,
) -> None:
    """`calls_with_usage` is what disambiguates the zero.

    Without it, an operator reading "0 prompt tokens" cannot tell whether
    nothing was sent or whether nothing was reported, and those have completely
    different follow-ups.
    """
    async def _silent(client, key, model, messages, json_mode, max_tokens, temperature):
        return llm_router._Result(content="ok")

    monkeypatch.setattr(llm_router, "_call_openai", _silent)
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])

    entry = llm_router.key_stats()["fp-alpha"]
    assert entry["successes"] == 1
    assert entry["prompt_tokens"] == 0
    assert entry["calls_with_usage"] == 0


@pytest.mark.asyncio
async def test_a_bare_string_response_still_counts_as_a_success(monkeypatch) -> None:
    """Accounting is monitoring. It must never be the thing that fails a
    request, so an un-instrumented call layer under-reports rather than
    raising."""
    async def _plain(client, key, model, messages, json_mode, max_tokens, temperature):
        return "just text"

    monkeypatch.setattr(llm_router, "_call_openai", _plain)
    assert (
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
        == "just text"
    )
    assert llm_router.key_stats()["fp-alpha"]["successes"] == 1


# ── Pricing ──────────────────────────────────────────────────────────────────


def test_an_unpriced_model_reports_priced_false_rather_than_free() -> None:
    """"We have no price for this" must never silently read as "this was
    free"."""
    assert llm_providers.estimate_cost_usd("mystery-model", 10_000, 10_000) == 0.0
    assert not llm_providers.is_priced("mystery-model")


def test_the_price_table_covers_every_permitted_model() -> None:
    for model in llm_providers.ALLOWED_MODELS:
        assert llm_providers.is_priced(model), model


# ── Failures ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failure_is_identifiable_by_fingerprint(monkeypatch) -> None:
    async def _fail(client, key, model, messages, json_mode, max_tokens, temperature):
        raise _http_error(500)

    monkeypatch.setattr(llm_router, "_call_openai", _fail)
    with pytest.raises(llm_router.LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])

    entry = llm_router.key_stats()["fp-alpha"]
    assert entry["failures"] == llm_providers.retry_budget_for("rerank")
    assert entry["successes"] == 0


@pytest.mark.asyncio
async def test_latency_is_recorded_for_failed_attempts_too(monkeypatch) -> None:
    """A timeout is the slowest thing that can happen, so excluding it from the
    latency record would make the average describe only the good days."""
    async def _fail(client, key, model, messages, json_mode, max_tokens, temperature):
        raise _http_error(503)

    monkeypatch.setattr(llm_router, "_call_openai", _fail)
    with pytest.raises(llm_router.LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])

    entry = llm_router.provider_stats()[llm_providers.PROVIDER]
    assert entry["attempts"] >= 1
    assert entry["avg_latency_ms"] >= 0.0


@pytest.mark.asyncio
async def test_one_success_closes_the_breaker_again(monkeypatch) -> None:
    calls = {"n": 0}

    async def _flaky(client, key, model, messages, json_mode, max_tokens, temperature):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return llm_router._Result(content="recovered", had_usage=True)

    monkeypatch.setattr(llm_router, "_call_openai", _flaky)
    assert (
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
        == "recovered"
    )
    key = _RouterKey(api_key="sk-do-not-log-me", fingerprint="fp-alpha")
    assert not llm_router._is_cooling_down(key)


# ── The boundary ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_snapshot_ever_carries_key_material(monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "_call_openai", _usage(10, 10))
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])

    for snapshot in (
        llm_router.key_stats(),
        llm_router.model_stats(),
        llm_router.provider_stats(),
    ):
        assert "sk-do-not-log-me" not in repr(snapshot)


def test_the_configured_key_count_reports_presence_not_the_key() -> None:
    counts = llm_providers.configured_key_count()
    assert set(counts) == {llm_providers.PROVIDER}
    # Zero, one or two: the two model credentials are independent, and one of
    # two is a state an operator has to be able to see. Everything on the
    # unconfigured tier raises and degrades while the other tier looks healthy.
    assert counts[llm_providers.PROVIDER] in (0, 1, 2)
    assert "sk-" not in repr(counts)


def test_the_per_model_view_says_which_credential_is_missing() -> None:
    """A count of one is ambiguous and the ambiguity is operationally
    expensive: which half of the product is down depends on which key it is."""
    models = llm_providers.configured_models()
    assert set(models) == set(llm_providers.ALLOWED_MODELS)
    assert all(isinstance(present, bool) for present in models.values())
    assert "sk-" not in repr(models)
