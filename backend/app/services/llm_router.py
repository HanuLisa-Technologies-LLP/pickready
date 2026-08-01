"""LangGraph-routed LLM provider router (21 keys, ESD §8.4 + 2026-07-27 spec).

Every LLM call in PickReady — JD generation, technical question banks,
behavioural assessment, report synthesis, email composition, re-ranking,
extraction — goes through this module. A compiled LangGraph state machine
decides which key to try next, and walks a fallback chain until one succeeds or
the task's retry budget is exhausted.

ROUTING
-------
`config/llm_providers.py` holds the policy as data: a preferred PROVIDER ORDER
per task type, a per-task timeout, and a per-task retry budget. Within each
provider tier the router ROUND-ROBINS across that provider's healthy keys, so
concurrent assessments spread load instead of hammering key #1 into a 429.

Key source: the `llm_provider_keys` table (encrypted at rest) when it has rows;
otherwise the populated env slots (3-21 of them). Empty slots are skipped.

THE GRAPH
---------
    START -> attempt --(retry)--> attempt
                    --(success)-> succeeded -> END
                    --(fail)----> exhausted -> END

`attempt` picks the next eligible key, calls it, and records success/failure.
`should_continue` is the conditional edge. The loop is bounded twice over: by
the task's retry budget and by an explicit LangGraph recursion limit, so a
pathological chain can never spin.

CIRCUIT BREAKER
---------------
2+ consecutive failures marks a key unhealthy (persisted to its DB row when it
came from the table) and it is skipped until a 15 min cooldown elapses. One
provider failing never crashes the calling task — the chain is walked first,
then a typed LLMUnavailableError is raised so the Celery task's retry/backoff
policy (or the caller's deterministic fallback) takes over.

RECOVERY (the `healthy` flag must never permanently disable a key):
  * Any successful call clears the in-memory breaker AND restores
    `healthy = true` / `last_error_at = NULL` on the DB row.
  * A row with `healthy = false` is only skipped while its cooldown is still
    running. Once the cooldown has elapsed — or if `last_error_at` is NULL —
    the key goes half-open and is retried, regardless of the persisted flag.
  * If every key is still inside its cooldown, the router fails fast with
    `LLMUnavailableError`; callers use their explicit deterministic fallback.

MONITORING
----------
Every attempt is logged with provider, fingerprint, latency and outcome, and
aggregated into `provider_stats()` (success rate, avg latency, attempt counts)
for the admin health endpoint. Success rates below 50% are logged as warnings.

SECURITY: API keys are never logged, and never included in exception messages.
"""
from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from app.config.llm_providers import (
    PROVIDER_MODELS,
    max_tokens_for,
    provider_order,
    retry_budget_for,
    timeout_for,
    total_budget_for,
)
from app.core.security import decrypt_secret
from app.models import LLMProviderKey

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Retained as module constants: existing callers and tests import these names.
GROQ_MODEL = PROVIDER_MODELS["groq"]
GEMINI_MODEL = PROVIDER_MODELS["gemini"]
OPENROUTER_MODEL = PROVIDER_MODELS["openrouter"]

