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
import logging
import threading
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


# ── Shutdown, and the loop the hub is bound to ───────────────────────────────
#
# These pin the 2026-09-14 fix. The defect was not a wrong answer, it was a
# suite that stopped: `TestClient.__exit__` closed its portal's event loop while
# the hub's reader task was still awaiting a redis socket, and on Windows
# `ProactorEventLoop.close()` waits on outstanding overlapped I/O, so the join
# never returned. No test failed. The run simply hung, with the last thing
# printed being a passing dot.
#
# The product half of the same bug is worse and platform-independent: nothing
# ever stopped the reader on shutdown, and `_ensure_reader` treated a task
# belonging to a CLOSED loop as a live subscription, so a hub that outlived its
# loop went quiet for good without logging anything.


async def test_shutdown_is_safe_when_nothing_ever_subscribed(
    hub: realtime.Hub,
) -> None:
    """The lifespan calls this on every shutdown, including a process that
    never opened a socket. It must not raise, or every clean exit becomes an
    error in the log."""
    await hub.shutdown()


async def test_shutdown_releases_the_room_registry(hub: realtime.Hub) -> None:
    tenant, conversation = uuid.uuid4(), uuid.uuid4()
    await hub.join(tenant_id=tenant, conversation_id=conversation)
    assert hub.local_listeners(tenant_id=tenant, conversation_id=conversation) == 1

    await hub.shutdown()

    # A socket cannot survive the loop it was accepted on, so a room that
    # outlived shutdown would be a queue nobody will ever read from, counted
    # as a live listener for the rest of the process.
    assert hub.local_listeners(tenant_id=tenant, conversation_id=conversation) == 0


async def test_a_new_loop_does_not_inherit_the_previous_loop_s_rooms(
    hub: realtime.Hub,
) -> None:
    """The regression, reproduced the way the suite hit it: a second event loop
    in one process, which is what every `TestClient` in this suite creates.

    The old code kept one lock, one reader and one room registry across every
    loop the process ever ran. A queue created on a closed loop can never be
    drained, so counting it as a live listener means the hub reports delivery
    to a socket that is gone; and the reader it refused to replace (a task on a
    dead loop never reports done) meant nothing arrived for the sockets that
    were still real.
    """
    tenant, conversation = uuid.uuid4(), uuid.uuid4()

    # A whole separate loop, on its own thread, opened and closed before this
    # test's loop asks the hub anything.
    def on_another_loop() -> None:
        asyncio.run(hub.join(tenant_id=tenant, conversation_id=conversation))

    thread = threading.Thread(target=on_another_loop)
    thread.start()
    thread.join()

    await hub.join(tenant_id=tenant, conversation_id=conversation)
    try:
        # One: ours. The queue from the dead loop is discarded rather than
        # counted, which is what stops the hub reporting a listener that can
        # never receive anything.
        assert hub.local_listeners(tenant_id=tenant, conversation_id=conversation) == 1
    finally:
        await hub.shutdown()


async def test_a_closed_loop_cannot_fail_the_commit_that_stored_the_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`publish_after_commit` schedules onto the loop that was running when it
    was called, and that handler runs INSIDE `commit()`.

    So a RuntimeError from `create_task` does not cost a notification, it costs
    the request: it comes out of `session.commit()` after the message is
    already durably stored, and the sender is told the send failed while every
    other participant can already read it. `hub.publish` was careful about
    exactly this and the two lines that scheduled it were not.

    Since PLAN-p3 WP1 the callback runs through `core/after_commit.on_commit`,
    which would LOG an exception rather than raise it, so "commit did not
    raise" alone would pass with the guard deleted. The assertion is therefore
    that the guard ran (`realtime.schedule_failed`) and the generic catch did
    not (`after_commit.callback_failed`). A real `Session` with no bind
    commits an empty transaction, which is enough to fire `after_commit`.
    """
    from sqlalchemy.orm import Session

    dead = asyncio.new_event_loop()
    dead.close()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: dead)

    session = Session()
    realtime.publish_after_commit(
        session, {"tenant_id": "t", "conversation_id": "c", "message": {}}
    )
    with caplog.at_level(logging.WARNING):
        session.commit()

    messages = [record.getMessage() for record in caplog.records]
    assert any(m.startswith("realtime.schedule_failed") for m in messages), messages
    assert not any(m.startswith("after_commit.callback_failed") for m in messages)


async def test_a_rolled_back_publish_never_fires_on_a_later_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listener this replaced was `once=True` per call and survived a
    rollback, so a publish registered in a transaction that rolled back fired
    on the NEXT commit of the same session: every listening tab rendered a
    message that was never stored. On `on_commit` the pending publish is
    discarded when the outermost transaction ends without committing.

    Mutation-checked: restoring the old `event.listens_for(..., once=True)`
    registration makes the second assertion fail with one publish.
    """
    from sqlalchemy.orm import Session

    published: list[dict] = []

    async def _record(payload: dict) -> None:
        published.append(payload)

    monkeypatch.setattr(realtime.hub, "publish", _record)

    session = Session()
    session.begin()
    realtime.publish_after_commit(
        session, {"tenant_id": "t", "conversation_id": "never-stored", "message": {}}
    )
    session.rollback()

    session.begin()
    realtime.publish_after_commit(
        session, {"tenant_id": "t", "conversation_id": "stored", "message": {}}
    )
    session.commit()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [p["conversation_id"] for p in published] == ["stored"]
