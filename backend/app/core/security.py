"""OTP hashing, JWT issuance/verification, and secret encryption helpers.

- OTPs are stored hashed (HMAC-SHA256 keyed by JWT_SECRET) — never plaintext,
  never logged (ESD §16).
- Access JWT: 15 min, embeds user_id / tenant_id / role / audience.
  Candidate-portal sessions use a distinct audience claim (ESD §13).
- LLM provider keys are Fernet-encrypted at rest via LLM_KEY_ENCRYPTION_SECRET.
"""
import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.fernet import Fernet

from app.core.config import get_settings

# Three distinct token audiences — ONE per portal, so a token minted for one
# portal can never be replayed against another (cross-portal reuse must be
# impossible). The decoding dependencies (deps.py) reject any audience mismatch
# with 401/403.
AUDIENCE_OWNER = "pickready:owner"          # super_admin (platform owner) console
AUDIENCE_ORG = "pickready:org"              # client / hr_manager / recruiter / hiring_manager
AUDIENCE_CANDIDATE = "pickready:candidate"  # candidate portal — separate session scope

# Deprecated alias. The single "internal" audience was split into OWNER + ORG.
# Kept only so existing imports (auth.py, otp.py) don't raise ImportError during
# the transition — it now points at the ORG audience. New code must select the
# audience via `audience_for_role`, never this constant.
AUDIENCE_INTERNAL = AUDIENCE_ORG

# Roles that live in the org (tenant) portal. super_admin is deliberately NOT
# here, it is the owner portal; candidate is its own portal; bd is a platform
# console that shares the owner audience.
#
# TWO ROLES WERE MISSING FROM THIS SET AND NEITHER COULD SIGN IN
# --------------------------------------------------------------
# `recruitment_manager` was added by the hierarchy release (migration 0050) and
# never added here, so `audience_for_role` raised
# `ValueError: no audience defined for role 'recruitment_manager'` for every
# such account. That is not a permission refusal a user could understand: the
# token is never minted, so the login fails before any capability is consulted.
# It went unnoticed because a hand-maintained list has no failure mode until
# somebody holds the missing value.
#
# `interview_manager` (2026-08-29, RBAC_SPECIFICATION.md 5) would have arrived
# with the identical defect. `test_rbac_conformance` now asserts that this set
# is exactly `Role` minus the three non-org portals, so the next role added
# fails a test instead of failing a person's login.
_ORG_ROLES = frozenset(
    {
        "client",
        "recruitment_manager",
        "hr_manager",
        "recruiter",
        "hiring_manager",
        "interview_manager",
    }
)

ALGORITHM = "HS256"


def audience_for_role(role: "str | Any") -> str:
    """Map a role to the ONE audience its tokens may carry.

    super_admin -> owner portal, candidate -> candidate portal, every other
    (tenant) role -> org portal. Accepts a Role enum or its string value.
    """
    value = getattr(role, "value", role)
    if value == "super_admin":
        return AUDIENCE_OWNER
    # Business Development is a PLATFORM console, like the Provider Portal, so
    # it shares the owner audience. It is not a tenant role: a bd user has no
    # tenant, and an org token must never reach /bd. What a bd user may DO is
    # decided entirely by capability data, never by this mapping.
    if value == "bd":
        return AUDIENCE_OWNER
    if value == "candidate":
        return AUDIENCE_CANDIDATE
    if value in _ORG_ROLES:
        return AUDIENCE_ORG
    raise ValueError(f"no audience defined for role {value!r}")


# ── OTP ──────────────────────────────────────────────────────────────────────

def generate_otp() -> str:
    """6-digit numeric OTP, cryptographically random."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str, identifier: str) -> str:
    """HMAC the OTP together with the identifier (email/phone) it was sent to,
    so a hash can't be replayed across identifiers."""
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{identifier}:{code}".encode(), hashlib.sha256).hexdigest()


def verify_otp(code: str, identifier: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code, identifier), code_hash)


# ── JWT ──────────────────────────────────────────────────────────────────────

def create_access_token(
    user_id: uuid.UUID | str,
    role: str,
    tenant_id: uuid.UUID | str | None,
    audience: str = AUDIENCE_ORG,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_refresh_token(user_id: uuid.UUID | str, audience: str = AUDIENCE_ORG) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_ttl_days),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, audience: str = AUDIENCE_INTERNAL) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid/expired token or audience mismatch."""
    return jwt.decode(
        token, get_settings().jwt_secret, algorithms=[ALGORITHM], audience=audience
    )


# ── Secret encryption (LLM provider keys at rest, ESD §8.4) ─────────────────

def _fernet() -> Fernet:
    secret = get_settings().llm_key_encryption_secret
    if not secret:
        raise RuntimeError("LLM_KEY_ENCRYPTION_SECRET is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
