"""OTP request/verify service (FR-1.1..1.5 / ESD §5) + unified multi-context
login (API_CONTRACT.md rev 2).

- Codes are generated via core.security.generate_otp and stored ONLY as an
  HMAC hash in `otp_challenges` (never logged, never plaintext at rest).
- Sending is dispatched (`pickready.send_email` / `pickready.send_sms`)
  — never inline (claude.md rule 4).
- Attempt/rate-limit counters live in Redis for atomic increments, with an
  in-memory fallback when Redis is unavailable (dev/tests).
- First CLIENT login requires BOTH email and phone verified before internal
  tokens are issued (FR-1.2) — `verify_challenge` returns the still-pending
  channels instead of tokens until both are stamped.
- Unified login (rev 2): one identifier may belong to several users across
  roles/tenants (three portals, ONE login). Verification resolves ALL matching
  users; exactly one -> tokens as before, several -> a short-lived single-use
  `context_token` plus the list of workspaces, finalized via
  `select_context`.
"""
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

import jwt as pyjwt
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.core.security import ALGORITHM, AUDIENCE_CANDIDATE, AUDIENCE_INTERNAL
from app.models.enums import OTPChannel, Role, UserStatus
from app.models.tenant import Tenant
from app.models.user import OTPChallenge, User

# Max OTP *requests* per identifier per hour (abuse guard, FR-1.4).
REQUEST_CAP_PER_HOUR = 5
_REQUEST_WINDOW_SECONDS = 3600

# Minimum gap between two OTP requests for the same identifier. Distinct from
# the hourly cap so the UI can show "please wait 30 seconds" rather than the
# harsher hourly message.
RESEND_THROTTLE_SECONDS = 30

# Context tokens (multi-workspace login, contract rev 2): short-TTL proof of a
# successful OTP, exchanged for cookies via /auth/select-context. Single-use.
CONTEXT_TOKEN_TTL_MINUTES = 5
AUDIENCE_CONTEXT = "pickready:context"

# Portal identifiers returned in the context list (contract rev 2).
PORTAL_OWNER = "owner"
PORTAL_ORG = "org"
PORTAL_CANDIDATE = "candidate"


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
    """Too many OTP requests for this identifier (hourly cap)."""


class OTPResendThrottled(OTPError):
    """A code was already sent to this identifier within the last 30 seconds."""


class ContextTokenInvalid(OTPError):
    """Malformed, expired, or wrong-type context token."""


class ContextTokenConsumed(OTPError):
    """Context token already exchanged for a session (single-use)."""


class ContextUserMismatch(OTPError):
    """The selected user_id is not one of the token's login contexts."""


class AlreadyRegistered(OTPError):
    """A candidate account already exists for this email — sign in instead."""


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


def _resend_key(identifier: str) -> str:
    return f"otp:resend:{identifier}"


def _context_key(jti: str) -> str:
    return f"otp:ctx:{jti}"


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


# ── Pure multi-context resolution core (DB-free, unit-testable) ──────────────

def portal_for_role(role: Role | str) -> str:
    """Which portal a login context lands in: owner = super_admin role,
    candidate = candidate role, everything else is the shared org portal."""
    role = Role(role)
    if role == Role.super_admin:
        return PORTAL_OWNER
    if role == Role.candidate:
        return PORTAL_CANDIDATE
    return PORTAL_ORG


def eligible_login_users(
    users: Sequence[User], *, owner_email: str
) -> list[User]:
    """Pure filter for login eligibility:
    - disabled users never log in;
    - a super_admin row whose email is not the platform owner's is treated as
      nonexistent (owner invariant, defense in depth — the migration deletes
      such rows).
    Invited users ARE eligible — first OTP login is how they activate."""
    out: list[User] = []
    for u in users:
        if u.status == UserStatus.disabled:
            continue
        if u.role == Role.super_admin and (
            (u.email or "").strip().lower() != (owner_email or "").strip().lower()
        ):
            continue
        out.append(u)
    return out


@dataclass
class LoginResolution:
    """Shape of the post-OTP response: exactly one user -> issue a session;
    several -> present a workspace chooser (contexts + context_token)."""
    user: User | None = None
    contexts: list[User] = field(default_factory=list)

    @property
    def is_multi(self) -> bool:
        return len(self.contexts) > 1


