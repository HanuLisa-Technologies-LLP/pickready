"""OTP request/verify service (FR-1.1..1.5 / ESD §5).

- Codes are generated via core.security.generate_otp and stored ONLY as an
  HMAC hash in `otp_challenges` (never logged, never plaintext at rest).
- Sending is enqueued via Celery (`pickready.send_email` / `pickready.send_sms`)
  — never inline (claude.md rule 4).
- Attempt/rate-limit counters live in Redis for atomic increments, with an
  in-memory fallback when Redis is unavailable (dev/tests).
- First CLIENT login requires BOTH email and phone verified before internal
  tokens are issued (FR-1.2) — `verify_challenge` returns the still-pending
  channels instead of tokens until both are stamped.
"""
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_INTERNAL
from app.models.enums import OTPChannel, Role, UserStatus
from app.models.user import OTPChallenge, User

# Max OTP *requests* per identifier per hour (abuse guard, FR-1.4).
# ASSUMPTION: PRD sets no explicit request cap; 10/hour is a sensible default.
REQUEST_CAP_PER_HOUR = 10
_REQUEST_WINDOW_SECONDS = 3600


# ── Typed errors ─────────────────────────────────────────────────────────────

class OTPError(Exception):
    """Base for OTP failures."""


class ChallengeNotFound(OTPError):
    pass


class UserNotFound(OTPError):
    pass


class OTPExpired(OTPError):
    pass


class OTPConsumed(OTPError):
    pass


class OTPInvalid(OTPError):
    def __init__(self, attempts_remaining: int):
        self.attempts_remaining = attempts_remaining
        super().__init__(f"invalid code ({attempts_remaining} attempts remaining)")


class OTPLocked(OTPError):
    """Max attempts exceeded — identifier is in its cooldown window."""


class OTPRateLimited(OTPError):
    """Too many OTP requests for this identifier."""


# ── Rate limiter (Redis with in-memory fallback) ─────────────────────────────

class RateLimiter:
    """Counters/flags in Redis for atomicity under concurrency (ESD §5),
    degrading to per-process memory when Redis is unreachable (dev/tests).

    `clock` is injectable for tests (monotonic seconds)."""

    def __init__(self, redis_client=None, clock: Callable[[], float] = time.monotonic):
        self._redis = redis_client
        self._clock = clock
        self._mem: dict[str, tuple[float, int]] = {}  # key -> (expires_at, count)

    async def incr(self, key: str, ttl_seconds: int) -> int:
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, ttl_seconds, nx=True)
                count, _ = await pipe.execute()
                return int(count)
            except Exception:  # noqa: BLE001 — redis down: fall back to memory
                pass
        now = self._clock()
        expires_at, count = self._mem.get(key, (now, 0))
        if expires_at <= now:
            expires_at, count = now + ttl_seconds, 0
        count += 1
        self._mem[key] = (expires_at, count)
        return count

    async def set_flag(self, key: str, ttl_seconds: int) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(key, "1", ex=ttl_seconds)
                return
            except Exception:  # noqa: BLE001
                pass
        self._mem[key] = (self._clock() + ttl_seconds, 1)

    async def is_set(self, key: str) -> bool:
        if self._redis is not None:
            try:
                return bool(await self._redis.exists(key))
            except Exception:  # noqa: BLE001
                pass
        expires_at, count = self._mem.get(key, (0.0, 0))
        return count > 0 and expires_at > self._clock()


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        client = None
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        except Exception:  # noqa: BLE001 — package/connection unavailable (dev/tests)
            client = None
        _limiter = RateLimiter(client)
    return _limiter


def _lock_key(identifier: str) -> str:
    return f"otp:lock:{identifier}"


def _request_key(identifier: str) -> str:
    return f"otp:req:{identifier}"


# ── Pure check core (DB-free, unit-testable) ─────────────────────────────────

def check_challenge(
    challenge: OTPChallenge,
    code: str,
    *,
    now: datetime,
    max_attempts: int,
) -> None:
    """Validate a code against a challenge. Mutates `challenge.attempts` on a
    wrong code. Raises OTPConsumed / OTPExpired / OTPLocked / OTPInvalid;
    returns None on success (caller marks consumed)."""
    if challenge.consumed_at is not None:
        raise OTPConsumed("challenge already consumed")
    if now >= challenge.expires_at:
        raise OTPExpired("challenge expired")
    attempts = challenge.attempts or 0
    if attempts >= max_attempts:
        raise OTPLocked("max attempts exceeded")
    if not security.verify_otp(code, challenge.identifier, challenge.code_hash):
        challenge.attempts = attempts + 1
        raise OTPInvalid(attempts_remaining=max_attempts - challenge.attempts)


# ── Request / verify (async, DB-backed) ──────────────────────────────────────

@dataclass
class RequestResult:
    challenge: OTPChallenge
    # Plaintext code, held in memory only so the API layer can expose it as a
    # dev-only `debug_code` field (never logged, never returned in production).
    code: str


