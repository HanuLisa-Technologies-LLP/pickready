"""A failure to RECORD a Razorpay event is never answered as a duplicate.

THE DEFECT (PLAN-p7 WP-B6, audit section 3.9)
----------------------------------------------
The webhook dedupes by inserting the delivery into `webhook_events`, UNIQUE on
(provider, event_id). It caught the failure of that INSERT with
`except Exception`, rolled back and answered 200 `{"status": "duplicate"}`.
A unique violation is a duplicate. Nothing else is: a DataError, a lost
connection or a constraint that is not the dedupe index told Razorpay the
event had been handled, Razorpay never retried it, and a paid
`subscription.charged` granted nothing. The customer was charged and the
record of the charge was a 200 in somebody's retry dashboard.

The handler now states the no-op in SQL (`ON CONFLICT ON CONSTRAINT
uq_webhook_events_provider_id DO NOTHING`), so the database absorbs the
duplicate and nothing else, and every other failure answers 5xx, which is the
retry that gets the charge granted.

The failure is provoked for real rather than by patching the session: an
event type longer than `webhook_events.event_type` (80 characters) makes
Postgres itself refuse the INSERT, which is exactly the class of error the
old `except` swallowed. Fixtures and the signing helper are the ones
`test_billing_webhook_idempotency.py` defines, imported rather than copied, so
the two files cannot disagree about how Razorpay delivers.

A SECOND PROPERTY, SAME ROUTE: the two tasks a webhook starts
(`release_held_assessments` after a grant, `send_payment_failed_email` after
a failed charge) are dispatched AFTER THE COMMIT, so a delivery that fails
after them sends nothing about a state that was never stored.
"""
from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.main import app
from app.workers import dispatch as dispatch_module
from tests.test_billing_webhook_idempotency import (  # noqa: F401 -- fixtures
    World,
    _charged_payload,
    _deliver,
    _ledger,
    _run,
    _sessions,
    world,
)

#: One character wider than `webhook_events.event_type` (String(80)).
OVERSIZED_EVENT = "subscription.charged." + "x" * 60


@pytest.fixture
def http_5xx(world: World) -> Iterator[TestClient]:  # noqa: F811 -- fixture use
    """A client that reports a server error as a status, the way Razorpay
    sees it, rather than re-raising it into the test."""
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


async def _recorded_events(tenant: uuid.UUID) -> int:
    async with _sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM webhook_events "
                            "WHERE event_id LIKE :like"
                        ),
                        {"like": f"evt_{tenant.hex[:8]}%"},
                    )
                ).scalar_one()


def test_a_failed_insert_is_a_server_error_not_a_duplicate(
    http_5xx: TestClient, world: World  # noqa: F811
) -> None:
    assert len(OVERSIZED_EVENT) > 80
    payload = _charged_payload(world, f"pay_{uuid.uuid4().hex[:14]}")
    payload["event"] = OVERSIZED_EVENT
    response = _deliver(
        http_5xx, payload, event_id=f"evt_{world.tenant.hex[:8]}_oversized"
    )

    assert response.status_code >= 500, (
        f"a failure to record the event answered {response.status_code} "
        f"{response.text[:120]}, so Razorpay would never retry it"
    )
    assert "duplicate" not in response.text
    # Nothing was recorded, so a retry is not refused as a redelivery.
    assert _run(_recorded_events(world.tenant)) == 0


def test_a_redelivery_is_still_a_duplicate(
    http_5xx: TestClient, world: World  # noqa: F811
) -> None:
    """The control. Narrowing the handler must not turn a genuine redelivery
    into a second grant or an error."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{world.tenant.hex[:8]}_again"
    first = _deliver(http_5xx, _charged_payload(world, payment_id), event_id=event_id)
    second = _deliver(http_5xx, _charged_payload(world, payment_id), event_id=event_id)

    assert first.status_code == 200 and first.json() == {"status": "ok"}
    assert second.status_code == 200 and second.json() == {"status": "duplicate"}
    assert len(_run(_ledger(world.tenant))) == 1
    assert _run(_recorded_events(world.tenant)) == 1


def test_a_grant_releases_held_assessments_after_the_commit(
    http_5xx: TestClient, world: World  # noqa: F811
) -> None:
    dispatch_module.clear_recorded()
    try:
        response = _deliver(
            http_5xx,
            _charged_payload(world, f"pay_{uuid.uuid4().hex[:14]}"),
            event_id=f"evt_{world.tenant.hex[:8]}_release",
        )
        assert response.status_code == 200, response.text
        released = [
            call
            for call in dispatch_module.recorded()
            if call.name == "pickready.release_held_assessments"
        ]
        assert [call.args for call in released] == [(str(world.tenant),)]
    finally:
        dispatch_module.clear_recorded()


def test_a_failed_charge_emails_once_after_the_commit(
    http_5xx: TestClient, world: World  # noqa: F811
) -> None:
    payload = {
        "event": "payment.failed",
        "created_at": 1_760_000_100,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{uuid.uuid4().hex[:14]}",
                    "amount": 499_900,
                    "subscription_id": world.subscription_id,
                    "error_description": "Card declined",
                }
            },
        },
    }
    dispatch_module.clear_recorded()
    try:
        response = _deliver(
            http_5xx, payload, event_id=f"evt_{world.tenant.hex[:8]}_failed"
        )
        assert response.status_code == 200, response.text
        emails = [
            call
            for call in dispatch_module.recorded()
            if call.name == "pickready.send_payment_failed_email"
        ]
        assert [call.args for call in emails] == [(str(world.tenant),)]
    finally:
        dispatch_module.clear_recorded()
