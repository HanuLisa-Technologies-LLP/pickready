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

import asyncio
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
    """The CALLER's session. Records anything the router does to it.

    Since 2026-08-06 the correct recording is NOTHING. Key-health bookkeeping
    runs on its own session (`_persist_key_health`), because it used to call
    `commit()` on this one -- which, on a request handler, ends the handler's
    transaction mid-request and makes every later statement raise
    "Can't operate on closed transaction inside context manager". Traced live in
    `assessments.respond`: a candidate got a 500 mid-assessment every time a
    provider key was condemned during their turn.
    """

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


@pytest.fixture
def health_writes(monkeypatch):
    """Capture what the breaker persists, without needing a database.

    Patches the router's OWN-session writer, which is the seam that replaced
    writing through the caller's session.
    """
    captured: list[tuple[str, dict]] = []

    async def _capture(statement: str, params: dict) -> bool:
        captured.append((statement, params))
        return True

    monkeypatch.setattr(llm_router, "_persist_key_health", _capture)
    return captured


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
async def test_record_success_restores_db_flag_after_a_restart(health_writes):
    """The regression: fresh process (empty breaker), row loaded as unhealthy.
    The old code only wrote healthy = true when THIS process tripped the
    breaker, so the row stayed false forever."""
    key = _key(db_healthy=False)
    session = _FakeSession()
    await llm_router._record_success(key, session)
    assert len(health_writes) == 1
    sql = health_writes[0][0].lower()
    assert "healthy = true" in sql
    assert "last_error_at = null" in sql
    assert key.db_healthy is True
    # THE PROPERTY THAT MATTERS MOST: the caller's transaction is untouched.
    assert session.statements == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_record_success_on_a_healthy_key_does_not_write(health_writes):
    session = _FakeSession()
    await llm_router._record_success(_key(db_healthy=True), session)
    assert health_writes == []
    assert session.statements == []


@pytest.mark.asyncio
async def test_record_failure_trips_only_at_the_threshold(health_writes):
    key = _key()
    session = _FakeSession()
    await llm_router._record_failure(key, session)
    assert not llm_router._is_skippable(key)          # 1 failure: still usable
    await llm_router._record_failure(key, session)
    assert llm_router._is_skippable(key)              # 2 failures: circuit open
    sql = health_writes[-1][0].lower()
    assert "healthy = false" in sql
    assert session.statements == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_the_breaker_never_commits_the_callers_transaction(health_writes):
    """The bug this file's `_FakeSession` docstring describes, stated directly.

    A key is condemned exactly when providers are failing -- which is precisely
    when the degradation paths exist to carry a candidate through their
    assessment. Committing the caller's transaction there meant the bookkeeping
    written to protect availability was the thing destroying it, and only under
    the conditions it was written for.
    """
    session = _FakeSession()
    key = _key(db_healthy=False, fp="db:never-commit")
    for _ in range(_FAILURE_THRESHOLD + 1):
        await llm_router._record_failure(key, session)
    await llm_router._record_success(key, session)

    assert health_writes, "the breaker must still persist key health somewhere"
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.statements == []


@pytest.mark.asyncio
async def test_an_unreachable_database_does_not_break_the_health_write(monkeypatch):
    """The REAL writer swallows its own errors and reports False.

    Bookkeeping must not crash a task: a lost health update costs one extra
    failed attempt later, which is strictly better than a failed request.
    """
    def _no_engine():
        raise RuntimeError("no engine")

    monkeypatch.setattr("app.core.db.get_session_factory", _no_engine)
    assert await llm_router._persist_key_health("UPDATE x SET y = 1", {}) is False


@pytest.mark.asyncio
async def test_a_raising_health_write_never_reaches_the_caller(monkeypatch):
    """And if the writer itself ever regressed into raising, neither breaker
    entry point may propagate it -- they run inside a candidate's live turn."""
    async def _boom(statement, params):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(llm_router, "_persist_key_health", _boom)
    key = _key(db_healthy=False, fp="db:write-fails")

    await llm_router._record_success(key, _FakeSession())
    assert key.db_healthy is False  # not claimed as recovered on a failed write


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

    async def _ok(client, key, messages, json_mode, max_tokens, temperature=0.0):
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

    async def _ok(client, key, messages, json_mode, max_tokens, temperature=0.0):
        attempts.append(key.fingerprint)
        return "ok"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _ok)
    assert await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}]) == "ok"
    assert attempts == ["db:good"]