def resolve_login(users: Sequence[User], *, owner_email: str) -> LoginResolution:
    """Pure mapper: matched users -> single-session vs multi-context shape.
    Raises UserNotFound when nobody eligible remains."""
    eligible = eligible_login_users(users, owner_email=owner_email)
    if not eligible:
        raise UserNotFound("no account for this identifier")
    if len(eligible) == 1:
        return LoginResolution(user=eligible[0])
    return LoginResolution(contexts=eligible)


def pending_channels_for(user: User) -> list[str]:
    """Client first login: BOTH channels must verify before tokens (FR-1.2).
    Once both are stamped, subsequent logins accept either channel."""
    pending: list[str] = []
    if user.role == Role.client:
        if user.email_verified_at is None:
            pending.append(OTPChannel.email.value)
        if user.phone_verified_at is None:
            pending.append(OTPChannel.sms.value)
    return pending


# ── Dual-channel dispatch decision (pure, DB-free) ───────────────────────────

@dataclass(frozen=True)
class DispatchTarget:
    """One delivery of the SINGLE challenge code."""
    channel: OTPChannel
    destination: str


def _distinct(values) -> str | None:
    """The one non-empty value shared by every candidate, else None."""
    seen = {v.strip() for v in values if v and v.strip()}
    return seen.pop() if len(seen) == 1 else None


def dispatch_targets(
    identifier: str, users: Sequence[User], requested: OTPChannel
) -> list[DispatchTarget]:
    """Decide where ONE challenge code is delivered.

    The requested channel is always the primary target and always addresses the
    identifier the caller typed. When the resolved user(s) also expose the OTHER
    contact method, the same code is dispatched there too, so the user may enter
    whichever code arrives first (a single challenge, two deliveries).

    # ASSUMPTION: when several users share the identifier but disagree on the
    # alternate contact, we do NOT fan the code out to every candidate address
    # (that would leak an OTP to accounts the requester may not control) — the
    # alternate is used only when all matched users agree on a single value.
    """
    targets = [DispatchTarget(requested, identifier)]
    other = OTPChannel.sms if requested == OTPChannel.email else OTPChannel.email
    alternate = _distinct(
        (u.phone if other == OTPChannel.sms else u.email) for u in users
    )
    if alternate and alternate.strip().lower() != (identifier or "").strip().lower():
        targets.append(DispatchTarget(other, alternate))
    return targets


# ── Context tokens (signed, single-use) ──────────────────────────────────────

def make_context_token(
    identifier: str,
    user_ids: Sequence[uuid.UUID | str],
    *,
    now: datetime | None = None,
    source_user_id: uuid.UUID | str | None = None,
) -> str:
    """Short-TTL JWT proving OTP success for `identifier`, listing the user
    contexts it may be exchanged for. Single-use via the jti ledger."""
    settings = get_settings()
    now = now or _utcnow()
    payload = {
        "type": "context",
        "aud": AUDIENCE_CONTEXT,
        "identifier": identifier,
        "user_ids": [str(u) for u in user_ids],
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(minutes=CONTEXT_TOKEN_TTL_MINUTES),
    }
    if source_user_id is not None:
        # Present only for an in-session workspace switch. It gives the audit
        # trail an unambiguous previous context without granting authority.
        payload["source_user_id"] = str(source_user_id)
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_context_token(token: str) -> dict:
    """Raises ContextTokenInvalid on any signature/expiry/shape problem."""
    try:
        payload = pyjwt.decode(
            token, get_settings().jwt_secret, algorithms=[ALGORITHM],
            audience=AUDIENCE_CONTEXT,
        )
    except pyjwt.PyJWTError as exc:
        raise ContextTokenInvalid("invalid or expired context token") from exc
    if payload.get("type") != "context" or not payload.get("jti"):
        raise ContextTokenInvalid("invalid or expired context token")
    return payload


# ── Request / verify (async, DB-backed) ──────────────────────────────────────

