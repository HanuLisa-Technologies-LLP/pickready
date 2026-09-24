"""The send worker CLAIMS a row before it sends it (Phase 6 WP6-C).

THE DEFECT
----------
`_send_lifecycle_email_async` read the row, saw `queued`, and sent. Two
invocations for one row (a redelivered task, a double dispatch, and now the
re-dispatch `reconcile_queued_emails` makes) both saw `queued` and both sent:
the candidate got the email twice. The fix is one conditional UPDATE
(`email_outbox.claim`), committed before the transport is called, so exactly
one invocation can move the row out of `queued`.

The send-time sender chokepoint tests that lived in `test_email_senders.py`
moved here and onto a REAL database: a fake session cannot execute the claim,
and a test that stubs the one statement under test asserts nothing.

Mutation check: replacing the claim with the old read-then-send made
`test_two_workers_on_one_row_send_it_once` see two transport calls.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.models.email_sender import (
    SENDER_DISABLED,
    SENDER_EMAIL_VERIFIED,
    SENDER_PENDING_VERIFICATION,
    SENDER_REVOKED,
    SENDER_VERIFICATION_EXPIRED,
)
from app.services import email_outbox
from app.services.sms_service import TransientDeliveryError
from app.workers import tasks
from tests import comms_world


@pytest.fixture
async def world():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping send-claim tests")
    sessions = comms_world.factory()
    w = await comms_world.seed(sessions)
    try:
        yield w
    finally:
        await comms_world.cleanup(sessions, w)


async def _queue(world, *, sender_id=None, thread=False) -> uuid.UUID:
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            row = await email_outbox.queue_candidate_email(
                session,
                tenant_id=world.tenant,
                email_type="shortlist",
                recipient_email=world.candidate_email,
                candidate_id=world.candidate,
                job_id=world.job,
                link_id=world.link,
                subject="Subject",
                body="Body",
                generated_by_ai=False,
                edited_by_human=True,
                sent_by=world.recruiter if thread else None,
                requested_sender_id=sender_id,
                thread=thread,
            )
            return row.id


async def _send(email_log_id) -> dict:
    """Run the worker function on its own session, as the task does."""
    sessions = comms_world.factory()
    async with sessions() as session:
        await comms_world.bypass(session)
        return await tasks._send_lifecycle_email_async(session, str(email_log_id))


async def _row(email_log_id) -> dict:
    return (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT status, error, sent_at, claimed_at, provider_message_id, "
            " transport FROM email_log WHERE id = :id",
            {"id": str(email_log_id)},
        )
    )[0]


async def test_two_workers_on_one_row_send_it_once(world, monkeypatch) -> None:
    row_id = await _queue(world)
    calls: list[str] = []

    async def _slow_transport(**kwargs):
        calls.append(kwargs["to"])
        # Long enough that the second worker arrives while the first is still
        # "sending", which is the window the old read-then-send left open.
        await asyncio.sleep(0.3)
        return "provider-id-1"

    monkeypatch.setattr(tasks, "_deliver_email", _slow_transport)
    first, second = await asyncio.gather(_send(row_id), _send(row_id))

    assert len(calls) == 1, calls
    # One worker sent it; the other found no row to claim and did nothing,
    # whatever state it happened to read the row in.
    winners = [r for r in (first, second) if r == {"status": "sent"}]
    losers = [r for r in (first, second) if r.get("resent") is False]
    assert len(winners) == 1 and len(losers) == 1, (first, second)
    row = await _row(row_id)
    assert row["status"] == "sent" and row["provider_message_id"] == "provider-id-1"
    assert row["claimed_at"] is not None


async def test_a_sent_row_is_never_sent_again(world, monkeypatch) -> None:
    row_id = await _queue(world)
    calls: list[str] = []

    async def _transport(**kwargs):
        calls.append(kwargs["to"])
        return "provider-id-2"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)
    assert (await _send(row_id))["status"] == "sent"
    again = await _send(row_id)
    assert again == {"status": "sent", "resent": False}
    assert len(calls) == 1


async def test_a_transient_failure_releases_the_claim_for_the_retry(
    world, monkeypatch
) -> None:
    row_id = await _queue(world)

    async def _flaky(**kwargs):
        raise TransientDeliveryError("smtp", 421, "server_busy", "try later")

    monkeypatch.setattr(tasks, "_deliver_email", _flaky)
    with pytest.raises(TransientDeliveryError):
        await _send(row_id)
    row = await _row(row_id)
    assert row["status"] == "queued" and row["claimed_at"] is None

    async def _ok(**kwargs):
        return "provider-id-3"

    monkeypatch.setattr(tasks, "_deliver_email", _ok)
    assert (await _send(row_id))["status"] == "sent"


@pytest.mark.parametrize(
    "sender_status",
    [
        SENDER_PENDING_VERIFICATION,
        SENDER_EMAIL_VERIFIED,
        SENDER_VERIFICATION_EXPIRED,
        SENDER_DISABLED,
        SENDER_REVOKED,
    ],
)
async def test_a_sender_no_longer_active_fails_the_queued_email_honestly(
    world, monkeypatch, sender_status: str
) -> None:
    """The worker re-loads the sender AT SEND TIME; anything not active fails
    the message with the reason on the row, and nothing is delivered."""
    sessions = comms_world.factory()
    sender = await comms_world.add_sender(sessions, world)
    row_id = await _queue(world, sender_id=sender)
    await comms_world.rows(
        sessions,
        "UPDATE client_email_senders SET status = :s WHERE id = :id RETURNING id",
        {"s": sender_status, "id": str(sender)},
    )

    async def _never(**kwargs):
        raise AssertionError("a non-active sender must never reach the transport")

    monkeypatch.setattr(tasks, "_deliver_email", _never)
    assert await _send(row_id) == {"status": "failed", "error": "sender_not_active"}
    row = await _row(row_id)
    assert row["status"] == "failed"
    assert sender_status.replace("_", " ") in (row["error"] or "")


async def test_an_active_sender_reaches_the_transport_door(world, monkeypatch) -> None:
    sender = await comms_world.add_sender(comms_world.factory(), world)
    row_id = await _queue(world, sender_id=sender)
    seen: dict = {}

    async def _transport(**kwargs):
        seen.update(kwargs)
        return "provider-id-4"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)
    assert (await _send(row_id))["status"] == "sent"
    assert seen["sender"].id == sender
    row = await _row(row_id)
    assert row["provider_message_id"] == "provider-id-4"


async def test_the_thread_message_follows_the_email(world, monkeypatch) -> None:
    """A human-sent email is shown in the thread as `pending`; the worker
    moves it with the email_log row so the two never disagree."""
    row_id = await _queue(world, thread=True)

    async def _transport(**kwargs):
        return "provider-id-5"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)
    await _send(row_id)
    message = await comms_world.rows(
        comms_world.factory(),
        "SELECT delivery_status FROM conversation_messages WHERE email_log_id = :id",
        {"id": str(row_id)},
    )
    assert message == [{"delivery_status": "sent"}]


async def test_a_threaded_email_carries_the_threads_reply_address(
    world, monkeypatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "inbound_email_domain", "reply.comms.test")
    row_id = await _queue(world, thread=True)
    seen: dict = {}

    async def _transport(**kwargs):
        seen.update(kwargs)
        return "provider-id-6"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)
    await _send(row_id)
    token = (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT c.thread_token FROM email_log e "
            "JOIN conversations c ON c.id = e.conversation_id WHERE e.id = :id",
            {"id": str(row_id)},
        )
    )[0]["thread_token"]
    assert seen["reply_to"] == f"conversations+{token}@reply.comms.test"


async def test_without_an_inbound_domain_there_is_no_thread_reply_address(
    world, monkeypatch
) -> None:
    """Pilot's state: the binding is written, the Reply-To is not, and the
    transport keeps its previous behaviour."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "inbound_email_domain", "")
    row_id = await _queue(world, thread=True)
    seen: dict = {}

    async def _transport(**kwargs):
        seen.update(kwargs)
        return "provider-id-7"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)
    await _send(row_id)
    assert seen["reply_to"] is None
