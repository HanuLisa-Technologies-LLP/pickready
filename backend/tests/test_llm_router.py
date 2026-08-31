"""The single-vendor router: retries, budgets, the breaker, and JSON mode.

WHAT THIS FILE REPLACED, AND WHAT IT KEPT
------------------------------------------
The previous version locked down a real production outage: all nine
`llm_provider_keys` rows sat at `healthy = false` indefinitely, so
`chat_completion` skipped every key, raised on every call, and matching silently
degraded to the placeholder "AI scoring unavailable" comment forever. The fix
was half-open recovery.

That table is no longer read -- the credential comes from the environment -- so
the DB-persistence half of those tests describes a mechanism that no longer
exists. THE INVARIANT DOES NOT GO WITH IT, and it is the first thing asserted
below: a breaker with no way back is a permanent outage wearing a reliability
feature's name, and this codebase has already shipped that bug once.

The new coverage on top of it is the things a single-vendor layer can get wrong:
the system-message lift (a dropped system message changes what was asked
without changing what the caller wrote), JSON mode, the predictive deadline, and
the rule that a non-429 4xx is not retried.

RE-POINTED 2026-08-31, when the model vendor moved from Anthropic to OpenAI.
One thing genuinely went away and is asserted absent below rather than left to
rot: the assistant-turn PREFILL. JSON mode is now `response_format`, which is a
stronger guarantee of the same property, and the invariant the prefill existed
to protect -- every JSON-mode caller parses a top-level OBJECT -- is pinned in
three places instead of one.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.config import llm_providers
from app.services import llm_router
from app.services.llm_router import (
    _COOLDOWN_SECONDS,
    _FAILURE_THRESHOLD,
    LLMUnavailableError,
    _RouterKey,
)
# Bound at import time, so it is the REAL resolver rather than the stub the
# autouse fixture installs on the module attribute. One test below needs to
# exercise the resolver itself rather than the router's use of it.
from app.services.llm_router import key_for_model as _real_key_for_model


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm_router.reset_provider_stats()
    # A credential, so the router gets past the "nothing configured" guard.
    # Tests that want the unconfigured path clear it explicitly.
    monkeypatch.setattr(
        llm_router,
        "key_for_model",
        lambda model: _RouterKey(api_key="k-test", fingerprint="fp1"),
    )
    yield
    llm_router.reset_provider_stats()


def _http_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    response = httpx.Response(status, request=request, headers=headers or {})
    return httpx.HTTPStatusError("boom", request=request, response=response)


def _completion(text: str, usage: dict | None = None) -> dict:
    """The Chat Completions body shape `parse_response` reads.

    Built here rather than loaded from a fixture because these tests are about
    the parser's behaviour on shapes that are DELIBERATELY odd (a null content,
    an empty choice list). The fixture set is asserted separately in
    `test_vendor_contracts.py`, against the contract that names it.
    """
    payload: dict = {
        "id": "chatcmpl_test",
        "object": "chat.completion",
        "created": 0,
        "model": llm_providers.MODEL_LUNA,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage
        if usage is not None
        else {"prompt_tokens": 0, "completion_tokens": 0},
    }
    return payload


def _stub_calls(monkeypatch, results):
    """Replace the HTTP layer with a scripted sequence.

    `results` entries are either a string (a successful response body) or an
    exception instance (raised). Every call is recorded so a test can assert how
    many attempts were actually spent, which is the thing budgets are about.
    """
    calls: list[dict] = []

    async def _fake(client, key, model, messages, json_mode, max_tokens, temperature):
        calls.append(
            {
                "model": model,
                "messages": messages,
                "json_mode": json_mode,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "key": key.fingerprint,
            }
        )
        outcome = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm_router, "_call_openai", _fake)
    return calls


# ── The breaker, and the recovery that must never be lost ────────────────────


def test_a_success_clears_the_breaker() -> None:
    key = _RouterKey(api_key="k", fingerprint="fp1")
    for _ in range(_FAILURE_THRESHOLD):
        llm_router._record_failure(key, terminal=False)
    assert llm_router._is_cooling_down(key)

    llm_router._record_success(key)
    assert not llm_router._is_cooling_down(key)


def test_the_breaker_goes_half_open_once_the_cooldown_elapses(monkeypatch) -> None:
    """The invariant the 2026 outage was about.

    A breaker that could open and never close left every credential skipped
    forever, so the router raised on every call and the product degraded
    permanently. The cooldown must EXPIRE, and expiry must not require a
    success -- which would be unobtainable, since the key is being skipped.
    """
    key = _RouterKey(api_key="k", fingerprint="fp1")
    for _ in range(_FAILURE_THRESHOLD):
        llm_router._record_failure(key, terminal=False)
    assert llm_router._is_cooling_down(key)

    state = llm_router._state("fp1")
    state.opened_at = time.monotonic() - _COOLDOWN_SECONDS - 1
    assert not llm_router._is_cooling_down(key)


def test_a_single_transient_failure_does_not_trip_the_breaker() -> None:
    """A 429 or a 5xx clears on its own. Condemning a credential over one of
    them would take the platform off models for fifteen minutes because of a
    burst."""
    key = _RouterKey(api_key="k", fingerprint="fp1")
    llm_router._record_failure(key, terminal=False)
    assert not llm_router._is_cooling_down(key)


def test_a_credential_failure_trips_on_the_first_occurrence() -> None:
    """A revoked key is not going to become valid on the second attempt.

    Tripping immediately is what gets the caller's deterministic fallback
    running one attempt sooner rather than three.
    """
    key = _RouterKey(api_key="k", fingerprint="fp1")
    llm_router._record_failure(key, terminal=True)
    assert llm_router._is_cooling_down(key)


@pytest.mark.asyncio
async def test_a_cooling_credential_fails_fast_rather_than_calling(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, ["never reached"])
    key = _RouterKey(api_key="k-test", fingerprint="fp1")
    for _ in range(_FAILURE_THRESHOLD):
        llm_router._record_failure(key, terminal=False)

    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert calls == []


@pytest.mark.asyncio
async def test_no_credential_configured_raises_the_typed_error(monkeypatch) -> None:
    monkeypatch.setattr(llm_router, "key_for_model", lambda model: None)
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    # It must name the variable AND the model. With two keys in play, "no
    # credential configured" leaves an operator checking the wrong one, and the
    # symptom is one tier of tasks degrading while the other looks healthy.
    assert llm_providers.ENV_VAR_FOR_MODEL[llm_providers.MODEL_LUNA] in str(
        excinfo.value
    )
    assert llm_providers.MODEL_LUNA in str(excinfo.value)


@pytest.mark.asyncio
async def test_the_key_is_chosen_by_the_model_and_never_substituted(
    monkeypatch,
) -> None:
    """Two keys, one vendor. The mapping is DATA and the router reads it.

    Falling back to the other key when one is absent would run a judging call on
    the extraction credential, produce a plausible answer, and leave nothing in
    the record saying which tier it came from. So the absence raises, and the
    raise names the tier that is missing rather than the other one.
    """
    monkeypatch.setattr(
        llm_router,
        "key_for_model",
        lambda model: (
            _RouterKey(api_key="k-luna", fingerprint="fp-luna")
            if model == llm_providers.MODEL_LUNA
            else None
        ),
    )
    calls = _stub_calls(monkeypatch, ["ok"])

    # `rerank` routes to Luna, whose key is present.
    assert (
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
        == "ok"
    )
    assert calls[0]["key"] == "fp-luna"

    # `report_synthesis` routes to Terra, whose key is not. It must refuse
    # rather than reach for the credential that happens to be there.
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "report_synthesis", [{"role": "user", "content": "hi"}]
        )
    assert llm_providers.ENV_VAR_FOR_MODEL[llm_providers.MODEL_TERRA] in str(
        excinfo.value
    )
    assert len(calls) == 1, "the Terra call must not have reached the transport"


def test_the_credential_mapping_covers_every_permitted_model() -> None:
    """A model with no key mapping raises at resolution rather than at the
    endpoint, and a model in the roster with no mapping would be unreachable."""
    assert set(llm_providers.SETTINGS_ATTR_FOR_MODEL) == set(
        llm_providers.ALLOWED_MODELS
    )
    with pytest.raises(ValueError):
        _real_key_for_model("some-model-we-do-not-route-to")


@pytest.mark.asyncio
async def test_an_operator_write_off_is_reversible(monkeypatch) -> None:
    _stub_calls(monkeypatch, ["ok"])
    llm_router.trip_provider()
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    llm_router.clear_provider_breaker()
    assert await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}]) == "ok"


# ── Retries ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_transient_is_retried_and_can_succeed(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, [_http_error(503), "recovered"])
    result = await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert result == "recovered"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_non_429_client_error_is_not_retried(monkeypatch) -> None:
    """Our bug, not theirs. It fails identically on retry, so spending the
    budget only delays the caller's fallback."""
    calls = _stub_calls(monkeypatch, [_http_error(400)])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_credential_failure_is_not_retried(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, [_http_error(401)])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm("rerank", [{"role": "user", "content": "hi"}])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_the_retry_budget_bounds_the_attempts(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, [_http_error(503)])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
    assert len(calls) == llm_providers.retry_budget_for("rerank")


