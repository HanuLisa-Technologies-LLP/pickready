"""The single-vendor LLM router: every model call in Vivekium goes through here.

OpenAI Chat Completions, two model ids, one credential PER MODEL. A compiled
LangGraph state machine drives the retry loop, exactly as it did in the
multi-provider era -- `claude.md` rule 9 documents that state machine as an
architectural decision, and changing vendors is not a reason to quietly drop
it. What consolidating vendors DID remove is the reason the loop was
complicated: there is no longer a fallback chain to walk, no capacity registry
to consult, and no quota domain to discover. An attempt is now only ever worth
making against a transient.

THE VENDOR CHANGED ON 2026-08-31 AND THE SHAPE OF THIS MODULE DID NOT
----------------------------------------------------------------------
This module called the Anthropic Messages API until 2026-08-31, by a rule
written down in three places. The owner reversed the rule and the documents
were changed with the code rather than left contradicting it. Anthropic is
REMOVED, not kept as a fallback: there is no second transport, no
`if provider ==` branch, and no retained credential. One vendor, pointed
somewhere new.

Everything that was provider-agnostic survived untouched: retries, exponential
backoff, the per-attempt timeout, the total wall-clock budget, the predictive
deadline, the circuit breaker with half-open recovery, and a credential failure
tripping the breaker on the first occurrence.

WHAT THIS MODULE IS RESPONSIBLE FOR
------------------------------------
    resolve credential -> refuse what it cannot afford -> bound the attempt ->
    call -> classify a failure -> RECOVER ACCORDING TO ITS CLASS -> retry
    within budget -> account for what it cost -> trace it.

SEMANTIC RECOVERY, AND THE COST CEILING (RPN-AI-UP-001 W4.1 and W4.7)
----------------------------------------------------------------------
Until 2026-09-09 classification answered ONE question: retry, or do not. That
is right for a 429 and wrong for three failures this platform can receive, so
the class now decides HOW to recover, and the mapping is DATA in
`llm_providers.RECOVERY_FOR_FAILURE` rather than a chain of `if status ==` in
this file. Three of the rows are new and each of them changes what happens
rather than only when:

  * a CONTEXT OVERFLOW is retried COMPRESSED, through
    `context_budget.compress_messages`, which removes whole turns or whole
    sentences and never cuts inside one. Retrying identically would reproduce
    the same 400 and spend an attempt proving it, and a request shortened past
    a sentence boundary would hand a model half a sentence.
  * a REFUSAL is not retried at all and raises `ModelRefusal`, a SUBCLASS of
    `LLMUnavailableError` so every existing caller still degrades rather than
    500ing, carrying `needs_human_review` for the callers that want to tell
    "unreachable" from "read it and declined".
  * a TRUNCATED response (`finish_reason: "length"`) is never returned. It is
    retried ONCE at double the completion budget, capped by
    `MAX_COMPLETION_TOKENS_CEILING` and priced against the cost ceiling before
    the retry starts; a second cut raises `ResponseTruncated`, another
    `LLMUnavailableError` subclass. Until 2026-09-24 the cut text was handed
    back as a complete answer: half a JSON document to a structured caller and
    half a sentence to a prose one.
  * a SCHEMA VIOLATION, when the caller passed a `validate`, is retried with the
    validator's own message fed back VERBATIM through
    `agent_loop.reflection_text`. That is the only class whose retry carries a
    different prompt, and the mechanism is the loop's rather than a second copy
    of it. The message goes into the PROMPT and never into the error text: a
    validator message can quote the value it rejected, and a rejected value is a
    candidate's own words.

A TIMEOUT is now separated from a transport error, because they are different
facts. A refused connection demonstrably did not reach the vendor; a timeout may
have been served with only the answer lost, so the raised error carries
`unknown_outcome` rather than letting a side-effecting caller assume it did not
happen.

The cost ceiling is the latency ceiling in the other unit. `TASK_COST_CEILING_USD`
is checked against the WORST case a request could produce, BEFORE the call and
again before each retry, and every refusal is recorded in `cost_refusals()`,
because a budget that stopped something silently is indistinguishable from a
task that finished.

It RAISES on final failure. That is the same split `services/tools.execute`
keeps and it is deliberate: a router that swallowed an outage would hand its
caller an empty string indistinguishable from a model that legitimately had
nothing to say, and the caller would render it. `agent_loop.run_loop` and every
direct caller own the user-visible degradation; this module owns only the
question of whether the vendor answered.

WHY httpx AND NOT THE VENDOR SDK
---------------------------------
The SDK is good and this is not a criticism of it. The reason is that the SDK
runs its own retry policy, its own timeout handling and its own backoff, and
this module already owns all three against a per-task budget the SDK cannot see.
Two retry loops nested inside each other produce a worst case that is the
PRODUCT of their budgets, not the sum, and neither one can be reasoned about
from the other's side -- which is precisely the failure this file's
`TASK_TOTAL_BUDGET` exists to prevent. One HTTP call per attempt, bounded by one
timeout, is a thing a reviewer can check. `httpx` was also already the transport
here, so this is continuity rather than a new dependency.

JSON MODE IS NOW NATIVE, AND THE PREFILL IS GONE
-------------------------------------------------
Until 2026-08-31 this module obtained structured output by PREFILLING the
assistant turn with a single `{` and prepending it back onto the response,
because the Messages API had no `response_format`. That mechanism is DELETED --
the prefill branch in `build_payload`, the re-prepend in `parse_response`, and
the constant that carried the brace. Nothing here seeds an assistant turn any
more.

Chat Completions takes `response_format: {"type": "json_object"}`, which is a
STRONGER guarantee of the same property from the same direction: the prefill
constrained the first character and left the rest to the sampler, while the
native format constrains the whole body to parseable JSON. The invariant the
prefill existed to protect is unchanged and still pinned: every JSON-mode
caller in this codebase parses a top-level OBJECT (verified: no caller scans
for a leading `[`, and `tests/test_llm_router.py` re-runs that scan), so
`vendor_contract.check_openai_response` refuses a JSON-mode response whose text
does not open with `{` rather than handing a caller something its `json.loads`
will reject with no explanation attached.

The system instruction survives and is now load bearing for a second reason:
the published API REJECTS `json_object` with a 400 unless the token "json"
appears somewhere in the messages. `_JSON_SYSTEM_SUFFIX` is what satisfies
that, `llm_providers.JSON_MODE_REQUIRED_TOKEN` is the token, and
`vendor_contract.describe_request_hazards` names the constraint on any 400 so
the failure arrives as a sentence rather than as a permanent silent
degradation.

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

TWO KEYS, ONE PER MODEL
------------------------
`key_for_model` resolves the credential from
`llm_providers.SETTINGS_ATTR_FOR_MODEL`, which is DATA. An absent key for the
model being called raises `LLMUnavailableError` naming the missing environment
variable, exactly as an absent single credential did. It never falls back to
the other key: that would send a judging call to the extraction tier, which is
the boundary violation the two-tier split exists to prevent, and it would leave
no trace that it happened.

The breaker is keyed by credential FINGERPRINT, so the two keys trip
independently. That is the correct granularity and it always was: a revoked
reasoning key should not take the extraction path off models.

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
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from app.config.llm_providers import (
    CREDENTIAL_STATUSES,
    ENV_VAR_FOR_MODEL,
    FAILURE_CLIENT_ERROR,
    FAILURE_CONTEXT_OVERFLOW,
    FAILURE_REFUSAL,
    FAILURE_SCHEMA_VIOLATION,
    FAILURE_TIMEOUT,
    FAILURE_TRUNCATED,
    MAX_COMPLETION_TOKENS_CEILING,
    MAX_TRUNCATION_RETRIES,
    FAILURE_TRANSPORT,
    FAILURE_UNCLASSIFIED,
    JSON_OBJECT_RESPONSE_FORMAT,
    OPENAI_CHAT_COMPLETIONS_URL,
    PROVIDER,
    SETTINGS_ATTR_FOR_MODEL,
    VENDOR_ERROR_ALLOWED_FIELDS,
    classify_status,
    cost_ceiling_for,
    estimate_cost_usd,
    is_context_overflow_code,
    is_priced,
    is_refusal_finish_reason,
    is_retryable_status,
    is_truncation_finish_reason,
    jittered_backoff_seconds,
    max_tokens_for,
    model_for,
    recovery_for,
    retry_budget_for,
    temperature_for,
    timeout_for,
    total_budget_for,
)
from app.services import context_budget, cost_telemetry
from app.services.observability import otel
from app.services.agent_loop import reflection_text
from app.services.reliability import vendor_contract

_FAILURE_THRESHOLD = 2          # consecutive transient failures before tripping
_COOLDOWN_SECONDS = 15 * 60     # 15 min cool-off (claude.md rule 9)
_MIN_SUCCESS_RATE = 0.5         # below this, the vendor is logged as degraded
_STATS_ALARM_MIN_ATTEMPTS = 5   # don't alarm on a single unlucky call

#: The instruction half of JSON mode, and the half the API itself REQUIRES.
#:
#: `response_format: {"type": "json_object"}` is rejected with a 400 unless the
#: token "json" appears somewhere in the messages, so this constant is load
#: bearing for the request to be accepted at all and not merely for the model to
#: cooperate. It is written with the token in lower case for that reason: the
#: documented check is on the literal string, and relying on a case fold that is
#: not written down anywhere would be a guess sitting on the request path of
#: every extraction call in the product.
#:
#: Deliberately terse beyond that. A long instruction competes with the caller's
#: own system prompt for attention, and `response_format` is what actually
#: enforces the shape.
_JSON_SYSTEM_SUFFIX = (
    "Respond with exactly one raw json object and nothing else. "
    "No prose before or after it, and no markdown code fences."
)

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when the vendor could not serve the call within its budget.

    Callers catch this and fall back to their own DETERMINISTIC behaviour. It is
    never allowed to reach a user as a 500: an outage should cost the product
    its adaptivity, not its availability.

    `unknown_outcome` is set when the last thing that happened was a TIMEOUT.
    W4.1 is explicit that a timeout is UNKNOWN rather than absent for a side
    effecting call: the request may have been received and served, and only the
    answer was lost. Every caller in this codebase today is read-only in the
    vendor's terms, so nothing acts on it yet; it is carried because the
    alternative is a caller that has to guess, and a caller that guesses "it did
    not happen" is the one that sends a second email.
    """

    def __init__(self, message: str, *, unknown_outcome: bool = False) -> None:
        super().__init__(message)
        self.unknown_outcome = unknown_outcome


