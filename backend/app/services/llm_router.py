"""LLM provider router with fallback chains and a circuit breaker (ESD §8.4).

Nine keys (3x Groq, 3x Gemini, 3x OpenRouter) live in `llm_provider_keys`,
encrypted at rest; if the table is empty (fresh install, tests) the router
falls back to the raw env-var keys from Settings.

Chains:
  - rerank      (latency-sensitive):  Groq 1-3 -> Gemini 1-3 -> OpenRouter 1-3
  - extraction  (long-context):       Gemini 1-3 -> OpenRouter 1-3 -> Groq 1-3

Circuit breaker: 2+ consecutive failures marks a key unhealthy (persisted to
the DB row when the key came from the table) and it is skipped until a 15 min
cooldown elapses. One provider failing never crashes the calling task — the
chain is exhausted first, then a typed LLMUnavailableError is raised so the
Celery task's retry/backoff policy takes over.

SECURITY: API keys are never logged, and never included in exception messages.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models import LLMProviderKey

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GEMINI_MODEL = "gemini-2.0-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

#: provider order per role hint (ESD §8.4)
_CHAIN_ORDER: dict[str, list[str]] = {
    "rerank": ["groq", "gemini", "openrouter"],
    "extraction": ["gemini", "openrouter", "groq"],
}

_FAILURE_THRESHOLD = 2          # consecutive failures before tripping
_COOLDOWN_SECONDS = 15 * 60     # 15 min cool-off (claude.md rule 9)
_REQUEST_TIMEOUT = 90.0


class LLMUnavailableError(RuntimeError):
    """Raised only after every key in the fallback chain has been exhausted."""


@dataclass
class _RouterKey:
    """A usable (decrypted) key plus its routing metadata."""
    provider: str
    api_key: str
    fingerprint: str            # stable id for circuit-breaker state ("db:<uuid>" / "env:<name>")
    db_id: Any | None = None    # llm_provider_keys.id when table-sourced


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    unhealthy_until: float = 0.0  # time.monotonic() deadline; 0 = healthy


# In-memory breaker state, keyed by fingerprint. Worker processes are
# long-lived so this survives across tasks; the DB `healthy`/`last_error_at`
# columns persist the state across process restarts for table-sourced keys.
_breaker: dict[str, _BreakerState] = {}


def _state(fingerprint: str) -> _BreakerState:
    if fingerprint not in _breaker:
        _breaker[fingerprint] = _BreakerState()
    return _breaker[fingerprint]


def _is_skippable(key: _RouterKey) -> bool:
    return time.monotonic() < _state(key.fingerprint).unhealthy_until


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
    st = _state(key.fingerprint)
    was_tripped = st.consecutive_failures >= _FAILURE_THRESHOLD
    st.consecutive_failures = 0
    st.unhealthy_until = 0.0
    if was_tripped and key.db_id is not None and session is not None:
        try:
            await session.execute(
                text("UPDATE llm_provider_keys SET healthy = true WHERE id = :id"),
                {"id": key.db_id},
            )
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()


# ── Key loading ──────────────────────────────────────────────────────────────

def _env_keys() -> list[_RouterKey]:
    """Fallback when llm_provider_keys is empty: read the 9 env keys."""
    s = get_settings()
    out: list[_RouterKey] = []
    for provider, values in (
        ("groq", [s.groq_api_key_1, s.groq_api_key_2, s.groq_api_key_3]),
        ("gemini", [s.gemini_api_key_1, s.gemini_api_key_2, s.gemini_api_key_3]),
        ("openrouter", [s.openrouter_api_key_1, s.openrouter_api_key_2, s.openrouter_api_key_3]),
    ):
        for i, v in enumerate(values, start=1):
            if v:
                out.append(
                    _RouterKey(provider=provider, api_key=v, fingerprint=f"env:{provider}:{i}")
                )
    return out


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
                    if not row.healthy and row.last_error_at is not None:
                        elapsed = (
                            datetime.now(timezone.utc) - row.last_error_at
                        ).total_seconds()
                        if elapsed < _COOLDOWN_SECONDS and fp not in _breaker:
                            _breaker[fp] = _BreakerState(
                                consecutive_failures=_FAILURE_THRESHOLD,
                                unhealthy_until=time.monotonic()
                                + (_COOLDOWN_SECONDS - elapsed),
                            )
                    try:
                        api_key = decrypt_secret(row.key_encrypted)
                    except Exception:  # noqa: BLE001 — undecryptable key: skip, don't crash
                        continue
                    out.append(
                        _RouterKey(
                            provider=row.provider, api_key=api_key,
                            fingerprint=fp, db_id=row.id,
                        )
                    )
                if out:
                    return out
        except Exception:  # noqa: BLE001 — DB hiccup: fall back to env keys
            pass
    return _env_keys()


def _build_chain(keys: list[_RouterKey], role_hint: str) -> list[_RouterKey]:
    order = _CHAIN_ORDER[role_hint]
    chain: list[_RouterKey] = []
    for provider in order:
        chain.extend(k for k in keys if k.provider == provider)
    return chain


# ── Provider calls (httpx, no SDKs) ─────────────────────────────────────────

def _openai_style_payload(model: str, messages: list[dict], json_mode: bool) -> dict:
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


async def _call_groq(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool
) -> str:
    resp = await client.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key.api_key}"},
        json=_openai_style_payload(GROQ_MODEL, messages, json_mode),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_openrouter(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool
) -> str:
    resp = await client.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key.api_key}"},
        json=_openai_style_payload(OPENROUTER_MODEL, messages, json_mode),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_gemini(
    client: httpx.AsyncClient, key: _RouterKey, messages: list[dict], json_mode: bool
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
    gen_config: dict[str, Any] = {"temperature": 0.1}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    body["generationConfig"] = gen_config

    resp = await client.post(
        GEMINI_URL_TMPL.format(model=GEMINI_MODEL),
        params={"key": key.api_key},  # never logged
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


# ── Public API ───────────────────────────────────────────────────────────────

async def chat_completion(
    role_hint: Literal["rerank", "extraction"],
    messages: list[dict],
    response_format_json: bool = False,
    session: AsyncSession | None = None,
) -> str:
    """Run a chat completion through the fallback chain for `role_hint`.

    `messages` uses the OpenAI shape: [{"role": "system"|"user"|"assistant",
    "content": str}]. Returns the assistant message text. Raises
    LLMUnavailableError only after every eligible key failed or was skipped.
    """
    if role_hint not in _CHAIN_ORDER:
        raise ValueError(f"Unknown role_hint: {role_hint!r}")

    keys = await _load_keys(session)
    chain = _build_chain(keys, role_hint)
    if not chain:
        raise LLMUnavailableError(
            "No LLM provider keys configured (llm_provider_keys table empty and no env keys set)"
        )

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        for key in chain:
            if _is_skippable(key):
                errors.append(f"{key.provider}: skipped (circuit open)")
                continue
            try:
                result = await _PROVIDER_CALLERS[key.provider](
                    client, key, messages, response_format_json
                )
                await _record_success(key, session)
                return result
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                # Rate limits, 5xx, timeouts, malformed responses — mark and
                # move on to the next key. Never include the key material.
                await _record_failure(key, session)
                errors.append(f"{key.provider}: {type(exc).__name__}")

    raise LLMUnavailableError(
        f"All LLM providers exhausted for role_hint={role_hint}: {'; '.join(errors)}"
    )
