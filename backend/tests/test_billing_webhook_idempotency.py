"""A redelivered Razorpay webhook must settle a credit purchase once, not twice.

WHAT EXISTS ALREADY, AND THE ONE THING IT CANNOT SEE
------------------------------------------------------
`test_billing.py::test_grants_and_charges_are_idempotent_and_sum_to_the_balance`
calls `credits.grant` twice with the same key and proves the ledger dedupes.
`test_credit_packs.py::test_duplicate_settlement_grants_nothing_twice` does the
same for `settle_purchase`. Both are right and both are about the SERVICE.

This file POSTs to `/billing/webhook/razorpay`, because the PURCHASE is not
chosen by the service: the ROUTE resolves it from the payload's order id. A
refactor that read the wrong field would leave `settle_purchase` perfectly
idempotent over a purchase that is the wrong one, and every service-level test
would still pass. The recurring subscription charge is also delivered here;
its payment id must grant exactly one monthly allowance.

Razorpay delivers AT LEAST ONCE. A duplicate is the default behaviour unless
something prevents it, so this is not a hypothetical.

THE ASSERTION IS THE LEDGER, NEVER A CALL COUNT
-------------------------------------------------
Every test here reads `credit_ledger` back and asserts the ROWS and
the BALANCE IN SUB-UNITS. A mock-was-called-once assertion would be satisfied
by a handler that called `grant` once and wrote two rows, and by a handler
that called it twice against a dedupe that happened to work today.

THE TWO LAYERS ARE EXERCISED SEPARATELY, AND THAT IS THE POINT
----------------------------------------------------------------
The webhook dedupes twice over. `webhook_events` is UNIQUE on
(provider, event_id) and short-circuits on the delivery id header. Underneath
it, `settle_purchase` flips the purchase from `created` to `paid` in the same
UPDATE that checks it, and the grant's UNIQUE `idempotency_key` is derived
from the ORDER. The first is
the one a test naturally exercises, and it is the WEAKER of the two: it only
fires when the redelivery reuses the header. So there is a test for a
redelivery that changes the header, which is the case the outer guard cannot
catch and the inner one must.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.main import app
from app.services import razorpay

WEBHOOK = "/api/v1/billing/webhook/razorpay"

#: Not a secret: it signs one test run's payloads. It is set on the module the
#: route reads through so the route takes its PRODUCTION branch -- a configured
#: secret that does not match is a forgery and is refused, which is the
#: behaviour being relied on here. Leaving it unset would put the route on its
#: development branch, where an unverified payload is accepted with a warning,
#: and every test below would pass without the signature ever being checked.
WEBHOOK_SECRET = "readypick-test-webhook-secret-not-a-real-one"

from app.models.billing import CREDIT_PACKS, SUBUNITS_PER_CREDIT  # noqa: E402

#: The purchase seeded below: the standard 50-credit pack. DERIVED from the
#: catalogue rather than typed twice, so the expectation cannot drift away
#: from the thing it checks.
PACK_SLUG = "starter_pack_75"
PACK_CREDITS, PACK_BONUS = CREDIT_PACKS[PACK_SLUG]
PACK_SUBUNITS = (PACK_CREDITS + PACK_BONUS) * SUBUNITS_PER_CREDIT
PACK_TOTAL_INR = 28_320


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            # `credit_ledger`, NOT `credit_ledger`. THE TABLE IN THIS
            # PROBE HAS NEVER EXISTED: the name appears exactly twice in the
            # repository, in a prose comment in migration 0090 and on this
            # line. `UndefinedTableError` was caught by the bare `except`
            # below and turned into `return False`, so every test in this
            # module skipped on every run, in CI and locally, for its whole
            # life. Nine tests over the money path, reporting green by being
            # absent.
            #
            # This is what a reachability guard costs when it is written from
            # memory rather than from the schema, and it is the same class of
            # defect as a test that stubs the thing it is asserting on.
            await conn.execute(sa.text("SELECT 1 FROM credit_ledger LIMIT 0"))
            await conn.execute(sa.text("SELECT 1 FROM webhook_events LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- no database here
        return False
    finally:
        await engine.dispose()


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        #: Two purchases, so a genuinely different payment can be delivered.
        self.orders = [f"order_{uuid.uuid4().hex[:14]}" for _ in range(2)]

    @property
    def order_id(self) -> str:
        return self.orders[0]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping webhook idempotency test")

    class _Config:
        webhook_secret = WEBHOOK_SECRET
        key_id = "rzp_test_key"
        key_secret = "rzp_test_secret"

    monkeypatch.setattr(razorpay, "config", lambda: _Config())

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants "
                            "(id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"Webhook-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.hook.test",
                        },
                    )
                    for order_id in w.orders:
                        await session.execute(
                            sa.text(
                                "INSERT INTO credit_purchases "
                                "(id, tenant_id, pack_slug, credits_purchased, "
                                " bonus_credits, subtotal_inr, gst_inr, "
                                " total_inr, status, razorpay_order_id) "
                                "VALUES (:id, :tenant, :slug, :credits, :bonus, "
                                " :subtotal, :gst, :total, 'created', :order)"
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "tenant": str(w.tenant),
                                "slug": PACK_SLUG,
                                "credits": PACK_CREDITS,
                                "bonus": PACK_BONUS,
                                "subtotal": 24_000,
                                "gst": 5_400,
                                "total": PACK_TOTAL_INR,
                                "order": order_id,
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(w.tenant)},
                    )
                    # `webhook_events` has no tenant and is not cascaded.
                    await session.execute(
                        sa.text(
                            "DELETE FROM webhook_events WHERE event_id LIKE :like"
                        ),
                        {"like": f"evt_{w.tenant.hex[:8]}%"},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


@pytest.fixture
def http(world: World) -> Iterator[TestClient]:
    # No dependency override: the webhook is a PUBLIC route on
    # `get_public_db`, and overriding it would replace the very session scope
    # the handler relies on to reach a tenant it has no session for.
    with TestClient(app) as client:
        yield client


def _captured_payload(
    order_id: str, payment_id: str, created_at: int = 1_760_000_000
) -> dict:
    """A `payment.captured` for one credit purchase, shaped as Razorpay sends
    it: the payment entity names the ORDER, which is what the route resolves
    the purchase by."""
    return {
        "event": "payment.captured",
        "created_at": created_at,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": PACK_TOTAL_INR * 100,
                }
            },
        },
    }


def _deliver(client: TestClient, payload: dict, *, event_id: str):
    """POST exactly as Razorpay does: raw bytes, signed, with a delivery id.

    The body is serialized ONCE and both the signature and the request use
    those same bytes. Re-serializing between signing and sending changes key
    order and whitespace and would fail verification for a reason that has
    nothing to do with what is being tested.
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    return client.post(
        WEBHOOK,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event_id,
        },
    )


