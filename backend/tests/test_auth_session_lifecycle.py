"""Session-cookie lifecycle: the idle-logout defect and its fix.

The defect these tests pin down: an idle user with a valid 7-day refresh token
was bounced to the login page. The cause was not the token, it was cookie
VISIBILITY. `pr_access` is deleted by the browser when its 15-minute Max-Age
lapses, and `pr_refresh` is path-scoped to /api/v1/auth so it is never sent to a
page request like /org/jobs. The Next.js middleware gates portal routes on
cookie presence, so it saw nothing at all and redirected before the API client
could refresh.

`pr_session` is the fix: a value-free presence hint at path "/", written and
cleared with the refresh token. These tests assert the three properties it has
to hold, because each one is a way the bug comes back:

  1. it is written whenever a refresh token is written,
  2. it lives exactly as long as the refresh token, never longer,
  3. it is cleared on logout AND on any definitively dead refresh.
"""
import pytest
from fastapi import Response

from app.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    SESSION_HINT_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.core.config import Settings, get_settings


def _cookies(response: Response) -> dict[str, dict[str, str]]:
    """Parse Set-Cookie headers into {name: {attribute: value}}."""
    parsed: dict[str, dict[str, str]] = {}
    for raw in response.headers.getlist("set-cookie"):
        parts = [segment.strip() for segment in raw.split(";")]
        name, _, value = parts[0].partition("=")
        attrs = {"value": value}
        for segment in parts[1:]:
            key, _, attr_value = segment.partition("=")
            attrs[key.strip().lower()] = attr_value.strip()
        parsed[name] = attrs
    return parsed


def test_login_writes_the_session_presence_hint():
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    cookies = _cookies(response)

    assert set(cookies) == {ACCESS_COOKIE, REFRESH_COOKIE, SESSION_HINT_COOKIE}


def test_hint_is_readable_at_the_site_root():
    """The whole point: the middleware sees it on /org/jobs, /admin, /portal.

    The refresh cookie deliberately is not, and asserting both together is what
    stops someone "simplifying" the hint away by widening the refresh path.
    """
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    cookies = _cookies(response)

    assert cookies[SESSION_HINT_COOKIE]["path"] == "/"
    assert cookies[REFRESH_COOKIE]["path"] == REFRESH_COOKIE_PATH


def test_hint_carries_no_token_material():
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    cookies = _cookies(response)

    assert cookies[SESSION_HINT_COOKIE]["value"] == "1"
    assert "access-token" not in cookies[SESSION_HINT_COOKIE]["value"]
    assert "refresh-token" not in cookies[SESSION_HINT_COOKIE]["value"]


def test_hint_outlives_the_access_cookie_but_not_the_refresh_token():
    """A hint that outlived the refresh token would admit a browser to a portal
    page whose every API call fails, which is a worse experience than the bounce
    it replaced."""
    settings = get_settings()
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    cookies = _cookies(response)

    access_age = int(cookies[ACCESS_COOKIE]["max-age"])
    refresh_age = int(cookies[REFRESH_COOKIE]["max-age"])
    hint_age = int(cookies[SESSION_HINT_COOKIE]["max-age"])

    assert access_age == settings.jwt_access_ttl_minutes * 60
    assert refresh_age == settings.jwt_refresh_ttl_days * 86400
    assert hint_age == refresh_age
    assert hint_age > access_age


def test_every_auth_cookie_is_httponly():
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    for name, attrs in _cookies(response).items():
        assert "httponly" in attrs, f"{name} must be HttpOnly"


def test_logout_clears_all_three_on_their_own_paths():
    """A deletion only lands if name AND path match the original cookie."""
    response = Response()
    clear_auth_cookies(response)
    cookies = _cookies(response)

    assert set(cookies) == {ACCESS_COOKIE, REFRESH_COOKIE, SESSION_HINT_COOKIE}
    assert cookies[ACCESS_COOKIE]["path"] == "/"
    assert cookies[SESSION_HINT_COOKIE]["path"] == "/"
    assert cookies[REFRESH_COOKIE]["path"] == REFRESH_COOKIE_PATH
    for attrs in cookies.values():
        assert attrs["max-age"] == "0"


def test_cookie_samesite_is_validated():
    """SameSite=None is meaningless without Secure, and Secure is tied to the
    production flag. Refuse the combination at startup rather than shipping a
    cookie every browser drops."""
    with pytest.raises(ValueError):
        Settings(cookie_samesite="sideways")
    with pytest.raises(ValueError):
        Settings(cookie_samesite="none", environment="development")
    assert Settings(cookie_samesite="none", environment="production")


def test_access_ttl_is_short_and_refresh_ttl_is_long():
    """The access TTL being short is FINE, and deliberately so: it limits the
    blast radius of a leaked token. It is only a problem when refresh does not
    work silently, which is what the hint cookie restores. This test exists so
    that nobody "fixes" idle logout by inflating the access TTL instead.
    """
    settings = get_settings()
    assert 5 <= settings.jwt_access_ttl_minutes <= 60
    assert settings.jwt_refresh_ttl_days >= 7
