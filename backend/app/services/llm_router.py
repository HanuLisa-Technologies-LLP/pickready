"""The single-vendor LLM router: every model call in ReadyPick goes through here.

Anthropic Messages API, two model ids, one credential (spec-doc5 Part B). A
compiled LangGraph state machine drives the retry loop, exactly as it did in the
multi-provider era -- `claude.md` rule 9 documents that state machine as an
architectural decision, and consolidating vendors is not a reason to quietly
drop it. What consolidating vendors DID remove is the reason the loop was
complicated: there is no longer a fallback chain to walk, no capacity registry
to consult, and no quota domain to discover. An attempt is now only ever worth
making against a transient.

WHAT THIS MODULE IS RESPONSIBLE FOR
------------------------------------
    resolve credential -> bound the attempt -> call -> classify a failure ->
    back off -> retry within budget -> account for what it cost -> trace it.

It RAISES on final failure. That is the same split `services/tools.execute`
keeps and it is deliberate: a router that swallowed an outage would hand its
caller an empty string indistinguishable from a model that legitimately had
nothing to say, and the caller would render it. `agent_loop.run_loop` and every
direct caller own the user-visible degradation; this module owns only the
question of whether the vendor answered.

WHY httpx AND NOT THE ANTHROPIC SDK
------------------------------------
The SDK is good and this is not a criticism of it. The reason is that the SDK
runs its own retry policy, its own timeout handling and its own backoff, and
this module already owns all three against a per-task budget the SDK cannot see.
Two retry loops nested inside each other produce a worst case that is the
PRODUCT of their budgets, not the sum, and neither one can be reasoned about
from the other's side -- which is precisely the failure this file's
`TASK_TOTAL_BUDGET` exists to prevent. One HTTP call per attempt, bounded by one
timeout, is a thing a reviewer can check. `httpx` was also already the transport
here, so this is continuity rather than a new dependency.

JSON MODE
---------
The Anthropic Messages API has no `response_format: json_object`. Structured
output is obtained by two mechanisms used together:

  1. an explicit system instruction to emit one raw JSON object and nothing
     else, and
  2. PREFILLING the assistant turn with a single `{`, which the API then
     continues. The prefill is re-prepended to the response before it is
     returned, so callers see exactly the shape they always saw.

The prefill is what makes this a structural guarantee rather than a request: the
response physically cannot open with an apology or a markdown fence, because the
first character was not the model's to choose. Every JSON-mode caller in this
codebase parses a top-level OBJECT (verified: no caller scans for a leading
`[`), so constraining to `{` is correct for all of them, and
`tests/test_llm_router.py` pins that assumption so a future array-returning
caller fails loudly here instead of mysteriously there.

DEADLINES PREDICT, THEY DO NOT MERELY OBSERVE
----------------------------------------------
`elapsed >= deadline` sounds right and is not, for the same reason it was wrong
in `agent_loop`: one `conversation_turn` attempt is bounded at 20s and the total
budget is 40s, so after a slow first attempt `20 >= 40` is False, a second
attempt starts, and the real worst case is 40 seconds of waiting plus whatever
the second attempt takes. The check is
`elapsed + longest_attempt_so_far >= deadline`, so an attempt that cannot FINISH
inside the budget is never started. A failed attempt's duration counts, because
a timeout is the slowest and most informative thing that can happen.

CIRCUIT BREAKER
---------------
Consecutive failures trip the credential for a cooldown; any success clears it.
Kept, with one change of emphasis that matters more with a single vendor than it
did with three: a credential failure (401/403) trips IMMEDIATELY rather than
after two, because no amount of waiting fixes a revoked key and the honest
answer to the caller is available on the first attempt. A 429 or a 5xx does not
trip on a single occurrence, because those clear on their own.

Half-open recovery is unchanged and is not optional. This product has already
paid for getting it wrong once: a persisted `healthy = false` with no expiry left
every credential permanently skipped, the router raised on every call, and
matching degraded to a placeholder comment forever.

SECURITY: the API key is never logged and never included in an exception
message. `_fingerprint` is a truncated hash, which is enough to correlate two
log lines and not enough to reconstruct anything.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from app.config.llm_providers import (
    ANTHROPIC_API_VERSION,
    ANTHROPIC_MESSAGES_URL,
    CREDENTIAL_STATUSES,
    PROVIDER,
    backoff_seconds,
    classify_status,
    estimate_cost_usd,
    is_priced,
    is_retryable_status,
    max_tokens_for,
    model_for,
    retry_budget_for,
    temperature_for,
    timeout_for,
    total_budget_for,
)
from app.services import tracing

_FAILURE_THRESHOLD = 2          # consecutive transient failures before tripping
_COOLDOWN_SECONDS = 15 * 60     # 15 min cool-off (claude.md rule 9)
_MIN_SUCCESS_RATE = 0.5         # below this, the vendor is logged as degraded
_STATS_ALARM_MIN_ATTEMPTS = 5   # don't alarm on a single unlucky call

#: The instruction half of JSON mode. The prefill is the other half; neither is
#: sufficient alone. Deliberately terse: a long instruction competes with the
#: caller's own system prompt for attention, and the prefill is what actually
#: enforces the shape.
_JSON_SYSTEM_SUFFIX = (
    "Respond with exactly one raw JSON object and nothing else. "
    "No prose before or after it, and no markdown code fences."
)

#: What the assistant turn is seeded with in JSON mode, and what is prepended
#: back onto the response. Both halves must stay in step, so they are one
#: constant read twice rather than two literals.
_JSON_PREFILL = "{"

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when the vendor could not serve the call within its budget.

    Callers catch this and fall back to their own DETERMINISTIC behaviour. It is
    never allowed to reach a user as a 500: an outage should cost the product
    its adaptivity, not its availability.
    """