_FAILURE_THRESHOLD = 2          # consecutive failures before tripping
_COOLDOWN_SECONDS = 15 * 60     # 15 min cool-off (claude.md rule 9)
_REQUEST_TIMEOUT = 90.0         # default; per-task overrides in llm_providers
_MIN_SUCCESS_RATE = 0.5         # below this, a provider is logged as degraded
_STATS_ALARM_MIN_ATTEMPTS = 5   # don't alarm on a single unlucky call

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised only after every key in the fallback chain has been exhausted."""


@dataclass
class _RouterKey:
    """A usable (decrypted) key plus its routing metadata."""
    provider: str
    api_key: str
    fingerprint: str            # stable id for breaker state ("db:<uuid>" / "env:<name>")
    db_id: Any | None = None    # llm_provider_keys.id when table-sourced
    db_healthy: bool = True     # the persisted `healthy` flag as loaded


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    unhealthy_until: float = 0.0  # time.monotonic() deadline; 0 = healthy


@dataclass
class _ProviderStats:
    """Rolling counters per provider — monitoring only, never routing input."""
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    throttles: int = 0            # HTTP 429 specifically
    total_latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "throttles": self.throttles,
            "success_rate": (
                round(self.successes / self.attempts, 3) if self.attempts else None
            ),
            "avg_latency_ms": (
                round(self.total_latency_ms / self.attempts, 1) if self.attempts else None
            ),
        }


# In-memory breaker state, keyed by fingerprint. Worker processes are
# long-lived so this survives across tasks; the DB `healthy`/`last_error_at`
# columns persist the state across process restarts for table-sourced keys.
_breaker: dict[str, _BreakerState] = {}

#: Rolling per-provider health counters (monitoring surface).
_stats: dict[str, _ProviderStats] = {}

#: Round-robin cursors, one per provider. `itertools.count` is monotonic and
#: cheap; the modulo against the live key count happens at chain-build time so
#: adding or removing a key never wedges the cursor.
_rr_cursor: dict[str, itertools.count] = {}


def _state(fingerprint: str) -> _BreakerState:
    if fingerprint not in _breaker:
        _breaker[fingerprint] = _BreakerState()
    return _breaker[fingerprint]


def _is_skippable(key: _RouterKey) -> bool:
    return time.monotonic() < _state(key.fingerprint).unhealthy_until


#: HTTP statuses that condemn the whole PROVIDER ACCOUNT, not the one key:
#: 401 the credentials are wrong, 402 the prepaid balance is spent, 403 the
#: account is disabled. Every sibling key bills the same account, so trying
#: them is guaranteed to fail — and each pointless attempt costs one unit of a
#: retry budget that a genuinely healthy provider further down the chain needs.
#: 429 is deliberately NOT here: a rate limit is per key and per minute, and
#: the sibling key is exactly the right thing to try next.
_ACCOUNT_LEVEL_STATUSES = frozenset({401, 402, 403})


def is_account_level_failure(exc: Exception) -> bool:
    """True when `exc` says the provider ACCOUNT is unusable (pure; tested)."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _ACCOUNT_LEVEL_STATUSES
    )


def _provider_stats(provider: str) -> _ProviderStats:
    if provider not in _stats:
        _stats[provider] = _ProviderStats()
    return _stats[provider]


def provider_stats() -> dict[str, dict[str, Any]]:
    """Snapshot of per-provider health counters. Contains no key material."""
    return {provider: st.as_dict() for provider, st in sorted(_stats.items())}


def reset_provider_stats() -> None:
    """Clear the monitoring counters (used by tests and the admin console)."""
    _stats.clear()


def _record_attempt(
    provider: str, latency_ms: float, *, success: bool, throttled: bool = False
) -> None:
    st = _provider_stats(provider)
    st.attempts += 1
    st.total_latency_ms += latency_ms
    if success:
        st.successes += 1
    else:
        st.failures += 1
    if throttled:
        st.throttles += 1
    if (
        st.attempts >= _STATS_ALARM_MIN_ATTEMPTS
        and st.successes / st.attempts < _MIN_SUCCESS_RATE
    ):
        logger.warning(
            "llm_router.provider_degraded provider=%s success_rate=%.2f attempts=%d "
            "throttles=%d",
            provider, st.successes / st.attempts, st.attempts, st.throttles,
        )


async def _record_failure(key: _RouterKey, session: AsyncSession | None) -> None:
    st = _state(key.fingerprint)
    st.consecutive_failures += 1
    if st.consecutive_failures >= _FAILURE_THRESHOLD:
        st.unhealthy_until = time.monotonic() + _COOLDOWN_SECONDS
        if key.db_id is not None and session is not None:
            try:
                await session.execute(
                    text(
                        "UPDATE llm_provider_keys SET healthy = false, last_error_at = :at "
                        "WHERE id = :id"
                    ),
                    {"at": datetime.now(timezone.utc), "id": key.db_id},
                )
                await session.commit()
            except Exception:  # noqa: BLE001 — breaker bookkeeping must never crash the task
                await session.rollback()


async def _record_success(key: _RouterKey, session: AsyncSession | None) -> None:
    """A success fully rehabilitates the key — in memory AND in the DB.

    This is the fix for the "all nine keys stuck at healthy = false forever"
    failure: the persisted flag is cleared on ANY success, not just when this
    process happened to be the one that tripped the breaker (after a restart
    the in-memory breaker is empty, so the old `was_tripped` guard meant a
    recovered key was never written back as healthy).
    """
    st = _state(key.fingerprint)
    was_tripped = st.consecutive_failures >= _FAILURE_THRESHOLD or st.unhealthy_until > 0.0
    st.consecutive_failures = 0
    st.unhealthy_until = 0.0
    needs_db_clear = was_tripped or not key.db_healthy
    if needs_db_clear and key.db_id is not None and session is not None:
        try:
            await session.execute(
                text(
                    "UPDATE llm_provider_keys "
                    "SET healthy = true, last_error_at = NULL "
                    "WHERE id = :id AND (healthy = false OR last_error_at IS NOT NULL)"
                ),
                {"id": key.db_id},
            )
            await session.commit()
            key.db_healthy = True
            logger.info(
                "llm_router.key_recovered provider=%s fingerprint=%s",
                key.provider, key.fingerprint,
            )
        except Exception:  # noqa: BLE001
            await session.rollback()


