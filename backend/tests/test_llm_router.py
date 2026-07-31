"""LLM router circuit-breaker and key-health RECOVERY tests.

The bug these lock down: all nine `llm_provider_keys` rows sat at
`healthy = false` indefinitely, so `chat_completion` skipped every key, raised
LLMUnavailableError on every call, and matching silently degraded to the
placeholder "AI scoring unavailable" comment forever.

Invariants under test:
  * a success clears the in-memory breaker AND writes healthy = true /
    last_error_at = NULL back to the DB row, even when this process never saw
    the failure that tripped it (the post-restart case);
  * a persisted healthy = false only suppresses a key while its cooldown is
    running — afterwards the key goes half-open and is retried;
  * healthy = false with no last_error_at is never treated as wedged;
  * if every key is still cooling down the router fails fast, while elapsed
    cooldowns recover through the half-open path.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import llm_router
from app.services.llm_router import (
    _COOLDOWN_SECONDS,
    _FAILURE_THRESHOLD,
    _BreakerState,
    _RouterKey,
    LLMUnavailableError,
)


@pytest.fixture(autouse=True)
def _clean_breaker():
    llm_router._breaker.clear()
    yield
    llm_router._breaker.clear()


class _FakeSession:
    """Records the UPDATE statements the router issues against the key table."""

    def __init__(self):
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, params=None):
        self.statements.append((str(stmt), params or {}))
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):  # pragma: no cover — only on a DB error path
        self.rollbacks += 1


def _key(db_healthy: bool = True, fp: str = "db:k1") -> _RouterKey:
    return _RouterKey(
        provider="groq", api_key="secret", fingerprint=fp,
        db_id=uuid.uuid4(), db_healthy=db_healthy,
    )


# ── _record_success rehabilitates the key ───────────────────────────────────

@pytest.mark.asyncio
async def test_record_success_clears_in_memory_breaker():
    key = _key()
    llm_router._breaker[key.fingerprint] = _BreakerState(
        consecutive_failures=_FAILURE_THRESHOLD,
        unhealthy_until=time.monotonic() + _COOLDOWN_SECONDS,
    )
    await llm_router._record_success(key, _FakeSession())
    st = llm_router._breaker[key.fingerprint]
    assert st.consecutive_failures == 0
    assert st.unhealthy_until == 0.0
    assert not llm_router._is_skippable(key)


@pytest.mark.asyncio
async def test_record_success_restores_db_flag_after_a_restart():
    """The regression: fresh process (empty breaker), row loaded as unhealthy.
    The old code only wrote healthy = true when THIS process tripped the
    breaker, so the row stayed false forever."""
    key = _key(db_healthy=False)
    session = _FakeSession()
    await llm_router._record_success(key, session)
    assert session.commits == 1
    sql = session.statements[0][0].lower()
    assert "healthy = true" in sql
    assert "last_error_at = null" in sql
    assert key.db_healthy is True


@pytest.mark.asyncio
async def test_record_success_on_a_healthy_key_does_not_write():
    session = _FakeSession()
    await llm_router._record_success(_key(db_healthy=True), session)
    assert session.statements == []


@pytest.mark.asyncio
async def test_record_failure_trips_only_at_the_threshold():
    key = _key()
    session = _FakeSession()
    await llm_router._record_failure(key, session)
    assert not llm_router._is_skippable(key)          # 1 failure: still usable
    await llm_router._record_failure(key, session)
    assert llm_router._is_skippable(key)              # 2 failures: circuit open
    sql = session.statements[-1][0].lower()
    assert "healthy = false" in sql


# ── Half-open recovery from the persisted flag ──────────────────────────────

class _Row:
    def __init__(self, healthy: bool, last_error_at, provider="groq", priority=0):
        self.id = uuid.uuid4()
        self.provider = provider
        self.priority = priority
        self.healthy = healthy
        self.last_error_at = last_error_at
        self.key_encrypted = "enc"


class _LoadSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *a, **k):
        rows = self._rows

        class _Res:
            def scalars(self_inner):
                class _S:
                    def all(self_s):
                        return rows
                return _S()

        return _Res()


@pytest.fixture
def _plain_decrypt(monkeypatch):
    monkeypatch.setattr(llm_router, "decrypt_secret", lambda v: "secret")


@pytest.mark.asyncio
async def test_unhealthy_row_inside_cooldown_is_skipped(_plain_decrypt):
    row = _Row(healthy=False, last_error_at=datetime.now(timezone.utc))
    keys = await llm_router._load_keys(_LoadSession([row]))
    assert len(keys) == 1
    assert llm_router._is_skippable(keys[0]), "a fresh failure must still cool down"


@pytest.mark.asyncio
async def test_unhealthy_row_past_cooldown_goes_half_open(_plain_decrypt):
    stale = datetime.now(timezone.utc) - timedelta(seconds=_COOLDOWN_SECONDS + 60)
    row = _Row(healthy=False, last_error_at=stale)
    # A stale in-memory "open" deadline must not keep it wedged either.
    llm_router._breaker[f"db:{row.id}"] = _BreakerState(
        consecutive_failures=9, unhealthy_until=time.monotonic() + 9999
    )
    keys = await llm_router._load_keys(_LoadSession([row]))
    assert not llm_router._is_skippable(keys[0])
    assert keys[0].db_healthy is False  # flag still false, but the key IS retryable


@pytest.mark.asyncio
async def test_unhealthy_row_without_last_error_at_is_retryable(_plain_decrypt):
    row = _Row(healthy=False, last_error_at=None)
    keys = await llm_router._load_keys(_LoadSession([row]))
    assert not llm_router._is_skippable(keys[0])


@pytest.mark.asyncio
async def test_naive_last_error_at_does_not_crash_loading(_plain_decrypt):
    naive = datetime.utcnow() - timedelta(seconds=_COOLDOWN_SECONDS + 60)
    row = _Row(healthy=False, last_error_at=naive)
    keys = await llm_router._load_keys(_LoadSession([row]))
    assert not llm_router._is_skippable(keys[0])


# ── chat_completion: never permanently wedged ───────────────────────────────

@pytest.mark.asyncio
async def test_all_keys_in_cooldown_fail_fast(monkeypatch, caplog):
    keys = [_key(fp=f"db:k{i}") for i in range(3)]
    for k in keys:
        llm_router._breaker[k.fingerprint] = _BreakerState(
            consecutive_failures=_FAILURE_THRESHOLD,
            unhealthy_until=time.monotonic() + _COOLDOWN_SECONDS,
        )
    assert all(llm_router._is_skippable(k) for k in keys)

    monkeypatch.setattr(llm_router, "_load_keys", _returns(keys))
    attempts: list[str] = []

    async def _ok(client, key, messages, json_mode):
        attempts.append(key.fingerprint)
        return "recovered"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _ok)

    with caplog.at_level("WARNING"), pytest.raises(LLMUnavailableError):
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
    assert attempts == []
    assert any("all_keys_cooling_down" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_open_key_is_skipped_while_a_healthy_one_remains(monkeypatch):
    bad, good = _key(fp="db:bad"), _key(fp="db:good")
    llm_router._breaker[bad.fingerprint] = _BreakerState(
        consecutive_failures=_FAILURE_THRESHOLD,
        unhealthy_until=time.monotonic() + _COOLDOWN_SECONDS,
    )
    monkeypatch.setattr(llm_router, "_load_keys", _returns([bad, good]))
    attempts: list[str] = []

    async def _ok(client, key, messages, json_mode):
        attempts.append(key.fingerprint)
        return "ok"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _ok)
    assert await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}]) == "ok"
    assert attempts == ["db:good"]


@pytest.mark.asyncio
async def test_chain_exhaustion_still_raises_without_leaking_key_material(monkeypatch):
    keys = [_key(fp="db:k0"), _key(fp="db:k1")]
    monkeypatch.setattr(llm_router, "_load_keys", _returns(keys))

    async def _boom(client, key, messages, json_mode):
        raise httpx.ConnectError("down")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _boom)
    with pytest.raises(LLMUnavailableError) as exc:
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_success_after_recovery_clears_the_breaker_end_to_end(monkeypatch):
    key = _key(db_healthy=False, fp="db:recovered")
    session = _FakeSession()
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))

    async def _ok(client, k, messages, json_mode):
        return "fine"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _ok)
    await llm_router.chat_completion(
        "rerank", [{"role": "user", "content": "hi"}], session=session
    )
    assert key.db_healthy is True
    assert not llm_router._is_skippable(key)
    assert session.commits == 1


def _returns(keys):
    async def _loader(session):
        return keys
    return _loader
