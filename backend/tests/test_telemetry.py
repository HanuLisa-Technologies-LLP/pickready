"""Telemetry must be anonymous, bounded, and safe to run without Redis."""
from app.services.otp import RateLimiter
from app.services.telemetry import (
    LANDING_VIEW_LIMIT,
    LANDING_VIEW_WINDOW_SECONDS,
    public_client_key,
)


def test_public_client_key_is_stable_and_not_the_raw_host() -> None:
    key = public_client_key("203.0.113.10")
    assert key == public_client_key("203.0.113.10")
    assert "203.0.113.10" not in key


async def test_landing_limit_shape_is_enforced_by_rate_limiter() -> None:
    limiter = RateLimiter()
    key = f"telemetry:landing:{public_client_key('203.0.113.20')}"
    counts = [await limiter.incr(key, LANDING_VIEW_WINDOW_SECONDS) for _ in range(LANDING_VIEW_LIMIT + 1)]
    assert counts[-2] == LANDING_VIEW_LIMIT
    assert counts[-1] == LANDING_VIEW_LIMIT + 1