# ── Key loading ──────────────────────────────────────────────────────────────

def _env_keys() -> list[_RouterKey]:
    """Fallback when llm_provider_keys is empty: read the populated env slots.

    Elastic across the 21-key roster — `env_key_slots()` yields only the slots
    that actually carry a value, so 3 keys and 21 keys take the same path.
    """
    from app.config.llm_providers import env_key_slots

    return [
        _RouterKey(
            provider=provider,
            api_key=value,
            fingerprint=f"env:{provider}:{slot}",
        )
        for provider, slot, value in env_key_slots()
    ]


async def _load_keys(session: AsyncSession | None) -> list[_RouterKey]:
    if session is not None:
        try:
            rows = (
                await session.execute(
                    select(LLMProviderKey).order_by(
                        LLMProviderKey.provider, LLMProviderKey.priority
                    )
                )
            ).scalars().all()
            if rows:
                out: list[_RouterKey] = []
                for row in rows:
                    # Seed persisted health into the in-memory breaker so a
                    # restarted worker respects a still-cooling-down key.
                    fp = f"db:{row.id}"
                    # Half-open recovery: a persisted `healthy = false` only
                    # suppresses the key while its cooldown is still running.
                    # Once the cooldown has elapsed — or if last_error_at was
                    # never recorded — the key is retried anyway, so the flag
                    # can never permanently disable it.
                    if not row.healthy and row.last_error_at is not None:
                        last_error_at = row.last_error_at
                        if last_error_at.tzinfo is None:
                            last_error_at = last_error_at.replace(tzinfo=timezone.utc)
                        elapsed = (
                            datetime.now(timezone.utc) - last_error_at
                        ).total_seconds()
                        if elapsed < _COOLDOWN_SECONDS and fp not in _breaker:
                            _breaker[fp] = _BreakerState(
                                consecutive_failures=_FAILURE_THRESHOLD,
                                unhealthy_until=time.monotonic()
                                + (_COOLDOWN_SECONDS - elapsed),
                            )
                        elif elapsed >= _COOLDOWN_SECONDS:
                            # Cooldown expired: force the breaker open-to-half-open
                            # even if a stale in-memory deadline is still set.
                            _breaker[fp] = _BreakerState()
                            logger.info(
                                "llm_router.key_half_open provider=%s fingerprint=%s "
                                "cooldown_elapsed_s=%.0f",
                                row.provider, fp, elapsed,
                            )
                    elif not row.healthy:
                        # healthy = false with no last_error_at: unknown age,
                        # never leave it wedged — retry it.
                        _breaker[fp] = _BreakerState()
                    try:
                        api_key = decrypt_secret(row.key_encrypted)
                    except Exception:  # noqa: BLE001 — undecryptable key: skip, don't crash
                        continue
                    out.append(
                        _RouterKey(
                            provider=row.provider, api_key=api_key,
                            fingerprint=fp, db_id=row.id,
                            db_healthy=bool(row.healthy),
                        )
                    )
                if out:
                    return out
        except Exception:  # noqa: BLE001 — DB hiccup: fall back to env keys
            pass
    return _env_keys()


def rotate_within_provider(keys: list[_RouterKey], offset: int) -> list[_RouterKey]:
    """Rotate one provider's key list left by `offset` positions.

    This is the load-balancing primitive: the same set of keys, in the same
    relative order, but starting at a different one each call. Pure and
    side-effect free so it can be unit-tested directly.
    """
    if not keys:
        return []
    start = offset % len(keys)
    return keys[start:] + keys[:start]


def _next_offset(provider: str) -> int:
    if provider not in _rr_cursor:
        _rr_cursor[provider] = itertools.count()
    return next(_rr_cursor[provider])