# ── Credential ───────────────────────────────────────────────────────────────


def _fingerprint(api_key: str) -> str:
    """A stable, non-reversible label for one credential.

    Twelve hex characters of SHA-256. Enough to correlate a failure in one log
    line with a success in another; nowhere near enough to reconstruct the key.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class _RouterKey:
    api_key: str
    fingerprint: str
    source: str = "env"


def _load_key() -> _RouterKey | None:
    """The one credential, from the environment.

    THE `llm_provider_keys` TABLE IS NO LONGER READ, and it was deliberately not
    dropped in the same change that stopped reading it. It holds encrypted rows
    for three retired vendors, an audit trail of which credential served which
    call is still attached to it through the telemetry, and a rollback of this
    consolidation would need those rows intact rather than restored from a
    backup. It is unread, not gone -- the same treatment `technical_questions`
    got for the same reason.
    """
    from app.core.config import get_settings  # noqa: PLC0415 -- import cycle

    api_key = (get_settings().anthropic_api_key or "").strip()
    if not api_key:
        return None
    return _RouterKey(api_key=api_key, fingerprint=_fingerprint(api_key))


# ── Circuit breaker ──────────────────────────────────────────────────────────


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None


_breakers: dict[str, _BreakerState] = {}
#: Set by `trip_provider`. A vendor-level write-off, separate from the
#: credential breaker, so an operator can take the platform off models without
#: touching a credential.
_provider_written_off_at: float | None = None


def _state(fingerprint: str) -> _BreakerState:
    state = _breakers.get(fingerprint)
    if state is None:
        state = _BreakerState()
        _breakers[fingerprint] = state
    return state


def trip_provider(provider: str | None = None) -> None:
    """Take the vendor out of service for the cooldown. Operator action."""
    global _provider_written_off_at
    _provider_written_off_at = time.monotonic()


def provider_is_written_off(provider: str | None = None) -> bool:
    if _provider_written_off_at is None:
        return False
    return (time.monotonic() - _provider_written_off_at) < _COOLDOWN_SECONDS


def clear_provider_breaker(provider: str | None = None) -> None:
    global _provider_written_off_at
    _provider_written_off_at = None
    _breakers.clear()


def _is_cooling_down(key: _RouterKey) -> bool:
    """True while the credential is inside its cooldown.

    Once the cooldown elapses the credential goes HALF-OPEN and is retried,
    regardless of how many failures preceded it. That is the property that must
    never be lost: a breaker with no way back is a permanent outage wearing a
    reliability feature's name.
    """
    state = _state(key.fingerprint)
    if state.opened_at is None:
        return False
    return (time.monotonic() - state.opened_at) < _COOLDOWN_SECONDS


# ── Accounting ───────────────────────────────────────────────────────────────


@dataclass
class _Stats:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    calls_with_usage: int = 0

    def as_dict(self) -> dict[str, Any]:
        avg = self.total_latency_ms / self.attempts if self.attempts else 0.0
        rate = self.successes / self.attempts if self.attempts else 0.0
        return {
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(rate, 3),
            "avg_latency_ms": round(avg, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "calls_with_usage": self.calls_with_usage,
        }


_provider_stat = _Stats()
_model_stats: dict[str, _Stats] = {}
_key_stats: dict[str, _Stats] = {}
#: Which model a fingerprint most recently served, so `key_stats` can name it
#: without the caller having to join two views.
_key_models: dict[str, str] = {}


def _model_stat(model: str) -> _Stats:
    stat = _model_stats.get(model)
    if stat is None:
        stat = _Stats()
        _model_stats[model] = stat
    return stat


def _key_stat(fingerprint: str) -> _Stats:
    stat = _key_stats.get(fingerprint)
    if stat is None:
        stat = _Stats()
        _key_stats[fingerprint] = stat
    return stat


def provider_stats() -> dict[str, dict[str, Any]]:
    """Vendor-level counters, for the admin health endpoint.

    Still keyed by provider even though there is exactly one. The endpoint's
    question -- "is the platform able to call a model, and how well" -- did not
    change, and a shape change would have rippled into the UI for no gain.
    """
    return {PROVIDER: _provider_stat.as_dict()}


def model_stats() -> dict[str, dict[str, Any]]:
    """Per-model counters. The axis that actually varies now.

    Sonnet and Haiku have different latency profiles and a 3x price difference,
    so "which model is spending the budget" is the operator question the old
    per-provider breakdown used to answer.
    """
    return {model: stat.as_dict() for model, stat in sorted(_model_stats.items())}


def key_stats() -> dict[str, dict[str, Any]]:
    """Per-credential counters, keyed by fingerprint. Never by key material."""
    return {
        fp: {**stat.as_dict(), "model": _key_models.get(fp), "provider": PROVIDER}
        for fp, stat in sorted(_key_stats.items())
    }


def reset_provider_stats() -> None:
    """Test and operator hook: clear every counter and every breaker."""
    global _provider_stat
    _provider_stat = _Stats()
    _model_stats.clear()
    _key_stats.clear()
    _key_models.clear()
    _breakers.clear()
    clear_provider_breaker()


def _record(
    *,
    fingerprint: str,
    model: str,
    ok: bool,
    latency_ms: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    had_usage: bool = False,
) -> None:
    cost = estimate_cost_usd(model, prompt_tokens, completion_tokens)
    for stat in (_provider_stat, _model_stat(model), _key_stat(fingerprint)):
        stat.attempts += 1
        stat.total_latency_ms += latency_ms
        if ok:
            stat.successes += 1
        else:
            stat.failures += 1
        stat.prompt_tokens += prompt_tokens
        stat.completion_tokens += completion_tokens
        stat.estimated_cost_usd += cost
        if had_usage:
            stat.calls_with_usage += 1
    _key_models[fingerprint] = model

    if (
        _provider_stat.attempts >= _STATS_ALARM_MIN_ATTEMPTS
        and _provider_stat.successes / _provider_stat.attempts < _MIN_SUCCESS_RATE
    ):
        logger.warning(
            "llm_router.degraded provider=%s success_rate=%.2f attempts=%d",
            PROVIDER,
            _provider_stat.successes / _provider_stat.attempts,
            _provider_stat.attempts,
        )


def _record_failure(key: _RouterKey, *, terminal: bool) -> None:
    """Advance the breaker.

    `terminal` means the vendor said the credential itself is unusable. That
    trips on the FIRST occurrence: waiting fifteen minutes to re-discover that a
    revoked key is still revoked helps nobody, and the caller's deterministic
    fallback should take over immediately.
    """
    state = _state(key.fingerprint)
    state.consecutive_failures += 1
    if terminal or state.consecutive_failures >= _FAILURE_THRESHOLD:
        state.opened_at = time.monotonic()
        logger.warning(
            "llm_router.breaker_open key=%s consecutive_failures=%d terminal=%s",
            key.fingerprint,
            state.consecutive_failures,
            terminal,
        )


def _record_success(key: _RouterKey) -> None:
    state = _state(key.fingerprint)
    state.consecutive_failures = 0
    state.opened_at = None


# ── Failure classification ───────────────────────────────────────────────────


def status_of(exc: Exception) -> int | None:
    """The HTTP status behind an exception, or None if it was not an HTTP error."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def is_account_level_failure(exc: Exception) -> bool:
    """A credential or permission problem: 401 or 403.

    Retained under its original name because callers and tests import it. What
    it no longer includes is 402 -- that was OpenRouter's "this prepaid balance
    cannot cover the request you priced", which has no analogue on a paid
    Anthropic account and whose adaptive-max_tokens remedy went with it.
    """
    status = status_of(exc)
    return status is not None and status in CREDENTIAL_STATUSES


