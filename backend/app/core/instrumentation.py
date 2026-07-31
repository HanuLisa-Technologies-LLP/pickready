"""Per-request timing and SQL query counting.

Two numbers explain nearly every "the site feels slow" report: how long the
handler took, and how many SQL statements it issued to get there. A handler that
runs 4 queries in 20 ms is healthy; the same handler running 120 queries in
900 ms is an N+1, and the count is what tells you which one you are looking at.

Implementation note that is easy to get wrong: the counter is a ContextVar
holding a MUTABLE object, not an int. Starlette runs the rest of the stack in a
child task with a COPY of the context, so a child that rebinds an int ContextVar
updates only its own copy and the middleware reads back a zero. Handing every
context a reference to the same small object sidesteps that entirely, while two
concurrent requests still get one object each.

Enabled outside production only (``settings.environment != "production"``), so a
production deployment pays nothing for it.
"""
from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.engine import Engine

log = logging.getLogger("pickready.perf")

# Requests slower than this are logged at WARNING so they stand out in a noisy
# dev log; everything else is INFO.
SLOW_REQUEST_MS = 500.0
# A request issuing more statements than this is almost certainly an N+1.
SUSPICIOUS_QUERY_COUNT = 25


@dataclass
class QueryStats:
    count: int = 0
    total_ms: float = 0.0
    #: Statements seen, truncated. Only collected when explicitly asked for, so
    #: normal operation keeps no SQL text in memory.
    statements: list[str] = field(default_factory=list)
    collect: bool = False


_stats: ContextVar[QueryStats] = ContextVar("pr_query_stats", default=QueryStats())
_installed = False


def begin_request(*, collect: bool = False) -> QueryStats:
    stats = QueryStats(collect=collect)
    _stats.set(stats)
    return stats


def current_stats() -> QueryStats:
    return _stats.get()


def install_query_counter() -> None:
    """Attach the counting listeners to every SQLAlchemy engine. Idempotent."""
    global _installed
    if _installed:
        return
    _installed = True

    # Uvicorn configures only its own loggers, leaving the root logger at
    # WARNING, so an INFO line here would be silently dropped. Give the perf
    # logger its own handler and stop it propagating into the root.
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:    %(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    @event.listens_for(Engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._pr_started = time.perf_counter()

    @event.listens_for(Engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        stats = _stats.get()
        stats.count += 1
        started = getattr(context, "_pr_started", None)
        if started is not None:
            stats.total_ms += (time.perf_counter() - started) * 1000
        if stats.collect:
            stats.statements.append(" ".join(statement.split())[:200])


async def timing_middleware(request, call_next):
    """ASGI middleware: log path, status, wall time, query count, SQL time.

    Also returns the same numbers as ``Server-Timing`` and ``X-Query-Count``
    response headers so a browser's network panel shows them per request. These
    are diagnostics headers only; no response BODY shape changes (other agents
    are building UI against these payloads).
    """
    # Opt-in per request: `X-Debug-SQL: 1` logs every statement the request ran,
    # which is how you tell an N+1 apart from a genuinely wide query. Off by
    # default so normal traffic never holds SQL text in memory, and unreachable
    # in production because the middleware is not installed there.
    stats = begin_request(collect=request.headers.get("X-Debug-SQL") == "1")
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000

    response.headers["Server-Timing"] = (
        f"app;dur={elapsed_ms:.1f}, sql;dur={stats.total_ms:.1f}"
    )
    response.headers["X-Query-Count"] = str(stats.count)

    slow = elapsed_ms >= SLOW_REQUEST_MS or stats.count >= SUSPICIOUS_QUERY_COUNT
    log.log(
        logging.WARNING if slow else logging.INFO,
        "perf %s %s status=%s dur_ms=%.1f queries=%d sql_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        stats.count,
        stats.total_ms,
    )
    for index, statement in enumerate(stats.statements, start=1):
        log.info("perf   sql[%02d] %s", index, statement)
    return response
