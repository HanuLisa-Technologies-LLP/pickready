"""A redelivered Razorpay webhook must grant one month, not two.

WHAT EXISTS ALREADY, AND THE ONE THING IT CANNOT SEE
------------------------------------------------------
`test_billing.py::test_grants_and_charges_are_idempotent_and_sum_to_the_balance`
calls `credits.grant` twice with the same key and proves the ledger dedupes.
`test_credit_packs.py::test_duplicate_settlement_grants_nothing_twice` does the
same for `settle_purchase`. Both are right and both are about the SERVICE.

Nothing has ever POSTed to `/billing/webhook/razorpay`. That is the gap,
because the idempotency key is not chosen by the service: it is DERIVED IN THE
ROUTE, from `payment_entity["id"]`, through `_payment_key`. A refactor that
read the wrong field, or fell through to the
`f"sub:{subscription_id}:{created_at}"` default on a payload that did carry a
payment id, would leave `credits.grant` perfectly idempotent over a key that
is different every delivery, and every service-level test would still pass
while the customer was granted a month per retry.

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
it, the ledger's UNIQUE `idempotency_key` dedupes on the PAYMENT. The first is
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

#: 60 sub-units per credit. A plan granting 50 credits a month is 3000.
from app.models.billing import SUBUNITS_PER_CREDIT

#: Seeded onto the plan row. DERIVED, not typed twice: `monthly_subunits` is
#: `applications_per_month * SUBUNITS_PER_CREDIT` on the model, so hardcoding
#: the product is how the expectation drifts away from the thing it checks.
PLAN_APPLICATIONS = 50
PLAN_SUBUNITS = PLAN_APPLICATIONS * SUBUNITS_PER_CREDIT
PLAN_PRICE_INR = 4999


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
        self.plan = uuid.uuid4()
        self.subscription_id = f"sub_{uuid.uuid4().hex[:14]}"


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
                            # `monthly_subunits` IS NOT A COLUMN. It is a
                            # derived property on the model,
                            # `applications_per_month * SUBUNITS_PER_CREDIT`.
                            # This INSERT named it anyway, so it raised
                            # UndefinedColumnError on every run, which the
                            # broken reachability probe above then hid as a
                            # skip. `rate_per_application_inr` is NOT NULL and
                            # was missing too.
                            "INSERT INTO pricing_plans "
                            "(id, slug, name, applications_per_month, "
                            " price_inr, rate_per_application_inr, is_active) "
                            "VALUES (:id, :slug, 'Webhook Test Plan', "
                            " :apps, :price, :rate, true)"
                        ),
                        {
                            "id": str(w.plan),
                            "slug": f"webhook-test-{w.plan.hex[:8]}",
                            "apps": PLAN_APPLICATIONS,
                            "price": PLAN_PRICE_INR,
                            "rate": PLAN_PRICE_INR // PLAN_APPLICATIONS,
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants "
                            "(id, name, domain, spf_dkim_status, "
                            " current_plan_id, razorpay_subscription_id) "
                            "VALUES (:id, :name, :domain, 'pending', :plan, "
                            " :sub_id)"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"Webhook-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.hook.test",
                            "plan": str(w.plan),
                            "sub_id": w.subscription_id,
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
                    await session.execute(
                        sa.text("DELETE FROM pricing_plans WHERE id = :id"),
                        {"id": str(w.plan)},
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


def _charged_payload(world: World, payment_id: str, created_at: int = 1_760_000_000):
    return {
        "event": "subscription.charged",
        "created_at": created_at,
        "payload": {
            "subscription": {
                "entity": {
                    "id": world.subscription_id,
                    "current_end": created_at + 2_592_000,
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": PLAN_PRICE_INR * 100,
                    "subscription_id": world.subscription_id,
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


def test_one_delivery_grants_exactly_one_month(http: TestClient, world: World) -> None:
    """The control. Without it every "did not grant twice" assertion below is
    also satisfied by a handler that never granted at all."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    accepted = _deliver(
        http,
        _charged_payload(world, payment_id),
        event_id=f"evt_{world.tenant.hex[:8]}_1",
    )
    assert accepted.status_code == 200, accepted.text

    rows = _run(_ledger(world.tenant))
    assert len(rows) == 1, rows
    event_type, delta, key = rows[0]
    assert event_type == "grant"
    assert delta == PLAN_SUBUNITS
    # The key is derived from the PAYMENT, which is the fact that is stable
    # across redeliveries. A key carrying the delivery id or a timestamp would
    # be unique per attempt and would dedupe nothing.
    assert key == f"razorpay:payment:{payment_id}"
    assert _run(_balance(world.tenant)) == PLAN_SUBUNITS


# ── The redelivery, both ways it arrives ────────────────────────────────────