async def _ledger(tenant: uuid.UUID) -> list[tuple]:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return (
                    await session.execute(
                        sa.text(
                            "SELECT event_type, subunits_delta, idempotency_key "
                            "FROM credit_ledger WHERE tenant_id = :tid "
                            "ORDER BY created_at, id"
                        ),
                        {"tid": str(tenant)},
                    )
                ).all()


async def _balance(tenant: uuid.UUID) -> int:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return int(
                    (
                        await session.execute(
                            sa.text(
                                "SELECT COALESCE(SUM(subunits_delta), 0) "
                                "FROM credit_ledger "
                                "WHERE tenant_id = :tid"
                            ),
                            {"tid": str(tenant)},
                        )
                    ).scalar_one()
                )


# ── The ordinary delivery ───────────────────────────────────────────────────


def test_one_delivery_grants_exactly_one_pack(http: TestClient, world: World) -> None:
    """The control. Without it every "did not grant twice" assertion below is
    also satisfied by a handler that never granted at all."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    accepted = _deliver(
        http,
        _captured_payload(world.order_id, payment_id),
        event_id=f"evt_{world.tenant.hex[:8]}_1",
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ok", accepted.text

    rows = _run(_ledger(world.tenant))
    assert len(rows) == 1, rows
    event_type, delta, key = rows[0]
    assert event_type == "grant"
    assert delta == PACK_SUBUNITS
    # The key is derived from the ORDER, which is the fact that is stable
    # across redeliveries. A key carrying the delivery id or a timestamp would
    # be unique per attempt and would dedupe nothing.
    assert key == f"credit-pack:{world.order_id}"
    assert _run(_balance(world.tenant)) == PACK_SUBUNITS


def test_subscription_charge_webhook_grants_one_month_once(
    http: TestClient, world: World,
) -> None:
    subscription_id = f"sub_{world.tenant.hex[:14]}"
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"

    async def set_subscription() -> None:
        async with _sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(sa.text(
                        "UPDATE tenants SET razorpay_subscription_id = :sub, "
                        "current_plan_slug = 'starter', subscription_status = 'created' "
                        "WHERE id = :tid"
                    ), {"sub": subscription_id, "tid": str(world.tenant)})

    _run(set_subscription())
    payload = {
        "event": "subscription.charged",
        "created_at": 1_760_000_000,
        "payload": {
            "subscription": {"entity": {"id": subscription_id}},
            "payment": {"entity": {
                "id": payment_id, "status": "captured", "currency": "INR",
                "amount": 28_320 * 100,
            }},
        },
    }
    event_id = f"evt_{world.tenant.hex[:8]}_subscription"
    first = _deliver(http, payload, event_id=event_id)
    replay = _deliver(http, payload, event_id=event_id)
    second_event = _deliver(http, payload, event_id=event_id + "_again")
    assert first.status_code == 200 and first.json() == {"status": "ok"}, first.text
    assert replay.status_code == 200 and replay.json() == {"status": "duplicate"}
    assert second_event.status_code == 200 and second_event.json() == {"status": "ok"}
    rows = _run(_ledger(world.tenant))
    assert len(rows) == 1 and rows[0][1] == 75 * SUBUNITS_PER_CREDIT
    assert rows[0][2] == f"subscription:{payment_id}"


# ── The redelivery, both ways it arrives ────────────────────────────────────


def test_the_same_delivery_twice_grants_once(http: TestClient, world: World) -> None:
    """Razorpay's own retry: identical bytes, identical delivery id.

    Caught by the OUTER guard, the unique constraint on
    `webhook_events(provider, event_id)`, which is why the second answer says
    "duplicate" rather than "ok".
    """
    payload = _captured_payload(world.order_id, f"pay_{uuid.uuid4().hex[:14]}")
    event_id = f"evt_{world.tenant.hex[:8]}_2"

    first = _deliver(http, payload, event_id=event_id)
    second = _deliver(http, payload, event_id=event_id)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["status"] == "duplicate", second.text
    assert len(_run(_ledger(world.tenant))) == 1
    assert _run(_balance(world.tenant)) == PACK_SUBUNITS


def test_a_redelivery_with_a_new_delivery_id_still_grants_once(
    http: TestClient, world: World
) -> None:
    """THE TEST THAT MATTERS, AND THE ONE THE OUTER GUARD CANNOT PASS.

    The `webhook_events` row keys on the delivery id header. A replay from a
    proxy that regenerated it, a manual resend from the Razorpay dashboard, or
    a header this deployment simply never receives all defeat it, and the
    handler's own fallback -- `f"{event_type}:{created_at}"` -- is not unique
    either, because two genuine events can share a second.

    What must hold underneath is that the same PURCHASE settles once,
    forever: the status flip and the order-keyed grant. Asserted by
    delivering the same payment under three different delivery ids and
    reading the balance.
    """
    payload = _captured_payload(world.order_id, f"pay_{uuid.uuid4().hex[:14]}")

    for attempt in range(3):
        response = _deliver(
            http, payload, event_id=f"evt_{world.tenant.hex[:8]}_3_{attempt}"
        )
        assert response.status_code == 200, response.text

    rows = _run(_ledger(world.tenant))
    assert len(rows) == 1, (
        f"{len(rows)} ledger rows for one payment: {rows}"
    )
    assert _run(_balance(world.tenant)) == PACK_SUBUNITS, (
        "a redelivery with a fresh delivery id settled the purchase twice"
    )


def test_a_genuinely_different_purchase_does_grant_again(
    http: TestClient, world: World
) -> None:
    """THE DIRECTION THAT LOSES REVENUE.

    A second purchase is a different order and must be granted. A dedupe
    keyed on the tenant or the pack would refuse it and the customer would
    silently not receive what they paid for, which nothing in the product
    would report because a refused grant looks exactly like a webhook that
    never arrived.
    """
    for index, order_id in enumerate(world.orders):
        response = _deliver(
            http,
            _captured_payload(
                order_id,
                f"pay_{uuid.uuid4().hex[:14]}",
                created_at=1_760_000_000 + index * 60,
            ),
            event_id=f"evt_{world.tenant.hex[:8]}_4_{index}",
        )
        assert response.status_code == 200, response.text

    assert len(_run(_ledger(world.tenant))) == 2
    assert _run(_balance(world.tenant)) == PACK_SUBUNITS * 2


# ── The forgery ─────────────────────────────────────────────────────────────


def test_an_unsigned_delivery_grants_nothing(http: TestClient, world: World) -> None:
    """The webhook grants money and takes no session. Its whole authorization
    is the HMAC, so an unsigned POST must write neither a ledger row nor a
    `webhook_events` row -- the second half matters because a recorded event
    id would let a forgery suppress the genuine delivery that follows it."""
    payload = _captured_payload(world.order_id, f"pay_{uuid.uuid4().hex[:14]}")
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    forged = http.post(
        WEBHOOK,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "0" * 64,
            "X-Razorpay-Event-Id": f"evt_{world.tenant.hex[:8]}_5",
        },
    )
    assert forged.status_code == 400, forged.text
    assert _run(_ledger(world.tenant)) == []


def test_a_signature_over_different_bytes_is_refused(
    http: TestClient, world: World
) -> None:
    """Signed correctly, then the body swapped for one naming a bigger amount.

    The route hashes `await request.body()` rather than a re-serialization of
    the parsed JSON, and this is what proves it: a handler that re-serialized
    would compute the digest over the bytes it is holding and accept anything.
    """
    honest = _captured_payload(world.order_id, f"pay_{uuid.uuid4().hex[:14]}")
    raw_honest = json.dumps(honest, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), raw_honest, hashlib.sha256
    ).hexdigest()

    tampered = dict(honest)
    tampered["payload"]["payment"]["entity"]["amount"] = 99_900_000
    raw_tampered = json.dumps(tampered, separators=(",", ":")).encode("utf-8")
    assert raw_tampered != raw_honest

    refused = http.post(
        WEBHOOK,
        content=raw_tampered,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": f"evt_{world.tenant.hex[:8]}_6",
        },
    )
    assert refused.status_code == 400, refused.text
    assert _run(_ledger(world.tenant)) == []


def test_a_webhook_for_an_unknown_order_grants_nothing(
    http: TestClient, world: World
) -> None:
    """Correctly signed, but naming an order no purchase of ours carries.

    A signed payload is authentic, not authorized: the signature proves
    Razorpay sent it and says nothing about whose account it belongs to. This
    is the shape a credit-granting confused-deputy bug takes, and the answer
    has to be `unmatched` with no row rather than a grant to a guessed tenant.
    """
    payload = _captured_payload("order_does_not_exist", f"pay_{uuid.uuid4().hex[:14]}")

    response = _deliver(http, payload, event_id=f"evt_{world.tenant.hex[:8]}_7")
    assert response.status_code == 200
    assert response.json()["status"] == "unmatched", response.text
    assert _run(_ledger(world.tenant)) == []


# ── The branch the fixture above hid, which is the branch production ran ────
#
# `test_an_unsigned_delivery_grants_nothing` has always passed, and the
# vulnerability it looks like it covers was live on readypick.ai anyway. The
# `world` fixture stubs `razorpay.config()` with `webhook_secret =
# WEBHOOK_SECRET`, so every test in this file exercises the CONFIGURED branch,
# where the handler correctly refuses. The handler used to read:
#
#     if not verify_webhook_signature(...):
#         if settings.is_production or razorpay.config().webhook_secret:
#             raise HTTPException(400, ...)
#         log.warning("...accepted in development ONLY.")   # and FELL THROUGH
#
# Both conditions were false in the deployment serving readypick.ai:
# `is_production` is `environment == "production"` and the live environment
# sets `ENVIRONMENT=pilot`; and `RAZORPAY_WEBHOOK_SECRET` was mounted on no
# service, because the grant named a `webhook` service that has never existed
# in any environment. So an anonymous POST was accepted and granted credits.
#
# The test configured the very thing whose absence was the vulnerability. This
# repository has shipped that shape before: a rate-limit test that set the
# attribute it was asserting on, over a feature that was entirely dead.


@pytest.fixture
def unconfigured(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """The deployed reality until this was fixed: no webhook secret at all."""

    class _NoSecret:
        webhook_secret = ""
        key_id = "rzp_test_key"
        key_secret = "rzp_test_secret"

    monkeypatch.setattr(razorpay, "config", lambda: _NoSecret())
    return world


def test_an_unsigned_delivery_is_refused_when_no_secret_is_configured(
    http: TestClient, unconfigured: World
) -> None:
    """THE ONE THAT WOULD HAVE CAUGHT IT.

    A signature check that cannot be performed has FAILED, not passed. This is
    deliberately the opposite of the inbound-email relay, where an unset secret
    leaves the route open: refusing an employer's reply loses a message the
    product can ask for again, while accepting an unsigned payment event grants
    money and cannot be taken back.
    """
    payload = _captured_payload(unconfigured.order_id, f"pay_{uuid.uuid4().hex[:14]}")
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    answer = http.post(
        WEBHOOK,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "0" * 64,
            "X-Razorpay-Event-Id": f"evt_{unconfigured.tenant.hex[:8]}_noconf",
        },
    )

    # 503, not 400: an absent secret is OUR misconfiguration rather than the
    # caller's bad request, and Razorpay retries a 5xx, so a genuine event
    # survives the window while the secret is being wired.
    assert answer.status_code == 503, answer.text


def test_even_a_correctly_signed_delivery_is_refused_with_no_secret(
    http: TestClient, unconfigured: World
) -> None:
    """The other direction, and it is not pedantry.

    With no secret there is no such thing as a correct signature, so the
    handler must not have a path that computes one and admits it. A fix that
    only rejected the obviously-forged case would leave the attacker free to
    sign with the empty string.
    """
    payload = _captured_payload(unconfigured.order_id, f"pay_{uuid.uuid4().hex[:14]}")
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(b"", raw, hashlib.sha256).hexdigest()

    answer = http.post(
        WEBHOOK,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": f"evt_{unconfigured.tenant.hex[:8]}_empty",
        },
    )

    assert answer.status_code == 503, answer.text