class ModelRefusal(LLMUnavailableError):
    """The vendor answered and DECLINED. Not a transport failure.

    A SUBCLASS, deliberately. Every caller in this codebase catches
    `LLMUnavailableError` and degrades to deterministic behaviour, which stays
    the right answer here: a refusal must not become a 500. What the subclass
    adds is the ability for a caller that cares to tell "the vendor was
    unreachable" from "the vendor read the request and would not answer it",
    because only the second one is a person's problem.

    It is never retried. W4.1: asking a model that has already declined to
    decline again spends the budget on nothing.
    """

    needs_human_review = True

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        #: The vendor's own short reason. A `finish_reason` or the API's
        #: `refusal` string, never a candidate's text.
        self.reason = reason


class ResponseTruncated(LLMUnavailableError):
    """The vendor answered and was CUT OFF at the completion budget, twice.

    A SUBCLASS for the reason `ModelRefusal` is one: every caller already
    catches `LLMUnavailableError` and degrades to its deterministic behaviour,
    which is the right answer to "no complete answer arrived". What the subclass
    adds is the ability to tell a truncation from an outage, because only a
    truncation is fixed by editing a `TASK_MAX_TOKENS` row.

    The cut text is NEVER attached. It is model output derived from a prompt
    that carries a real candidate's answers, and half of it is not an answer.

    Not a person's problem, unlike a refusal: nothing about the content was
    declined, so it does not ask for human review.
    """

    needs_human_review = False

    def __init__(self, message: str, *, max_completion_tokens: int) -> None:
        super().__init__(message)
        #: The budget the LAST attempt was cut at, so a log line names the
        #: number the task's row needs to exceed.
        self.max_completion_tokens = max_completion_tokens


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


def key_for_model(model: str) -> _RouterKey | None:
    """The credential for one model, from the environment.

    TWO KEYS, ONE PER MODEL. Which settings attribute belongs to which model is
    DATA in `llm_providers.SETTINGS_ATTR_FOR_MODEL`, so adding or repointing a
    model is an edit to a table rather than a branch here.

    Returns None when the key for THIS model is absent, and the caller raises
    naming the environment variable. It deliberately does not fall back to the
    other key: an extraction credential serving a `dimension_evaluation` call
    would run a grade on the wrong tier, produce a plausible answer, and leave
    nothing in the record saying so.

    An unknown model raises rather than returning None, because "no credential
    for this model" and "this is not a model we route to" have completely
    different remedies and a shared return value would hide one behind the
    other.

    THE `llm_provider_keys` TABLE IS NO LONGER READ, and it was deliberately not
    dropped in the same change that stopped reading it. It holds encrypted rows
    for three retired vendors, an audit trail of which credential served which
    call is still attached to it through the telemetry, and a rollback of the
    consolidation would need those rows intact rather than restored from a
    backup. It is unread, not gone -- the same treatment `technical_questions`
    got for the same reason.
    """
    from app.core.config import get_settings  # noqa: PLC0415 -- import cycle

    try:
        attribute = SETTINGS_ATTR_FOR_MODEL[model]
    except KeyError as exc:
        raise ValueError(
            f"No credential mapping for model {model!r}; expected one of "
            f"{sorted(SETTINGS_ATTR_FOR_MODEL)}"
        ) from exc

    api_key = (getattr(get_settings(), attribute, "") or "").strip()
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
    #: Prompt-cache hits, summed over the calls that REPORTED one. Read beside
    #: `calls_reporting_cache`, never alone: this is a sum over a subset, so a
    #: zero here with a zero beside it means nothing was observed rather than
    #: that nothing was cached.
    cached_prompt_tokens: int = 0
    calls_reporting_cache: int = 0

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
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "calls_reporting_cache": self.calls_reporting_cache,
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

    The two tiers have different latency profiles and a price difference,
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