@dataclass
class VerifyResult:
    user: User
    access_token: str | None = None
    refresh_token: str | None = None
    # Channels still requiring verification before tokens can be issued
    # (client first-login dual OTP, FR-1.2).
    pending_channels: list[str] = field(default_factory=list)

    @property
    def authenticated(self) -> bool:
        return self.access_token is not None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _find_user(
    session: AsyncSession, identifier: str, channel: OTPChannel, audience: str
) -> User | None:
    ident_col = User.email if channel == OTPChannel.email else User.phone
    stmt = select(User).where(ident_col == identifier)
    if audience == AUDIENCE_CANDIDATE:
        stmt = stmt.where(User.role == Role.candidate)
    else:
        stmt = stmt.where(User.role != Role.candidate)
    return (await session.execute(stmt)).scalars().first()


async def request_otp(
    session: AsyncSession,
    *,
    identifier: str,
    channel: OTPChannel,
    audience: str = AUDIENCE_INTERNAL,
    limiter: RateLimiter | None = None,
    now: datetime | None = None,
) -> RequestResult:
    """Generate + store (hashed) a challenge and enqueue delivery.

    Raises OTPLocked (cooldown active), OTPRateLimited, UserNotFound.
    """
    settings = get_settings()
    limiter = limiter or get_limiter()
    now = now or _utcnow()

    if await limiter.is_set(_lock_key(identifier)):
        raise OTPLocked("identifier is in cooldown")
    if await limiter.incr(_request_key(identifier), _REQUEST_WINDOW_SECONDS) > REQUEST_CAP_PER_HOUR:
        raise OTPRateLimited("too many OTP requests")

    user = await _find_user(session, identifier, channel, audience)
    if user is None:
        if audience == AUDIENCE_CANDIDATE and channel == OTPChannel.email:
            # ASSUMPTION: candidates self-register on the portal via OTP —
            # a candidate user is created on first email-OTP request
            # (FR-9.1: candidates log in separately).
            user = User(role=Role.candidate, email=identifier, tenant_id=None,
                        status=UserStatus.invited)
            session.add(user)
            await session.flush()
        else:
            raise UserNotFound(f"no account for this {channel.value} identifier")

    code = security.generate_otp()
    challenge = OTPChallenge(
        user_id=user.id,
        identifier=identifier,
        channel=channel,
        code_hash=security.hash_otp(code, identifier),
        expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        attempts=0,
    )
    session.add(challenge)
    await session.flush()

    # Delivery is always a Celery task — never inline (claude.md rule 4).
    from app.workers.celery_app import celery_app

    if channel == OTPChannel.email:
        celery_app.send_task(
            "pickready.send_email",
            args=[
                str(user.tenant_id) if user.tenant_id else None,
                identifier,
                "otp",
                {"code": code, "ttl_minutes": settings.otp_ttl_minutes},
            ],
        )
    else:
        celery_app.send_task(
            "pickready.send_sms",
            args=[identifier, f"Your PickReady OTP is {code}. Valid for "
                              f"{settings.otp_ttl_minutes} minutes."],
        )
    return RequestResult(challenge=challenge, code=code)


async def verify_challenge(
    session: AsyncSession,
    *,
    challenge_id: uuid.UUID | str,
    code: str,
    limiter: RateLimiter | None = None,
    now: datetime | None = None,
) -> VerifyResult:
    """Verify a code. On success: consume the challenge, stamp the verified
    channel, and issue JWTs — unless the user is a client on first login with
    the other channel still unverified (dual-challenge, FR-1.2), in which case
    `pending_channels` is returned instead of tokens."""
    settings = get_settings()
    limiter = limiter or get_limiter()
    now = now or _utcnow()

    challenge = await session.get(OTPChallenge, uuid.UUID(str(challenge_id)))
    if challenge is None:
        raise ChallengeNotFound("unknown challenge")
    if await limiter.is_set(_lock_key(challenge.identifier)):
        raise OTPLocked("identifier is in cooldown")

    try:
        check_challenge(challenge, code, now=now, max_attempts=settings.otp_max_attempts)
    except OTPInvalid:
        await session.flush()  # persist the incremented attempt count
        if (challenge.attempts or 0) >= settings.otp_max_attempts:
            await limiter.set_flag(
                _lock_key(challenge.identifier), settings.otp_cooldown_minutes * 60
            )
        raise

    challenge.consumed_at = now
    user = await session.get(User, challenge.user_id)
    if user is None:
        raise UserNotFound("challenge user no longer exists")

    if challenge.channel == OTPChannel.email:
        user.email_verified_at = now
    else:
        user.phone_verified_at = now
    if user.status == UserStatus.invited:
        user.status = UserStatus.active

    # Client first login: BOTH channels must verify before tokens (FR-1.2).
    # Once both are stamped, subsequent logins accept either channel.
    pending: list[str] = []
    if user.role == Role.client:
        if user.email_verified_at is None:
            pending.append(OTPChannel.email.value)
        if user.phone_verified_at is None:
            pending.append(OTPChannel.sms.value)

    await session.flush()
    if pending:
        return VerifyResult(user=user, pending_channels=pending)

    audience = AUDIENCE_CANDIDATE if user.role == Role.candidate else AUDIENCE_INTERNAL
    access = security.create_access_token(
        user.id, user.role.value, user.tenant_id, audience=audience
    )
    refresh = security.create_refresh_token(user.id, audience=audience)
    return VerifyResult(user=user, access_token=access, refresh_token=refresh)
