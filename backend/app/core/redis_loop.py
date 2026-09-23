"""One process-wide redis-py client, bound to the event loop that built it.

WHY THIS EXISTS
---------------
A redis-py asyncio connection pool belongs to the loop that opened its
connections. Carried into a second loop, every call on it fails. One uvicorn
process has one loop and never notices; a worker does, because
`workers/runtime._run` gives every task body its own `asyncio.run`, so on a
warm Lambda the SECOND invocation met a client built on the first one's dead
loop. The 2026-09-16 realtime-hub section of `claude.md` wrote the rule down: a
process-wide singleton holding asyncio state must record the loop it is bound
to and start clean on a new one.

`core/cache._redis` followed the rule. `workers/status` and `web_research`'s
breaker did not, and both swallowed the resulting failures, so on a warm worker
the matching run's stage list and the outreach delivery state silently read
PENDING for ever. Three copies of "a client for this loop" is how two of them
came to be wrong, so this is the ONE implementation and all three use it.

WHAT A BUILD FAILURE COSTS
--------------------------
`redis.asyncio.from_url` does not connect, so a build failure is rare (a
malformed URL, or a fault injected at the factory). When it happens the failure
is LOGGED AT WARNING and latched FOR THAT LOOP: the rest of this loop's work
costs nothing more, and the next loop (the next worker invocation, the next
test) tries again rather than inheriting an outage that may have ended. The
caller decides what `None` means, and each caller already documents that: the
cache degrades to a miss, the run-status record reads PENDING, the web-search
breaker fails open.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: Stands in for "no running loop" so a client built from synchronous code is
#: still bound to SOMETHING comparable. Synchronous code has no pool to
#: mismatch yet, so it shares whatever the last loop built.
_NO_LOOP = object()


class LoopBoundRedis:
    """A lazily built `redis.asyncio` client, rebuilt whenever the loop changes.

    `name` appears in every log line so an operator can tell the cache's client
    from the status record's. The timeouts are the caller's, because the
    trade differs: the cache would rather miss fast, a status write can wait a
    little longer.
    """

    def __init__(
        self,
        *,
        name: str,
        socket_timeout: float,
        connect_timeout: float,
    ) -> None:
        self.name = name
        self._socket_timeout = socket_timeout
        self._connect_timeout = connect_timeout
        self._client: Any = None
        self._loop: Any = None
        #: The loop on which a build failed, or None. Latched per loop only.
        self._failed_on: Any = None

    @staticmethod
    def _current_loop() -> Any:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return _NO_LOOP

    def client(self) -> Any:
        """The client for the running loop, or None when one cannot be built."""
        loop = self._current_loop()
        if loop is _NO_LOOP and self._loop is not None:
            # Synchronous caller: reuse whatever exists rather than rebind.
            loop = self._loop
        if self._failed_on is not None and self._failed_on is loop:
            return None
        if self._client is not None and self._loop is not loop:
            # The loop this client was built on is not the one running now. Do
            # NOT close it here: closing awaits on the old loop, which may be
            # gone. Dropping the reference is what a dead loop allows.
            logger.debug("redis_loop.rebind name=%s", self.name)
            self._client = None
        if self._client is None:
            try:
                import redis.asyncio as redis_asyncio  # noqa: PLC0415 -- the harness patches this factory

                # Keywords written out, never splatted: the timeout sweep in
                # `tests/test_redis_client_timeouts.py` reads them off the call.
                self._client = redis_asyncio.from_url(
                    get_settings().redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=self._connect_timeout,
                    socket_timeout=self._socket_timeout,
                )
                self._loop = loop
                self._failed_on = None
            except Exception as exc:  # noqa: BLE001 -- every caller has a documented None path
                logger.warning(
                    "redis_loop.unavailable name=%s error=%s", self.name, type(exc).__name__
                )
                self._client = None
                self._failed_on = loop
                return None
        return self._client

    async def aclose(self) -> None:
        """Release the pool at shutdown. Never raises; a failure is logged.

        Closing on the loop that built the client is the only close that can
        succeed, so a client from another loop is dropped rather than awaited.
        """
        client, bound = self._client, self._loop
        self._client = None
        self._loop = None
        self._failed_on = None
        if client is None:
            return
        if bound is not self._current_loop():
            logger.debug("redis_loop.close_skipped_foreign_loop name=%s", self.name)
            return
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001 -- shutdown must not fail on a close
            logger.debug(
                "redis_loop.close_failed name=%s error=%s", self.name, type(exc).__name__
            )

    def reset_for_tests(self) -> None:
        """Forget everything, without awaiting anything. Tests only."""
        self._client = None
        self._loop = None
        self._failed_on = None
