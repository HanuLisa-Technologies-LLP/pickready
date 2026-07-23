"""OTP unit tests (FR-1.4 / ESD §5 / contract rev 2) — DB-free: pure
hash/check core, multi-context login resolution, context tokens, and the
in-memory rate limiter with an injected clock."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import security
from app.models.enums import OTPChannel, Role, UserStatus
from app.models.user import OTPChallenge, User
from app.services.otp import (
    PORTAL_CANDIDATE,
    PORTAL_ORG,
    PORTAL_OWNER,
    ContextTokenInvalid,
    OTPConsumed,
    OTPExpired,
    OTPInvalid,
    OTPLocked,
    RateLimiter,
    UserNotFound,
    check_challenge,
    decode_context_token,
    eligible_login_users,
    make_context_token,
    pending_channels_for,
    portal_for_role,
    resolve_login,
)

OWNER_EMAIL = "manjuchro@gmail.com"

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


# ── Multi-context login resolution (contract rev 2, pure core) ───────────────

def make_user(
    role: Role = Role.recruiter,
    email: str = "user@example.com",
    status: UserStatus = UserStatus.active,
    tenant_id: uuid.UUID | None = None,
) -> User:
    u = User(role=role, email=email, status=status, tenant_id=tenant_id)
    u.id = uuid.uuid4()  # normally DB-assigned; needed for identity checks
    return u


def test_portal_mapping() -> None:
    assert portal_for_role(Role.super_admin) == PORTAL_OWNER
    assert portal_for_role(Role.candidate) == PORTAL_CANDIDATE
    for role in (Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager):
        assert portal_for_role(role) == PORTAL_ORG


def test_single_user_resolves_to_session_shape() -> None:
    user = make_user()
    resolution = resolve_login([user], owner_email=OWNER_EMAIL)
    assert resolution.user is user
    assert resolution.is_multi is False
    assert resolution.contexts == []


def test_multiple_users_resolve_to_contexts_shape() -> None:
    a = make_user(role=Role.recruiter, tenant_id=uuid.uuid4())
    b = make_user(role=Role.hr_manager, tenant_id=uuid.uuid4())
    resolution = resolve_login([a, b], owner_email=OWNER_EMAIL)
    assert resolution.user is None
    assert resolution.is_multi is True
    assert resolution.contexts == [a, b]


def test_disabled_users_are_filtered_out() -> None:
    active = make_user()
    disabled = make_user(status=UserStatus.disabled)
    resolution = resolve_login([active, disabled], owner_email=OWNER_EMAIL)
    # The disabled context is dropped -> exactly one remains -> single-session
    assert resolution.user is active


def test_all_disabled_raises_user_not_found() -> None:
    with pytest.raises(UserNotFound):
        resolve_login(
            [make_user(status=UserStatus.disabled)], owner_email=OWNER_EMAIL
        )


def test_empty_match_raises_user_not_found() -> None:
    with pytest.raises(UserNotFound):
        resolve_login([], owner_email=OWNER_EMAIL)


def test_invited_users_are_eligible() -> None:
    # New staff activate via first OTP login — invited must NOT be filtered.
    invited = make_user(status=UserStatus.invited)
    assert eligible_login_users([invited], owner_email=OWNER_EMAIL) == [invited]


def test_fake_owner_is_treated_as_nonexistent() -> None:
    # Owner invariant, defense in depth: a super_admin row with any other
    # email must never be able to log in.
    impostor = make_user(role=Role.super_admin, email="evil@example.com")
    assert eligible_login_users([impostor], owner_email=OWNER_EMAIL) == []
    with pytest.raises(UserNotFound):
        resolve_login([impostor], owner_email=OWNER_EMAIL)


def test_real_owner_is_eligible_case_insensitively() -> None:
    owner = make_user(role=Role.super_admin, email="Manjuchro@GMAIL.com")
    assert eligible_login_users([owner], owner_email=OWNER_EMAIL) == [owner]


def test_fake_owner_filtered_from_multi_context_list() -> None:
    impostor = make_user(role=Role.super_admin, email="evil@example.com")
    org = make_user(role=Role.client, tenant_id=uuid.uuid4())
    resolution = resolve_login([impostor, org], owner_email=OWNER_EMAIL)
    assert resolution.user is org  # impostor dropped -> single context


# ── Dual-OTP pending channels (FR-1.2, pure) ─────────────────────────────────

def test_client_first_login_reports_both_pending() -> None:
    client = make_user(role=Role.client)
    assert pending_channels_for(client) == ["email", "sms"]


def test_client_with_email_verified_pends_sms_only() -> None:
    client = make_user(role=Role.client)
    client.email_verified_at = NOW
    assert pending_channels_for(client) == ["sms"]


def test_fully_verified_client_has_no_pending() -> None:
    client = make_user(role=Role.client)
    client.email_verified_at = NOW
    client.phone_verified_at = NOW
    assert pending_channels_for(client) == []


def test_non_client_roles_never_pend() -> None:
    for role in (Role.recruiter, Role.hr_manager, Role.hiring_manager,
                 Role.super_admin, Role.candidate):
        assert pending_channels_for(make_user(role=role)) == []


# ── Context tokens (signed, short-TTL, typed) ────────────────────────────────

def test_context_token_roundtrip() -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]
    token = make_context_token("who@example.com", ids)
    payload = decode_context_token(token)
    assert payload["type"] == "context"
    assert payload["identifier"] == "who@example.com"
    assert payload["user_ids"] == [str(i) for i in ids]
    assert payload["jti"]


def test_context_token_jti_is_unique_per_token() -> None:
    a = decode_context_token(make_context_token("x@y.com", [uuid.uuid4()]))
    b = decode_context_token(make_context_token("x@y.com", [uuid.uuid4()]))
    assert a["jti"] != b["jti"]


def test_expired_context_token_rejected() -> None:
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    token = make_context_token("x@y.com", [uuid.uuid4()], now=stale)
    with pytest.raises(ContextTokenInvalid):
        decode_context_token(token)


def test_tampered_context_token_rejected() -> None:
    token = make_context_token("x@y.com", [uuid.uuid4()])
    with pytest.raises(ContextTokenInvalid):
        decode_context_token(token[:-4] + "AAAA")


def test_access_token_is_not_a_context_token() -> None:
    # A regular access JWT must never pass as a context token (different
    # audience + type claims).
    access = security.create_access_token(uuid.uuid4(), "client", uuid.uuid4())
    with pytest.raises(ContextTokenInvalid):
        decode_context_token(access)