def is_retryable(exc: Exception) -> bool:
    """True for a transient: 429, any 5xx, a timeout, or a transport error.

    A non-429 4xx is OUR bug -- a malformed request, an unknown model id, a
    message list the API refuses -- and it will fail identically on retry, so
    spending the budget on it delays the caller's fallback for nothing.
    """
    status = status_of(exc)
    if status is not None:
        return is_retryable_status(status)
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError))


def retry_after_seconds(exc: Exception) -> float | None:
    """The vendor's own `retry-after`, when it sent one.

    Strictly better information than any local backoff curve, and honouring it
    is the difference between backing off and guessing. Bounded by the caller's
    remaining wall-clock budget, never trusted blindly -- a large `retry-after`
    must not park an interactive request past its deadline.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


# ── The Anthropic call ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Result:
    """One response: the text, and what it cost in tokens.

    Counts default to 0 rather than None so accounting never has to branch. A
    response that omits usage therefore under-reports rather than crashing, and
    `calls_with_usage` records how often that happened, so a zero can be read as
    "nothing was sent" rather than being ambiguous with "nothing was reported".
    """

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    had_usage: bool = False


def as_result(value: "_Result | str") -> _Result:
    """Normalise whatever the call layer returned.

    A bare string is accepted and wrapped, because tests substitute simple
    string-returning stubs for `_call_anthropic` and a change to how the router
    ACCOUNTS should not require rewriting every routing test. Accounting is
    monitoring; it must never be the thing that fails a request.
    """
    if isinstance(value, _Result):
        return value
    return _Result(content=str(value))


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Separate system text from the conversation turns.

    The Messages API takes the system prompt as a TOP-LEVEL parameter, not as a
    message with `role: "system"`. Every caller in this codebase writes the
    OpenAI shape, and rewriting twenty-odd call sites to a vendor's transport
    detail would push a vendor concern out into the services. It is translated
    here, once, which is what a router is for.

    Multiple system messages are joined rather than dropped: several callers
    build a system prompt in layers (a base instruction plus retrieved context
    plus an experience-memory hint), and losing any layer would silently change
    what was asked without changing what the caller wrote.
    """
    system_parts = [
        str(m.get("content") or "")
        for m in messages
        if m.get("role") == "system" and m.get("content")
    ]
    turns = [
        {"role": "assistant" if m.get("role") == "assistant" else "user",
         "content": str(m.get("content") or "")}
        for m in messages
        if m.get("role") != "system" and m.get("content")
    ]
    return "\n\n".join(system_parts), turns