@pytest.mark.asyncio
async def test_the_error_message_never_quotes_a_response_body(monkeypatch) -> None:
    """A vendor error body can echo the request, and the request carries a real
    candidate's answers."""
    _stub_calls(monkeypatch, [_http_error(503)])
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "SECRET-ANSWER-TEXT"}]
        )
    assert "SECRET-ANSWER-TEXT" not in str(excinfo.value)
    assert "k-test" not in str(excinfo.value)


# ── The predictive deadline ──────────────────────────────────────────────────


def test_the_deadline_predicts_the_next_attempt() -> None:
    """`elapsed >= deadline` sounds right and is not.

    With a 20s attempt cap and a 40s budget, a first attempt that takes 20s
    leaves `20 >= 40` False, a second attempt starts, and the real worst case is
    40 seconds of waiting plus a second attempt. Checking whether the LONGEST
    observed attempt still fits is what stops an attempt that cannot finish.
    """
    ctx = llm_router._RouteContext(
        task_type="conversation_turn",
        model=llm_providers.MODEL_TERRA,
        key=_RouterKey(api_key="k", fingerprint="fp1"),
        messages=[],
        json_mode=False,
        client=None,  # type: ignore[arg-type]
        retry_budget=4,
        max_tokens=100,
        temperature=0.0,
        attempt_timeout=20.0,
        deadline=time.monotonic() + 15.0,
    )
    # Nothing observed yet: 15s of room is room.
    assert not llm_router._budget_exhausted(ctx)
    # An attempt has now been seen to take 20s. Another one cannot finish.
    ctx.longest_attempt = 20.0
    assert llm_router._budget_exhausted(ctx)


