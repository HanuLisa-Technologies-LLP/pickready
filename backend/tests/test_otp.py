"""OTP unit tests (FR-1.4 / ESD §5) — DB-free: pure hash/check core plus the
in-memory rate limiter with an injected clock."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core import security
from app.models.enums import OTPChannel
from app.models.user import OTPChallenge
from app.services.otp import (
    OTPConsumed,
    OTPExpired,
    OTPInvalid,
    OTPLocked,
    RateLimiter,
    check_challenge,
)

NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)
MAX_ATTEMPTS = 5


def make_challenge(
    code: str = "123456",
    identifier: str = "user@example.com",
    expires_in_minutes: int = 5,
    attempts: int = 0,
    consumed_at: datetime | None = None,
) -> OTPChallenge:
    return OTPChallenge(
        identifier=identifier,
        channel=OTPChannel.email,
        code_hash=security.hash_otp(code, identifier),
        expires_at=NOW + timedelta(minutes=expires_in_minutes),
        attempts=attempts,
        consumed_at=consumed_at,
    )


# ── Hashing ──────────────────────────────────────────────────────────────────

def test_hash_verify_roundtrip() -> None:
    h = security.hash_otp("123456", "a@b.com")
    assert security.verify_otp("123456", "a@b.com", h) is True
    assert security.verify_otp("654321", "a@b.com", h) is False


def test_hash_bound_to_identifier() -> None:
    # The same code hashed for a different identifier must not verify —
    # hashes cannot be replayed across identifiers.
    h = security.hash_otp("123456", "a@b.com")
    assert security.verify_otp("123456", "other@b.com", h) is False


def test_hash_is_not_plaintext() -> None:
    assert "123456" not in security.hash_otp("123456", "a@b.com")


# ── check_challenge core ─────────────────────────────────────────────────────

def test_correct_code_passes() -> None:
    challenge = make_challenge()
    check_challenge(challenge, "123456", now=NOW, max_attempts=MAX_ATTEMPTS)
    assert challenge.attempts == 0  # success does not consume an attempt


def test_wrong_code_raises_and_increments_attempts() -> None:
    challenge = make_challenge()
    with pytest.raises(OTPInvalid) as excinfo:
        check_challenge(challenge, "000000", now=NOW, max_attempts=MAX_ATTEMPTS)
    assert challenge.attempts == 1
    assert excinfo.value.attempts_remaining == MAX_ATTEMPTS - 1


def test_expired_challenge_rejected() -> None:
    challenge = make_challenge(expires_in_minutes=5)
    late = NOW + timedelta(minutes=5)  # TTL boundary is exclusive of success
    with pytest.raises(OTPExpired):
        check_challenge(challenge, "123456", now=late, max_attempts=MAX_ATTEMPTS)


def test_not_yet_expired_at_ttl_minus_second() -> None:
    challenge = make_challenge(expires_in_minutes=5)
    just_in_time = NOW + timedelta(minutes=4, seconds=59)
    check_challenge(challenge, "123456", now=just_in_time, max_attempts=MAX_ATTEMPTS)


def test_consumed_challenge_rejected_even_with_correct_code() -> None:
    challenge = make_challenge(consumed_at=NOW)
    with pytest.raises(OTPConsumed):
        check_challenge(challenge, "123456", now=NOW, max_attempts=MAX_ATTEMPTS)


def test_attempt_limit_locks_even_with_correct_code() -> None:
    challenge = make_challenge(attempts=MAX_ATTEMPTS)
    with pytest.raises(OTPLocked):
        check_challenge(challenge, "123456", now=NOW, max_attempts=MAX_ATTEMPTS)


def test_attempts_accumulate_to_lockout() -> None:
    challenge = make_challenge()
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(OTPInvalid):
            check_challenge(challenge, "999999", now=NOW, max_attempts=MAX_ATTEMPTS)
    assert challenge.attempts == MAX_ATTEMPTS
    # The next try — even with the CORRECT code — is locked out.
    with pytest.raises(OTPLocked):
        check_challenge(challenge, "123456", now=NOW, max_attempts=MAX_ATTEMPTS)


# ── RateLimiter (in-memory fallback, mocked clock) ───────────────────────────

class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_incr_counts_within_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    assert await limiter.incr("k", ttl_seconds=60) == 1
    assert await limiter.incr("k", ttl_seconds=60) == 2
    assert await limiter.incr("k", ttl_seconds=60) == 3


async def test_incr_window_expires_and_resets() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    await limiter.incr("k", ttl_seconds=60)
    await limiter.incr("k", ttl_seconds=60)
    clock.advance(61)
    assert await limiter.incr("k", ttl_seconds=60) == 1  # fresh window


async def test_lock_flag_expires_after_cooldown() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    await limiter.set_flag("otp:lock:x", ttl_seconds=900)  # 15-minute cooldown
    assert await limiter.is_set("otp:lock:x") is True
    clock.advance(899)
    assert await limiter.is_set("otp:lock:x") is True
    clock.advance(2)
    assert await limiter.is_set("otp:lock:x") is False


async def test_flag_unset_by_default() -> None:
    limiter = RateLimiter(clock=FakeClock())
    assert await limiter.is_set("nope") is False
