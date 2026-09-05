"""Mailbox-ownership OTP state, in Redis only (spec section 4).

WHAT IS STORED AND WHAT NEVER IS
--------------------------------
Redis holds `email_verification:{sender_id}` with exactly the spec's three
fields: `otp_hash`, `expires_at`, `attempts`. The PLAINTEXT code exists in
two places for its whole life: the return value the caller hands to the email
dispatch, and the mailbox it lands in. It is never persisted, never logged and
never audited (the same ESD section 16 rule the login OTP path follows). The
hash is HMAC-SHA256 keyed with the app secret, so a leaked Redis dump does not
become a usable code and a plain unsalted digest of a six-digit space (one
million values) cannot be brute-forced offline either.

WHY THIS FAILS CLOSED, UNLIKE THE CACHE
---------------------------------------
`core/cache` and `services/rate_limit` fail OPEN because they protect cost.
This protects a security decision: a verification that "passed" because Redis
was down would mark a mailbox as owned by somebody who never saw the code. So
Redis being unreachable raises `VerificationUnavailable`, which the API turns
into a 503 -- the same trade `services/proctoring/state` already makes, for
the same reason.

ASSUMPTION (spec section 3): the POC who owns the mailbox may not be a
ReadyPick user at all. The code lands in THEIR mailbox; an authorized portal
user (manage_email_senders) types it into the portal on their behalf. The
verification endpoint therefore authenticates the ENTERING user, and the OTP
itself is what proves the mailbox side.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "OTP_LENGTH",
    "ResendCooldownActive",
    "VerificationUnavailable",
    "VerifyOutcome",
    "issue_otp",
    "verify_otp",
]

OTP_LENGTH = 6

#: The spec's own key shape (section 4).
_KEY_PREFIX = "email_verification"

REASON_VERIFIED = "verified"
REASON_NOT_ISSUED = "not_issued"
REASON_EXPIRED = "expired"
REASON_MISMATCH = "mismatch"
REASON_ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class VerificationUnavailable(RuntimeError):
    """Redis could not answer. A verification decision that was not made must
    not read as any particular outcome; the API answers 503."""


class ResendCooldownActive(RuntimeError):
    """A resend was asked for inside the cooldown window (spec: 45 seconds)."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(
            f"A code was just sent. You can request another in "
            f"{self.retry_after} seconds."
        )


@dataclass(frozen=True)
class VerifyOutcome:
    verified: bool
    reason: str
    attempts_remaining: int = 0


_client: Any = None
_client_loop: asyncio.AbstractEventLoop | None = None
_SOCKET_TIMEOUT_SECONDS = 2


def _redis():
    """A loop-affine client, exactly as `services/proctoring/state` builds one:
    an asyncio Redis connection belongs to the loop that opened it."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        import redis.asyncio as redis_asyncio

        _client = redis_asyncio.from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )
        _client_loop = loop
    return _client


async def _call(operation: str, coroutine_factory):
    from redis.exceptions import RedisError

    try:
        return await coroutine_factory()
    except (OSError, RedisError, asyncio.TimeoutError) as exc:
        logger.error(
            "email_senders.verification.unavailable op=%s err=%s",
            operation,
            type(exc).__name__,
        )
        raise VerificationUnavailable(
            f"sender verification state unavailable during {operation}"
        ) from exc


def _key(sender_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{sender_id}"


def _cooldown_key(sender_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:cooldown:{sender_id}"


def _hash(sender_id: uuid.UUID, code: str) -> str:
    """HMAC-SHA256 over (sender_id, code), keyed with the app secret. The
    sender id in the message binds a hash to ITS sender, so a code issued for
    one mailbox can never verify another."""
    return hmac.new(
        get_settings().jwt_secret.encode("utf-8"),
        f"sender-otp:{sender_id}:{code}".encode("utf-8"),
        sha256,
    ).hexdigest()


async def issue_otp(sender_id: uuid.UUID) -> str:
    """Mint a fresh code for this sender and return the PLAINTEXT, once.

    A fresh issue REPLACES any outstanding code (a resend invalidates the old
    one, so "which of the two codes counts" never has two answers) and resets
    the attempt counter. Raises `ResendCooldownActive` inside the cooldown
    window and `VerificationUnavailable` when Redis cannot answer.
    """
    settings = get_settings()
    client = _redis()

    cooldown = _cooldown_key(sender_id)
    # SET NX is the atomic "am I first in this window" question.
    accepted = await _call(
        "issue.cooldown",
        lambda: client.set(
            cooldown, "1", nx=True, ex=settings.sender_otp_resend_cooldown_seconds
        ),
    )
    if not accepted:
        ttl = await _call("issue.cooldown_ttl", lambda: client.ttl(cooldown))
        raise ResendCooldownActive(
            ttl if ttl and ttl > 0 else settings.sender_otp_resend_cooldown_seconds
        )

    # A CSPRNG six-digit code, uniform over the whole space including leading
    # zeros (spec section 4: cryptographically secure).
    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    expires_at = int(time.time()) + settings.sender_otp_ttl_seconds
    key = _key(sender_id)

    async def _write():
        pipe = client.pipeline(transaction=True)
        pipe.delete(key)
        pipe.hset(
            key,
            mapping={
                "otp_hash": _hash(sender_id, code),
                "expires_at": str(expires_at),
                "attempts": "0",
            },
        )
        pipe.expire(key, settings.sender_otp_ttl_seconds)
        return await pipe.execute()

    await _call("issue.write", _write)
    return code


async def verify_otp(sender_id: uuid.UUID, code: str) -> VerifyOutcome:
    """Judge one entered code. Never raises for a wrong code -- the outcome
    says what happened; only Redis unavailability raises.

    The attempt is counted BEFORE the comparison, so a caller cannot probe the
    hash without spending an attempt, and once `sender_otp_max_attempts` is
    spent the code is dead whatever is entered next (spec section 4). A
    correct entry deletes the state, so an already-used code is unusable.
    """
    settings = get_settings()
    client = _redis()
    key = _key(sender_id)

    state = await _call("verify.read", lambda: client.hgetall(key))
    if not state or "otp_hash" not in state:
        return VerifyOutcome(False, REASON_NOT_ISSUED)

    try:
        expires_at = int(state.get("expires_at", "0"))
    except ValueError:
        expires_at = 0
    if time.time() > expires_at:
        # Expired state is removed so it can never be retried into life.
        await _call("verify.expire_cleanup", lambda: client.delete(key))
        return VerifyOutcome(False, REASON_EXPIRED)

    attempts = await _call(
        "verify.count_attempt", lambda: client.hincrby(key, "attempts", 1)
    )
    if attempts > settings.sender_otp_max_attempts:
        return VerifyOutcome(False, REASON_ATTEMPTS_EXHAUSTED)

    entered = (code or "").strip()
    if not hmac.compare_digest(
        _hash(sender_id, entered), str(state["otp_hash"])
    ):
        remaining = max(0, settings.sender_otp_max_attempts - attempts)
        if remaining == 0:
            return VerifyOutcome(False, REASON_ATTEMPTS_EXHAUSTED)
        return VerifyOutcome(False, REASON_MISMATCH, attempts_remaining=remaining)

    async def _invalidate():
        pipe = client.pipeline(transaction=True)
        pipe.delete(key)
        pipe.delete(_cooldown_key(sender_id))
        return await pipe.execute()

    await _call("verify.invalidate", _invalidate)
    return VerifyOutcome(True, REASON_VERIFIED)
