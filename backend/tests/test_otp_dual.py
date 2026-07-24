"""Dual-channel OTP + rate-limiting unit tests (FR-1.2 / FR-1.4 / ESD §5).

DB-free like test_otp.py: the dual-channel dispatch DECISION, the
accept-either-channel / FR-1.2 stamping semantics, and the Redis-backed limiter
gates (hourly cap, 30s resend throttle, 15-minute cooldown) are all exercised
through pure helpers with an injected clock — no database session required.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import OTPChannel, Role, UserStatus
from app.models.user import User
from app.services.otp import (
    REQUEST_CAP_PER_HOUR,
    RESEND_THROTTLE_SECONDS,
    DispatchTarget,
    RateLimiter,
    _lock_key,
    _request_key,
    _resend_key,
    _stamp_channel,
    dispatch_targets,
    pending_channels_for,
)

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def make_user(
    role: Role = Role.recruiter,
    email: str | None = "user@example.com",
    phone: str | None = None,
    status: UserStatus = UserStatus.active,
) -> User:
    u = User(role=role, email=email, phone=phone, status=status, tenant_id=None)
    u.id = uuid.uuid4()
    return u


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── Dual-channel dispatch decision (pure) ────────────────────────────────────

def test_email_request_with_phone_on_file_fans_out_to_both() -> None:
    user = make_user(email="a@b.com", phone="+15551234567")
    targets = dispatch_targets("a@b.com", [user], OTPChannel.email)
    assert targets == [
        DispatchTarget(OTPChannel.email, "a@b.com"),
        DispatchTarget(OTPChannel.sms, "+15551234567"),
    ]


def test_sms_request_with_email_on_file_fans_out_to_both() -> None:
    user = make_user(email="a@b.com", phone="+15551234567")
    targets = dispatch_targets("+15551234567", [user], OTPChannel.sms)
    assert [t.channel for t in targets] == [OTPChannel.sms, OTPChannel.email]
    assert targets[1].destination == "a@b.com"


def test_single_contact_method_sends_only_that_channel() -> None:
    user = make_user(email="a@b.com", phone=None)
    targets = dispatch_targets("a@b.com", [user], OTPChannel.email)
    assert targets == [DispatchTarget(OTPChannel.email, "a@b.com")]


def test_alternate_equal_to_identifier_is_not_duplicated() -> None:
    # Defensive: if the alternate somehow equals the identifier, don't send
    # the same address twice.
    user = make_user(email="a@b.com", phone="a@b.com")
    targets = dispatch_targets("a@b.com", [user], OTPChannel.email)
    assert targets == [DispatchTarget(OTPChannel.email, "a@b.com")]


def test_conflicting_alternates_across_users_are_not_fanned_out() -> None:
    # Two accounts share the email but list different phones — we must NOT leak
    # the code to a phone the requester may not control.
    a = make_user(email="a@b.com", phone="+15550000001")
    b = make_user(email="a@b.com", phone="+15550000002")
    targets = dispatch_targets("a@b.com", [a, b], OTPChannel.email)
    assert targets == [DispatchTarget(OTPChannel.email, "a@b.com")]


def test_agreeing_alternate_across_users_is_fanned_out() -> None:
    a = make_user(email="a@b.com", phone="+15550000001")
    b = make_user(email="a@b.com", phone="+15550000001")
    targets = dispatch_targets("a@b.com", [a, b], OTPChannel.email)
    assert [t.channel for t in targets] == [OTPChannel.email, OTPChannel.sms]


# ── Accept-either-channel + FR-1.2 still enforced (pure stamping model) ───────

def test_dual_sent_challenge_stamps_only_requested_channel() -> None:
    # A client requests on email (code also texted). Verifying stamps ONLY the
    # channel the challenge was bound to — the requester might have read either
    # delivery, so we can only claim ownership of the requested channel.
    client = make_user(role=Role.client, email="c@corp.com", phone="+15551110000")
    _stamp_channel(client, OTPChannel.email, NOW)
    assert client.email_verified_at == NOW
    assert client.phone_verified_at is None


def test_client_first_login_one_channel_still_pends_the_other() -> None:
    # FR-1.2: a client's FIRST login must verify BOTH channels before tokens.
    # Dual-send does not shortcut this — one verified channel still leaves the
    # other pending, so tokens are withheld.
    client = make_user(role=Role.client, email="c@corp.com", phone="+15551110000")
    assert pending_channels_for(client) == ["email", "sms"]
    _stamp_channel(client, OTPChannel.email, NOW)  # verified via email delivery
    assert pending_channels_for(client) == ["sms"]  # tokens still withheld


def test_client_both_channels_verified_has_no_pending() -> None:
    client = make_user(role=Role.client, email="c@corp.com", phone="+15551110000")
    _stamp_channel(client, OTPChannel.email, NOW)
    _stamp_channel(client, OTPChannel.sms, NOW + timedelta(minutes=1))
    assert pending_channels_for(client) == []


def test_non_client_returning_login_accepts_either_channel() -> None:
    # A recruiter never dual-verifies — a single challenge (either channel) is
    # enough, so nothing is ever pending.
    rec = make_user(role=Role.recruiter, email="r@corp.com", phone="+15552220000")
    assert pending_channels_for(rec) == []


# ── Rate limiting / cooldown gates (limiter with injected clock) ─────────────

async def test_hourly_request_cap_is_five() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    key = _request_key("a@b.com")
    # First 5 requests are allowed; the 6th trips the cap.
    for _ in range(REQUEST_CAP_PER_HOUR):
        assert await limiter.incr(key, 3600) <= REQUEST_CAP_PER_HOUR
    assert await limiter.incr(key, 3600) > REQUEST_CAP_PER_HOUR
    assert REQUEST_CAP_PER_HOUR == 5


async def test_resend_throttle_blocks_within_30s_then_clears() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    key = _resend_key("a@b.com")
    await limiter.set_flag(key, RESEND_THROTTLE_SECONDS)
    assert await limiter.is_set(key) is True          # immediate resend blocked
    clock.advance(RESEND_THROTTLE_SECONDS - 1)
    assert await limiter.is_set(key) is True           # 29s later still blocked
    clock.advance(2)
    assert await limiter.is_set(key) is False           # after 30s allowed again
    assert RESEND_THROTTLE_SECONDS == 30


async def test_cooldown_flag_blocks_both_request_and_verify() -> None:
    # After max attempts a 15-minute lock is armed; BOTH request_otp and
    # verify_challenge consult the SAME lock key, so both are rejected.
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    identifier = "c@corp.com"
    await limiter.set_flag(_lock_key(identifier), 15 * 60)
    # request_otp gate and verify_challenge gate both read _lock_key(identifier).
    assert await limiter.is_set(_lock_key(identifier)) is True
    clock.advance(15 * 60 - 1)
    assert await limiter.is_set(_lock_key(identifier)) is True
    clock.advance(2)
    assert await limiter.is_set(_lock_key(identifier)) is False


async def test_request_and_resend_use_distinct_keys() -> None:
    # The 30s throttle and the hourly cap must not collide.
    assert _request_key("a@b.com") != _resend_key("a@b.com")
    assert _lock_key("a@b.com") not in (_request_key("a@b.com"), _resend_key("a@b.com"))