def test_a_failed_attempts_duration_counts_toward_the_prediction(monkeypatch) -> None:
    """A timeout is the slowest and most informative thing that can happen, so
    excluding failures from the estimate would exclude the worst case."""
    ctx = llm_router._RouteContext(
        task_type="rerank",
        model=llm_providers.MODEL_LUNA,
        key=_RouterKey(api_key="k", fingerprint="fp1"),
        messages=[],
        json_mode=False,
        client=None,  # type: ignore[arg-type]
        retry_budget=4,
        max_tokens=100,
        temperature=0.0,
        attempt_timeout=20.0,
        deadline=time.monotonic() + 5.0,
    )
    ctx.longest_attempt = 30.0
    assert llm_router._budget_exhausted(ctx)
    assert (
        llm_router.should_continue({"attempts": 1, "result": None, "ctx": ctx})
        == "exhausted"
    )


def test_no_deadline_means_no_budget_check() -> None:
    ctx = llm_router._RouteContext(
        task_type="rerank",
        model=llm_providers.MODEL_LUNA,
        key=_RouterKey(api_key="k", fingerprint="fp1"),
        messages=[],
        json_mode=False,
        client=None,  # type: ignore[arg-type]
        retry_budget=4,
        max_tokens=100,
        temperature=0.0,
        attempt_timeout=20.0,
        deadline=None,
    )
    ctx.longest_attempt = 9999.0
    assert not llm_router._budget_exhausted(ctx)


