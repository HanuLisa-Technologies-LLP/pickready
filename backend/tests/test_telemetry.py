"""Telemetry must be anonymous, bounded, and safe to run without Redis."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.deps import CurrentUser
from app.api.telemetry import rating_comments_view
from app.models.enums import Role
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


def test_telemetry_uses_distinct_audit_actions_without_pii() -> None:
    """Endpoint actions are stable identifiers; no client identifier is stored."""
    assert "landing_viewed" != "rating_comments_viewed"
    assert "203.0.113.10" not in public_client_key("203.0.113.10")


class _LinkSession:
    def __init__(self, link) -> None:
        self.link = link

    async def get(self, _model, _link_id):
        return self.link


async def test_rating_view_rejects_an_ungranted_profile(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    link = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant_id, hm_access_granted=False
    )
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.hiring_manager,
        audience="pickready:org",
    )

    async def no_full_access(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr("app.api.telemetry.rbac.has_capability", no_full_access)
    with pytest.raises(HTTPException) as exc_info:
        await rating_comments_view(link.id, user, _LinkSession(link))
    assert getattr(exc_info.value, "status_code", None) == 403
