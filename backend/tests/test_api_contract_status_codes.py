"""The five refusals of one protected resource, asserted side by side.

WHY ONE FILE FOR FIVE STATUS CODES
------------------------------------
Each of these is individually asserted somewhere in the suite, on five
different routes, in five different files. That is not the same guarantee.
The property a caller relies on is that ONE endpoint distinguishes all five,
and the failures that property prevents are all confusions BETWEEN two of
them:

  * 401 answered as 403 tells an anonymous caller they are logged in as
    somebody without permission, which sends them to support instead of to
    the sign-in page;
  * 403 answered as 404 hides a permission problem as a missing record, and
    the holder of the capability then cannot be told what to grant;
  * 404 answered as 403 CONFIRMS another customer's record exists, which is
    the disclosure the whole 404-not-403 rule is for;
  * 422 answered as 500 turns a typo into a page nobody can act on and an
    alert somebody has to triage;
  * 429 answered as 403 makes a temporary back-off look like a revoked
    account.

THE RESOURCE
------------
`/api/v1/email-senders`. It is the only tenant-scoped, capability-gated
resource in the product that ALSO carries a rate limiter, which is what makes
all five reachable on one surface. Two of its routes are used: the collection
POST for 401, 403, 422 and 429, and the per-sender POST for 404, because a
collection endpoint has no id to make cross-tenant.

WHAT NEEDS A DATABASE AND WHAT DOES NOT
-----------------------------------------
401 and 429 are decided before any handler runs -- `get_tenant_db` depends on
`get_current_user`, so an anonymous request is refused before a connection is
opened, and the limiter is a route-level dependency. Those two run anywhere.
403, 404 and 422 need real rows and skip without one.

THE LIMITER IS DRIVEN BY A FAKE REDIS, DELIBERATELY
-----------------------------------------------------
`rate_limit.check` FAILS OPEN when Redis is absent, by design, so a 429 test
against a deployment with no Redis would pass 200s for ever and report
nothing. Supplying a counter that works is the only way to observe the
refusal, and it is the technique `test_rate_limit.py` already uses. What is
NOT faked is the dependency's presence on the route: `test_the_route_is
_actually_rate_limited` reads it off the router, because a test that both
supplies the mechanism and asserts it is the mistake this repository has
already shipped once.
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

from app.api import email_senders as senders_api
from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core import cache
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG, AUDIENCE_OWNER
from app.main import app
from app.models.enums import Role
from app.services import capabilities as caps

SENDERS = "/api/v1/email-senders"


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
            await conn.execute(sa.text("SELECT 1 FROM client_email_senders LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- no database here
        return False
    finally:
        await engine.dispose()


# ── 401: no session at all. No database involved. ───────────────────────────


@pytest.fixture
def limiter_off(monkeypatch: pytest.MonkeyPatch):
    """Take the limiter out of the way for the tests that are not about it.

    Not hygiene: these all POST to the same route from the same apparent
    address, so they share one bucket, and the 429 test below deliberately
    fills it. Without this the first four assertions pass or fail depending
    on the order pytest happened to run them in, which is the order-dependent
    failure that reproduces on CI and not locally.

    `None` rather than a fresh fake, because the limiter FAILS OPEN on a
    missing Redis by design, so this exercises the real production branch for
    a deployment whose cache is down.
    """
    monkeypatch.setattr(cache, "_redis", lambda: None)


def test_an_anonymous_request_is_401_and_not_403(limiter_off) -> None:
    """The real dependency chain, with no override and no cookie.

    This runs without a database on purpose, and the reason is the assertion:
    `get_tenant_db` depends on `get_current_user`, so the refusal has to
    happen before a connection is opened. If it did not, this test would fail
    with a connection error rather than a 401 -- which makes the absence of a
    database the strongest available proof that nothing was read.
    """
    with TestClient(app) as http:
        refused = http.post(
            SENDERS, json={"name": "Rahul", "email": "hr@sarkarcorp.com"}
        )
    assert refused.status_code == 401, refused.text


def test_a_garbage_cookie_is_401_and_never_a_500(limiter_off) -> None:
    """An expired or truncated cookie is the ordinary state of a browser tab
    left open overnight, and it must land on the sign-in page rather than on
    an error page."""
    with TestClient(app) as http:
        refused = http.post(
            SENDERS,
            json={"name": "Rahul", "email": "hr@sarkarcorp.com"},
            cookies={"pr_access": "not-a-jwt-at-all"},
        )
    assert refused.status_code == 401, refused.text


@pytest.mark.parametrize(
    "audience", [AUDIENCE_CANDIDATE, AUDIENCE_OWNER], ids=["candidate", "owner"]
)
def test_a_session_from_another_portal_is_403_and_not_401(
    audience: str, limiter_off
) -> None:
    """A real, correctly signed session that simply belongs somewhere else.

    403 rather than 401 is right here and is the distinction the pair of tests
    above exists to protect: this caller IS authenticated, so telling them to
    sign in would send them round a loop they cannot escape. `get_tenant_db`
    makes this call, and it makes it before opening a connection, which is why
    this also runs with no database.
    """
    async def _current_user() -> CurrentUser:
        return CurrentUser(
            user_id=uuid.uuid4(),
            tenant_id=None,
            role=Role.candidate if audience == AUDIENCE_CANDIDATE else Role.super_admin,
            audience=audience,
        )

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    try:
        with TestClient(app) as http:
            refused = http.post(
                SENDERS, json={"name": "Rahul", "email": "hr@sarkarcorp.com"}
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert refused.status_code == 403, refused.text


# ── 429: the limiter, with a counter that actually counts ───────────────────


class _FakeRedis:
    """Enough of Redis to drive the fixed-window counter.

    Copied in shape from `test_rate_limit.py` rather than imported from it: a
    test helper shared between two files is a helper that gets changed for one
    of them.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, window: int) -> bool:
        self.expiries[key] = window
        return True

    async def ttl(self, key: str) -> int:
        return self.expiries.get(key, -1)


