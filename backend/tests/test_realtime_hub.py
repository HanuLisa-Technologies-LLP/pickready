"""The realtime hub: who receives a notification, and who never does.

WHY THIS IS TESTED WITHOUT REDIS
----------------------------------
The hub has two halves. The cross-instance half is a Redis pub/sub channel and
is exercised in a deployed environment; the half tested here is the one that
decides DELIVERY, and it is pure Python: which queues a payload reaches, whether
a slow client can block anybody else, and whether an instance that has lost
Redis still works for its own sockets.

The isolation assertion is the important one. A room keyed only by conversation
id would be a cross-tenant broadcast waiting for an id collision, and the
failure would be silent in exactly the way a leak is: everything works, and one
customer sees another customer's message.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services import realtime

pytestmark = pytest.mark.asyncio


def _event(tenant: uuid.UUID, conversation: uuid.UUID, body: str) -> dict:
    return realtime.message_event(
        tenant_id=tenant,
        conversation_id=conversation,
        message={
            "id": uuid.uuid4(),
            "author_party": "recruiter",
            "author_user_id": None,
            "author_name": "Priya Raman",
            "body": body,
            "channel": "chat",
            "delivery_status": "delivered",
            "created_at": "2026-09-12T00:00:00+00:00",
        },
    )


@pytest.fixture
def hub() -> realtime.Hub:
    """A fresh hub per test. The module singleton is process-wide, and a test
    that leaked a queue into it would fail a later, unrelated test."""
    return realtime.Hub()


async def test_a_joined_listener_receives_the_message(hub: realtime.Hub) -> None:
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    queue = await hub.join(tenant_id=tenant, conversation_id=conversation)
    try:
        await hub.publish(_event(tenant, conversation, "Thursday works."))
        delivered = await asyncio.wait_for(queue.get(), timeout=2)
        assert delivered["message"]["body"] == "Thursday works."
    finally:
        await hub.leave(tenant_id=tenant, conversation_id=conversation, queue=queue)


async def test_another_tenants_message_is_never_delivered(hub: realtime.Hub) -> None:
    """The room is keyed by TENANT and conversation, not by conversation alone.

    Keyed on the conversation only, this would be a cross-tenant broadcast the
    first time two customers' ids collided, and nothing would report it.
    """
    conversation = uuid.uuid4()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    queue = await hub.join(tenant_id=mine, conversation_id=conversation)
    try:
        # Deliberately the SAME conversation id under a different tenant.
        await hub.publish(_event(theirs, conversation, "Another customer's thread."))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.2)
    finally:
        await hub.leave(tenant_id=mine, conversation_id=conversation, queue=queue)


async def test_a_different_conversation_in_the_same_tenant_is_not_delivered(
    hub: realtime.Hub,
) -> None:
    tenant = uuid.uuid4()
    mine, other = uuid.uuid4(), uuid.uuid4()
    queue = await hub.join(tenant_id=tenant, conversation_id=mine)
    try:
        await hub.publish(_event(tenant, other, "A different thread."))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.2)
    finally:
        await hub.leave(tenant_id=tenant, conversation_id=mine, queue=queue)


async def test_every_listener_on_one_conversation_receives_it(
    hub: realtime.Hub,
) -> None:
    """Two recruiters with the same thread open both see it, which is the whole
    reason the fan-out exists rather than a per-socket subscription."""
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    first = await hub.join(tenant_id=tenant, conversation_id=conversation)
    second = await hub.join(tenant_id=tenant, conversation_id=conversation)
    try:
        assert hub.local_listeners(tenant_id=tenant, conversation_id=conversation) == 2
        await hub.publish(_event(tenant, conversation, "Seen by both."))
        for queue in (first, second):
            got = await asyncio.wait_for(queue.get(), timeout=2)
            assert got["message"]["body"] == "Seen by both."
    finally:
        for queue in (first, second):
            await hub.leave(
                tenant_id=tenant, conversation_id=conversation, queue=queue
            )


async def test_leaving_removes_the_room(hub: realtime.Hub) -> None:
    """A queue left registered is a slow leak that ends with the reader fanning
    out to sockets nobody is holding."""
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    queue = await hub.join(tenant_id=tenant, conversation_id=conversation)
    await hub.leave(tenant_id=tenant, conversation_id=conversation, queue=queue)
    assert hub.local_listeners(tenant_id=tenant, conversation_id=conversation) == 0


async def test_a_client_that_stopped_draining_never_blocks_the_publisher(
    hub: realtime.Hub,
) -> None:
    """The queue is BOUNDED and overflow is dropped, and the drop is safe
    because the message is already in Postgres: the socket carries a hint, and a
    client that misses one re-reads the thread.

    The property asserted is that publishing never blocks. A hub that awaited
    `queue.put` would let one asleep background tab stall the fan-out for every
    other browser on the instance.
    """
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    queue = await hub.join(tenant_id=tenant, conversation_id=conversation)
    try:
        for index in range(queue.maxsize + 5):
            await asyncio.wait_for(
                hub.publish(_event(tenant, conversation, f"message {index}")),
                timeout=2,
            )
        assert queue.full()
        assert queue.qsize() == queue.maxsize
        # The OLDEST messages are kept and the newest dropped, which is the
        # right way round: a reader draining a backlog reads forward in order.
        assert (await queue.get())["message"]["body"] == "message 0"
    finally:
        await hub.leave(tenant_id=tenant, conversation_id=conversation, queue=queue)


async def test_an_instance_with_no_redis_still_serves_its_own_sockets(
    hub: realtime.Hub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degradation is RECORDED, never silent (claude.md, 2026-09-09), and the
    local half keeps working. Failing the send because the notification did not
    fan out would turn a cosmetic delay into a failed message."""
    import app.core.cache as cache

    monkeypatch.setattr(cache, "_redis", lambda: None)
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    queue = await hub.join(tenant_id=tenant, conversation_id=conversation)
    try:
        await hub.publish(_event(tenant, conversation, "Local only."))
        got = await asyncio.wait_for(queue.get(), timeout=2)
        assert got["message"]["body"] == "Local only."
    finally:
        await hub.leave(tenant_id=tenant, conversation_id=conversation, queue=queue)


async def test_the_channel_name_carries_the_tenant() -> None:
    """Every cache and channel key in this product contains the tenant id, and
    this is the most consequential place for it."""
    tenant = uuid.uuid4()
    assert realtime.channel_for(tenant).endswith(str(tenant))
    assert realtime.channel_for(tenant) != realtime.channel_for(uuid.uuid4())


async def test_the_wire_shape_carries_no_internal_identifier() -> None:
    """What travels is what the participant is already authorised to read.

    A client token, an email message id or an internal delivery detail reaching
    a browser would be an accident of using the stored row as the wire shape,
    which is exactly why `message_event` builds the payload field by field.
    """
    payload = realtime.message_event(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message={
            "id": uuid.uuid4(),
            "author_party": "recruiter",
            "author_user_id": uuid.uuid4(),
            "author_name": "Priya Raman",
            "body": "Hello",
            "channel": "chat",
            "delivery_status": "delivered",
            "created_at": "2026-09-12T00:00:00+00:00",
            "client_token": "tok-should-not-travel",
            "email_message_id": "<should-not-travel@example.com>",
            "delivery_detail": "internal detail",
        },
    )
    assert set(payload["message"]) == {
        "id",
        "author_party",
        "author_user_id",
        "author_name",
        "body",
        "channel",
        "delivery_status",
        "created_at",
    }
