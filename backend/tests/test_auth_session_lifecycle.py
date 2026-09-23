"""Browser-session cookie lifecycle. Redis independently bounds idle time."""
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


def test_every_auth_cookie_is_a_browser_session_cookie():
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    cookies = _cookies(response)
    for name, attrs in cookies.items():
        assert "max-age" not in attrs, name
        assert "expires" not in attrs, name


def test_tabs_share_cookies_but_browser_close_drops_them():
    """Cookie semantics: tab closure preserves the shared browser jar.

    This simulates normal browser close. Browsers configured to restore a
    previous session may restore session cookies too; only a live browser run
    can characterize that browser setting.
    """
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    browser_jar = _cookies(response)
    second_tab = browser_jar.copy()
    assert second_tab[REFRESH_COOKIE]["value"] == "refresh-token"
    del second_tab  # tab close leaves the browser jar untouched
    reopened_tab = browser_jar.copy()
    assert reopened_tab[REFRESH_COOKIE]["value"] == "refresh-token"
    assert reopened_tab[SESSION_HINT_COOKIE]["value"] == "1"
    browser_jar = {
        name: attrs for name, attrs in browser_jar.items()
        if "max-age" in attrs or "expires" in attrs
    }  # a full browser close discards session cookies
    assert browser_jar == {}


def test_every_auth_cookie_is_httponly():
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    for name, attrs in _cookies(response).items():
        assert "httponly" in attrs, f"{name} must be HttpOnly"
        assert attrs["samesite"].lower() in {"lax", "strict"}


def test_https_auth_cookies_are_secure(monkeypatch):
    from types import SimpleNamespace
    from app.core import config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        serves_over_https=True, cookie_samesite="strict", cookie_domain=None,
    ))
    response = Response()
    set_auth_cookies(response, "access-token", "refresh-token")
    for attrs in _cookies(response).values():
        assert "secure" in attrs


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


def test_access_ttl_is_short_and_idle_deadline_is_server_side():
    """The access TTL being short is FINE, and deliberately so: it limits the
    blast radius of a leaked token. It is only a problem when refresh does not
    work silently, which is what the hint cookie restores. This test exists so
    that nobody "fixes" idle logout by inflating the access TTL instead.
    """
    settings = get_settings()
    assert 5 <= settings.jwt_access_ttl_minutes <= 60
    assert settings.jwt_refresh_ttl_days >= 7
    from app.services.auth_sessions import IDLE_SECONDS
    assert IDLE_SECONDS == 30 * 60