#: Every call this router REFUSED on cost, newest last, bounded.
#:
#: W4.7: "record every refusal, because a budget that stopped something
#: silently is indistinguishable from a task that finished". That is the same
#: argument `reliability.budget.Budget.refusals` already makes, and this is the
#: router's copy of it for the ceiling the router owns.
#:
#: Bounded because it is in-process memory on a long-lived worker. Dropping the
#: OLDEST is right for this record: a refusal is an alarm, and the alarm you
#: need is the one that just fired.
_COST_REFUSAL_LOG_LIMIT = 100
_cost_refusal_log: list[dict[str, Any]] = []


def _record_cost_refusal(
    *,
    task_type: str,
    model: str,
    estimated_usd: float,
    ceiling_usd: float,
    stage: str,
) -> None:
    """Record one cost refusal. Identifiers and numbers, never content."""
    _cost_refusal_log.append(
        {
            "task_type": task_type,
            "model": model,
            "estimated_usd": round(estimated_usd, 6),
            "ceiling_usd": round(ceiling_usd, 6),
            "stage": stage,
        }
    )
    del _cost_refusal_log[:-_COST_REFUSAL_LOG_LIMIT]
    logger.warning(
        "llm_router.cost_refused task=%s model=%s estimated_usd=%.4f "
        "ceiling_usd=%.4f stage=%s",
        task_type, model, estimated_usd, ceiling_usd, stage,
    )


def cost_refusals() -> list[dict[str, Any]]:
    """The recorded cost refusals, for the admin health endpoint."""
    return [dict(entry) for entry in _cost_refusal_log]


def reset_provider_stats() -> None:
    """Test and operator hook: clear every counter and every breaker."""
    global _provider_stat
    _provider_stat = _Stats()
    _model_stats.clear()
    _key_stats.clear()
    _key_models.clear()
    _breakers.clear()
    _cost_refusal_log.clear()
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
    cached_prompt_tokens: int | None = None,
) -> None:
    cost = estimate_cost_usd(
        model,
        prompt_tokens,
        completion_tokens,
        cached_prompt_tokens=cached_prompt_tokens or 0,
    )
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
        if cached_prompt_tokens is not None:
            stat.cached_prompt_tokens += cached_prompt_tokens
            stat.calls_reporting_cache += 1
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
    account and whose adaptive-max_tokens remedy went with it.
    """
    status = status_of(exc)
    return status is not None and status in CREDENTIAL_STATUSES


def is_retryable(exc: Exception) -> bool:
    """True for a transient: 429, any 5xx, a timeout, or a transport error.

    A non-429 4xx is OUR bug -- a malformed request, an unknown model id, a
    message list the API refuses -- and it will fail identically on retry, so
    spending the budget on it delays the caller's fallback for nothing.

    NOT the router loop's authority any more, and retained deliberately rather
    than left ambiguous: `_attempt` asks `classify_failure` and then
    `recovery_for`, which can tell a context overflow from an ordinary 400 and
    this cannot. This remains the answer to the narrower question "is this
    exception a transient", which is what `scripts/reembed.py` and
    `tools/executor` import it for.
    """
    status = status_of(exc)
    if status is not None:
        return is_retryable_status(status)
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError))


def vendor_error_code(exc: Exception) -> str | None:
    """The vendor's own short error code, from an error body. Never a message.

    ONLY the fields in `VENDOR_ERROR_ALLOWED_FIELDS` are read, and that is an
    allowlist rather than a denylist for the reason the trace allowlist is one:
    `error.message` on a 400 can echo the request, and the request carries a
    real candidate's answers and a real job description. A code is a short
    vendor-controlled enum and is safe to log; a message is content and is not.

    Returns None when the body was not JSON or carried no code. That is not a
    silent fallback: the caller's only use for it is to distinguish a context
    overflow from every other 400, and "no code" correctly means "not
    identifiable as an overflow", which leaves the 400 classified exactly as it
    was before this function existed.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    reader = getattr(response, "json", None)
    if not callable(reader):
        return None
    try:
        body = reader()
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    for name in VENDOR_ERROR_ALLOWED_FIELDS:
        value = error.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def classify_failure(exc: Exception) -> str:
    """The failure CLASS, which `recovery_for` turns into a strategy.

    This is the half of W4.1 that had to grow. `classify_status` answered four
    ways and the router asked it one question ("retry?"); the classes below are
    what a semantic recovery needs to tell apart, and two of them are not HTTP
    statuses at all.

    A timeout is separated from a transport error because they are different
    facts: a refused connection demonstrably did not reach the vendor, while a
    timeout may have been served and only the answer lost. An exception that is
    neither an HTTP status nor a transport failure is UNCLASSIFIED rather than
    assumed transient, which preserves the previous behaviour exactly: a
    `VendorContractViolation` was never retried and still is not.
    """
    status = status_of(exc)
    if status is None:
        if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
            return FAILURE_TIMEOUT
        if isinstance(exc, httpx.TransportError):
            return FAILURE_TRANSPORT
        return FAILURE_UNCLASSIFIED
    kind = classify_status(status)
    if kind == FAILURE_CLIENT_ERROR and is_context_overflow_code(
        vendor_error_code(exc)
    ):
        # The one 400 that is not simply our bug: the request was too long, and
        # the recovery is to send less rather than to send it again.
        return FAILURE_CONTEXT_OVERFLOW
    return kind


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


