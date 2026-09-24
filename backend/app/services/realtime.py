"""Real-time fan-out for conversations, across every API instance.

THE SOCKET IS A NOTIFICATION, NEVER THE TRANSPORT OF RECORD
-------------------------------------------------------------
A message is written to Postgres FIRST and published SECOND. The socket carries
"this conversation changed, and here is the message", and a client that missed
it can always re-read the thread. That ordering is what makes the whole design
survivable: a dropped frame, a reconnect, a browser asleep in a background tab
and a deploy that moves a connection to another task are all the same event, and
none of them can lose a message, because the message was never only in flight.

Building it the other way round, with the socket as the delivery mechanism and
the database as a cache, would mean inventing acknowledgements, retries and an
outbox. This product already has a durable store; it does not need a second one.

WHY REDIS, AND WHY IT IS NOT OPTIONAL HERE
--------------------------------------------
The API runs one to four Fargate tasks. An in-process registry alone would
deliver a message only to the browser that happens to be connected to the SAME
task, so two recruiters in one conversation would see each other's messages
roughly half the time, which is worse than not having realtime at all: it looks
like it works.

So every instance subscribes to one Redis channel per tenant, and publishes to
it. `core/cache` already owns the Redis connection and is the one place that
knows how to build one; opening a second client here would be a second answer
to "where does Redis come from".

A PUBLISH FAILURE IS LOGGED AND SWALLOWED, DELIBERATELY, AND IT IS THE ONE
PLACE IN THIS FEATURE WHERE THAT IS CORRECT: the message is already committed.
Failing the request because the notification did not go out would turn a
cosmetic degradation (the other tab refreshes a second later) into a failed
send, which is the opposite trade.

THE CHANNEL IS KEYED BY TENANT
--------------------------------
Every cache and channel key in this product contains the tenant id, and this is
the most consequential place for it: a channel keyed only by conversation id
would be a cross-tenant broadcast waiting for an id collision, and subscribers
filter locally anyway. Belt and braces, in the direction that matters.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from app.core.after_commit import on_commit

logger = logging.getLogger(__name__)

#: One channel per tenant. See the module docstring: never per conversation
#: alone, and never global.
CHANNEL_PREFIX = "readypick:conv"

#: How long shutdown will wait for the reader and its connection to let go.
#: Deliberately short: this runs while the process is already leaving, and the
#: cost of giving up is an abandoned socket the kernel reaps, while the cost of
#: waiting is a deploy that appears to stall for no visible reason.
SHUTDOWN_TIMEOUT_SECONDS = 5.0


def channel_for(tenant_id: uuid.UUID | str) -> str:
    return f"{CHANNEL_PREFIX}:{tenant_id}"


class Hub:
    """The per-process registry of live sockets, plus one Redis subscription.

    One subscription per PROCESS rather than per socket: a hundred open tabs on
    one task is one Redis consumer, not a hundred. The reader task starts on the
    first subscriber and stops when the last one leaves, so an instance serving
    no conversations holds no connection.
    """

    def __init__(self) -> None:
        # {tenant: {conversation: {queue}}}. A queue per socket rather than the
        # socket itself, so a slow client cannot block the reader that feeds
        # every other client on this instance.
        self._rooms: dict[str, dict[str, set[asyncio.Queue]]] = {}
        self._reader: asyncio.Task | None = None
        self._pubsub: Any = None
        # THE LOOP THE READER, THE PUBSUB CONNECTION AND THE LOCK BELONG TO.
        #
        # This hub is a module-level singleton, and an asyncio task is not a
        # portable object: it belongs to the loop that created it, and so do a
        # lock and a redis connection. One process normally has exactly one
        # loop, so this is set once and never changes again.
        #
        # It is recorded anyway, because the failure when it DOES change is
        # silent in the worst way. `_ensure_reader` guarded with "is there a
        # task, and is it not done" -- and a task belonging to a CLOSED loop is
        # never done. So the hub would decide it was still subscribed, never
        # resubscribe, and quietly stop delivering to every socket on the
        # instance for the rest of the process's life, with nothing logged
        # because nothing failed.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    # ── Loop binding ────────────────────────────────────────────────────────

    def _bind(self) -> asyncio.Lock:
        """Return this loop's lock, discarding state left by a previous loop.

        A lock, like a task, belongs to one loop. Reusing the one a dead loop
        was waiting on is how a singleton turns into a deadlock, so a new loop
        gets a new lock and inherits no reader.

        The stale reader is DROPPED rather than cancelled, and that asymmetry
        is deliberate: cancelling a task means touching its loop, and this path
        exists precisely for the case where that loop is gone. Dropping leaks
        the task on a loop nobody is running, which is inert; reaching into a
        closed loop raises. The path that matters in production is
        `shutdown()`, which cancels properly while the loop is still alive.
        """
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._lock = asyncio.Lock()
            self._reader = None
            self._pubsub = None
            self._rooms.clear()
        elif self._lock is None:  # pragma: no cover - set together with _loop
            self._lock = asyncio.Lock()
        return self._lock

    # ── Membership ──────────────────────────────────────────────────────────

    async def join(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._bind():
            room = self._rooms.setdefault(str(tenant_id), {}).setdefault(
                str(conversation_id), set()
            )
            room.add(queue)
            await self._ensure_reader()
        return queue

    async def leave(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        queue: asyncio.Queue,
    ) -> None:
        async with self._bind():
            tenant = self._rooms.get(str(tenant_id))
            if not tenant:
                return
            room = tenant.get(str(conversation_id))
            if room:
                room.discard(queue)
                if not room:
                    tenant.pop(str(conversation_id), None)
            if not tenant:
                self._rooms.pop(str(tenant_id), None)
            if not self._rooms:
                await self._stop_reader()

    def local_listeners(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> int:
        return len(self._rooms.get(str(tenant_id), {}).get(str(conversation_id), set()))

    # ── Delivery ────────────────────────────────────────────────────────────

    def _deliver_local(self, payload: dict) -> None:
        tenant = self._rooms.get(str(payload.get("tenant_id")), {})
        for queue in tuple(tenant.get(str(payload.get("conversation_id")), set())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A client that has not drained a hundred messages is not
                # keeping up. Dropping the notification is safe BECAUSE the
                # message is already in Postgres: the socket carries a hint,
                # and a client that misses one re-reads the thread.
                logger.warning(
                    "realtime.queue_full conversation=%s",
                    payload.get("conversation_id"),
                )

    async def publish(self, payload: dict) -> None:
        """Fan out to this instance and to every other one."""
        self._deliver_local(payload)
        try:
            from app.core.cache import _redis

            client = _redis()
            if client is None:
                return
            await client.publish(
                channel_for(payload["tenant_id"]), json.dumps(payload, default=str)
            )
        except Exception as exc:  # noqa: BLE001
            # SWALLOWED ON PURPOSE, and this is the only place in this feature
            # where that is right: the message is committed. Failing the caller
            # would turn "the other tab refreshes a second later" into "the
            # send failed". Recorded rather than silent.
            logger.warning("realtime.publish_failed error=%s", type(exc).__name__)

    # ── The single Redis subscription ───────────────────────────────────────

    async def _ensure_reader(self) -> None:
        if self._reader is not None and not self._reader.done():
            return
        try:
            from app.core.cache import _redis

            client = _redis()
            if client is None:
                return
            self._pubsub = client.pubsub()
            await self._pubsub.psubscribe(f"{CHANNEL_PREFIX}:*")
            self._reader = asyncio.create_task(self._read_forever())
        except Exception as exc:  # noqa: BLE001
            # Without Redis this instance still works for its OWN sockets. That
            # is a real degradation and it is recorded as one, rather than
            # pretending cross-instance delivery is happening.
            logger.warning("realtime.subscribe_failed error=%s", type(exc).__name__)

    async def _read_forever(self) -> None:
        pubsub = self._pubsub
        if pubsub is None:  # pragma: no cover - guarded by _ensure_reader
            return
        try:
            async for message in pubsub.listen():
                if not message or message.get("type") != "pmessage":
                    continue
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    logger.warning("realtime.bad_payload")
                    continue
                self._deliver_local(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("realtime.reader_stopped error=%s", type(exc).__name__)

    async def shutdown(self) -> None:
        """Stop the subscription. Called from the app's lifespan on the way out.

        WHY THE LIFESPAN HAS TO DO THIS, RATHER THAN `leave()` ALONE
        ------------------------------------------------------------
        `leave()` stops the reader when the LAST socket goes, which covers the
        quiet case and not the real one. A deploy stops a task while sockets
        are still open, so the reader and its redis connection were simply
        never closed: the loop was torn down underneath a task still awaiting a
        socket.

        On Linux that is untidy. On Windows it HANGS, and that is how this was
        found: `ProactorEventLoop.close()` waits on outstanding overlapped I/O,
        so `TestClient.__exit__` blocked forever joining its portal thread and
        took the whole test suite with it, with no failure and no output.

        Idempotent, and safe to call when nothing ever subscribed.
        """
        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            # A different loop's reader is not ours to await. `_bind` will
            # discard it; saying so is better than pretending we stopped it.
            logger.warning("realtime.shutdown_skipped_foreign_loop")
            return
        await self._stop_reader()
        self._rooms.clear()

    async def _stop_reader(self) -> None:
        """Cancel the reader and close the subscription, in BOUNDED time.

        Both awaits carry a deadline, because this runs on the way out and a
        shutdown step that can block forever is the bug this whole change is
        about. A redis connection that was opened on a different loop, or whose
        server has gone away, can leave `close()` waiting on a socket nobody is
        going to answer; that must cost a logged second, not the process.

        Giving up is safe here and nowhere else: the process is going away, so
        an abandoned connection is reaped by the kernel. It is recorded rather
        than passed over, because a shutdown that regularly times out means the
        reader is wedged and that is worth seeing.
        """
        reader, pubsub = self._reader, self._pubsub
        self._reader, self._pubsub = None, None
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(reader, timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if pubsub is not None:
            try:
                await asyncio.wait_for(
                    pubsub.close(), timeout=SHUTDOWN_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning("realtime.pubsub_close_timed_out")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "realtime.pubsub_close_failed error=%s", type(exc).__name__
                )


#: One hub per process.
hub = Hub()

#: Publishes in flight. asyncio holds only a WEAK reference to a task, so a
#: fire-and-forget one can be collected mid-await and the notification simply
#: never happens -- a bug that reads as "sometimes the other tab does not
#: update" and is never reproducible.
_PENDING_PUBLISHES: set[asyncio.Task] = set()


def publish_after_commit(session: Any, payload: dict) -> None:
    """Announce `payload` the moment `session`'s transaction commits.

    THE TRIGGER IS SQLALCHEMY'S OWN `after_commit`, AND THE ALTERNATIVE DOES
    NOT WORK. FastAPI sends the response, and therefore runs any
    `BackgroundTasks`, INSIDE the dependency exit stack, so a background
    publish still fires before a `get_tenant_db`-style session commits.
    `tests/test_conversations_api.py` asserts the ordering from a second
    connection and caught exactly that.

    A rolled-back transaction publishes NOTHING, which is the half that matters
    most: a notification for a message that was never stored would have every
    listening tab render one that does not exist.

    ONE AFTER-COMMIT MECHANISM (PLAN-p3 WP1). This hangs off
    `core/after_commit.on_commit`, the same hook `dispatch_after_commit` uses,
    rather than a listener of its own. The listener it replaced was installed
    `once=True` per call and was NOT removed by a rollback, so a publish
    registered in a transaction that rolled back fired on the NEXT commit of
    the same session, announcing a message that was never stored.
    `on_commit` discards what is pending when the outermost transaction ends
    without committing.
    """
    loop = asyncio.get_running_loop()

    def _fire() -> None:
        # THIS RUNS INSIDE `commit()`, SO WHAT IT RAISES, THE CALLER RAISES.
        #
        # `hub.publish` already refuses to fail a send it could not announce.
        # That care was undone by the two lines that SCHEDULE it: this handler
        # runs inside SQLAlchemy's commit, so a RuntimeError from `create_task`
        # (the loop closed under a cancelled request, or a session committed
        # from somewhere other than the loop that opened it) comes out of
        # `session.commit()` and 500s the request -- AFTER the message is
        # durably stored. The sender is told their message failed while every
        # other participant can already read it, which is worse than the missed
        # notification it is reporting.
        #
        # So the scheduling is guarded exactly as the publish itself is, and
        # the failure is recorded rather than swallowed.
        # Built before the try so it can be CLOSED if scheduling fails. An
        # un-awaited coroutine is not free: it warns from whatever unrelated
        # code the garbage collector happens to be running, which is a worse
        # thing to debug than the failure that caused it.
        coro = hub.publish(payload)
        try:
            task = loop.create_task(coro)
        except RuntimeError as exc:
            coro.close()
            logger.warning("realtime.schedule_failed error=%s", exc)
            return
        _PENDING_PUBLISHES.add(task)
        task.add_done_callback(_PENDING_PUBLISHES.discard)

    on_commit(
        session,
        _fire,
        label=f"realtime.publish conversation={payload.get('conversation_id')}",
    )


def message_event(
    *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, message: dict
) -> dict:
    """The wire shape. Ids, words and timestamps only.

    The message BODY travels, because the whole point is that the other tab
    renders it without a round trip, and it is the same text that participant
    is already authorised to read: a socket only ever joins a conversation its
    caller passed `authorize_participant` for.
    """
    return {
        "type": "message",
        "tenant_id": str(tenant_id),
        "conversation_id": str(conversation_id),
        "message": {
            "id": str(message["id"]),
            "author_party": message["author_party"],
            "author_user_id": (
                str(message["author_user_id"])
                if message.get("author_user_id")
                else None
            ),
            "author_name": message.get("author_name"),
            "body": message["body"],
            "channel": message["channel"],
            "delivery_status": message["delivery_status"],
            "created_at": str(message["created_at"]),
        },
    }
