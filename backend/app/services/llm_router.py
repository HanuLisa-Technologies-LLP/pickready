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

import asyncio
import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from app.services import tracing
from app.config.llm_providers import (
    MIN_CEILING_FRACTION,
    MIN_USEFUL_MAX_TOKENS,
    PROVIDER_MODELS,
    DEFAULT_TEMPERATURE,
    max_tokens_for,
    temperature_for,
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


#: "…but can only afford 2674." — OpenRouter, HTTP 402.
_OPENROUTER_AFFORD_RE = re.compile(r"can only afford (\d+)", re.IGNORECASE)
#: "…on tokens per day (TPD): Limit 100000, Used 99729, Requested 4133." — Groq,
#: HTTP 429. What is left is Limit - Used, which is what we may still ask for.
_GROQ_QUOTA_RE = re.compile(
    r"Limit (\d+), Used (\d+), Requested (\d+)", re.IGNORECASE
)


def affordable_max_tokens(exc: Exception) -> int | None:
    """The output ceiling the provider says it CAN still serve, or None.

    Pure and side-effect free (unit-tested directly).

    THE BUG THIS CLOSES. A 402 from OpenRouter and a token-quota 429 from Groq
    both look terminal, and the router treated them that way: the 402 condemned
    the whole OpenRouter account for the call and the 429 burned a retry. But
    neither says "no". Both say "not THAT many", and both name the number they
    would accept:

      OpenRouter 402: "You requested up to 4096 tokens, but can only afford
                       2674."
      Groq 429:       "tokens per day (TPD): Limit 100000, Used 99729,
                       Requested 4133."

    Asking again for a ceiling the provider has already told us it can cover is
    the one retry guaranteed to be worth making. Returning None means the body
    named no usable number, and the caller keeps the existing behaviour.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    try:
        body = exc.response.text
    except Exception:  # noqa: BLE001 — a body we cannot read tells us nothing
        return None
    if not body:
        return None

    match = _OPENROUTER_AFFORD_RE.search(body)
    if match:
        return int(match.group(1))

    match = _GROQ_QUOTA_RE.search(body)
    if match:
        limit, used = int(match.group(1)), int(match.group(2))
        return max(limit - used, 0)
    return None


def is_account_level_failure(exc: Exception) -> bool:
    """True when `exc` says the provider ACCOUNT is unusable (pure; tested).

    A status in `_ACCOUNT_LEVEL_STATUSES` that ALSO names a still-affordable
    ceiling is deliberately excluded: the account is solvent, we simply asked
    for more than it covers, and `affordable_max_tokens` turns that into a
    retry rather than writing the provider off for the rest of the call.
    """
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _ACCOUNT_LEVEL_STATUSES
        and not _is_retryable_ceiling(affordable_max_tokens(exc))
    )


def _is_retryable_ceiling(ceiling: int | None, requested: int | None = None) -> bool:
    """A stated ceiling is worth re-asking for only if it can carry real work.

    Two independent bars, and both matter. The absolute floor rejects a ceiling
    that is useless for any task (a provider with 271 tokens of quota left). The
    fractional bar rejects a ceiling that is useless for THIS task: it is
    relative to what the caller asked for, because 2600 tokens is a complete JD
    and a truncated report.
    """
    if ceiling is None or ceiling < MIN_USEFUL_MAX_TOKENS:
        return False
    if requested is not None and ceiling < requested * MIN_CEILING_FRACTION:
        return False
    return True


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


async def _persist_key_health(statement: str, params: dict) -> bool:
    """Write one key-health row on the breaker's OWN session.

    THE BUG THIS FIXES, AND IT WAS A BAD ONE
    ----------------------------------------
    Both health writes used to run on the CALLER's session and finish with
    `await session.commit()`. On a Celery task that is harmless. On a request
    handler it is not: `commit()` ends the handler's transaction in the middle
    of the request, so the `async with session.begin()` block that FastAPI's
    dependency opened is closed underneath it and every subsequent statement
    raises

        InvalidRequestError: Can't operate on closed transaction inside
        context manager.

    Traced live on 2026-08-06 in `assessments.respond`. The sequence is: write
    the candidate's answer and flush, classify the answer (a model call), the
    call fails often enough to condemn a key, the breaker commits, and the very
    next read -- loading the transcript to write the following question --
    raises. The candidate gets a 500 mid-assessment.

    Note WHEN that happens: a key is condemned exactly when providers are
    failing, which is precisely the moment the degradation paths exist to carry
    the candidate through. The bookkeeping meant to protect availability was
    destroying it, and only under the conditions it was written for.

    Its own session is the fix, and it is also simply correct: breaker state is
    not part of the caller's unit of work. It must persist whether or not the
    caller's transaction commits, and it must not drag the caller's uncommitted
    work in with it.

    Returns True when the row was written. Never raises -- bookkeeping must not
    crash a task, and a lost health update costs one extra failed attempt later.
    """
    try:
        from app.core.db import get_session_factory

        factory = get_session_factory()
        async with factory() as own:
            # `llm_provider_keys` is a platform table with no tenant column and
            # no RLS policy, so this needs no tenant scoping.
            await own.execute(text(statement), params)
            await own.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.debug("llm_router.key_health_write_failed", exc_info=True)
        return False


async def _try_persist_key_health(statement: str, params: dict) -> bool:
    """`_persist_key_health` with a second net under it.

    The implementation above already swallows everything, so this looks
    redundant and is not: both callers run INSIDE a candidate's live turn, and
    this is the exact shape of bug that was just removed from this module -- a
    piece of bookkeeping taking down the request it was supposed to be invisible
    to. A future edit that lets an exception out of the writer must not be able
    to reach the caller.
    """
    try:
        return await _persist_key_health(statement, params)
    except Exception:  # noqa: BLE001
        logger.debug("llm_router.key_health_write_raised", exc_info=True)
        return False


async def _record_failure(key: _RouterKey, session: AsyncSession | None) -> None:
    st = _state(key.fingerprint)
    st.consecutive_failures += 1
    if st.consecutive_failures >= _FAILURE_THRESHOLD:
        st.unhealthy_until = time.monotonic() + _COOLDOWN_SECONDS
        if key.db_id is not None:
            # `session` is deliberately no longer used for this. See
            # `_persist_key_health`: committing the caller's transaction here
            # 500ed a candidate's turn every time a key was condemned mid-request.
            await _try_persist_key_health(
                "UPDATE llm_provider_keys SET healthy = false, last_error_at = :at "
                "WHERE id = :id",
                {"at": datetime.now(timezone.utc), "id": key.db_id},
            )


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
    if needs_db_clear and key.db_id is not None:
        # Own session, for the same reason as `_record_failure`. This one is if
        # anything worse on the caller's: a SUCCESS is the common case, so the
        # stray commit fired on ordinary healthy traffic and not only during an
        # outage.
        written = await _try_persist_key_health(
            "UPDATE llm_provider_keys "
            "SET healthy = true, last_error_at = NULL "
            "WHERE id = :id AND (healthy = false OR last_error_at IS NOT NULL)",
            {"id": key.db_id},
        )
        if written:
            key.db_healthy = True
            logger.info(
                "llm_router.key_recovered provider=%s fingerprint=%s",
                key.provider, key.fingerprint,
            )


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
    model: str, messages: list[dict], json_mode: bool, max_tokens: int,
    temperature: float,
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        # Passed in from config/llm_providers.temperature_for(task_type), never
        # a literal. It used to be 0.1 hardcoded here AND again in Gemini's
        # generationConfig below, so the two could drift apart silently and no
        # task could be scored deterministically.
        "temperature": temperature,
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
    max_tokens: int, temperature: float,
) -> str:
    resp = await client.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key.api_key}"},
        json=_openai_style_payload(
            GROQ_MODEL, messages, json_mode, max_tokens, temperature
        ),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_openrouter(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool,
    max_tokens: int, temperature: float,
) -> str:
    resp = await client.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key.api_key}"},
        json=_openai_style_payload(
            OPENROUTER_MODEL, messages, json_mode, max_tokens, temperature
        ),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_gemini(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool,
    max_tokens: int, temperature: float,
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
        "temperature": temperature,
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
    #: Sampling temperature for this call, from
    #: config/llm_providers.temperature_for(task_type). Defaulted to the
    #: deterministic value so a context built by hand in a test scores
    #: reproducibly rather than inheriting whatever the last edit happened to
    #: leave in the router.
    temperature: float = DEFAULT_TEMPERATURE
    #: PER-PROVIDER overrides of `max_tokens`, learned mid-call from a provider
    #: that told us what it could still afford (see `affordable_max_tokens`).
    #:
    #: Scoped per provider on purpose. OpenRouter running a nearly empty prepaid
    #: balance is a fact about OpenRouter, and letting its 2674-token ceiling
    #: follow the call onto a Gemini key with a full daily quota would quietly
    #: truncate a report for no reason. A provider only ever constrains itself.
    provider_max_tokens: dict[str, int] = field(default_factory=dict)
    #: Providers that answered with an ACCOUNT-level failure during THIS call.
    #: Their remaining keys are skipped (see `_is_account_level_failure`).
    dead_providers: set[str] = field(default_factory=set)
    #: Hard wall-clock ceiling for ONE attempt. Not the same thing as the httpx
    #: timeout, and that difference is the point — see `_attempt`.
    attempt_timeout: float | None = None
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
    # The ceiling this PROVIDER gets: the task's, unless this provider has
    # already told us mid-call that it can only serve less.
    effective_max_tokens = min(
        getattr(ctx, "max_tokens", 4096),
        getattr(ctx, "provider_max_tokens", {}).get(
            key.provider, getattr(ctx, "max_tokens", 4096)
        ),
    )
    started = time.monotonic()
    try:
        # asyncio.wait_for, NOT just the httpx timeout. httpx's `read` timeout
        # only fires when NO byte arrives for that long, and OpenRouter pads a
        # pending completion with whitespace to hold the connection open — so a
        # jd_generation attempt with a 15s read timeout was observed running 47
        # seconds, and background chains stopped returning at all. This is a
        # wall-clock ceiling on the attempt that no amount of keep-alive traffic
        # can defeat. It is also clamped to whatever is left of the whole call's
        # budget, so one attempt can never overrun the deadline that the
        # conditional edge is enforcing between attempts.
        bounds = [
            t
            for t in (
                getattr(ctx, "attempt_timeout", None),
                (ctx.deadline - time.monotonic()) if ctx.deadline is not None else None,
            )
            if t is not None
        ]
        limit = min(bounds) if bounds else None  # None == unbounded (tests)
        result = await asyncio.wait_for(
            _PROVIDER_CALLERS[key.provider](
                ctx.client, key, ctx.messages, ctx.json_mode,
                effective_max_tokens, ctx.temperature,
            ),
            timeout=limit,
        )
    except (
        httpx.HTTPError, KeyError, IndexError, ValueError, asyncio.TimeoutError
    ) as exc:
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

        # The provider may have refused the CEILING rather than the request.
        # If it named a smaller number it can still serve, adopt that number and
        # re-ask THIS key — do not advance the index and do not condemn the
        # provider. Only ever reduce, so this cannot ratchet upward, and the
        # attempt counter still moves, so the retry budget bounds the loop.
        ceiling = affordable_max_tokens(exc)
        if (
            _is_retryable_ceiling(ceiling, effective_max_tokens)
            and ceiling < effective_max_tokens
            and hasattr(ctx, "provider_max_tokens")
        ):
            logger.warning(
                "llm_router.max_tokens_reduced task_type=%s provider=%s "
                "fingerprint=%s from=%d to=%d status=%s",
                ctx.task_type, key.provider, key.fingerprint,
                effective_max_tokens, ceiling, status_code,
            )
            ctx.provider_max_tokens[key.provider] = ceiling
            return {
                "current_index": index,
                "attempts": attempts + 1,
                "error": type(exc).__name__,
            }

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
    # One traced run per logical call, named after the task type, so the
    # LangSmith dashboard separates the scorers from report synthesis from the
    # interviewer with no per-agent wiring. It wraps the WHOLE chain rather
    # than one attempt: what matters operationally is whether this call
    # eventually produced an answer and how long the fallback walk took, not
    # that key three of twenty-one was rate limited.
    with tracing.trace_llm(task_type, messages=messages) as run:
        try:
            result = await _invoke_llm_inner(
                task_type, messages, response_format_json, session, timeout,
                total_budget,
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
    session: AsyncSession | None,
    timeout: float | None,
    total_budget: float | None,
) -> str:
    """The routing chain itself. Split out so `invoke_llm` is only the tracing
    wrapper, and so a tracing failure can never be mistaken for a router bug."""
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
            temperature=temperature_for(task_type),
            attempt_timeout=request_timeout,
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
