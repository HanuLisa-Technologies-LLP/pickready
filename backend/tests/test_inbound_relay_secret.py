"""The inbound-mail webhook's relay secret.

`POST /verification/inbound-email` is a PUBLIC route that writes into
verification requests, BGV threads and conversations. Its only protection was
that a caller had to know a per-thread token, and those travel by email, so they
exist in every mailbox that ever received or forwarded one of these threads.
Nothing stopped a POST straight at the API, bypassing SES, its DKIM and SPF
checks, and the relay Lambda entirely.

The sibling webhooks already do this properly: the SES event webhook verifies an
SNS RSA signature and the Razorpay webhook verifies an HMAC. This one is Lambda
to API, so a shared secret is enough. It does not need to prove SES sent the
mail, only that our own relay made the call.

These exercise `_require_relay_secret` DIRECTLY rather than through the route.
The gate is a dependency with no database access of its own, and a test that
stood up a session, a tenant and a thread in order to assert a 403 would be
testing the fixtures. What matters is the decision, and it is asserted in BOTH
directions: a test that only asserted the refusal would pass over a gate that
refused everything, including our own Lambda, which would silently drop every
employer reply and is worse than the hole it closes.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import verification


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/verification/inbound-email",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
            "query_string": b"",
            "client": ("203.0.113.9", 4444),
        }
    )


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch):
    """A deployment that HAS been given the secret."""

    class _Settings:
        inbound_webhook_secret = "the-relay-secret"

    monkeypatch.setattr(verification, "get_settings", lambda: _Settings())
    return "the-relay-secret"


def test_a_caller_with_no_header_is_refused(configured) -> None:
    with pytest.raises(HTTPException) as refused:
        verification._require_relay_secret(_request())
    # 403 rather than 401: there is no authentication scheme to negotiate here,
    # so inviting the caller to retry with credentials would be a lie.
    assert refused.value.status_code == 403


def test_a_wrong_secret_is_refused(configured) -> None:
    with pytest.raises(HTTPException) as refused:
        verification._require_relay_secret(
            _request({verification.INBOUND_SECRET_HEADER: "not-the-relay-secret"})
        )
    assert refused.value.status_code == 403


def test_a_secret_that_is_merely_a_prefix_is_refused(configured) -> None:
    """The obvious wrong implementation is `startswith`, and it would admit
    this."""
    with pytest.raises(HTTPException):
        verification._require_relay_secret(
            _request({verification.INBOUND_SECRET_HEADER: "the-relay"})
        )


def test_the_relay_itself_is_admitted(configured) -> None:
    """THE DIRECTION THAT MATTERS MOST. A gate that refused our own Lambda would
    drop every employer reply, silently, and look exactly like a mail routing
    fault."""
    verification._require_relay_secret(
        _request({verification.INBOUND_SECRET_HEADER: configured})
    )


def test_an_unconfigured_secret_leaves_the_route_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EMPTY IS A REAL STATE, not a silent equivalent of configured.

    The same shape `inbound_email_domain` already uses. A deployment that has not
    been given the value must keep accepting genuine replies rather than refusing
    all of them, and it says so in the log every time.
    """

    class _Settings:
        inbound_webhook_secret = ""

    monkeypatch.setattr(verification, "get_settings", lambda: _Settings())
    verification._require_relay_secret(_request())


def test_the_comparison_is_constant_time() -> None:
    """A plain `==` returns as soon as two bytes differ, which leaks the secret's
    prefix to anyone willing to time a few thousand requests. Asserted over the
    source because the timing itself is not observable in a unit test."""
    source = inspect.getsource(verification._require_relay_secret)
    assert "compare_digest" in source, "the secret must not be compared with =="


def test_the_route_actually_depends_on_the_gate() -> None:
    """The check above is worthless if nothing calls it. Asserts the dependency
    is wired to the route, which is the half a future refactor drops."""
    for route in verification.router.routes:
        if getattr(route, "path", None) == "/inbound-email":
            names = [
                dependency.call.__name__
                for dependency in route.dependant.dependencies
                if getattr(dependency, "call", None) is not None
            ]
            assert "_require_relay_secret" in names, names
            return
    raise AssertionError("the /inbound-email route was not found")