def test_the_same_delivery_twice_grants_once(http: TestClient, world: World) -> None:
    """Razorpay's own retry: identical bytes, identical delivery id.

    Caught by the OUTER guard, the unique constraint on
    `webhook_events(provider, event_id)`, which is why the second answer says
    "duplicate" rather than "ok".
    """
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    payload = _charged_payload(world, payment_id)
    event_id = f"evt_{world.tenant.hex[:8]}_2"

    first = _deliver(http, payload, event_id=event_id)
    second = _deliver(http, payload, event_id=event_id)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["status"] == "duplicate", second.text
    assert len(_run(_ledger(world.tenant))) == 1
    assert _run(_balance(world.tenant)) == PLAN_SUBUNITS


def test_a_redelivery_with_a_new_delivery_id_still_grants_once(
    http: TestClient, world: World
) -> None:
    """THE TEST THAT MATTERS, AND THE ONE THE OUTER GUARD CANNOT PASS.

    The `webhook_events` row keys on the delivery id header. A replay from a
    proxy that regenerated it, a manual resend from the Razorpay dashboard, or
    a header this deployment simply never receives all defeat it, and the
    handler's own fallback -- `f"{event_type}:{created_at}"` -- is not unique
    either, because two genuine events can share a second.

    What must hold underneath is that the same PAYMENT ID grants once,
    forever, and that is the ledger's job. Asserted by delivering the same
    payment under three different delivery ids and reading the balance.
    """
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    payload = _charged_payload(world, payment_id)

    for attempt in range(3):
        response = _deliver(
            http, payload, event_id=f"evt_{world.tenant.hex[:8]}_3_{attempt}"
        )
        assert response.status_code == 200, response.text

    rows = _run(_ledger(world.tenant))
    assert len(rows) == 1, (
        f"{len(rows)} ledger rows for one payment: {rows}"
    )
    assert _run(_balance(world.tenant)) == PLAN_SUBUNITS, (
        "a redelivery with a fresh delivery id granted a second month"
    )


def test_a_genuinely_different_payment_does_grant_again(
    http: TestClient, world: World
) -> None:
    """THE DIRECTION THAT LOSES REVENUE.

    Next month's charge is a different payment id and must be granted. A
    dedupe keyed on the subscription, the tenant or the plan would refuse it
    and the customer would silently stop being topped up, which nothing in the
    product would report because a refused grant looks exactly like a webhook
    that never arrived.
    """
    for index in range(2):
        response = _deliver(
            http,
            _charged_payload(
                world,
                f"pay_{uuid.uuid4().hex[:14]}",
                created_at=1_760_000_000 + index * 2_592_000,
            ),
            event_id=f"evt_{world.tenant.hex[:8]}_4_{index}",
        )
        assert response.status_code == 200, response.text

    assert len(_run(_ledger(world.tenant))) == 2
    assert _run(_balance(world.tenant)) == PLAN_SUBUNITS * 2


# ── The forgery ─────────────────────────────────────────────────────────────


def test_an_unsigned_delivery_grants_nothing(http: TestClient, world: World) -> None:
    """The webhook grants money and takes no session. Its whole authorization
    is the HMAC, so an unsigned POST must write neither a ledger row nor a
    `webhook_events` row -- the second half matters because a recorded event
    id would let a forgery suppress the genuine delivery that follows it."""
    payload = _charged_payload(world, f"pay_{uuid.uuid4().hex[:14]}")
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
    """Signed correctly, then the body swapped for one naming a bigger plan.

    The route hashes `await request.body()` rather than a re-serialization of
    the parsed JSON, and this is what proves it: a handler that re-serialized
    would compute the digest over the bytes it is holding and accept anything.
    """
    honest = _charged_payload(world, f"pay_{uuid.uuid4().hex[:14]}")
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


def test_a_webhook_for_an_unknown_subscription_grants_nothing(
    http: TestClient, world: World
) -> None:
    """Correctly signed, but naming a subscription no tenant holds.

    A signed payload is authentic, not authorized: the signature proves
    Razorpay sent it and says nothing about whose account it belongs to. This
    is the shape a credit-granting confused-deputy bug takes, and the answer
    has to be `unmatched` with no row rather than a grant to a guessed tenant.
    """
    payload = _charged_payload(world, f"pay_{uuid.uuid4().hex[:14]}")
    payload["payload"]["subscription"]["entity"]["id"] = "sub_does_not_exist"
    payload["payload"]["payment"]["entity"]["subscription_id"] = "sub_does_not_exist"

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
    payload = _charged_payload(unconfigured, f"pay_{uuid.uuid4().hex[:14]}")
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
    payload = _charged_payload(unconfigured, f"pay_{uuid.uuid4().hex[:14]}")
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
