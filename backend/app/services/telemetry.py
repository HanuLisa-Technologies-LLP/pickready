"""Privacy-preserving UI telemetry helpers.

Public events are rate-limited using an HMAC-derived Redis key. The source IP
is never persisted in the audit log or written to application logs.
"""
import hashlib
import hmac

from app.core.config import get_settings
from app.services.otp import get_limiter

LANDING_VIEW_LIMIT = 30
LANDING_VIEW_WINDOW_SECONDS = 60 * 60


def public_client_key(client_host: str | None) -> str:
    """Return a non-reversible, per-deployment rate-limit key component."""
    host = client_host or "unknown"
    digest = hmac.new(
        get_settings().jwt_secret.encode(), host.encode(), hashlib.sha256
    ).hexdigest()
    return digest


async def landing_view_allowed(client_host: str | None) -> bool:
    count = await get_limiter().incr(
        f"telemetry:landing:{public_client_key(client_host)}",
        LANDING_VIEW_WINDOW_SECONDS,
    )
    return count <= LANDING_VIEW_LIMIT
