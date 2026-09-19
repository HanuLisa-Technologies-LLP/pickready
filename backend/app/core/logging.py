"""The one logging configuration, and the request id every line carries.

WHY THIS FILE EXISTS
---------------------
`structlog` was imported and called (`app/main.py`, `app/api/telemetry.py`)
while `structlog.configure(...)` appeared NOWHERE in the tree. structlog was
therefore running on its default `PrintLogger`, writing to stdout on its own
terms, while roughly 145 modules logged through `logging.getLogger(__name__)`
and reached the stdlib's last-resort handler. Two logging systems, neither
producing JSON, and no way to join a structlog event to the stdlib line that
followed it.

`configure_logging()` is the single fix and there is exactly one of it. It
configures structlog to emit through the STDLIB rather than beside it
(`ProcessorFormatter`), so both systems run the same processor chain and land
in the same renderer: JSON in production, a human-readable console line
everywhere else. Nothing in the 145 modules changes; they were already writing
to the stdlib and now their records are formatted by the same code.

THE REQUEST ID
---------------
`RequestIdMiddleware` reads an inbound `X-Request-Id`, or mints one, binds it
to a structlog contextvar so `merge_contextvars` puts it on every line the
request produces, and echoes it on the response so a browser network panel and
a log query name the same thing.

It is a PURE ASGI middleware rather than a `BaseHTTPMiddleware`, for one
concrete reason: `BaseHTTPMiddleware` runs the downstream app in a separate
anyio task, and a contextvar's lifetime across that boundary is a detail of
Starlette's internals rather than a contract. A pure ASGI middleware runs in
the same task as the endpoint, so the binding is simply in scope.

It never raises. A logging correlation aid that can 500 a request has inverted
its own value, so the only thing it does outside a guard is call the app.

HTTP ONLY, deliberately. A WebSocket is one long-lived connection rather than a
request, so a single id on it would label an hour of traffic as one event and
read as correlation while correlating nothing. The socket in this product is a
notification channel whose writes all arrive as ordinary POSTs, and those do
carry an id.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable

import structlog

#: The header, spelled once. Read on the way in, written on the way out.
REQUEST_ID_HEADER = "x-request-id"

#: The structlog contextvar key, and therefore the field name on every line.
REQUEST_ID_FIELD = "request_id"

#: The operator's level override, read from the environment rather than through
#: `Settings`. Not a new convention: `app/workers/entrypoints.configure_logging`
#: already reads `LOG_LEVEL` this way, and for the reason stated there -- logging
#: has to be configured before anything that could fail while VALIDATING
#: configuration, or the line saying which setting was wrong is the one that
#: gets swallowed. Default INFO, which is strictly more than the stdlib's
#: last-resort WARNING that every `logging.getLogger` module reached before this.
_LEVEL_ENV_VAR = "LOG_LEVEL"
_DEFAULT_LEVEL = "INFO"

#: Loggers whose own handlers would double every line once the root has one.
#: uvicorn installs its own; letting it propagate to ours AND keep its own is
#: how one request becomes two log lines that disagree about their format.
_PROPAGATE_ONLY = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _shared_processors() -> list[Any]:
    """The chain both systems run, structlog events and stdlib records alike."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging(*, production: bool | None = None) -> None:
    """Configure structlog and the stdlib root logger, once, into one stream.

    Provenance: there was no configuration at all before 2026-09-17; see the
    module docstring. Idempotent, because the root handler list is REPLACED
    rather than appended to: calling this twice leaves one handler, not two.
    """
    if production is None:
        # THE ENVIRONMENT VARIABLE, NEVER the settings accessor. The Lambda entry
        # points configure logging BEFORE the secret bootstrap has loaded
        # this function's secrets (deliberately, so a failed secret fetch is
        # readable), and constructing a full validated Settings at that
        # moment trips the production JWT boot refusal on a process whose
        # secrets simply have not arrived yet. That took every Lambda task
        # down on 2026-09-19, at import, on the first production roll. A log
        # format decision needs one string, so it reads the one string.
        production = (
            os.environ.get("ENVIRONMENT", "").strip().lower() == "production"
        )

    shared = _shared_processors()
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if production
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # `foreign_pre_chain` is what pulls a plain `logging.getLogger` record
        # through the same processors as a structlog event, which is the whole
        # point: one format, whichever system wrote the line.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get(_LEVEL_ENV_VAR, _DEFAULT_LEVEL).upper())

    for name in _PROPAGATE_ONLY:
        vendor = logging.getLogger(name)
        vendor.handlers = []
        vendor.propagate = True


def new_request_id() -> str:
    return uuid.uuid4().hex


def _inbound_request_id(headers: list[tuple[bytes, bytes]]) -> str | None:
    """The caller's own id, if it sent a usable one.

    Bounded and filtered: the value is echoed back in a response header and
    written into log lines, so an unbounded or control-character-bearing value
    from an anonymous caller is a header-injection and log-forging surface. An
    unusable value is REPLACED with a fresh id rather than rejected -- the
    request is not the header's fault.
    """
    wanted = REQUEST_ID_HEADER.encode("latin-1")
    for name, value in headers:
        if name.lower() != wanted:
            continue
        # latin-1 maps every byte, so this cannot raise; a header is bytes on
        # the wire and this is the encoding ASGI and HTTP/1.1 agree on.
        candidate = value.decode("latin-1").strip()
        if not candidate or len(candidate) > 128:
            return None
        if not all(ch.isalnum() or ch in "-_.:" for ch in candidate):
            return None
        return candidate
    return None


class RequestIdMiddleware:
    """Bind a request id for the duration of one request, and echo it back.

    Pure ASGI on purpose (see the module docstring). Registered OUTERMOST so
    the binding is in place before anything else in the stack logs.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope.get("headers") or [])
        if request_id is None:
            request_id = new_request_id()

        tokens = None
        try:
            tokens = structlog.contextvars.bind_contextvars(
                **{REQUEST_ID_FIELD: request_id}
            )
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            logging.getLogger(__name__).warning(
                "request_id.bind_failed err=%s", type(exc).__name__
            )

        header = (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))

        async def send_with_request_id(message: dict) -> None:
            if message.get("type") == "http.response.start":
                # Copied rather than mutated in place: the same headers list
                # object can be reused by a streaming response.
                message = dict(message)
                message["headers"] = [
                    *(h for h in message.get("headers") or [] if h[0].lower() != header[0]),
                    header,
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            if tokens is not None:
                structlog.contextvars.reset_contextvars(**tokens)
