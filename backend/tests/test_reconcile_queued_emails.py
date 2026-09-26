"""The sweep that repairs a lost send, and never resends one (Phase 6 WP6-C).

`email_outbox` dispatches a send after its request commits. If that invoke
fails, the row is durable and nothing is working on it: without a sweep a
confirmation or a reminder is simply never sent, and "not sent" and "nothing
to send" produce the same empty log. `pickready.reconcile_queued_emails` asks
the TABLE every fifteen minutes.

What it must NOT do is as important as what it does:

* a row claimed and still `processing` may already have left the transport,
  so it is REPORTED and never resent (a duplicate email is worse than a late
  alarm);
* a queued row older than a day is reported and never sent, because a
  confirmation delivered a month late is a mistake, not a repair.

The task runs for real here (sync, `asyncio.run` inside, exactly as a Lambda
runs it) against the shared test database, so assertions are about THIS
test's rows rather than global counts, which other modules also write.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.workers import dispatch as dispatch_mod
from app.workers import tasks
from tests import comms_world

SEND = "pickready.send_lifecycle_email"


def _insert(world, *, status: str, age: timedelta, claimed_age: timedelta | None):
    row_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async def _write() -> None:
        sessions = comms_world.factory()
        async with sessions() as session:
            async with session.begin():
                await comms_world.bypass(session)
                await session.execute(
                    sa.text(
                        "INSERT INTO email_log (id, tenant_id, email_type, "
                        " recipient_email, candidate_id, job_id, "
                        " job_candidate_link_id, subject, body, status, "
                        " edited_by_human, generated_by_ai, created_at, claimed_at) "
                        "VALUES (:id, :tid, 'shortlist', :to, :cid, :jid, :lid, "
                        " 'S', 'B', :status, false, false, :created, :claimed)"
                    ),
                    {
                        "id": str(row_id),
                        "tid": str(world.tenant),
                        "to": world.candidate_email,
                        "cid": str(world.candidate),
                        "jid": str(world.job),
                        "lid": str(world.link),
                        "status": status,
                        "created": now - age,
                        "claimed": (now - claimed_age) if claimed_age else None,
                    },
                )

    asyncio.run(_write())
    return str(row_id)


@pytest.fixture
def world():
    if not asyncio.run(comms_world.reachable()):
        pytest.skip("no database reachable -- skipping the reconcile sweep test")
    sessions = comms_world.factory()
    w = asyncio.run(comms_world.seed(sessions))
    try:
        yield w
    finally:
        asyncio.run(comms_world.cleanup(comms_world.factory(), w))


def test_a_lost_send_is_redispatched_and_nothing_else_is(world, monkeypatch) -> None:
    from app.services import email_outbox

    # Other modules leave queued rows in the shared database, and the sweep
    # takes the OLDEST first; lifting the batch keeps this row in view without
    # changing what the sweep decides about any row.
    monkeypatch.setattr(email_outbox, "SWEEP_BATCH", 1_000_000)
    lost =_insert(world, status="queued", age=timedelta(minutes=20), claimed_age=None)
    fresh = _insert(world, status="queued", age=timedelta(minutes=2), claimed_age=None)
    ancient = _insert(world, status="queued", age=timedelta(days=3), claimed_age=None)
    stuck = _insert(
        world,
        status="processing",
        age=timedelta(hours=2),
        claimed_age=timedelta(minutes=45),
    )
    busy = _insert(
        world,
        status="processing",
        age=timedelta(hours=2),
        claimed_age=timedelta(minutes=1),
    )

    result = tasks.reconcile_queued_emails()

    sent = {item.args[0] for item in dispatch_mod.recorded() if item.name == SEND}
    assert lost in sent
    # Still inside its own dispatch's first attempt.
    assert fresh not in sent
    # Too late to be worth sending: reported, never sent.
    assert ancient not in sent
    # Claimed: may already have gone out. Reported, never resent.
    assert stuck not in sent and busy not in sent
    assert result["abandoned_queued"] >= 1
    assert result["stuck_processing"] >= 1


def test_a_redispatched_row_that_was_merely_slow_is_sent_once(world, monkeypatch) -> None:
    """The safety of re-dispatching rests on the claim: the sweep's invocation
    and the original one race, and exactly one sends."""
    row = _insert(world, status="queued", age=timedelta(minutes=20), claimed_age=None)
    calls: list[str] = []

    async def _transport(**kwargs):
        calls.append(kwargs["to"])
        return "provider-id-sweep"

    monkeypatch.setattr(tasks, "_deliver_email", _transport)

    async def _both() -> list[dict]:
        async def _one() -> dict:
            sessions = comms_world.factory()
            async with sessions() as session:
                await comms_world.bypass(session)
                return await tasks._send_lifecycle_email_async(session, row)

        return list(await asyncio.gather(_one(), _one()))

    results = asyncio.run(_both())
    assert len(calls) == 1, results
