"""The JSON the three live sign-in routes answer, pinned as a shape.

`OTPVerifyOut` was renamed `SessionOut` on 2026-09-24 when the code-login
handlers that gave it its name were deleted. A rename is exactly the change
during which a field quietly disappears or appears, and the frontend reads
these keys by name, so the shape is captured here and compared: the ONLY
difference allowed is that `pending_channels` is gone (it carried the retired
dual-channel gate, and no screen ever read it).
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.schemas.auth import ContextOut, SessionOut, UserOut

#: The captured shape, minus `pending_channels`. Order-free.
SESSION_OUT_FIELDS = {"user", "capabilities", "contexts", "context_token"}
USER_OUT_FIELDS = {
    "id", "role", "tenant_id", "full_name", "email", "email_verified",
    "phone_verified", "password_enabled", "workspace_name",
}
CONTEXT_OUT_FIELDS = {"user_id", "role", "tenant_id", "tenant_name", "portal"}

LIVE_SESSION_ROUTES = {
    ("POST", "/api/v1/auth/firebase/session"),
    ("POST", "/api/v1/auth/workspaces"),
    ("POST", "/api/v1/auth/select-context"),
}


def test_the_session_response_keeps_its_keys_and_drops_only_pending_channels() -> None:
    assert set(SessionOut.model_fields) == SESSION_OUT_FIELDS
    assert set(UserOut.model_fields) == USER_OUT_FIELDS
    assert set(ContextOut.model_fields) == CONTEXT_OUT_FIELDS


def test_an_empty_session_serialises_to_the_same_keys() -> None:
    assert set(SessionOut().model_dump(mode="json")) == SESSION_OUT_FIELDS


@pytest.fixture(scope="module")
def routes() -> list[APIRoute]:
    from app.main import app

    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_the_three_live_routes_answer_session_out(routes) -> None:
    found = {
        (method, route.path): route.response_model
        for route in routes
        for method in route.methods
        if (method, route.path) in LIVE_SESSION_ROUTES
    }
    assert set(found) == LIVE_SESSION_ROUTES
    assert all(model is SessionOut for model in found.values()), found


def test_no_code_login_route_is_mounted(routes) -> None:
    paths = {route.path for route in routes}
    for retired in (
        "/api/v1/auth/otp/request",
        "/api/v1/auth/otp/verify",
        "/api/v1/auth/register-candidate",
    ):
        assert retired not in paths, retired