def _build_chain(
    keys: list[_RouterKey], task_type: str, *, balance: bool = True
) -> list[_RouterKey]:
    """Ordered fallback chain for `task_type`.

    Provider tiers follow the task's preference order; within a tier the keys
    are round-robined so consecutive calls start on different keys. Ordering
    ACROSS tiers is never disturbed — a preferred provider's keys always come
    before the next provider's.
    """
    chain: list[_RouterKey] = []
    for provider in provider_order(task_type):
        tier = [k for k in keys if k.provider == provider]
        if not tier:
            continue
        chain.extend(
            rotate_within_provider(tier, _next_offset(provider)) if balance else tier
        )
    return chain


def probe_each_provider_first(chain: list[_RouterKey]) -> list[_RouterKey]:
    """Reorder a tier-grouped chain so EVERY provider is tried once before any
    provider gets a second key.

    Pure and side-effect free (unit-tested directly).

    THE BUG THIS CLOSES. `_build_chain` emits all of provider A's keys, then all
    of B's, then all of C's — while the retry budget bounds how many keys a call
    may actually burn. jd_generation has 3 keys per provider and a budget of 4,
    so a dead first tier consumed 3 attempts and a dead second tier the 4th: the
    third provider was NEVER reached. In production OpenRouter was 402ing (out of
    prepaid credit) and Gemini 429ing (free-tier quota), while three perfectly
    healthy Groq keys sat at positions 7-9 and were never called. Every AI
    feature fell back to its deterministic template and reported itself as
    "AI unavailable".

    Interleaving keeps the task's provider PREFERENCE intact — the first entry is
    still the preferred provider's first key — but guarantees that one dead tier
    can only ever cost one attempt before the next tier is tried.
    """
    tiers: list[list[_RouterKey]] = []
    for key in chain:
        if tiers and tiers[-1][0].provider == key.provider:
            tiers[-1].append(key)
        else:
            tiers.append([key])
    ordered: list[_RouterKey] = []
    for round_index in range(max((len(t) for t in tiers), default=0)):
        for tier in tiers:
            if round_index < len(tier):
                ordered.append(tier[round_index])
    return ordered


# ── Provider calls (httpx, no SDKs) ─────────────────────────────────────────