def build_payload(
    *,
    model: str,
    messages: list[dict],
    json_mode: bool,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """The request body, built once so the shape is reviewable in one place."""
    system, turns = split_system(messages)
    if json_mode:
        system = f"{system}\n\n{_JSON_SYSTEM_SUFFIX}".strip()
        # The prefill. See the module docstring: this is the half that makes
        # JSON mode structural rather than advisory.
        turns = turns + [{"role": "assistant", "content": _JSON_PREFILL}]
    payload: dict[str, Any] = {
        "model": model,
        "messages": turns,
        "max_tokens": max_tokens,
        # From config/llm_providers.temperature_for(task_type), never a
        # literal. A scoring call sampling above zero makes a candidate's grade
        # depend on when they were scored.
        "temperature": temperature,
    }
    if system:
        payload["system"] = system
    return payload


def parse_response(payload: dict, *, json_mode: bool) -> _Result:
    """Turn a Messages API response into text plus usage.

    In JSON mode the prefill is prepended back, so the caller receives the whole
    object it would have received from an endpoint with a native JSON mode. The
    caller never learns the prefill happened, which is the point: the mechanism
    is this module's business.
    """
    blocks = payload.get("content") or []
    text = "".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if json_mode:
        text = _JSON_PREFILL + text
    usage = payload.get("usage") or {}
    had_usage = bool(usage)
    return _Result(
        content=text,
        prompt_tokens=int(usage.get("input_tokens") or 0),
        completion_tokens=int(usage.get("output_tokens") or 0),
        had_usage=had_usage,
    )


async def _call_anthropic(
    client: httpx.AsyncClient,
    key: _RouterKey,
    model: str,
    messages: list[dict],
    json_mode: bool,
    max_tokens: int,
    temperature: float,
) -> _Result:
    resp = await client.post(
        ANTHROPIC_MESSAGES_URL,
        headers={
            # The key travels in a HEADER, never a query string. As a query
            # parameter it lands in httpx's own INFO log line and from there
            # into the platform's log sink in plain text, which is how this
            # module's "keys are never logged" guarantee was broken once before
            # on a different vendor.
            "x-api-key": key.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        },
        json=build_payload(
            model=model,
            messages=messages,
            json_mode=json_mode,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    resp.raise_for_status()
    return parse_response(resp.json(), json_mode=json_mode)


# ── The LangGraph state machine ──────────────────────────────────────────────


@dataclass
class _RouteContext:
    """Everything a node needs that is NOT graph state.

    Held in one opaque slot on the state dict. LangGraph never inspects or
    serialises it (this graph runs without a checkpointer), so an httpx client
    is safe to carry here.
    """

    task_type: str
    model: str
    key: _RouterKey
    messages: list[dict]
    json_mode: bool
    client: httpx.AsyncClient
    retry_budget: int
    max_tokens: int
    temperature: float
    attempt_timeout: float
    deadline: float | None
    errors: list[str] = field(default_factory=list)
    #: The longest attempt seen so far, in seconds. This is what makes the
    #: deadline PREDICTIVE rather than merely observational -- see the module
    #: docstring.
    longest_attempt: float = 0.0


class RouterState(TypedDict, total=False):
    task_type: str
    attempts: int
    result: str | None
    error: str | None
    ctx: _RouteContext


def _budget_exhausted(ctx: _RouteContext) -> bool:
    """True when the NEXT attempt could not finish inside the budget.

    Note `longest_attempt` rather than a mean: the question is whether there is
    room for the worst case we have actually observed, and a timeout -- the
    slowest thing that can happen -- is exactly the observation that should stop
    another attempt from starting.
    """
    if ctx.deadline is None:
        return False
    remaining = ctx.deadline - time.monotonic()
    return remaining <= 0 or remaining < ctx.longest_attempt


def should_continue(state: RouterState) -> str:
    if state.get("result") is not None:
        return "succeeded"
    ctx = state["ctx"]
    if state.get("attempts", 0) >= ctx.retry_budget:
        return "exhausted"
    if state.get("error") == "__terminal__":
        return "exhausted"
    if _budget_exhausted(ctx):
        ctx.errors.append("wall-clock budget exhausted before the next attempt")
        return "exhausted"
    return "retry"


async def _attempt(state: RouterState) -> dict:
    ctx = state["ctx"]
    attempts = state.get("attempts", 0) + 1

    delay = backoff_seconds(attempts)
    if delay and ctx.deadline is not None:
        # Never sleep past the deadline: an interactive caller waiting out a
        # backoff it can no longer use is strictly worse than failing now.
        delay = min(delay, max(0.0, ctx.deadline - time.monotonic()))
    if delay:
        await asyncio.sleep(delay)

    started = time.monotonic()
    try:
        raw = await _call_anthropic(
            ctx.client,
            ctx.key,
            ctx.model,
            ctx.messages,
            ctx.json_mode,
            ctx.max_tokens,
            ctx.temperature,
        )
    except Exception as exc:  # noqa: BLE001 -- classified immediately below
        elapsed = time.monotonic() - started
        ctx.longest_attempt = max(ctx.longest_attempt, elapsed)
        terminal_credential = is_account_level_failure(exc)
        retryable = is_retryable(exc)
        status = status_of(exc)
        kind = classify_status(status) if status is not None else "transport"
        _record(
            fingerprint=ctx.key.fingerprint,
            model=ctx.model,
            ok=False,
            latency_ms=elapsed * 1000,
            )
        _record_failure(ctx.key, terminal=terminal_credential)
        # The message names the classification and the status, and NEVER the
        # response body: an Anthropic error body can echo the request, and the
        # request carries a real candidate's answers.
        ctx.errors.append(f"{kind} ({status if status is not None else type(exc).__name__})")
        logger.warning(
            "llm_router.attempt_failed task=%s model=%s key=%s kind=%s status=%s "
            "attempt=%d latency_ms=%.0f",
            ctx.task_type, ctx.model, ctx.key.fingerprint, kind, status,
            attempts, elapsed * 1000,
        )
        if not retryable:
            return {"attempts": attempts, "error": "__terminal__"}
        # The vendor's own retry-after wins over the local curve when it is
        # short enough to still leave room for an attempt.
        wait = retry_after_seconds(exc)
        if wait and ctx.deadline is not None:
            remaining = ctx.deadline - time.monotonic()
            if wait >= remaining - ctx.longest_attempt:
                ctx.errors.append(f"retry-after {wait:.0f}s exceeds remaining budget")
                return {"attempts": attempts, "error": "__terminal__"}
            await asyncio.sleep(wait)
        return {"attempts": attempts, "error": kind}

    elapsed = time.monotonic() - started
    ctx.longest_attempt = max(ctx.longest_attempt, elapsed)
    result = as_result(raw)
    _record(
        fingerprint=ctx.key.fingerprint,
        model=ctx.model,
        ok=True,
        latency_ms=elapsed * 1000,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        had_usage=result.had_usage,
    )
    _record_success(ctx.key)
    logger.info(
        "llm_router.ok task=%s model=%s key=%s attempt=%d latency_ms=%.0f "
        "in=%d out=%d priced=%s",
        ctx.task_type, ctx.model, ctx.key.fingerprint, attempts, elapsed * 1000,
        result.prompt_tokens, result.completion_tokens, is_priced(ctx.model),
    )
    return {"attempts": attempts, "result": result.content, "error": None}


def _terminal(state: RouterState) -> dict:
    return {}


def _build_graph():
    graph = StateGraph(RouterState)
    graph.add_node("attempt", _attempt)
    graph.add_node("succeeded", _terminal)
    graph.add_node("exhausted", _terminal)
    graph.add_edge(START, "attempt")
    graph.add_conditional_edges(
        "attempt",
        should_continue,
        {"retry": "attempt", "succeeded": "succeeded", "exhausted": "exhausted"},
    )
    graph.add_edge("succeeded", END)
    graph.add_edge("exhausted", END)
    return graph.compile()


_router_graph = _build_graph()

#: A second, independent bound on the loop. The retry budget is the intended
#: one; this is the backstop that makes a pathological graph impossible rather
#: than merely unlikely.
_RECURSION_LIMIT = 32


# ── Public API ───────────────────────────────────────────────────────────────


async def invoke_llm(
    task_type: str,
    messages: list[dict],
    response_format_json: bool = False,
    session: AsyncSession | None = None,
    timeout: float | None = None,
    total_budget: float | None = None,
) -> str:
    """Run a completion for `task_type` through the router.

    `messages` uses the OpenAI shape -- [{"role": "system"|"user"|"assistant",
    "content": str}] -- which is translated to the Messages API's system/turns
    split here rather than at twenty-odd call sites.

    `session` is retained in the signature and is now unused. It used to load
    credentials from `llm_provider_keys`; it is kept because roughly twenty
    callers pass it, and churning all of them to remove an argument would be a
    large diff whose only effect is a smaller signature. Removing it is a
    reasonable later cleanup, not part of a vendor consolidation.

    Returns the assistant text. Raises `LLMUnavailableError` when the vendor
    could not serve the call within its budget.
    """
    # One traced run per logical call, named after the task type, so the
    # dashboard separates the scorers from report synthesis from the interviewer
    # with no per-agent wiring. It wraps the WHOLE call rather than one attempt:
    # what matters operationally is whether this call eventually produced an
    # answer and how long it took, not that attempt two was rate limited.
    with tracing.trace_llm(task_type, messages=messages) as run:
        try:
            result = await _invoke_llm_inner(
                task_type, messages, response_format_json, timeout, total_budget,
            )
        except Exception as exc:  # noqa: BLE001 -- re-raised immediately
            if run is not None:
                run.end(error=f"{type(exc).__name__}: {exc}")
            raise
        if run is not None:
            run.end(output=result)
        return result


async def _invoke_llm_inner(
    task_type: str,
    messages: list[dict],
    response_format_json: bool,
    timeout: float | None,
    total_budget: float | None,
) -> str:
    """The retry loop itself.

    Split out so `invoke_llm` is only the tracing wrapper, and so a tracing
    failure can never be mistaken for a router bug.
    """
    model = model_for(task_type)  # raises ValueError on an unknown task type

    if provider_is_written_off():
        raise LLMUnavailableError(
            f"{PROVIDER} is written off by operator action; "
            f"task_type={task_type} using the caller's fallback"
        )

    key = _load_key()
    if key is None:
        raise LLMUnavailableError(
            "No Anthropic credential configured (ANTHROPIC_API_KEY is unset)"
        )
    if _is_cooling_down(key):
        logger.warning(
            "llm_router.cooling_down task_type=%s key=%s, using caller fallback "
            "until the half-open recovery probe is due",
            task_type, key.fingerprint,
        )
        raise LLMUnavailableError(
            f"The Anthropic credential is cooling down for task_type={task_type}"
        )

    request_timeout = timeout if timeout is not None else timeout_for(task_type)
    budget = total_budget if total_budget is not None else total_budget_for(task_type)

    async with httpx.AsyncClient(timeout=request_timeout) as client:
        ctx = _RouteContext(
            task_type=task_type,
            model=model,
            key=key,
            messages=messages,
            json_mode=response_format_json,
            client=client,
            retry_budget=retry_budget_for(task_type),
            max_tokens=max_tokens_for(task_type),
            temperature=temperature_for(task_type),
            attempt_timeout=request_timeout,
            deadline=time.monotonic() + budget if budget else None,
        )
        final: RouterState = await _router_graph.ainvoke(
            {"task_type": task_type, "attempts": 0, "result": None,
             "error": None, "ctx": ctx},
            config={"recursion_limit": _RECURSION_LIMIT},
        )

    result = final.get("result")
    if result is not None:
        return result
    raise LLMUnavailableError(
        f"Anthropic exhausted for task_type={task_type}: {'; '.join(ctx.errors)}"
    )


async def chat_completion(
    role_hint: str,
    messages: list[dict],
    response_format_json: bool = False,
    session: AsyncSession | None = None,
) -> str:
    """Backwards-compatible alias for `invoke_llm`.

    Every pre-2026-07-27 caller passes a role hint (`rerank`, `extraction`) and
    a good many later ones pass a real task type. Both work: this is a pure
    rename with the current routing machinery underneath.
    """
    return await invoke_llm(
        role_hint, messages, response_format_json=response_format_json, session=session
    )