@pytest.mark.asyncio
async def test_a_retry_after_larger_than_the_budget_stops_the_chain(monkeypatch) -> None:
    """Honouring `retry-after` must not park an interactive request past its
    deadline. Sleeping through a budget the caller can no longer use is strictly
    worse than failing now and letting the fallback run."""
    calls = _stub_calls(monkeypatch, [_http_error(429, {"retry-after": "600"})])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "conversation_turn", [{"role": "user", "content": "hi"}]
        )
    assert len(calls) == 1


def test_retry_after_is_parsed_and_a_missing_one_is_none() -> None:
    assert llm_router.retry_after_seconds(_http_error(429, {"retry-after": "12"})) == 12.0
    assert llm_router.retry_after_seconds(_http_error(429)) is None
    assert llm_router.retry_after_seconds(_http_error(429, {"retry-after": "soon"})) is None


# ── The OpenAI-shape translation ─────────────────────────────────────────────


def test_system_messages_are_lifted_out_and_joined() -> None:
    """Several callers build a system prompt in layers -- a base instruction,
    retrieved context, an experience-memory hint. Dropping any layer would
    change what was asked without changing what the caller wrote."""
    system, turns = llm_router.split_system(
        [
            {"role": "system", "content": "base"},
            {"role": "system", "content": "context"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
    )
    assert system == "base\n\ncontext"
    assert turns == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]


def test_an_unknown_role_is_treated_as_user_rather_than_dropped() -> None:
    """Dropping it would silently remove content from the prompt. Coercing it
    keeps the content and, at worst, mislabels who said it."""
    _system, turns = llm_router.split_system(
        [{"role": "tool", "content": "observation"}]
    )
    assert turns == [{"role": "user", "content": "observation"}]


def test_a_payload_carries_the_ceiling_and_the_seed_and_never_the_two_refused_names() -> None:
    """The request shape the live API actually accepts, pinned in both directions.

    VERIFIED LIVE 2026-08-31, and both halves of this were 400s before that run:

        max_tokens  -> 400 unsupported_parameter: use max_completion_tokens
        temperature -> 400 unsupported_value: only the default (1) is supported

    The negative assertions matter more than the positive ones. Sending either
    refused name fails EVERY request, so a regression is loud rather than
    subtle, but only if something checks that they are absent. `temperature` in
    particular is the one a future contributor is most likely to re-add, because
    the standing rule that judging tasks sample at 0.0 is written down in three
    places and this vendor cannot honour it.
    """
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_TERRA,
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        json_mode=False,
        max_tokens=512,
        temperature=0.3,
    )
    assert payload["model"] == llm_providers.MODEL_TERRA
    assert payload["max_completion_tokens"] == 512
    assert "max_tokens" not in payload
    assert "temperature" not in payload
    # `seed` is what replaced temperature as the reproducibility lever. A
    # CONSTANT, because two runs over the same evidence must send the same one
    # or there is no reproducibility to have.
    assert payload["seed"] == llm_router._SEED
    # The system prompt is a MESSAGE at the head of the array, not a top-level
    # field. That is the one shape difference the vendor change forced on the
    # request, and it is the one a reviewer would otherwise have to take on
    # trust from a docstring.
    assert payload["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert "system" not in payload


def test_a_payload_with_no_system_message_carries_no_system_turn() -> None:
    """An empty system message is not the same as no system prompt, and sending
    one spends tokens on nothing."""
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_LUNA,
        messages=[{"role": "user", "content": "u"}],
        json_mode=False,
        max_tokens=64,
        temperature=0.0,
    )
    assert payload["messages"] == [{"role": "user", "content": "u"}]
    assert all(m["role"] != "system" for m in payload["messages"])