# ── The vendor call ──────────────────────────────────────────────────────────


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
    #: `usage.prompt_tokens_details.cached_tokens`, a SUBSET of `prompt_tokens`.
    #:
    #: None means the response did not report the field, which is NOT the same
    #: as a reported zero and must never be flattened into one. A deployment
    #: whose endpoint does not carry `prompt_tokens_details` at all would read
    #: as "the cache never hit" if this defaulted to 0, and the platform would
    #: then be measuring the absence of a feature as the failure of one.
    cached_prompt_tokens: int | None = None
    #: The vendor's own `finish_reason`, carried so a refusal can be told from
    #: an answer. Defaults None so `as_result("plain")` and every
    #: string-returning stub in the test suite keep working unchanged.
    finish_reason: str | None = None
    #: The published API's structured refusal string, when there is one. Both
    #: this and `finish_reason` are checked, because either can arrive alone.
    refusal: str | None = None

    @property
    def is_refusal(self) -> bool:
        return bool(self.refusal) or is_refusal_finish_reason(self.finish_reason)

    @property
    def is_truncated(self) -> bool:
        """The model stopped at the completion budget rather than finishing."""
        return is_truncation_finish_reason(self.finish_reason)

    @property
    def refusal_reason(self) -> str:
        """A short reason, from the vendor's own vocabulary. Never content.

        `refusal` is a sentence the model wrote and could quote the request, so
        it is NOT used here even though it is the more informative of the two.
        What this returns is the finish reason, which is an enum.
        """
        if is_refusal_finish_reason(self.finish_reason):
            return str(self.finish_reason)
        return "the response carried a structured refusal"


def as_result(value: "_Result | str") -> _Result:
    """Normalise whatever the call layer returned.

    A bare string is accepted and wrapped, because tests substitute simple
    string-returning stubs for `_call_openai` and a change to how the router
    ACCOUNTS should not require rewriting every routing test. Accounting is
    monitoring; it must never be the thing that fails a request.
    """
    if isinstance(value, _Result):
        return value
    return _Result(content=str(value))


def split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Separate system text from the conversation turns.

    Chat Completions takes the system prompt as a MESSAGE at the head of the
    array rather than as a top-level field, so the two halves are recombined in
    `build_payload` immediately after being split here. The split is still
    worth doing rather than passing the caller's list through: JSON mode has to
    append its instruction to the system text, and several callers send more
    than one system message.

    Multiple system messages are joined rather than dropped, and that is the
    behaviour that must not be lost in the vendor change: several callers build
    a system prompt in layers (a base instruction plus retrieved context plus
    an experience-memory hint), and losing any layer would silently change what
    was asked without changing what the caller wrote.
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


#: The sampling seed sent on every request.
#:
#: A CONSTANT, not a per-call value, and that is the point: two runs over the
#: same evidence must send the same seed or there is no reproducibility to have.
#: It is not a secret and it is not tuned; any fixed integer would do, and it is
#: fixed here so that a change to it is a reviewed line in a diff rather than a
#: value drifting per environment.
#:
#: `temperature` cannot be used for this on the current models, which accept
#: only their default of 1. See `build_payload` for the measurement and for what
#: the product gave up.
_SEED = 20260829

def build_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    """The request body, built once so the shape is reviewable in one place.

    The system prompt goes back on the FRONT of the message array, which is
    where Chat Completions takes it. There is no prefill and no trailing
    assistant turn: JSON mode is `response_format`, natively.
    """
    system, turns = split_system(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": turns,
        # `max_completion_tokens`, NOT `max_tokens`. VERIFIED AGAINST THE LIVE
        # ENDPOINT 2026-08-31: `max_tokens` returns
        #     400 unsupported_parameter: "'max_tokens' is not supported with
        #     this model. Use 'max_completion_tokens' instead."
        # This was recorded as an open question that could not be settled
        # without a call, and the call settled it. The two names are not
        # interchangeable and the older one is refused outright rather than
        # ignored, so there is no silent-truncation failure mode here: a
        # regression fails every request loudly.
        "max_completion_tokens": max_tokens,
        # DETERMINISM, AND WHAT THIS PRODUCT LOST WHEN THE VENDOR CHANGED.
        #
        # `temperature` is NOT sent. VERIFIED AGAINST THE LIVE ENDPOINT
        # 2026-08-31, on both models:
        #     400 unsupported_value: "'temperature' does not support 0.0 with
        #     this model. Only the default (1) value is supported."
        #
        # That is a real loss and it is worth naming rather than absorbing. The
        # standing rule was that every task which JUDGES samples at 0.0, because
        # a scoring call above zero makes a candidate's grade depend on WHEN
        # they were scored, and a disagreeing rescore then reads as a broken
        # rubric rather than as noise. These models cannot do that.
        #
        # `seed` is the closest substitute the API offers and it is what is sent
        # instead. Measured here on 2026-08-31: three calls at one seed returned
        # byte-identical text. Be exact about the strength of that: OpenAI
        # documents `seed` as BEST EFFORT, not a guarantee, and the
        # `system_fingerprint` that would let a caller detect a backend change
        # came back null on these models, so a silent change of backend is not
        # observable from the response.
        #
        # What still holds, and it is the part that matters most: the AGGREGATOR
        # makes zero model calls and is deterministic arithmetic over the bands.
        # So the step that turns five dimension bands into the grade a client
        # reads cannot vary. What can now vary is the band a single evaluator
        # returns for identical evidence, which is a narrower exposure than the
        # old rule was defending, but it is not nothing.
        "seed": _SEED,
    }
    if json_mode:
        # Two halves, and both are required. `response_format` is the guarantee;
        # the system suffix is what the API demands before it will accept the
        # format at all, because the token "json" must appear in the messages.
        system = f"{system}\n\n{_JSON_SYSTEM_SUFFIX}".strip()
        payload["response_format"] = dict(JSON_OBJECT_RESPONSE_FORMAT)
    if system:
        payload["messages"] = [{"role": "system", "content": system}] + turns
    return payload


def parse_response(payload: dict[str, Any], *, json_mode: bool) -> _Result:
    """Turn a Chat Completions response into text plus usage.

    `json_mode` no longer changes what this function does to the text, and that
    is the visible half of the prefill's removal: there is nothing to prepend,
    because the response IS the whole object. The parameter is retained because
    `_call_openai` passes it on to the contract check, which does still care --
    a JSON-mode body that does not open with `{` is a violation there rather
    than a `json.JSONDecodeError` in some caller with no explanation attached.

    A response with no choices, or a choice whose content is null, yields "" --
    which is exactly the silent failure `vendor_contract.check_openai_response`
    exists to catch on the first live call, and exactly why that check cannot
    be folded in here.
    """
    choices = payload.get("choices") or []
    text = ""
    finish_reason: str | None = None
    refusal: str | None = None
    if choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        finish_reason = str(reason) if isinstance(reason, str) and reason else None
        message = choices[0].get("message")
        if isinstance(message, dict):
            text = str(message.get("content") or "")
            declined = message.get("refusal")
            refusal = str(declined) if isinstance(declined, str) and declined else None
    usage = payload.get("usage") or {}
    had_usage = bool(usage)
    return _Result(
        content=text,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        had_usage=had_usage,
        cached_prompt_tokens=_cached_prompt_tokens(usage),
        finish_reason=finish_reason,
        refusal=refusal,
    )