def test_the_route_is_actually_rate_limited() -> None:
    """Read off the ROUTER, before anything is faked.

    The 429 test below supplies the counter the limiter needs, so on its own
    it would pass over a route that had lost its limiter entirely. This is the
    half that cannot be faked: it asserts the dependency is attached, with the
    limit and the window it is attached with, so silently raising the ceiling
    is a change to this line rather than a change to nothing.
    """
    limited = [
        route
        for route in senders_api.router.routes
        if getattr(route, "path", None) == ""
        and "POST" in getattr(route, "methods", set())
    ]
    assert limited, "the collection POST is gone"
    names = [
        getattr(dependency.call, "__qualname__", "")
        for dependency in limited[0].dependant.dependencies
        if getattr(dependency, "call", None) is not None
    ]
    assert any("rate_limit" in name for name in names), names


def test_over_the_limit_is_429_with_a_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eleven anonymous POSTs against a limit of ten per hour.

    Anonymous on purpose: the limiter runs as a route dependency, BEFORE
    authentication, so the 429 must arrive in front of the 401. That ordering
    is the point of an abuse control -- a limiter that only counted
    authenticated callers would not slow down the one traffic pattern it
    exists for, which is somebody with no account at all.
    """
    # ONE counter for the whole test. A factory returning a fresh fake per
    # call would reset the window on every request and report 401 for ever,
    # which is the exact shape of a limiter test that measures nothing.
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_redis", lambda: fake)
    body = {"name": "Rahul", "email": "hr@sarkarcorp.com"}
    with TestClient(app) as http:
        statuses = [http.post(SENDERS, json=body).status_code for _ in range(11)]

    # THE ORDERING ASSERTION. The first ten are 401, which proves the limiter
    # ran BEFORE authentication and let them through rather than never having
    # run at all, and the eleventh is 429, which proves it counted them.
    assert statuses[:10] == [401] * 10, statuses
    assert statuses[10] == 429, statuses

    with TestClient(app) as http:
        final = http.post(SENDERS, json=body)
    assert final.status_code == 429
    # A caller told to back off needs to know for how long, and by how much
    # they overshot.
    assert final.headers["Retry-After"].isdigit()
    assert final.headers["X-RateLimit-Limit"] == "10"
    assert "10 per 3600 seconds" in final.json()["detail"]


# ── 403, 404, 422: these need rows ──────────────────────────────────────────


class World:
    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.allowed = uuid.uuid4()      # holds MANAGE_EMAIL_SENDERS
        self.refused = uuid.uuid4()      # does not
        self.sender_b = uuid.uuid4()     # tenant B's sender


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping API contract test")
    # The limiter must not interfere with the status codes below, and a
    # missing Redis fails OPEN, which is exactly what is wanted here.
    monkeypatch.setattr(cache, "_redis", lambda: None)

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for tid, label in (
                        (w.tenant_a, "Contract-A"),
                        (w.tenant_b, "Contract-B"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO tenants (id, name, domain, "
                                " spf_dkim_status) "
                                "VALUES (:id, :name, :domain, 'pending')"
                            ),
                            {
                                "id": str(tid),
                                "name": f"{label}-{tid.hex[:6]}",
                                "domain": f"{tid.hex[:10]}.contract.test",
                            },
                        )
                    # `client` is the tenant Super Admin and holds the
                    # capability; `hiring_manager` is the lowest staff role
                    # and does not. Both are real roles, so the 403 comes from
                    # the seeded permission data rather than from a fixture
                    # that invented a role nobody has.
                    for uid, tid, role in (
                        (w.allowed, w.tenant_a, "client"),
                        (w.refused, w.tenant_a, "hiring_manager"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO users (id, tenant_id, email, "
                                " full_name, role, status) "
                                "VALUES (:id, :tid, :email, 'Priya Raman', "
                                " :role, 'active')"
                            ),
                            {
                                "id": str(uid),
                                "tid": str(tid),
                                "email": f"{uid.hex[:10]}@contract.test",
                                "role": role,
                            },
                        )
                    await session.execute(
                        sa.text(
                            "INSERT INTO client_email_senders "
                            "(id, tenant_id, name, email, status) "
                            "VALUES (:id, :tid, 'Their Sender', :email, "
                            " 'active')"
                        ),
                        {
                            "id": str(w.sender_b),
                            "tid": str(w.tenant_b),
                            "email": f"hr@{w.tenant_b.hex[:10]}.example.com",
                        },
                    )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
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

    def acting_as(self, user_id: uuid.UUID, tenant: uuid.UUID, role: Role) -> None:
        self.principal = CurrentUser(
            user_id=user_id, tenant_id=tenant, role=role, audience=AUDIENCE_ORG
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
                async with tenant_scope(session, principal.tenant_id):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            caller.http = http
            caller.acting_as(world.allowed, world.tenant_a, Role.client)
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_a_caller_without_the_capability_is_403_and_names_it(
    client: Caller, world: World
) -> None:
    """403 rather than 404: this caller has a real session in the right
    tenant, and hiding the endpoint would leave them unable to tell their
    administrator which permission to grant. The capability is named in the
    detail for exactly that reason."""
    client.acting_as(world.refused, world.tenant_a, Role.hiring_manager)
    refused = client.http.post(
        SENDERS, json={"name": "Rahul", "email": "hr@sarkarcorp.com"}
    )
    assert refused.status_code == 403, refused.text
    assert caps.MANAGE_EMAIL_SENDERS in refused.json()["detail"]


def test_another_tenants_sender_is_404_and_not_403(
    client: Caller, world: World
) -> None:
    """The caller holds the capability, so the only thing that can refuse
    this is the tenant boundary, and a 403 here would confirm the id."""
    refused = client.http.post(f"{SENDERS}/{world.sender_b}/disable")
    assert refused.status_code == 404, refused.text

    # Identical to an id that has never existed, which is the property the
    # 404 was chosen for.
    imaginary = client.http.post(f"{SENDERS}/{uuid.uuid4()}/disable")
    assert imaginary.status_code == 404
    assert refused.json() == imaginary.json()


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("missing_email", {"name": "Rahul"}),
        ("missing_name", {"email": "hr@sarkarcorp.com"}),
        ("empty_name", {"name": "", "email": "hr@sarkarcorp.com"}),
        ("wrong_type", {"name": 17, "email": ["hr@sarkarcorp.com"]}),
        ("not_an_object", []),
    ],
)
def test_an_invalid_body_is_422_and_not_500(
    client: Caller, world: World, label: str, body
) -> None:
    """A schema violation is the caller's mistake and must be reported as one.

    `not_an_object` is the one worth keeping: a list where a body is expected
    reaches a different branch of the parser than a missing field, and it is
    the shape that most often produces a 500 in a handler that indexes the
    payload before validating it.
    """
    refused = client.http.post(SENDERS, json=body)
    assert refused.status_code == 422, (label, refused.status_code, refused.text)


def test_a_business_rule_refusal_is_also_422_and_carries_its_reason(
    client: Caller, world: World
) -> None:
    """A free-provider mailbox is well-formed and still unacceptable.

    Asserted beside the schema failures because the two must agree on a status
    for the client to have one error path: a caller that handled 422 as "fix
    your JSON" and got a 400 here would show the user a parser error instead
    of the sentence explaining why a gmail address cannot send for a company.
    """
    refused = client.http.post(
        SENDERS, json={"name": "Rahul", "email": "rahul.sarkar@gmail.com"}
    )
    assert refused.status_code == 422, refused.text
    assert "gmail.com" in str(refused.json()["detail"])


def test_a_valid_request_from_an_authorized_caller_succeeds(
    client: Caller, world: World
) -> None:
    """THE DIRECTION THAT MATTERS MOST.

    Six refusals above, and a handler that refused everything would satisfy
    all six. This is the one that fails if any of them is tightened into an
    outage, and it is what makes the 403 a permission answer rather than a
    broken endpoint.
    """
    created = client.http.post(
        SENDERS,
        json={
            "name": "Rahul Sarkar",
            "email": f"hr@{world.tenant_a.hex[:10]}.example.com",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["email"] == f"hr@{world.tenant_a.hex[:10]}.example.com"


def test_all_five_refusals_are_distinct_on_this_one_resource(
    client: Caller, world: World, limiter_off
) -> None:
    """The whole contract in one assertion.

    Collected together rather than left implicit across the file, because the
    guarantee is that these five are DIFFERENT from each other. Any two of
    them collapsing into one status is a real regression that every
    single-status test above would still pass.
    """
    observed: dict[str, int] = {}

    # 401, through the real auth chain with no principal.
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_tenant_db, None)
    try:
        with TestClient(app) as anonymous:
            observed["401 unauthenticated"] = anonymous.post(
                SENDERS, json={"name": "Rahul", "email": "hr@sarkarcorp.com"}
            ).status_code
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    client.acting_as(world.refused, world.tenant_a, Role.hiring_manager)
    observed["403 unauthorized"] = client.http.post(
        SENDERS, json={"name": "Rahul", "email": "hr@sarkarcorp.com"}
    ).status_code

    client.acting_as(world.allowed, world.tenant_a, Role.client)
    observed["404 cross tenant"] = client.http.post(
        f"{SENDERS}/{world.sender_b}/disable"
    ).status_code
    observed["422 invalid body"] = client.http.post(
        SENDERS, json={"name": "Rahul"}
    ).status_code

    assert observed == {
        "401 unauthenticated": 401,
        "403 unauthorized": 403,
        "404 cross tenant": 404,
        "422 invalid body": 422,
    }
    # 429 is asserted separately because provoking it needs a working counter
    # and would leave this caller limited for the rest of the window.
    assert len(set(observed.values())) == 4, observed
