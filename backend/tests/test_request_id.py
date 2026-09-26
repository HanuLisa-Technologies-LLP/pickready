"""One id per request, on the response and on every log line of that request.

WHY IT IS A TEST AND NOT A CONVENTION
---------------------------------------
Before 2026-09-17 there was no request correlation id at all, and structlog was
running unconfigured beside the stdlib rather than through it. A user reporting
"it failed at about half past two" left an operator grepping a shared log by
timestamp. The id is the join, so it has to be present on the wire (what the
user can quote) AND in the context (what the operator can grep). A test that
only asserted the header would pass over a middleware that bound nothing.

These run against the REAL application, not a hand-built stub, because the
thing most likely to break is the registration in `app/main.py` rather than the
middleware itself. `/docs` is used deliberately: it is served by FastAPI, needs
no database and no authorization, so a failing infrastructure dependency cannot
make this test lie in either direction.
"""
from __future__ import annotations

import logging

import structlog
from fastapi.testclient import TestClient

from app.core.logging import REQUEST_ID_FIELD, REQUEST_ID_HEADER, configure_logging
from app.main import app


def test_a_response_carries_a_request_id() -> None:
    with TestClient(app) as http:
        response = http.get("/docs")
    minted = response.headers.get(REQUEST_ID_HEADER)
    assert minted, "no request id on the response"
    assert len(minted) >= 8


def test_two_requests_get_two_different_ids() -> None:
    """A constant would satisfy the test above and correlate nothing."""
    with TestClient(app) as http:
        first = http.get("/docs").headers[REQUEST_ID_HEADER]
        second = http.get("/docs").headers[REQUEST_ID_HEADER]
    assert first != second


def test_an_inbound_id_is_echoed_rather_than_replaced() -> None:
    """A load balancer or a frontend that already has an id owns the trace."""
    with TestClient(app) as http:
        response = http.get("/docs", headers={REQUEST_ID_HEADER: "edge-abc123"})
    assert response.headers[REQUEST_ID_HEADER] == "edge-abc123"


def test_an_unusable_inbound_id_is_replaced_rather_than_reflected() -> None:
    """The value is echoed in a header and written into logs.

    An anonymous caller therefore controls a string that lands in both, which
    is a header-injection and log-forging surface. Replaced rather than
    refused: the request is not the header's fault.
    """
    with TestClient(app) as http:
        forged = http.get(
            "/docs", headers={REQUEST_ID_HEADER: "ok" + " " * 3 + "injected"}
        ).headers[REQUEST_ID_HEADER]
        overlong = http.get(
            "/docs", headers={REQUEST_ID_HEADER: "a" * 200}
        ).headers[REQUEST_ID_HEADER]
    assert "injected" not in forged
    assert overlong != "a" * 200
    assert len(overlong) <= 128


def test_the_id_is_bound_where_a_log_line_can_pick_it_up() -> None:
    """The half a header assertion cannot see.

    `merge_contextvars` is what puts the id on a line; if the middleware bound
    nothing, every header assertion above would still pass and no log line
    would carry the id.
    """
    seen: list[dict] = []

    def _capture(_logger, _name, event_dict):
        seen.append(dict(event_dict))
        raise structlog.DropEvent

    original = structlog.get_config()
    try:
        structlog.configure(
            processors=[structlog.contextvars.merge_contextvars, _capture],
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=False,
        )

        @app.get("/__request_id_probe__", include_in_schema=False)
        async def _probe() -> dict:
            structlog.get_logger().info("probe")
            return {"ok": True}

        with TestClient(app) as http:
            response = http.get("/__request_id_probe__")
    finally:
        structlog.configure(**original)
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__request_id_probe__"
        ]

    assert response.status_code == 200
    assert seen, "the probe logged nothing"
    assert seen[-1].get(REQUEST_ID_FIELD) == response.headers[REQUEST_ID_HEADER]


def test_configure_logging_is_idempotent() -> None:
    """It is called at import of `app.main`; calling it again must not double
    every line, which is what appending a handler rather than replacing the
    list would do."""
    configure_logging(production=False)
    before = len(logging.getLogger().handlers)
    configure_logging(production=False)
    assert len(logging.getLogger().handlers) == before == 1


def test_production_renders_json_and_development_does_not() -> None:
    """One configuration, two renderers, chosen by the environment.

    Re-configured back to development afterwards so the rest of the session
    reads the console format it started with.
    """
    from structlog.processors import JSONRenderer

    try:
        configure_logging(production=True)
        handler = logging.getLogger().handlers[0]
        assert any(
            isinstance(processor, JSONRenderer)
            for processor in handler.formatter.processors
        )
        configure_logging(production=False)
        handler = logging.getLogger().handlers[0]
        assert not any(
            isinstance(processor, JSONRenderer)
            for processor in handler.formatter.processors
        )
    finally:
        configure_logging(production=False)