@dataclass
class RequestResult:
    challenge: OTPChallenge
    # Plaintext code, held in memory only so the API layer can expose it as a
    # dev-only `debug_code` field (never logged, never returned in production).
    code: str
    # Which channels the single code was actually dispatched to, so the UI can
    # say "Check your email and SMS" (dual-channel simultaneous send).
    channels_sent: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoginContext:
    """One selectable workspace in the multi-context verify response."""
    user_id: uuid.UUID
    role: Role
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    portal: str


@dataclass
class VerifyResult:
    user: User | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    # Channels still requiring verification before tokens can be issued
    # (client first-login dual OTP, FR-1.2).
    pending_channels: list[str] = field(default_factory=list)
    # Multi-context login (contract rev 2): several users share the verified
    # identifier — no cookies yet, the caller picks a workspace.
    contexts: list[LoginContext] = field(default_factory=list)
    context_token: str | None = None

    @property
    def authenticated(self) -> bool:
        return self.access_token is not None

    @property
    def multi_context(self) -> bool:
        return self.context_token is not None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _find_users(session: AsyncSession, identifier: str) -> list[User]:
    """ALL users whose email OR phone equals the identifier — across roles and
    tenants (unified login, contract rev 2). Ordering keeps the context list
    stable between requests."""
    stmt = (
        select(User)
        .where(or_(User.email == identifier, User.phone == identifier))
        .order_by(User.created_at, User.id)
    )
    return list((await session.execute(stmt)).scalars().all())


def _issue_tokens(user: User) -> tuple[str, str]:
    # Role-aware audience so the token is scoped to exactly one portal
    # (owner / org / candidate) — cross-portal reuse is rejected. The owner
    # (super_admin) MUST get AUDIENCE_OWNER, else the /admin console 403s its
    # own valid login.
    audience = security.audience_for_role(user.role)
    access = security.create_access_token(
        user.id, user.role.value, user.tenant_id, audience=audience
    )
    refresh = security.create_refresh_token(user.id, audience=audience)
    return access, refresh


async def register_candidate(
    session: AsyncSession,
    *,
    full_name: str,
    email: str,
    phone: str | None = None,
) -> "Candidate":  # noqa: F821 — forward ref, imported lazily below
    """Candidate self-service sign-up (register first, log in later).

    Creates a candidate `User` (role=candidate, tenant_id NULL — candidates are
    external actors) plus the shared `Candidate` record so the portal has a
    profile to attach to at first login. OTP-only: no password is taken; the
    account is created active and the OTP at first login proves email ownership.

    If a candidate was already sourced by an employer (a `Candidate` row with
    that email and no linked user), we reuse and link it rather than creating a
    duplicate — the portal matches candidates by email OR user_id.

    Raises AlreadyRegistered if a candidate account already exists for the email.
    """
    from app.models.candidate import Candidate  # local import avoids a cycle

    email = email.strip().lower()
    phone = phone.strip() if phone else None

    existing_user = (
        await session.execute(
            select(User).where(User.email == email, User.role == Role.candidate)
        )
    ).scalars().first()
    if existing_user is not None:
        raise AlreadyRegistered("a candidate account already exists for this email")

    user = User(
        role=Role.candidate,
        email=email,
        phone=phone,
        full_name=full_name.strip(),
        tenant_id=None,
        status=UserStatus.active,  # OTP at first login is the ownership proof
    )
    session.add(user)
    await session.flush()

    # Reuse a previously-sourced (employer-created) candidate record if present.
    candidate = (
        await session.execute(
            select(Candidate).where(Candidate.email == email)
        )
    ).scalars().first()
    if candidate is None:
        candidate = Candidate(
            tenant_id=None, user_id=user.id, full_name=full_name.strip(),
            email=email, phone=phone, consent_databank=False,
        )
        session.add(candidate)
    else:
        if candidate.user_id is None:
            candidate.user_id = user.id
        candidate.full_name = candidate.full_name or full_name.strip()
        candidate.phone = candidate.phone or phone
    await session.flush()
    return candidate