# ── JSON mode ────────────────────────────────────────────────────────────────


def test_json_mode_asks_for_the_native_format_and_says_the_required_token() -> None:
    """JSON mode is `response_format`, and the instruction is not decoration.

    Both halves are asserted because both are required for the request to be
    ACCEPTED: the published API rejects the json_object format with a 400
    unless the token "json" appears somewhere in the messages, so the system
    suffix is load bearing for the call rather than merely persuasive.
    """
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_LUNA,
        messages=[{"role": "user", "content": "u"}],
        json_mode=True,
        max_tokens=64,
        temperature=0.0,
    )
    assert payload["response_format"] == llm_providers.JSON_OBJECT_RESPONSE_FORMAT
    system = payload["messages"][0]
    assert system["role"] == "system"
    assert llm_providers.JSON_MODE_REQUIRED_TOKEN in system["content"].lower()


def test_a_non_json_call_asks_for_no_response_format() -> None:
    """The negative direction. A `response_format` sent on every call would
    make the assertion above pass for free."""
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_TERRA,
        messages=[{"role": "user", "content": "u"}],
        json_mode=False,
        max_tokens=64,
        temperature=0.0,
    )
    assert "response_format" not in payload


def test_the_prefill_machinery_is_gone_rather_than_disabled() -> None:
    """DELETED on 2026-08-31, and asserted absent rather than trusted.

    The prefill seeded a trailing assistant turn with an opening brace and
    `parse_response` prepended it back. `response_format` is a stronger
    guarantee of the same property, so keeping both would be two mechanisms for
    one behaviour -- and the leftover trailing assistant turn would still be on
    the wire, where at least one published API rejects it.
    """
    payload = llm_router.build_payload(
        model=llm_providers.MODEL_LUNA,
        messages=[{"role": "user", "content": "u"}],
        json_mode=True,
        max_tokens=64,
        temperature=0.0,
    )
    assert payload["messages"][-1]["role"] != "assistant"
    assert not hasattr(llm_router, "_JSON_PREFILL")

    # And nothing is prepended: the response IS the whole object now.
    result = llm_router.parse_response(
        _completion('{"grade": "Matching"}'), json_mode=True
    )
    assert result.content == '{"grade": "Matching"}'


def test_the_text_is_read_from_the_choice_and_json_mode_does_not_alter_it() -> None:
    assert llm_router.parse_response(_completion("hello"), json_mode=False).content == (
        "hello"
    )
    assert llm_router.parse_response(_completion("hello"), json_mode=True).content == (
        "hello"
    )


def test_no_json_mode_caller_expects_a_top_level_array() -> None:
    """Pins the invariant the prefill used to rest on, which outlived it.

    Every JSON-mode caller in this codebase parses a top-level OBJECT. The
    prefill enforced that by constraining the first character; the enforcement
    now sits in `vendor_contract.check_openai_response`, which refuses a
    JSON-mode response whose text does not open with a brace. Either way the
    assumption is the same, and a future array-returning caller should fail
    loudly HERE rather than mysteriously at its own parse.
    """
    import pathlib
    import re

    services = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    array_scanners = re.compile(r"""\.find\(\s*['"]\[['"]|startswith\(\s*['"]\[['"]""")
    offenders = [
        path.name
        for path in services.rglob("*.py")
        if array_scanners.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "These modules scan for a leading '[', which the JSON-mode prefill "
        f"makes impossible: {offenders}"
    )