@pytest.mark.asyncio
async def test_chain_exhaustion_still_raises_without_leaking_key_material(monkeypatch):
    keys = [_key(fp="db:k0"), _key(fp="db:k1")]
    monkeypatch.setattr(llm_router, "_load_keys", _returns(keys))

    async def _boom(client, key, messages, json_mode, max_tokens, temperature=0.0):
        raise httpx.ConnectError("down")

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _boom)
    with pytest.raises(LLMUnavailableError) as exc:
        await llm_router.chat_completion("rerank", [{"role": "user", "content": "hi"}])
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_success_after_recovery_clears_the_breaker_end_to_end(
    monkeypatch, health_writes
):
    key = _key(db_healthy=False, fp="db:recovered")
    session = _FakeSession()
    monkeypatch.setattr(llm_router, "_load_keys", _returns([key]))

    async def _ok(client, k, messages, json_mode, max_tokens, temperature=0.0):
        return "fine"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _ok)
    await llm_router.chat_completion(
        "rerank", [{"role": "user", "content": "hi"}], session=session
    )
    assert key.db_healthy is True
    assert not llm_router._is_skippable(key)
    assert len(health_writes) == 1
    # End to end through the real entry point, the caller's transaction is still
    # its own. This is the assertion that would have caught the 500.
    assert session.commits == 0


def _returns(keys):
    async def _loader(session):
        return keys
    return _loader


@pytest.mark.asyncio
async def test_a_stalled_provider_is_abandoned_at_the_attempt_timeout(monkeypatch):
    """One slow provider must not hold a call open past its budget.

    Regression: httpx's `read` timeout only fires when NO byte arrives for that
    long, and OpenRouter pads a pending completion with whitespace to keep the
    connection alive. A jd_generation attempt with a 15s read timeout was
    observed running 47 seconds in production, and the background assessment
    tasks stopped returning at all. The attempt is now bounded by wall clock.
    """
    from app.services import llm_router

    llm_router._breaker.clear()

    async def _stalls(client, key, messages, json_mode, max_tokens, temperature=0.0):
        await asyncio.sleep(5)
        return "never reached"

    async def _fast(client, key, messages, json_mode, max_tokens, temperature=0.0):
        return "ok"

    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "openrouter", _stalls)
    monkeypatch.setitem(llm_router._PROVIDER_CALLERS, "groq", _fast)
    monkeypatch.setattr(
        llm_router,
        "_load_keys",
        lambda session: _resolved([
            llm_router._RouterKey(provider="openrouter", api_key="x", fingerprint="o1"),
            llm_router._RouterKey(provider="groq", api_key="x", fingerprint="g1"),
        ]),
    )

    started = time.monotonic()
    result = await llm_router.invoke_llm(
        "jd_generation", [{"role": "user", "content": "hi"}], timeout=0.2
    )
    elapsed = time.monotonic() - started

    # The stalled first-choice provider was abandoned and the healthy one served.
    assert result == "ok"
    assert elapsed < 4, f"attempt was not bounded: took {elapsed:.1f}s"


async def _resolved(value):
    return value


# ── Sampling policy (item 7: determinism where the task JUDGES) ──────────────

def test_every_judging_task_is_deterministic() -> None:
    """A scoring call must return the same grade for the same answer.

    Anything above zero means a candidate's grade depends partly on when they
    happened to be scored. That is indefensible in a hiring decision and, worse,
    unfalsifiable: a rescore that disagrees looks like a broken rubric rather
    than sampling noise.

    report_synthesis is included deliberately even though its output is prose.
    It STATES the grades a client reads, so it judges.
    """
    from app.config.llm_providers import temperature_for

    for task in ("behavioral_assessment", "report_synthesis", "rerank", "extraction"):
        assert temperature_for(task) == 0.0, (
            f"{task} samples at {temperature_for(task)}; scoring must be reproducible"
        )


def test_an_unknown_task_defaults_to_deterministic() -> None:
    """The safe direction. A new task that should have been creative reads a
    little flat; a new SCORING task silently sampling at 0.5 would make grades
    non-reproducible and nothing would announce it."""
    from app.config.llm_providers import temperature_for

    assert temperature_for("some_task_added_next_year") == 0.0


def test_the_conversation_turn_is_allowed_to_vary() -> None:
    """The one place sounding different to different people is the point.

    At 0.0 the interviewer repeats near-identical phrasing to every candidate,
    which is the "static script" complaint. WHAT is asked stays fixed by the
    framework; only the phrasing varies.
    """
    from app.config.llm_providers import temperature_for

    assert temperature_for("conversation_turn") > 0.5


def test_temperature_is_not_hardcoded_in_the_router() -> None:
    """It was previously a 0.1 literal in TWO places -- the OpenAI-style payload
    and Gemini's generationConfig -- so they could drift apart silently and no
    task could be made deterministic. Policy is data, in config/llm_providers."""
    import pathlib

    source = pathlib.Path(
        __file__
    ).resolve().parents[1].joinpath("app/services/llm_router.py").read_text(
        encoding="utf-8"
    )
    assert '"temperature": 0.1' not in source
    assert "temperature_for" in source