async def request_otp(
    session: AsyncSession,
    *,
    identifier: str,
    channel: OTPChannel,
    audience: str = AUDIENCE_INTERNAL,
    limiter: RateLimiter | None = None,
    now: datetime | None = None,
) -> RequestResult:
    """Generate + store (hashed) ONE challenge and enqueue delivery on every
    channel the resolved user(s) expose (dual-channel simultaneous send).

    One challenge per identifier regardless of how many users share it or how
    many channels it is delivered on — the code hash is bound to the identifier,
    not the channel, so entering the code received on EITHER channel verifies
    the same challenge. Context selection happens at verify time. `audience` is
    accepted for backward compat but no longer routes the lookup (contract
    rev 2); it only still gates candidate self-registration below.

    Raises OTPLocked (cooldown active), OTPResendThrottled (30s), OTPRateLimited
    (5/hour), UserNotFound.
    """
    settings = get_settings()
    limiter = limiter or get_limiter()
    now = now or _utcnow()

    if await limiter.is_set(_lock_key(identifier)):
        raise OTPLocked("identifier is in cooldown")
    if await limiter.is_set(_resend_key(identifier)):
        raise OTPResendThrottled("a code was sent moments ago")
    if await limiter.incr(_request_key(identifier), _REQUEST_WINDOW_SECONDS) > REQUEST_CAP_PER_HOUR:
        raise OTPRateLimited("too many OTP requests")
    # Armed before the user lookup so an unknown identifier is throttled too
    # (otherwise the 30s gate doubles as an account-enumeration oracle).
    await limiter.set_flag(_resend_key(identifier), RESEND_THROTTLE_SECONDS)

    users = eligible_login_users(
        await _find_users(session, identifier), owner_email=settings.owner_email
    )
    if not users:
        if audience == AUDIENCE_CANDIDATE and channel == OTPChannel.email:
            # ASSUMPTION: candidates self-register on the portal via OTP —
            # a candidate user is created on first email-OTP request
            # (FR-9.1: candidates log in separately). The audience field no
            # longer routes lookups, but it still signals the self-register
            # intent; without it an unknown identifier is 404.
            user = User(role=Role.candidate, email=identifier, tenant_id=None,
                        status=UserStatus.invited)
            session.add(user)
            await session.flush()
            users = [user]
        else:
            raise UserNotFound(f"no account for this {channel.value} identifier")

    # The challenge is bound to the IDENTIFIER; user_id is informational and
    # only set when unambiguous (single matching user).
    single = users[0] if len(users) == 1 else None
    code = security.generate_otp()
    challenge = OTPChallenge(
        user_id=single.id if single is not None else None,
        identifier=identifier,
        channel=channel,
        code_hash=security.hash_otp(code, identifier),
        expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        attempts=0,
    )
    session.add(challenge)
    await session.flush()

    # Delivery is always a dispatched task — never inline (claude.md rule 4). Both
    # sends are enqueued back-to-back and run in parallel on the workers; the
    # request handler never blocks on either.
    from app.workers.dispatch import dispatch

    tenant_id = (
        str(single.tenant_id) if single is not None and single.tenant_id else None
    )
    channels_sent: list[str] = []
    for target in dispatch_targets(identifier, users, channel):
        if target.channel == OTPChannel.email:
            dispatch(
                "pickready.send_email",
                args=[
                    tenant_id,
                    target.destination,
                    "otp",
                    {"code": code, "ttl_minutes": settings.otp_ttl_minutes},
                ],
            )
        else:
            dispatch(
                "pickready.send_sms",
                args=[target.destination,
                      f"Your ReadyPick OTP is {code}. Valid for "
                      f"{settings.otp_ttl_minutes} minutes."],
            )
        channels_sent.append(target.channel.value)
    return RequestResult(challenge=challenge, code=code, channels_sent=channels_sent)


def _stamp_channel(user: User, channel: OTPChannel, now: datetime) -> None:
    """Stamp ONLY the challenge's own (requested/primary) channel.

    # ASSUMPTION: with dual-channel dispatch we cannot know which delivery the
    # user actually read the code from, so a dual-sent challenge still proves
    # ownership of the requested channel only. This is what keeps FR-1.2 intact:
    # a client's first login cannot satisfy both channels from one request — the
    # second channel needs its own request with channel=<other>.
    """
    if channel == OTPChannel.email:
        user.email_verified_at = user.email_verified_at or now
    else:
        user.phone_verified_at = user.phone_verified_at or now


