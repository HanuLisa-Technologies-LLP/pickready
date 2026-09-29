"""Six-digit security codes and their tickets, against the real Redis.

Registration and password security only (auth spec 2.7); never a login code.
"""
from __future__ import annotations

import time
import uuid

import pytest
from redis.exceptions import RedisError

from app.core import cache
from app.core.config import get_settings
from app.services import security_codes



async def _redis_or_skip() -> None:
    client = cache._redis()  # noqa: SLF001
    if client is None:
        pytest.skip("no Redis client could be built for REDIS_URL")
    try:
        await client.ping()
    except (RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")


def _subject() -> str:
    return f"Person-{uuid.uuid4().hex}@Codes.test"


async def _clear_cooldown(purpose: str, subject: str) -> None:
    await cache._redis().delete(security_codes._cooldown_key(purpose, subject))  # noqa: SLF001


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


async def test_a_code_is_six_digits_and_right_exactly_once() -> None:
    await _redis_or_skip()
    subject = _subject()
    code = await security_codes.issue_code("password_reset", subject)
    assert len(code) == 6 and code.isdigit()
    # Case of the address does not matter; the code is consumed on success.
    assert await security_codes.check_code("password_reset", subject.upper(), code) is True
    assert await security_codes.check_code("password_reset", subject, code) is False


async def test_redis_holds_an_hmac_never_the_code_or_the_address() -> None:
    await _redis_or_skip()
    subject = _subject()
    code = await security_codes.issue_code("company_register", subject)
    key = security_codes._key("company_register", subject)  # noqa: SLF001
    stored = await cache._redis().hgetall(key)  # noqa: SLF001
    assert code not in "".join(stored.values())
    assert subject.lower() not in key
    assert 0 < await cache._redis().ttl(key) <= get_settings().security_code_ttl_seconds  # noqa: SLF001


async def test_a_wrong_code_is_refused_and_counted() -> None:
    await _redis_or_skip()
    subject = _subject()
    code = await security_codes.issue_code("password_change", subject)
    assert await security_codes.check_code("password_change", subject, _wrong(code)) is False
    stored = await cache._redis().hgetall(security_codes._key("password_change", subject))  # noqa: SLF001
    assert stored["attempts"] == "1"
    assert await security_codes.check_code("password_change", subject, code) is True


async def test_the_attempt_limit_spends_the_code() -> None:
    await _redis_or_skip()
    subject = _subject()
    code = await security_codes.issue_code("password_change", subject)
    for _ in range(get_settings().security_code_max_attempts):
        assert await security_codes.check_code("password_change", subject, _wrong(code)) is False
    assert await security_codes.check_code("password_change", subject, code) is False


async def test_an_expired_code_is_refused() -> None:
    await _redis_or_skip()
    subject = _subject()
    code = await security_codes.issue_code("password_reset", subject)
    key = security_codes._key("password_reset", subject)  # noqa: SLF001
    await cache._redis().hset(key, "expires_at", str(int(time.time()) - 1))  # noqa: SLF001
    assert await security_codes.check_code("password_reset", subject, code) is False


async def test_a_resend_is_held_back_by_the_cooldown() -> None:
    await _redis_or_skip()
    subject = _subject()
    await security_codes.issue_code("company_register", subject)
    with pytest.raises(security_codes.CodeCooldown) as exc:
        await security_codes.issue_code("company_register", subject)
    assert exc.value.status_code == 429
    assert 0 < exc.value.retry_after <= get_settings().security_code_resend_seconds
    assert exc.value.headers["Retry-After"] == str(exc.value.retry_after)


async def test_a_new_code_replaces_the_old_one() -> None:
    await _redis_or_skip()
    subject = _subject()
    first = await security_codes.issue_code("company_register", subject)
    await _clear_cooldown("company_register", subject)
    second = await security_codes.issue_code("company_register", subject)
    if first == second:
        pytest.skip("the two draws collided (one in a million)")
    assert await security_codes.check_code("company_register", subject, first) is False
    assert await security_codes.check_code("company_register", subject, second) is True


async def test_a_code_is_bound_to_its_purpose_and_subject() -> None:
    await _redis_or_skip()
    subject = _subject()
    code = await security_codes.issue_code("password_reset", subject)
    assert await security_codes.check_code("password_change", subject, code) is False
    assert await security_codes.check_code("password_reset", _subject(), code) is False
    assert await security_codes.check_code("password_reset", subject, code) is True


async def test_malformed_input_is_simply_wrong() -> None:
    await _redis_or_skip()
    subject = _subject()
    await security_codes.issue_code("password_reset", subject)
    for entered in ("", "12345", "1234567", "abcdef"):
        assert await security_codes.check_code("password_reset", subject, entered) is False


async def test_only_the_three_purposes_exist() -> None:
    assert security_codes.PURPOSES == {"company_register", "password_change", "password_reset"}
    with pytest.raises(ValueError):
        await security_codes.issue_code("login", _subject())


async def test_no_store_means_no_code(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: None)
    with pytest.raises(security_codes.SecurityCodesUnavailable) as exc:
        await security_codes.issue_code("password_reset", _subject())
    assert exc.value.status_code == 503
    with pytest.raises(security_codes.SecurityCodesUnavailable):
        await security_codes.check_code("password_reset", _subject(), "123456")


async def test_a_ticket_is_single_use_and_names_its_subject() -> None:
    await _redis_or_skip()
    subject = _subject()
    ticket = await security_codes.mint_ticket("password_reset", subject)
    assert await security_codes.redeem_ticket("password_change", ticket) is None
    # A ticket offered to the wrong purpose is not spent by it.
    assert await security_codes.redeem_ticket("password_reset", ticket) == subject.lower()
    assert await security_codes.redeem_ticket("password_reset", ticket) is None
    assert await security_codes.redeem_ticket("password_reset", "") is None
