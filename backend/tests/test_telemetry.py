"""Telemetry must be anonymous, bounded, and safe to run without Redis."""


from app.core import cache
from app.services.telemetry import (
    LANDING_VIEW_LIMIT,
    LANDING_VIEW_WINDOW_SECONDS,
    landing_view_allowed,
    public_client_key,
)


def test_public_client_key_is_stable_and_not_the_raw_host() -> None:
    key = public_client_key("203.0.113.10")
    assert key == public_client_key("203.0.113.10")
    assert "203.0.113.10" not in key


async def test_landing_views_are_counted_by_the_one_rate_limiter(monkeypatch) -> None:
    """`services/rate_limit.check` is the counter, keyed by the HMAC of the host
    and never the host itself; the view past the limit is refused."""
    from tests.test_rate_limit import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    host = "203.0.113.20"
    answers = [await landing_view_allowed(host) for _ in range(LANDING_VIEW_LIMIT + 1)]
    assert answers[:-1] == [True] * LANDING_VIEW_LIMIT
    assert answers[-1] is False
    (key,) = fake.counts
    assert public_client_key(host) in key and host not in key
    assert fake.expiries[key] == LANDING_VIEW_WINDOW_SECONDS


async def test_a_landing_view_is_counted_open_when_redis_is_down(monkeypatch) -> None:
    """A landing counter fails OPEN: an outage must not stop anybody reading
    the page. The old limiter's silent per-process memory fallback is gone."""
    monkeypatch.setattr(cache, "_redis", lambda: None)
    assert await landing_view_allowed("203.0.113.30") is True