async def _build_contexts(
    session: AsyncSession, users: Sequence[User]
) -> list[LoginContext]:
    tenant_ids = {u.tenant_id for u in users if u.tenant_id is not None}
    names: dict[uuid.UUID, str] = {}
    if tenant_ids:
        rows = (
            await session.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
        ).scalars().all()
        names = {t.id: t.name for t in rows}
    return [
        LoginContext(
            user_id=u.id,
            role=u.role,
            tenant_id=u.tenant_id,
            tenant_name=names.get(u.tenant_id) if u.tenant_id else None,
            portal=portal_for_role(u.role),
        )
        for u in users
    ]


async def verify_challenge(
    session: AsyncSession,
    *,
    challenge_id: uuid.UUID | str,
    code: str,
    limiter: RateLimiter | None = None,
    now: datetime | None = None,
) -> VerifyResult:
    """Verify a code. On success, resolve ALL users sharing the verified
    identifier (unified login, contract rev 2):

    - exactly one eligible user -> consume, stamp the channel, issue JWTs
      (unless client first-login dual OTP is still pending — FR-1.2);
    - several -> NO cookies; return the workspace contexts plus a short-TTL
      single-use context_token, finalized via `select_context`."""
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
    resolution = resolve_login(
        await _find_users(session, challenge.identifier),
        owner_email=settings.owner_email,
    )

    # The OTP proves ownership of the identifier, which is shared by every
    # matched context — stamp the verified channel on all of them.
    matched = resolution.contexts if resolution.is_multi else [resolution.user]
    for u in matched:
        _stamp_channel(u, challenge.channel, now)

    if not resolution.is_multi:
        user = resolution.user
        # ASSUMPTION: invited -> active flips at session issuance (here for a
        # single context, in select_context for multi) — proving identifier
        # ownership alone doesn't activate accounts the user never entered.
        pending = pending_channels_for(user)
        if not pending and user.status == UserStatus.invited:
            user.status = UserStatus.active
        await session.flush()
        if pending:
            return VerifyResult(user=user, pending_channels=pending)
        access, refresh = _issue_tokens(user)
        return VerifyResult(user=user, access_token=access, refresh_token=refresh)

    contexts = await _build_contexts(session, resolution.contexts)
    token = make_context_token(
        challenge.identifier, [c.user_id for c in contexts], now=now
    )
    await session.flush()
    return VerifyResult(contexts=contexts, context_token=token)


async def select_context(
    session: AsyncSession,
    *,
    context_token: str,
    user_id: uuid.UUID | str,
    limiter: RateLimiter | None = None,
    now: datetime | None = None,
) -> VerifyResult:
    """Exchange a context_token (proof of OTP success) for a session as ONE of
    the identifier's users. Single-use: the token's jti is marked consumed in
    Redis (in-memory fallback) once cookies are issued.

    Raises ContextTokenInvalid / ContextTokenConsumed / ContextUserMismatch /
    UserNotFound."""
    settings = get_settings()
    limiter = limiter or get_limiter()
    now = now or _utcnow()

    payload = decode_context_token(context_token)
    jti = payload["jti"]
    if await limiter.is_set(_context_key(jti)):
        raise ContextTokenConsumed("context token already used")

    if str(user_id) not in payload.get("user_ids", []):
        raise ContextUserMismatch("user is not part of this login")

    user = await session.get(User, uuid.UUID(str(user_id)))
    if user is None or not eligible_login_users([user], owner_email=settings.owner_email):
        raise UserNotFound("account unavailable")

    # Client first-login dual OTP still applies at selection time (FR-1.2).
    # The token is NOT consumed here, so the client can verify the second
    # channel and select again within the TTL.
    pending = pending_channels_for(user)
    if pending:
        return VerifyResult(user=user, pending_channels=pending)

    if user.status == UserStatus.invited:
        user.status = UserStatus.active
    await session.flush()

    await limiter.set_flag(_context_key(jti), CONTEXT_TOKEN_TTL_MINUTES * 60)
    access, refresh = _issue_tokens(user)
    return VerifyResult(user=user, access_token=access, refresh_token=refresh)
