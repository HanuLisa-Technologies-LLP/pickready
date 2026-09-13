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

from sqlalchemy import event

logger = logging.getLogger(__name__)

#: One channel per tenant. See the module docstring: never per conversation
#: alone, and never global.
CHANNEL_PREFIX = "readypick:conv"


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
        self._lock = asyncio.Lock()

    # ── Membership ──────────────────────────────────────────────────────────

    async def join(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
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
        async with self._lock:
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

    async def _stop_reader(self) -> None:
        reader, pubsub = self._reader, self._pubsub
        self._reader, self._pubsub = None, None
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.close()


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
    """
    loop = asyncio.get_running_loop()

    @event.listens_for(session.sync_session, "after_commit", once=True)
    def _fire(_sync_session) -> None:  # noqa: ANN001 -- SQLAlchemy's signature
        task = loop.create_task(hub.publish(payload))
        _PENDING_PUBLISHES.add(task)
        task.add_done_callback(_PENDING_PUBLISHES.discard)


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
