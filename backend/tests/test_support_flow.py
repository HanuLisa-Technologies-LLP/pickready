"""The support surface end to end: the gate, the FSM, and the notification.

WHAT IS ASSERTED HERE THAT `test_support_rls.py` DOES NOT COVER
----------------------------------------------------------------
That file proves the DATABASE refuses a cross-tenant row. This one proves the
product behaves: the capability gate runs, the thread moves to the right state
for the side that wrote, the first staff replier claims it, a resolved thread
reopens when the customer writes again, and a notification is DISPATCHED rather
than sent inline.

THE NOTIFICATION ASSERTION IS ABOUT THE DISPATCH, NOT ABOUT AN EMAIL
----------------------------------------------------------------------
Deliberately, and the brief asked for it this way. Asserting an email arrived
would mean either standing up SMTP in the suite or mocking the sender and
asserting the mock was called, and a test whose only assertion is that a mock
was called is not a test. What matters at this boundary is that the work left
the request handler by the one route background work travels in this product,
under the right task name, with the identifiers needed to do it.

The `record` dispatch backend is what the suite runs on, and `conftest`'s
autouse fixture clears it between tests, so a count here is this test's own.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_superadmin_db,
    get_tenant_db,
)
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG, AUDIENCE_OWNER
from app.main import app
from app.models.enums import Role
from app.models.support import (
    THREAD_AWAITING_CUSTOMER,
    THREAD_OPEN,
    THREAD_RESOLVED,
)
from app.services import support as support_fsm
from app.services.capabilities import OPEN_SUPPORT_THREADS
from app.workers import dispatch as dispatch_module

SUPPORT = "/api/v1/support"
PROVIDER_SUPPORT = "/api/v1/provider/support"


def _run(coro):
    """One fresh loop per call.

    TestClient runs the application on its own loop, so a session or engine
    bound to a loop that has closed is the failure that surfaces later as an
    unrelated timeout in a different test.
    """
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
            await conn.execute(sa.text("SELECT 1 FROM support_threads LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- no database here
        return False
    finally:
        await engine.dispose()


class World:
    """Two customers and one platform user, created through the bypass scope."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        self.staff = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping support flow test")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for tid, name in (
                        (w.tenant_a, "Support-Flow-A"),
                        (w.tenant_b, "Support-Flow-B"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO tenants "
                                "(id, name, domain, spf_dkim_status) "
                                "VALUES (:id, :name, :domain, 'pending')"
                            ),
                            {
                                "id": str(tid),
                                "name": name,
                                "domain": f"{tid}.flow.test",
                            },
                        )
                    for uid, tid, role, email in (
                        (w.user_a, w.tenant_a, "client", "a@flow.test"),
                        (w.user_b, w.tenant_b, "client", "b@flow.test"),
                        (w.staff, None, "super_admin", "staff@flow.test"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO users "
                                "(id, tenant_id, email, full_name, role, status) "
                                "VALUES (:id, :tid, :email, :name, :role, 'active')"
                            ),
                            {
                                "id": str(uid),
                                "tid": str(tid) if tid else None,
                                "email": email,
                                "name": email.split("@")[0],
                                "role": role,
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM users WHERE id = :id"),
                        {"id": str(w.staff)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                        {"ids": [str(w.tenant_a), str(w.tenant_b)]},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


class Caller:
    def __init__(self) -> None:
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_customer(self, world: World, tenant: uuid.UUID) -> None:
        self.principal = CurrentUser(
            user_id=world.user_a if tenant == world.tenant_a else world.user_b,
            tenant_id=tenant,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    def as_staff(self, world: World) -> None:
        self.principal = CurrentUser(
            user_id=world.staff,
            tenant_id=None,
            role=Role.super_admin,
            audience=AUDIENCE_OWNER,
        )


@pytest.fixture
def client(world: World) -> Iterator[Caller]:
    sessions = _sessions()
    caller = Caller()

    async def _current_user() -> CurrentUser:
        assert caller.principal is not None
        return caller.principal

    async def _tenant_db():
        principal = caller.principal
        assert principal is not None
        async with sessions() as session:
            async with session.begin():
                # The scope is entered exactly as the production dependency
                # enters it, so a cross-tenant case is refused by Postgres and
                # not only by the application's own lookup.
                async with tenant_scope(session, principal.tenant_id):
                    yield session

    async def _superadmin_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    app.dependency_overrides[get_superadmin_db] = _superadmin_db
    try:
        with TestClient(app) as http:
            caller.http = http
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _open_thread(caller: Caller, subject: str = "Billing page will not load"):
    return caller.http.post(
        f"{SUPPORT}/threads",
        json={"subject": subject, "body": "It has been failing since this morning."},
    )


# ── The FSM, as a pure unit ──────────────────────────────────────────────────

def test_the_status_is_derived_from_who_wrote_and_takes_no_current_status() -> None:
    """The signature is the guarantee.

    `status_after_message` cannot consult the current status, so it has no arm
    that can be forgotten and no way for a handler to pass a status of its own
    choosing. If it could, "waiting on ReadyPick" would mean "threads somebody
    remembered to mark".
    """
    assert support_fsm.status_after_message("customer") == THREAD_OPEN
    assert support_fsm.status_after_message("staff") == THREAD_AWAITING_CUSTOMER
    with pytest.raises(support_fsm.IllegalThreadTransition):
        support_fsm.status_after_message("robot")


def test_resolved_is_not_terminal_in_either_direction() -> None:
    """Unlike `email_senders.revoked`, which is. A resolved thread that could
    not reopen would force a follow-up question into a new thread and lose the
    history that made it answerable."""
    assert support_fsm.assert_transition(THREAD_RESOLVED, THREAD_OPEN) == THREAD_OPEN
    with pytest.raises(support_fsm.IllegalThreadTransition):
        support_fsm.assert_transition(THREAD_OPEN, THREAD_OPEN)


# ── The customer side ────────────────────────────────────────────────────────

def test_opening_a_thread_writes_one_message_and_dispatches_one_notification(
    client: Caller, world: World
) -> None:
    client.as_customer(world, world.tenant_a)
    response = _open_thread(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == THREAD_OPEN
    assert body["message_count"] == 1
    assert body["messages"][0]["author_side"] == "customer"

    names = [record.name for record in dispatch_module.recorded()]
    assert "pickready.notify_support_message" in names, names
    # Never inline: a request handler must not wait on a mail transport.
    assert "pickready.send_email" not in names, names


def test_the_capability_is_the_gate_and_a_role_name_is_not(
    client: Caller, world: World
) -> None:
    """Revoking the capability for this tenant must close the route.

    Asserted by writing a DENY row rather than by changing the caller's role,
    because a role change would also pass if the handler branched on the role,
    which is the thing this has to prove it does not do.
    """
    from app.services import rbac

    sessions = _sessions()

    async def _deny() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO role_permissions "
                            "(id, tenant_id, role, capability, allowed) "
                            "VALUES (:id, :tid, 'client', :cap, false)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "tid": str(world.tenant_a),
                            "cap": OPEN_SUPPORT_THREADS,
                        },
                    )

    _run(_deny())
    _run(rbac.invalidate_role_permissions(world.tenant_a, [Role.client]))

    client.as_customer(world, world.tenant_a)
    assert _open_thread(client).status_code == 403


def test_a_blank_subject_is_refused_by_the_schema_not_by_the_database(
    client: Caller, world: World
) -> None:
    """Whitespace is not content, and the refusal must reach the caller as
    their own bad request rather than as a 500 out of a CHECK constraint."""
    client.as_customer(world, world.tenant_a)
    response = client.http.post(
        f"{SUPPORT}/threads", json={"subject": "   ", "body": "hello"}
    )
    assert response.status_code == 422, response.text


def test_another_tenants_thread_is_a_404_and_never_a_403(
    client: Caller, world: World
) -> None:
    """403 would confirm the id exists, which tells somebody probing that they
    have found a real thread belonging to a competitor."""
    client.as_customer(world, world.tenant_a)
    thread_id = _open_thread(client).json()["id"]

    client.as_customer(world, world.tenant_b)
    assert client.http.get(f"{SUPPORT}/threads/{thread_id}").status_code == 404
    assert (
        client.http.post(
            f"{SUPPORT}/threads/{thread_id}/messages", json={"body": "peeking"}
        ).status_code
        == 404
    )


def test_a_tenants_list_holds_only_its_own_threads(
    client: Caller, world: World
) -> None:
    client.as_customer(world, world.tenant_a)
    _open_thread(client, "A's problem")
    client.as_customer(world, world.tenant_b)
    _open_thread(client, "B's problem")

    page = client.http.get(f"{SUPPORT}/threads").json()
    assert [t["subject"] for t in page["items"]] == ["B's problem"], page


# ── The staff side ───────────────────────────────────────────────────────────

def test_a_staff_reply_moves_the_thread_and_claims_it(
    client: Caller, world: World
) -> None:
    client.as_customer(world, world.tenant_a)
    thread_id = _open_thread(client).json()["id"]

    client.as_staff(world)
    reply = client.http.post(
        f"{PROVIDER_SUPPORT}/threads/{thread_id}/messages",
        json={"body": "Looking at it now."},
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["author_side"] == "staff"

    detail = client.http.get(f"{PROVIDER_SUPPORT}/threads/{thread_id}").json()
    assert detail["status"] == THREAD_AWAITING_CUSTOMER
    # Claimed by replying: there is no separate claim flow to remember.
    assert detail["assigned_to"] == str(world.staff)
    assert detail["tenant_name"] == "Support-Flow-A"


def test_the_staff_queue_spans_customers_and_counts_what_is_owed(
    client: Caller, world: World
) -> None:
    client.as_customer(world, world.tenant_a)
    _open_thread(client, "A waiting")
    client.as_customer(world, world.tenant_b)
    _open_thread(client, "B waiting")

    client.as_staff(world)
    page = client.http.get(f"{PROVIDER_SUPPORT}/threads").json()
    assert {"A waiting", "B waiting"} <= {t["subject"] for t in page["items"]}

    # The filter narrows the page; `open_total` deliberately does not narrow,
    # because it answers "how much is owed" rather than "how much is on screen".
    filtered = client.http.get(
        f"{PROVIDER_SUPPORT}/threads", params={"tenant_id": str(world.tenant_a)}
    ).json()
    assert {t["subject"] for t in filtered["items"]} == {"A waiting"}
    assert filtered["open_total"] >= 2


def test_an_unrecognised_status_filter_is_refused_rather_than_ignored(
    client: Caller, world: World
) -> None:
    """Ignored, it would return everything and read as the filter having found
    nothing that matched, which is a wrong answer wearing a right one.

    `pending` specifically: it is the word the brief proposed for this state
    and the word a reader is most likely to try.
    """
    client.as_staff(world)
    response = client.http.get(
        f"{PROVIDER_SUPPORT}/threads", params={"status": "pending"}
    )
    # 422 specifically, not merely "not 200". An unrecognised query parameter
    # is the caller's mistake; a 500 would send whoever reads the error looking
    # for an outage, which is how a typo costs somebody an afternoon.
    assert response.status_code == 422, response.text
    assert "awaiting_customer" in response.json()["detail"]


def test_a_customer_reply_reopens_a_resolved_thread(
    client: Caller, world: World
) -> None:
    client.as_customer(world, world.tenant_a)
    thread_id = _open_thread(client).json()["id"]

    client.as_staff(world)
    patched = client.http.patch(
        f"{PROVIDER_SUPPORT}/threads/{thread_id}", json={"status": THREAD_RESOLVED}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == THREAD_RESOLVED

    client.as_customer(world, world.tenant_a)
    assert (
        client.http.post(
            f"{SUPPORT}/threads/{thread_id}/messages",
            json={"body": "It is happening again."},
        ).status_code
        == 201
    )
    detail = client.http.get(f"{SUPPORT}/threads/{thread_id}").json()
    assert detail["status"] == THREAD_OPEN


def test_an_illegal_manual_move_is_a_409_naming_both_ends(
    client: Caller, world: World
) -> None:
    client.as_customer(world, world.tenant_a)
    thread_id = _open_thread(client).json()["id"]

    client.as_staff(world)
    response = client.http.patch(
        f"{PROVIDER_SUPPORT}/threads/{thread_id}", json={"status": THREAD_OPEN}
    )
    assert response.status_code == 409, response.text
    assert THREAD_OPEN in response.json()["detail"]
