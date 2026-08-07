"""Short-lived, tenant-bound application URLs for private resume delivery."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from app.core.security import ALGORITHM

AUDIENCE = "pickready:resume"


def issue_resume_token(
    profile_id: uuid.UUID, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> str:
    issued = now or datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(profile_id),
            "tenant_id": str(tenant_id),
            "aud": AUDIENCE,
            "iat": issued,
            "exp": issued
            + timedelta(seconds=get_settings().resume_signed_url_ttl_seconds),
            "type": "resume_access",
        },
        get_settings().jwt_secret,
        algorithm=ALGORITHM,
    )


def verify_resume_token(
    token: str, profile_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    payload = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
    )
    if (
        payload.get("type") != "resume_access"
        or payload.get("sub") != str(profile_id)
        or payload.get("tenant_id") != str(tenant_id)
    ):
        raise jwt.InvalidTokenError("resume access scope mismatch")