def test_a_null_content_reads_as_empty_rather_than_as_the_string_none() -> None:
    """A null content is the tool-call shape, which this platform never asks
    for. Stringifying it would put "None" into a JSON parse, and into a report.

    The empty string it yields instead is not a good outcome either, which is
    exactly why `check_openai_response` refuses a null content on the first
    live response rather than leaving it to look like a quiet model.
    """
    payload = _completion("x")
    payload["choices"][0]["message"]["content"] = None
    assert llm_router.parse_response(payload, json_mode=False).content == ""


def test_a_response_with_no_choices_reads_as_empty_rather_than_raising() -> None:
    assert llm_router.parse_response({"choices": []}, json_mode=False).content == ""


# ── Accounting ───────────────────────────────────────────────────────────────


def test_usage_is_recorded_and_a_missing_one_stays_distinguishable() -> None:
    with_usage = llm_router.parse_response(
        _completion("x", usage={"prompt_tokens": 10, "completion_tokens": 4}),
        json_mode=False,
    )
    assert (with_usage.prompt_tokens, with_usage.completion_tokens) == (10, 4)
    assert with_usage.had_usage

    payload = _completion("x")
    payload.pop("usage")
    without = llm_router.parse_response(payload, json_mode=False)
    assert (without.prompt_tokens, without.completion_tokens) == (0, 0)
    assert not without.had_usage


def test_a_bare_string_from_the_call_layer_is_accepted() -> None:
    """Tests substitute string-returning stubs. Accounting is monitoring; it
    must never be the thing that fails a request."""
    assert llm_router.as_result("plain").content == "plain"
    assert llm_router.as_result(llm_router._Result(content="typed")).content == "typed"


@pytest.mark.asyncio
async def test_stats_separate_the_model_from_the_credential(monkeypatch) -> None:
    _stub_calls(monkeypatch, ["ok"])
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "a"}])
    await llm_router.invoke_llm("report_synthesis", [{"role": "user", "content": "b"}])

    models = llm_router.model_stats()
    assert models[llm_providers.MODEL_LUNA]["successes"] == 1
    assert models[llm_providers.MODEL_TERRA]["successes"] == 1
    assert llm_router.provider_stats()[llm_providers.PROVIDER]["attempts"] == 2
    assert llm_router.key_stats()["fp1"]["attempts"] == 2


@pytest.mark.asyncio
async def test_no_key_material_reaches_the_stats_surface(monkeypatch) -> None:
    _stub_calls(monkeypatch, ["ok"])
    await llm_router.invoke_llm("rerank", [{"role": "user", "content": "a"}])
    rendered = repr(llm_router.key_stats())
    assert "k-test" not in rendered
    assert "fp1" in rendered


def test_a_fingerprint_is_not_reversible_and_is_stable() -> None:
    a = llm_router._fingerprint("sk-secret")
    assert a == llm_router._fingerprint("sk-secret")
    assert a != llm_router._fingerprint("sk-other")
    assert "secret" not in a
    assert len(a) == 12


# ── The task type still validates ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unknown_task_type_raises_before_any_call(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, ["ok"])
    with pytest.raises(ValueError):
        await llm_router.invoke_llm("nonsense", [{"role": "user", "content": "hi"}])
    assert calls == []


@pytest.mark.asyncio
async def test_chat_completion_is_a_pure_alias(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, ["ok"])
    assert (
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
        == "ok"
    )
    assert calls[0]["model"] == llm_providers.MODEL_LUNA


@pytest.mark.asyncio
async def test_the_task_types_temperature_and_ceiling_reach_the_call(monkeypatch) -> None:
    calls = _stub_calls(monkeypatch, ["ok"])
    await llm_router.invoke_llm(
        "dimension_evaluation", [{"role": "user", "content": "hi"}]
    )
    assert calls[0]["temperature"] == 0.0, "a grade must not vary with when it ran"
    assert calls[0]["max_tokens"] == llm_providers.max_tokens_for("dimension_evaluation")