def _cached_prompt_tokens(usage: object) -> int | None:
    """The vendor's reported prompt-cache hit, or None when it said nothing.

    Chat Completions applies prompt caching AUTOMATICALLY to a sufficiently
    long identical prefix and reports the hit here; there is no request-side
    marker to send and this API does not take one. So the only thing the
    product controls is prompt ORDER (`services/prompt_cache`), and the only
    thing it can observe is this field.

    THE ABSENT CASE IS A REAL CASE AND IS RETURNED AS None. A response with no
    `prompt_tokens_details` is the normal shape for an endpoint that does not
    report caching, and rounding that to zero would make "we cannot see the
    cache" indistinguishable from "the cache did not hit" in every downstream
    average. A present-but-unparseable value is also None for the same reason:
    a number nobody can read is not a measurement.
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        return None
    raw = details.get("cached_tokens")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return max(0, int(raw))


def _refusal_result(payload: object) -> _Result | None:
    """A `_Result` when the body is a refusal, None when it is an answer.

    Narrow on purpose. A body only counts as a refusal when the vendor SAID so,
    through a structured `refusal` string or a `finish_reason` on the refusal
    list. An empty answer is not a refusal: that is the silent-failure case
    `vendor_contract.check_openai_response` exists to catch, and misreading it
    as a decline would replace one honest loud failure with a quiet one.
    """
    if not isinstance(payload, dict):
        return None
    result = parse_response(payload, json_mode=False)
    return result if result.is_refusal else None


async def _call_openai(
    client: httpx.AsyncClient,
    key: _RouterKey,
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool,
    max_tokens: int,
    temperature: float,
) -> _Result:
    payload = build_payload(
        model=model,
        messages=messages,
        json_mode=json_mode,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    resp = await client.post(
        OPENAI_CHAT_COMPLETIONS_URL,
        headers={
            # The key travels in a HEADER, never a query string. As a query
            # parameter it lands in httpx's own INFO log line and from there
            # into the platform's log sink in plain text, which is how this
            # module's "keys are never logged" guarantee was broken once before
            # on a different vendor. The scheme changed with the vendor; the
            # rule did not.
            "Authorization": f"Bearer {key.api_key}",
            "content-type": "application/json",
        },
        json=payload,
    )
    resp.raise_for_status()
    body = resp.json()
    refused = _refusal_result(body)
    if refused is not None:
        # A REFUSAL IS NOT CHECKED AGAINST THE CONTRACT, and that is deliberate
        # rather than an omission. `check_openai_response` refuses a null
        # `content` because a null content is the tool-call shape this platform
        # never requests -- but a structured refusal is exactly a null content
        # with a `refusal` beside it, and running the check would report a
        # contract violation for a body that is precisely the published shape.
        # The once-per-path memo is left unset, so the next ordinary response on
        # this model still gets the first-live-use check it was built for.
        return refused
    # FAIL LOUD ON FIRST LIVE USE (spec-doc6 §12.5). The shape below was
    # hand-authored from the published schema and has never been seen from the
    # endpoint, so the first response on each model is checked against it and a
    # disagreement raises an error naming the fixture rather than being parsed
    # into an empty string. `parse_response` cannot do this job: it reads
    # `choices[0].message.content` and a differently shaped body simply yields
    # "", which reads downstream exactly like a model that had little to say.
    vendor_contract.check_openai_response(body, model=model, json_mode=json_mode)
    return parse_response(body, json_mode=json_mode)


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
    messages: list[dict[str, Any]]
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
    #: The caller's deterministic output check. Raises with a message when the
    #: response does not satisfy the schema. Feeding that message back VERBATIM
    #: is the only class of retry that carries a different prompt (W4.1).
    validate: Callable[[str], None] | None = None
    #: The validator's last message, appended as one extra turn on the next
    #: attempt and REPLACED rather than accumulated, so three rejections do not
    #: send three corrections.
    feedback: str | None = None
    #: The dollar ceiling for this logical call, and what it has already spent.
    #: Both here rather than in the graph state because a refusal has to be
    #: recorded on the way past, and graph state is replaced wholesale by each
    #: node's return value.
    cost_ceiling_usd: float = 0.0
    spent_usd: float = 0.0
    #: How many times the messages have already been compressed for an overflow
    #: retry. Bounded: a request that overflows after two halvings is not a
    #: request one more halving will fix.
    compressions: int = 0
    #: Set by the refusal path, read by `_invoke_llm_inner` to decide WHICH
    #: exception to raise.
    refusal_reason: str | None = None
    #: Set by a timeout. A timeout's outcome is UNKNOWN rather than absent.
    unknown_outcome: bool = False
    #: How many truncation retries this call has made, bounded by
    #: `MAX_TRUNCATION_RETRIES`. Each one doubles `max_tokens` above.
    truncations: int = 0
    #: True when the LAST attempt came back truncated, read by
    #: `_invoke_llm_inner` to raise `ResponseTruncated` rather than the generic
    #: exhaustion error. Cleared by any later attempt that fails differently, so
    #: the exception names what actually ended the call.
    truncated: bool = False


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


#: The most compressions one logical call will make before giving up. TWO,
#: because each halves the request: a body that still overflows after two is a
#: body whose single indivisible part is too large, and a third pass would be
#: spending the caller's budget to discover that again.
_MAX_COMPRESSIONS = 2


def _worst_case_cost_usd(
    model: str, messages: list[dict[str, Any]], max_tokens: int
) -> float:
    """What one attempt could cost if the model emitted its whole ceiling.

    WORST case, not expected case, because this number is used to REFUSE before
    the work. Checking after would mean the overspend has already happened and
    the ceiling is a report rather than a limit -- the argument
    `reliability/budget.py` already makes for its own ceilings.

    The prompt half is estimated from the assembled message text through
    `context_budget.estimate_tokens`, which is the same four-characters-per-token
    estimate `agent_loop` uses for output, so the two halves of a call are
    counted in one unit.
    """
    prompt_text = "\n".join(str(m.get("content") or "") for m in messages)
    return estimate_cost_usd(
        model, context_budget.estimate_tokens(prompt_text), max_tokens
    )


def _cost_exhausted(ctx: _RouteContext) -> bool:
    """True when the NEXT attempt could not be afforded.

    The same shape as `_budget_exhausted`, in dollars instead of seconds, and
    for the same reason: an attempt that cannot finish inside the budget is
    never started, so an attempt that cannot be paid for is never started
    either.
    """
    if ctx.cost_ceiling_usd <= 0:
        return False
    next_attempt = _worst_case_cost_usd(
        ctx.model, _outgoing_messages(ctx), ctx.max_tokens
    )
    return ctx.spent_usd + next_attempt > ctx.cost_ceiling_usd


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
    if _cost_exhausted(ctx):
        detail = (
            f"the {ctx.cost_ceiling_usd:.4f} USD ceiling for "
            f"{ctx.task_type} would be exceeded by another attempt, with "
            f"{ctx.spent_usd:.4f} USD already estimated"
        )
        ctx.errors.append(f"cost ceiling reached: {detail}")
        _record_cost_refusal(
            task_type=ctx.task_type,
            model=ctx.model,
            estimated_usd=ctx.spent_usd,
            ceiling_usd=ctx.cost_ceiling_usd,
            stage="before a retry",
        )
        return "exhausted"
    return "retry"


def _outgoing_messages(ctx: _RouteContext) -> list[dict[str, Any]]:
    """The message list this attempt will actually send.

    `ctx.messages` is what the caller asked for, possibly compressed by an
    earlier overflow. The validator's feedback is appended HERE rather than
    written into `ctx.messages`, so it is replaced on every rejection instead of
    accumulating: three rejections must send one correction, not three.
    """
    if ctx.feedback is None:
        return list(ctx.messages)
    return list(ctx.messages) + [
        {"role": "user", "content": reflection_text([ctx.feedback])}
    ]


async def _attempt(state: RouterState) -> dict[str, Any]:
    ctx = state["ctx"]
    attempts = state.get("attempts", 0) + 1

    # Jittered rather than bare exponential (W4.1, the provider-error row). The
    # draw is made here and passed in, because `llm_providers` is data and pure
    # functions and a `random` call inside it would make the policy table
    # unstubbable.
    delay = jittered_backoff_seconds(attempts, random.random())
    if delay and ctx.deadline is not None:
        # Never sleep past the deadline: an interactive caller waiting out a
        # backoff it can no longer use is strictly worse than failing now.
        delay = min(delay, max(0.0, ctx.deadline - time.monotonic()))
    if delay:
        await asyncio.sleep(delay)

    outgoing = _outgoing_messages(ctx)
    started = time.monotonic()
    try:
        raw = await _call_openai(
            ctx.client,
            ctx.key,
            ctx.model,
            outgoing,
            ctx.json_mode,
            ctx.max_tokens,
            ctx.temperature,
        )
    except Exception as exc:  # noqa: BLE001 -- classified immediately below
        elapsed = time.monotonic() - started
        ctx.longest_attempt = max(ctx.longest_attempt, elapsed)
        # SEMANTIC RECOVERY (W4.1). The class decides HOW to recover, and the
        # mapping is DATA in `llm_providers` rather than a chain of `if status
        # ==` here. What used to be one boolean ("retryable") is now a table
        # entry that also says whether to trip the breaker at once, whether to
        # honour a Retry-After, whether the outcome is unknown, and whether the
        # next attempt should send something different.
        kind = classify_failure(exc)
        recovery = recovery_for(kind)
        retryable = recovery.retry
        status = status_of(exc)
        _record(
            fingerprint=ctx.key.fingerprint,
            model=ctx.model,
            ok=False,
            latency_ms=elapsed * 1000,
            )
        _record_failure(ctx.key, terminal=recovery.trips_breaker_immediately)
        # The last thing that happened is no longer a truncation, so the error
        # this call eventually raises must not claim it was.
        ctx.truncated = False
        if recovery.outcome_is_unknown:
            ctx.unknown_outcome = True
        # The message names the classification and the status, and NEVER the
        # response body: a vendor error body can echo the request, and the
        # request carries a real candidate's answers.
        ctx.errors.append(f"{kind} ({status if status is not None else type(exc).__name__})")
        if kind == FAILURE_CONTEXT_OVERFLOW:
            # COMPRESS AND RETRY, never retry identically. An identical retry
            # reproduces the same 400 and spends an attempt proving it.
            if ctx.compressions >= _MAX_COMPRESSIONS:
                ctx.errors.append(
                    f"context overflow persisted after {ctx.compressions} "
                    f"compressions; another halving would not fix it"
                )
                return {"attempts": attempts, "error": "__terminal__"}
            compression = context_budget.compress_messages(ctx.messages)
            if not compression.compressed:
                # Nothing whole could be removed. Stopping here is the honest
                # answer: cutting inside a sentence hands a model half a
                # sentence, and a model handed half a sentence completes it
                # from its own priors.
                ctx.errors.append(f"could not compress: {compression.note}")
                return {"attempts": attempts, "error": "__terminal__"}
            ctx.messages = compression.messages
            ctx.compressions += 1
            ctx.errors.append(f"compressed the request: {compression.note}")
            logger.warning(
                "llm_router.compressed task=%s model=%s pass=%d note=%s",
                ctx.task_type, ctx.model, ctx.compressions, compression.note,
            )
        if status == 400 and kind == FAILURE_CLIENT_ERROR:
            # A 400 is OUR bug by classification, and it is not retried. Which
            # of our bugs is the question a reader is left with, and the answer
            # is usually one of two published constraints this request may not
            # satisfy. Naming them costs nothing on the path that never fires
            # and saves an outage's worth of guessing on the path that does.
            # Built from the model and OUR OWN payload, never from the response
            # body, which can echo a real candidate's answers.
            #
            # A context overflow is also a 400 and is deliberately excluded: it
            # has its own recovery two blocks up, and the hazards below describe
            # a malformed request rather than a long one.
            for hazard in vendor_contract.describe_request_hazards(
                ctx.model,
                build_payload(
                    model=ctx.model,
                    messages=outgoing,
                    json_mode=ctx.json_mode,
                    max_tokens=ctx.max_tokens,
                    temperature=ctx.temperature,
                ),
            ):
                ctx.errors.append(f"known request hazard: {hazard}")
                logger.error(
                    "llm_router.request_hazard task=%s model=%s hazard=%s",
                    ctx.task_type, ctx.model, hazard,
                )
        logger.warning(
            "llm_router.attempt_failed task=%s model=%s key=%s kind=%s status=%s "
            "attempt=%d latency_ms=%.0f",
            ctx.task_type, ctx.model, ctx.key.fingerprint, kind, status,
            attempts, elapsed * 1000,
        )
        if not retryable:
            return {"attempts": attempts, "error": "__terminal__"}
        # The vendor's own retry-after wins over the local curve when it is
        # short enough to still leave room for an attempt. Only the rate-limit
        # class carries one, and the table says so rather than the header's mere
        # presence deciding: a 5xx that happened to include the header would
        # otherwise park the caller on the vendor's schedule for a failure that
        # is not about rate at all.
        wait = retry_after_seconds(exc) if recovery.honours_retry_after else None
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
    # The token counts are only visible HERE. `invoke_llm` opened the span four
    # frames up and `_invoke_llm_inner` returns a bare string, so without this
    # the duration histogram is populated and `gen_ai.client.token.usage` is
    # permanently empty. It is not a second writer: it finds the span this call
    # is already inside and reports against it.
    otel.record_usage_on_active_span(
        input_tokens=result.prompt_tokens,
        output_tokens=result.completion_tokens,
        cached_input_tokens=result.cached_prompt_tokens,
    )
    _record(
        fingerprint=ctx.key.fingerprint,
        model=ctx.model,
        ok=True,
        latency_ms=elapsed * 1000,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        had_usage=result.had_usage,
        cached_prompt_tokens=result.cached_prompt_tokens,
    )
    # The per-assessment cost record, when this call is running inside one.
    # A no-op with nothing bound, so no caller has to know about it: the
    # router is the one place every model call in the product passes through,
    # and attributing the spend anywhere else would mean N call sites each
    # remembering to do it.
    cost_telemetry.note_usage(
        task_type=ctx.task_type,
        model=ctx.model,
        provider=PROVIDER,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cached_prompt_tokens=result.cached_prompt_tokens,
        had_usage=result.had_usage,
    )
    _record_success(ctx.key)
    # What this attempt actually cost, so the ceiling refuses the NEXT one on
    # measured spend rather than on an estimate of an estimate. A response that
    # reported no usage falls back to the worst case, which is the safe
    # direction: under-counting a call the vendor billed would let the ceiling
    # be passed silently.
    ctx.spent_usd += (
        estimate_cost_usd(
            ctx.model,
            result.prompt_tokens,
            result.completion_tokens,
            cached_prompt_tokens=result.cached_prompt_tokens or 0,
        )
        if result.had_usage
        else _worst_case_cost_usd(ctx.model, outgoing, ctx.max_tokens)
    )
    logger.info(
        "llm_router.ok task=%s model=%s key=%s attempt=%d latency_ms=%.0f "
        "in=%d out=%d cached_in=%s priced=%s",
        ctx.task_type, ctx.model, ctx.key.fingerprint, attempts, elapsed * 1000,
        result.prompt_tokens, result.completion_tokens,
        "unreported" if result.cached_prompt_tokens is None
        else result.cached_prompt_tokens,
        is_priced(ctx.model),
    )

    if result.is_refusal:
        # A REFUSAL IS NOT A TRANSPORT FAILURE, and it is not retried (W4.1).
        # The credential worked and the vendor answered, so the breaker is
        # already cleared above; what is wrong is the request's content, and
        # that is a person's problem rather than a schedule's.
        ctx.refusal_reason = result.refusal_reason
        ctx.errors.append(f"{FAILURE_REFUSAL} ({result.refusal_reason})")
        logger.warning(
            "llm_router.refused task=%s model=%s reason=%s",
            ctx.task_type, ctx.model, result.refusal_reason,
        )
        return {"attempts": attempts, "error": "__terminal__"}

    if result.is_truncated:
        # A TRUNCATED RESPONSE IS NOT AN ANSWER, and it is never returned. The
        # credential worked and the vendor served the call, so the breaker is
        # already cleared above and stays cleared. What is wrong is the budget,
        # so the one retry worth making carries a larger one (W4.1's
        # "different request, not a different schedule" rule).
        #
        # The cut text goes NOWHERE: not into `ctx.errors`, not into the log
        # line, not back to the model as feedback. It is model output written
        # from a prompt that carries a real candidate's answers.
        cut_at = ctx.max_tokens
        ctx.truncated = True
        ctx.errors.append(
            f"{FAILURE_TRUNCATED} (finish_reason=length, "
            f"max_completion_tokens={cut_at})"
        )
        logger.warning(
            "llm_router.truncated task=%s model=%s attempt=%d max_tokens=%d",
            ctx.task_type, ctx.model, attempts, cut_at,
        )
        larger = min(cut_at * 2, MAX_COMPLETION_TOKENS_CEILING)
        if ctx.truncations >= MAX_TRUNCATION_RETRIES:
            ctx.errors.append(
                f"still truncated after {ctx.truncations} larger-budget "
                f"retry; the task's TASK_MAX_TOKENS row is too small"
            )
            return {"attempts": attempts, "error": "__terminal__"}
        if larger <= cut_at:
            ctx.errors.append(
                f"max_completion_tokens={cut_at} is already at the "
                f"{MAX_COMPLETION_TOKENS_CEILING} ceiling; a retry could not "
                f"ask for more"
            )
            return {"attempts": attempts, "error": "__terminal__"}
        # `should_continue` prices the NEXT attempt with this larger budget
        # before it starts, so a doubled budget the cost ceiling cannot afford
        # is refused rather than attempted.
        ctx.max_tokens = larger
        ctx.truncations += 1
        return {"attempts": attempts, "error": FAILURE_TRUNCATED}

    if ctx.validate is not None:
        try:
            ctx.validate(result.content)
        except Exception as exc:  # noqa: BLE001 -- any validator, any library
            # THE ONLY RETRY THAT CARRIES A DIFFERENT PROMPT. The validator has
            # already written the instruction that fixes this, so it is fed back
            # VERBATIM through `agent_loop.reflection_text`, which is the same
            # mechanism the loop has used since 2026-08-06 rather than a second
            # copy of it.
            #
            # The message goes into the PROMPT and never into `ctx.errors`. A
            # validator message can quote the value it rejected, `ctx.errors` is
            # joined into the exception a caller logs, and a rejected value is a
            # candidate's own words. So the record carries the exception CLASS
            # and the prompt carries the sentence.
            ctx.feedback = str(exc)
            ctx.truncated = False
            ctx.errors.append(f"{FAILURE_SCHEMA_VIOLATION} ({type(exc).__name__})")
            logger.info(
                "llm_router.schema_violation task=%s model=%s attempt=%d error=%s",
                ctx.task_type, ctx.model, attempts, type(exc).__name__,
            )
            return {"attempts": attempts, "error": FAILURE_SCHEMA_VIOLATION}
        # Accepted. Any feedback from an earlier attempt has done its job and
        # must not travel further.
        ctx.feedback = None

    return {"attempts": attempts, "result": result.content, "error": None}


def _terminal(state: RouterState) -> dict[str, Any]:
    return {}


def _build_graph() -> Any:
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
    messages: list[dict[str, Any]],
    response_format_json: bool = False,
    session: AsyncSession | None = None,
    timeout: float | None = None,
    total_budget: float | None = None,
    validate: Callable[[str], None] | None = None,
) -> str:
    """Run a completion for `task_type` through the router.

    `messages` uses the [{"role": "system"|"user"|"assistant", "content": str}]
    shape every caller already writes. The system messages are lifted out,
    joined, and put back at the head of the array here rather than at
    twenty-odd call sites.

    `session` is retained in the signature and is now unused. It used to load
    credentials from `llm_provider_keys`; it is kept because roughly twenty
    callers pass it, and churning all of them to remove an argument would be a
    large diff whose only effect is a smaller signature. Removing it is a
    reasonable later cleanup, not part of a vendor consolidation.

    `validate` is the caller's DETERMINISTIC output check, and passing one turns
    a schema violation into a retry that carries the validator's own message
    back to the model (W4.1). It must raise, with a message, when the response
    does not satisfy the schema; a Pydantic `model_validate` is the shape this
    was built for. It is only accepted alongside `response_format_json`, because
    a schema is a claim about structured output and the feedback wording says so
    in as many words.

    Returns the assistant text. Raises `LLMUnavailableError` when the vendor
    could not serve the call within its budget, `ModelRefusal` when the vendor
    answered and declined, or `ResponseTruncated` when every answer it gave was
    cut off at the completion budget. The last two are subclasses, so an
    existing `except LLMUnavailableError` still degrades.
    """
    if validate is not None and not response_format_json:
        raise ValueError(
            "validate is only accepted with response_format_json=True: a "
            "schema violation is a claim about structured output, and the "
            "feedback the router sends back says 'return the corrected result "
            "in the same JSON shape'"
        )
    # ONE span per logical call, and it is the only tracer (OpenTelemetry only,
    # owner ruling 2026-09-24; the second, vendor-hosted tracer that used to
    # wrap this is deleted). It wraps the WHOLE call rather than one attempt: what
    # matters operationally is whether this call eventually produced an answer
    # and how long it took, not that attempt two was rate limited. `model_for`
    # is resolved here rather than left inside: a span with no
    # `gen_ai.request.model` is unqueryable in every GenAI dashboard. It is the
    # identical pure lookup `_invoke_llm_inner` makes as its first statement, so
    # an unknown task type still raises the identical error from the identical
    # function, one frame earlier.
    with otel.genai_span(
        otel.OPERATION_CHAT,
        request_model=model_for(task_type),
        task_type=task_type,
    ):
        return await _invoke_llm_inner(
            task_type, messages, response_format_json, timeout, total_budget,
            validate,
        )


async def _invoke_llm_inner(
    task_type: str,
    messages: list[dict[str, Any]],
    response_format_json: bool,
    timeout: float | None,
    total_budget: float | None,
    validate: Callable[[str], None] | None = None,
) -> str:
    """The retry loop itself.

    Split out so `invoke_llm` is only the span wrapper, and so a tracing
    failure can never be mistaken for a router bug.
    """
    model = model_for(task_type)  # raises ValueError on an unknown task type

    if provider_is_written_off():
        raise LLMUnavailableError(
            f"{PROVIDER} is written off by operator action; "
            f"task_type={task_type} using the caller's fallback"
        )

    key = key_for_model(model)
    if key is None:
        # Names the variable, and names the model it belongs to. "No credential
        # configured" with two keys in play would leave an operator checking
        # the wrong one, and the symptom is one tier of tasks degrading while
        # the other looks perfectly healthy.
        raise LLMUnavailableError(
            f"No credential configured for model {model} "
            f"({ENV_VAR_FOR_MODEL[model]} is unset); task_type={task_type}"
        )
    if _is_cooling_down(key):
        logger.warning(
            "llm_router.cooling_down task_type=%s key=%s, using caller fallback "
            "until the half-open recovery probe is due",
            task_type, key.fingerprint,
        )
        raise LLMUnavailableError(
            f"The credential for model {model} is cooling down for "
            f"task_type={task_type}"
        )

    request_timeout = timeout if timeout is not None else timeout_for(task_type)
    budget = total_budget if total_budget is not None else total_budget_for(task_type)
    max_tokens = max_tokens_for(task_type)

    # THE COST CEILING REFUSES BEFORE THE WORK (W4.7), exactly as the latency
    # ceiling does. Checked against the WORST case one attempt could produce,
    # because a check against the typical case would let the pathological one
    # through, which is the only case a ceiling exists for.
    ceiling = cost_ceiling_for(task_type)
    worst_case = _worst_case_cost_usd(model, messages, max_tokens)
    if worst_case > ceiling:
        _record_cost_refusal(
            task_type=task_type,
            model=model,
            estimated_usd=worst_case,
            ceiling_usd=ceiling,
            stage="before the first attempt",
        )
        raise LLMUnavailableError(
            f"the request for task_type={task_type} on {model} could cost up "
            f"to {worst_case:.4f} USD against a {ceiling:.4f} USD ceiling; the "
            f"prompt is far larger than the context budget allows and was "
            f"refused rather than sent"
        )

    async with httpx.AsyncClient(timeout=request_timeout) as client:
        ctx = _RouteContext(
            task_type=task_type,
            model=model,
            key=key,
            messages=list(messages),
            json_mode=response_format_json,
            client=client,
            retry_budget=retry_budget_for(task_type),
            max_tokens=max_tokens,
            temperature=temperature_for(task_type),
            attempt_timeout=request_timeout,
            deadline=time.monotonic() + budget if budget else None,
            validate=validate,
            cost_ceiling_usd=ceiling,
        )
        final: RouterState = await _router_graph.ainvoke(
            {"task_type": task_type, "attempts": 0, "result": None,
             "error": None, "ctx": ctx},
            config={"recursion_limit": _RECURSION_LIMIT},
        )

    result = final.get("result")
    if result is not None:
        return result
    if ctx.refusal_reason is not None:
        raise ModelRefusal(
            f"{PROVIDER} declined task_type={task_type} on {model}: "
            f"{'; '.join(ctx.errors)}",
            reason=ctx.refusal_reason,
        )
    if ctx.truncated:
        raise ResponseTruncated(
            f"{PROVIDER} truncated task_type={task_type} on {model}: "
            f"{'; '.join(ctx.errors)}",
            max_completion_tokens=ctx.max_tokens,
        )
    raise LLMUnavailableError(
        f"{PROVIDER} exhausted for task_type={task_type}: {'; '.join(ctx.errors)}",
        unknown_outcome=ctx.unknown_outcome,
    )


async def chat_completion(
    role_hint: str,
    messages: list[dict[str, Any]],
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