def _openai_style_payload(
    model: str, messages: list[dict], json_mode: bool, max_tokens: int
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        # Always explicit. OpenRouter prices an unbounded request at the model
        # maximum and 402s a prepaid account that cannot cover it — see
        # config/llm_providers.TASK_MAX_TOKENS.
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


async def _call_groq(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool,
    max_tokens: int,
) -> str:
    resp = await client.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key.api_key}"},
        json=_openai_style_payload(GROQ_MODEL, messages, json_mode, max_tokens),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_openrouter(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool,
    max_tokens: int,
) -> str:
    resp = await client.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key.api_key}"},
        json=_openai_style_payload(OPENROUTER_MODEL, messages, json_mode, max_tokens),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_gemini(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool,
    max_tokens: int,
) -> str:
    system_texts = [m["content"] for m in messages if m.get("role") == "system"]
    contents = [
        {
            "role": "model" if m.get("role") == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
        if m.get("role") != "system"
    ]
    body: dict[str, Any] = {"contents": contents}
    if system_texts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
    gen_config: dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": max_tokens,
    }
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    body["generationConfig"] = gen_config

    resp = await client.post(
        GEMINI_URL_TMPL.format(model=GEMINI_MODEL),
        # The key goes in a HEADER, never the query string. As `params={"key":
        # ...}` it landed in httpx's own INFO log line ("HTTP Request: POST
        # https://...:generateContent?key=AQ.Ab8RN6...") and from there into
        # Cloud Logging in plain text, which breaks this module's stated
        # guarantee that key material is never logged. Google documents
        # x-goog-api-key as equivalent to the query parameter.
        headers={"x-goog-api-key": key.api_key},
        json=body,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


_PROVIDER_CALLERS = {
    "groq": _call_groq,
    "gemini": _call_gemini,
    "openrouter": _call_openrouter,
}


# ── The LangGraph state machine ──────────────────────────────────────────────

@dataclass
class _RouteContext:
    """Everything a node needs that is NOT graph state.

    Held in a single opaque slot on the state dict. LangGraph never inspects
    or serialises it (this graph runs without a checkpointer), so an httpx
    client and a live DB session are safe to carry here.
    """
    task_type: str
    chain: list[_RouterKey]
    messages: list[dict]
    json_mode: bool
    client: httpx.AsyncClient
    session: AsyncSession | None
    retry_budget: int
    #: Wall-clock deadline (time.monotonic scale) for the WHOLE chain. The
    #: per-attempt timeout alone does not bound what a caller waits for: a 15s
    #: attempt timeout with a 4-key budget is still a 60s request. Set to None
    #: to disable (used by tests that drive the graph directly).
    deadline: float | None = None
    #: Output ceiling sent to every provider on this call. Defaulted so the
    #: tests that build a context by hand keep working unchanged.
    max_tokens: int = 4096
    #: Providers that answered with an ACCOUNT-level failure during THIS call.
    #: Their remaining keys are skipped (see `_is_account_level_failure`).
    dead_providers: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


class RouterState(TypedDict, total=False):
    """Graph state. Mirrors the spec's RouterState with the key index and
    retry counter that drive the conditional edge."""
    task_type: str
    current_index: int      # position in ctx.chain
    attempts: int           # keys actually CALLED (skips don't count)
    result: str | None
    error: str | None
    ctx: _RouteContext


def should_continue(state: RouterState) -> str:
    """Conditional edge: return the result, try the next key, or give up.

    Pure over the state dict — unit-tested directly in tests/test_llm_router.py.
    """
    if state.get("result") is not None:
        return "success"
    ctx: _RouteContext = state["ctx"]
    if state.get("attempts", 0) >= ctx.retry_budget:
        return "fail"
    if state.get("current_index", 0) >= len(ctx.chain):
        return "fail"
    # getattr, not attribute access: `should_continue` is a pure function over
    # the state dict and is driven directly by tests with a stand-in context.
    # A missing deadline means "unbounded", which is the safe reading.
    deadline = getattr(ctx, "deadline", None)
    if deadline is not None and time.monotonic() >= deadline:
        # Out of wall-clock. Stop here rather than starting an attempt whose
        # own timeout would run past a budget the caller is already over.
        ctx.errors.append("total time budget exhausted")
        return "fail"
    return "retry"


async def _attempt(state: RouterState) -> dict:
    """Try the next eligible key. Never raises — failures become state."""
    ctx: _RouteContext = state["ctx"]
    index = state.get("current_index", 0)
    attempts = state.get("attempts", 0)

    # Skip keys whose breaker is open, and every key belonging to a provider
    # whose ACCOUNT already failed in this call. Skips advance the index but do
    # NOT consume retry budget — budget counts real network attempts.
    dead = getattr(ctx, "dead_providers", frozenset())
    while index < len(ctx.chain) and (
        _is_skippable(ctx.chain[index]) or ctx.chain[index].provider in dead
    ):
        reason = (
            "account unusable"
            if ctx.chain[index].provider in dead
            else "circuit open"
        )
        ctx.errors.append(f"{ctx.chain[index].provider}: skipped ({reason})")
        index += 1
    if index >= len(ctx.chain):
        return {"current_index": index, "error": "chain exhausted"}

    key = ctx.chain[index]
    started = time.monotonic()
    try:
        result = await _PROVIDER_CALLERS[key.provider](
            ctx.client, key, ctx.messages, ctx.json_mode,
            getattr(ctx, "max_tokens", 4096),
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        # Rate limits, 5xx, timeouts, malformed responses — mark and move on to
        # the next key. Never include the key material in the message.
        latency_ms = (time.monotonic() - started) * 1000
        throttled = (
            isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
        )
        _record_attempt(key.provider, latency_ms, success=False, throttled=throttled)
        await _record_failure(key, ctx.session)
        # The HTTP status is the whole diagnosis (401 bad key, 402 out of
        # credit, 429 quota) and it was previously logged at INFO, which the
        # production log level drops. A silently-degraded AI feature then looks
        # identical to a working one that "chose" its deterministic fallback,
        # so this is WARNING and carries the status code. Never the key.
        status_code = (
            exc.response.status_code
            if isinstance(exc, httpx.HTTPStatusError)
            else None
        )
        logger.warning(
            "llm_router.attempt task_type=%s provider=%s fingerprint=%s "
            "latency_ms=%.0f outcome=failure error=%s status=%s throttled=%s",
            ctx.task_type, key.provider, key.fingerprint, latency_ms,
            type(exc).__name__, status_code, throttled,
        )
        ctx.errors.append(
            f"{key.provider}: {type(exc).__name__}"
            + (f" (HTTP {status_code})" if status_code else "")
        )
        if is_account_level_failure(exc) and hasattr(ctx, "dead_providers"):
            # Don't spend the rest of the budget on sibling keys that bill the
            # same dead account — the next PROVIDER is the useful thing to try.
            ctx.dead_providers.add(key.provider)
        return {
            "current_index": index + 1,
            "attempts": attempts + 1,
            "error": type(exc).__name__,
        }

    latency_ms = (time.monotonic() - started) * 1000
    _record_attempt(key.provider, latency_ms, success=True)
    await _record_success(key, ctx.session)
    logger.info(
        "llm_router.attempt task_type=%s provider=%s fingerprint=%s "
        "latency_ms=%.0f outcome=success",
        ctx.task_type, key.provider, key.fingerprint, latency_ms,
    )
    return {
        "current_index": index + 1,
        "attempts": attempts + 1,
        "result": result,
    }


def _terminal(state: RouterState) -> dict:
    """Terminal nodes carry no logic — they exist so the graph has explicit,
    inspectable success/failure states rather than an implicit exit."""
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
        {"success": "succeeded", "retry": "attempt", "fail": "exhausted"},
    )
    graph.add_edge("succeeded", END)
    graph.add_edge("exhausted", END)
    return graph.compile()


#: Compiled once at import. Stateless — every call passes its own RouterState.
_router_graph = _build_graph()

#: Hard ceiling on graph steps. The retry budget stops the loop long before
#: this; the limit exists so a future edit can never produce an endless graph.
_RECURSION_LIMIT = 64


# ── Public API ───────────────────────────────────────────────────────────────

async def invoke_llm(
    task_type: str,
    messages: list[dict],
    response_format_json: bool = False,
    session: AsyncSession | None = None,
    timeout: float | None = None,
    total_budget: float | None = None,
) -> str:
    """Run a chat completion for `task_type` through the LangGraph router.

    `task_type` is one of the five spec task types (jd_generation,
    technical_questions, behavioral_assessment, report_synthesis,
    email_composition) or a legacy role hint (rerank, extraction).

    `messages` uses the OpenAI shape: [{"role": "system"|"user"|"assistant",
    "content": str}]. Returns the assistant message text. Raises
    LLMUnavailableError only after every eligible key failed or was skipped.
    """
    keys = await _load_keys(session)
    # raises ValueError on an unknown type
    chain = probe_each_provider_first(_build_chain(keys, task_type))
    if not chain:
        raise LLMUnavailableError(
            "No LLM provider keys configured (llm_provider_keys table empty and "
            "no env keys set)"
        )

    if all(_is_skippable(k) for k in chain):
        logger.warning(
            "llm_router.all_keys_cooling_down task_type=%s keys=%d, "
            "using caller fallback until a half-open recovery probe is due",
            task_type, len(chain),
        )
        raise LLMUnavailableError(
            f"All LLM provider keys are cooling down for task_type={task_type}"
        )

    request_timeout = timeout if timeout is not None else timeout_for(task_type)
    # Two independent bounds. The per-attempt timeout stops ONE slow provider;
    # the deadline stops the chain of them. An interactive task walking four
    # dead keys at 15s each would otherwise be a 60-second request with a
    # 15-second timeout on it, which is the trap this closes.
    budget = total_budget if total_budget is not None else total_budget_for(task_type)
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        ctx = _RouteContext(
            task_type=task_type,
            chain=chain,
            messages=messages,
            json_mode=response_format_json,
            client=client,
            session=session,
            retry_budget=retry_budget_for(task_type),
            deadline=time.monotonic() + budget if budget else None,
            max_tokens=max_tokens_for(task_type),
        )
        final: RouterState = await _router_graph.ainvoke(
            {
                "task_type": task_type,
                "current_index": 0,
                "attempts": 0,
                "result": None,
                "error": None,
                "ctx": ctx,
            },
            config={"recursion_limit": _RECURSION_LIMIT},
        )

    result = final.get("result")
    if result is not None:
        return result
    raise LLMUnavailableError(
        f"All LLM providers exhausted for task_type={task_type}: "
        f"{'; '.join(ctx.errors)}"
    )


async def chat_completion(
    role_hint: str,
    messages: list[dict],
    response_format_json: bool = False,
    session: AsyncSession | None = None,
) -> str:
    """Backwards-compatible alias for `invoke_llm`.

    Every pre-2026-07-27 caller passes role_hint="rerank" or "extraction";
    those chains are unchanged in config/llm_providers.TASK_ROUTES, so this is
    a pure rename with the new routing machinery underneath.
    """
    return await invoke_llm(
        role_hint, messages, response_format_json=response_format_json, session=session
    )
