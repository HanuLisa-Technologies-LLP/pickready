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

AUDIENCE_INTERNAL = "pickready:internal"   # super_admin, client, hr_manager, recruiter, hiring_manager
AUDIENCE_CANDIDATE = "pickready:candidate"  # candidate portal — separate session scope

ALGORITHM = "HS256"


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
    audience: str = AUDIENCE_INTERNAL,
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


def create_refresh_token(user_id: uuid.UUID | str, audience: str = AUDIENCE_INTERNAL) -> str:
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
