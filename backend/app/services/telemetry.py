"""Privacy-preserving UI telemetry helpers.

Public events are rate-limited using an HMAC-derived key. The source IP is
never persisted in the audit log or written to application logs.

The counter is `services/rate_limit.check`, the product's one rate limiter,
since 2026-09-24. It used to borrow the retired code-login service's limiter,
a third process-global Redis client that fell back to per-process memory on
any error. `rate_limit` fails OPEN by design, which is the right answer for a
landing-page view counter: an outage must not stop anybody reading the page.
"""
import hashlib
import hmac

from app.core.config import get_settings
from app.services import rate_limit

LANDING_VIEW_LIMIT = 30
LANDING_VIEW_WINDOW_SECONDS = 60 * 60
_LANDING_BUCKET = "telemetry_landing"


def public_client_key(client_host: str | None) -> str:
    """Return a non-reversible, per-deployment rate-limit key component."""
    host = client_host or "unknown"
    digest = hmac.new(
        get_settings().jwt_secret.encode(), host.encode(), hashlib.sha256
    ).hexdigest()
    return digest


async def landing_view_allowed(client_host: str | None) -> bool:
    decision = await rate_limit.check(
        _LANDING_BUCKET,
        public_client_key(client_host),
        limit=LANDING_VIEW_LIMIT,
        window=LANDING_VIEW_WINDOW_SECONDS,
    )
    return decision.allowed
