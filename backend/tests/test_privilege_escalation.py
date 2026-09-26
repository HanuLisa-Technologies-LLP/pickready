"""A Recruiter must not be able to climb, and the climb has four shapes.

WHAT EXISTS ALREADY, AND WHY IT IS NOT ENOUGH
----------------------------------------------
`test_staff.py::test_a_peer_or_a_superior_is_refused` and
`::test_a_manager_can_only_grant_what_they_hold` prove the two rules as PURE
FUNCTIONS. `test_permission_combinations.py` and `test_rbac_conformance.py`
prove the decision layer refuses a grant above the ceiling.

None of them goes through a route. The gap that leaves is specific and is the
gap that has actually bitten this repository before: a rule can be perfectly
implemented in `role_hierarchy` and simply not called by the handler, and the
only test that would notice is one that makes the request. The §24 capability
set shipped with no seeding migration for a whole phase, and every dashboard
control answered 403, because the half that was missing was the wiring rather
than the logic.

THE FOUR SHAPES
---------------
  1. Grant yourself a capability you do not hold. The ladder: grant it to a
     subordinate, then have them grant it back.
  2. Edit a peer or a superior. Two Recruiters editing each other makes the
     hierarchy decorative, because everyone at a level ends up holding
     everyone else's permissions.
  3. Change your own role. The one-step version of the ladder.
  4. Send `tenant_id` or `role` in the request body and have the server
     believe it. The classic mass-assignment bug: it needs no capability at
     all, only a field the schema quietly accepts.

Shape 4 is asserted WITHOUT a database, because a Pydantic model that ignores
an unknown field is a property of the model. The other three go through
`app.main.app` and need one.
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

from app.api.companies import STAFF_ROLES, validate_staff_role
from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.schemas.companies import StaffCreateIn, StaffPermissionsIn, StaffUpdateIn
from app.services import capabilities as caps
from app.services import role_hierarchy

STAFF = "/api/v1/companies/me/staff"


# ── Shape 4: the request body is not a place to put your own tenant ─────────
#
# No database: these are properties of the schema, and the schema is what
# stands between a crafted body and the ORM.


@pytest.mark.parametrize(
    "model", [StaffCreateIn, StaffUpdateIn], ids=["create", "update"]
)
def test_the_staff_body_has_no_tenant_or_permission_field_to_inject_into(
    model,
) -> None:
    """A field that does not exist cannot be mass-assigned.

    Asserted on the FIELD SET rather than by sending one crafted body, for the
    reason `test_miti_pipeline` asserts the evaluator's exact fields: a test
    that probed for `tenant_id` by name would pass over a future field called
    `organisation` or `overrides` and reopen the whole hole.
    """
    assert set(model.model_fields) <= {
        "email",
        "full_name",
        "phone",
        "role",
        "approval_level",
    }
    for forbidden in ("tenant_id", "permissions_json", "overrides", "status",
                      "firebase_uid", "id", "user_id"):
        assert forbidden not in model.model_fields


def test_a_body_carrying_tenant_id_and_permissions_is_parsed_without_them() -> None:
    """The attack as it is actually sent, not as a field-set assertion.

    Pydantic's default is to IGNORE an unknown key rather than raise, which is
    the right behaviour here and is also why this needs saying out loud: the
    body is accepted, so nothing in the response tells anybody the injection
    was attempted, and the only thing making it harmless is that the value
    never lands anywhere.
    """
    parsed = StaffCreateIn.model_validate(
        {
            "email": "new.recruiter@sarkarcorp.com",
            "full_name": "New Recruiter",
            "role": "recruiter",
            # The injection.
            "tenant_id": str(uuid.uuid4()),
            "permissions_json": {caps.MANAGE_BILLING: True},
            "status": "active",
        }
    )
    assert not hasattr(parsed, "tenant_id")
    assert not hasattr(parsed, "permissions_json")
    dumped = parsed.model_dump()
    assert "tenant_id" not in dumped and "permissions_json" not in dumped


def test_the_permissions_body_carries_overrides_and_nothing_else() -> None:
    """`StaffPermissionsIn` is the one body that legitimately carries
    capability names, so it is the one where an extra `role` or `tenant_id`
    would be most plausible and most damaging."""
    assert set(StaffPermissionsIn.model_fields) == {"overrides"}
    parsed = StaffPermissionsIn.model_validate(
        {"overrides": {caps.VIEW_REVIEW_SCREEN: True},
         "role": "client", "tenant_id": str(uuid.uuid4())}
    )
    assert parsed.overrides == {caps.VIEW_REVIEW_SCREEN: True}
    assert not hasattr(parsed, "role")


@pytest.mark.parametrize("role", ["client", "super_admin", "bd", "candidate"])
def test_a_privileged_role_cannot_be_minted_from_the_customer_portal(
    role: str,
) -> None:
    """Shape 3's escalation target. `client` IS the RBAC Super Admin, so a
    portal that accepted it as a staff role would let anyone holding
    `manage_staff` mint a second tenant owner."""
    assert Role(role) not in STAFF_ROLES
    with pytest.raises(ValueError):
        validate_staff_role(role)


def test_a_recruiter_is_below_every_role_it_could_try_to_reach() -> None:
    """The arithmetic the route depends on, pinned in one place.

    Not a duplicate of `test_staff.py::test_a_peer_or_a_superior_is_refused`:
    that asserts four hand-picked pairs, this asserts the CLOSURE, so a new
    role added at or above the Recruiter's rank fails here rather than
    silently becoming editable by them.
    """
    recruiter = Role.recruiter
    assert not role_hierarchy.can_manage(recruiter, recruiter), "self-management"
    for other in role_hierarchy.ROLE_RANK:
        if role_hierarchy.rank(other) <= role_hierarchy.rank(recruiter):
            assert not role_hierarchy.can_manage(recruiter, other), other
    # And the one thing they legitimately CAN do, or the assertions above
    # would also be satisfied by a hierarchy where nobody may manage anybody.
    assert role_hierarchy.can_manage(recruiter, Role.hiring_manager)


# ── Shapes 1 to 3, over HTTP ────────────────────────────────────────────────


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
            await conn.execute(sa.text("SELECT 1 FROM role_permissions LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- no database here
        return False
    finally:
        await engine.dispose()


class World:
    """One tenant, four people: the climber, a peer, a superior, a
    subordinate. Every shape needs a different one of them as its target."""

    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.recruiter = uuid.uuid4()
        self.peer_recruiter = uuid.uuid4()
        self.superior = uuid.uuid4()          # recruitment_manager
        self.subordinate = uuid.uuid4()       # hiring_manager


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping privilege escalation test")

    w = World()
    sessions = _sessions()
    people = (
        (w.recruiter, "recruiter", "Climbing Recruiter"),
        (w.peer_recruiter, "recruiter", "Peer Recruiter"),
        (w.superior, "recruitment_manager", "Their Manager"),
        (w.subordinate, "hiring_manager", "A Hiring Manager"),
    )

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants (id, name, domain, "
                            " spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"Escalation-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.esc.test",
                        },
                    )
                    for uid, role, name in people:
                        await session.execute(
                            sa.text(
                                "INSERT INTO users (id, tenant_id, email, "
                                " full_name, role, status) "
                                "VALUES (:id, :tid, :email, :name, :role, "
                                " 'active')"
                            ),
                            {
                                "id": str(uid),
                                "tid": str(w.tenant),
                                "email": f"{uid.hex[:10]}@esc.test",
                                "name": name,
                                "role": role,
                            },
                        )
                    # The climber must hold MANAGE_STAFF, or every refusal
                    # below would come from the capability gate and the
                    # hierarchy would never be exercised at all. This is the
                    # grant that makes the test about escalation rather than
                    # about authentication.
                    await session.execute(
                        sa.text(
                            "UPDATE users SET permissions_json = "
                            " CAST(:perms AS jsonb) WHERE id = :id"
                        ),
                        {
                            "perms": f'{{"{caps.MANAGE_STAFF}": true}}',
                            "id": str(w.recruiter),
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

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


class Caller:
    def __init__(self) -> None:
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_recruiter(self, world: World) -> None:
        self.principal = CurrentUser(
            user_id=world.recruiter,
            tenant_id=world.tenant,
            role=Role.recruiter,
            audience=AUDIENCE_ORG,
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
            caller.as_recruiter(world)
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def _stored_overrides(user_id: uuid.UUID) -> dict:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                value = (
                    await session.execute(
                        sa.text(
                            "SELECT permissions_json FROM users WHERE id = :id"
                        ),
                        {"id": str(user_id)},
                    )
                ).scalar_one()
    return value or {}


async def _stored_role(user_id: uuid.UUID) -> str:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return (
                    await session.execute(
                        sa.text("SELECT role FROM users WHERE id = :id"),
                        {"id": str(user_id)},
                    )
                ).scalar_one()


#: Capabilities a Recruiter does not hold in the seeded matrix. Both are
#: administrative: one edits the permission template itself, the other
#: authorises a corporate mailbox to send as the company.
NOT_HELD_BY_A_RECRUITER = (caps.EDIT_ROLE_PERMISSIONS, caps.AUTHORIZE_EMAIL_SENDERS)


@pytest.mark.parametrize("capability", NOT_HELD_BY_A_RECRUITER)
def test_a_recruiter_cannot_grant_a_capability_it_does_not_hold(
    client: Caller, world: World, capability: str
) -> None:
    """SHAPE 1, and the rung that makes it a ladder.

    The move is to push a capability DOWN onto a Hiring Manager and then have
    that Hiring Manager push it back up. Blocking the first step is what makes
    the capability set monotonically non-increasing as you descend, which is
    the only thing "hierarchy" can mean here.

    THE PREMISE IS PROVEN, NOT ASSUMED, and that is why the GET comes first.
    An earlier draft of this test used `manage_billing`, which a Recruiter
    DOES hold in the seeded matrix, so the grant succeeded and the test
    reported a privilege escalation that was not one. Reading `grantable` off
    the server first means a capability moving into the Recruiter's role
    default fails here as a stale fixture rather than silently turning this
    into a test of nothing.

    The stored overlay is read back afterwards: a handler that answered 403
    after flushing would pass a status-only assertion and still have granted
    the capability.
    """
    mine = client.http.get(f"{STAFF}/{world.subordinate}/permissions")
    assert mine.status_code == 200, mine.text
    assert capability not in mine.json()["grantable"], (
        f"{capability} is in this Recruiter's own set, so this test would "
        f"prove nothing. Pick one they do not hold."
    )

    refused = client.http.patch(
        f"{STAFF}/{world.subordinate}/permissions",
        json={"overrides": {capability: True}},
    )
    assert refused.status_code == 403, refused.text
    assert capability in refused.json()["detail"]
    assert _run(_stored_overrides(world.subordinate)) == {}


def test_a_recruiter_cannot_grant_itself_anything(
    client: Caller, world: World
) -> None:
    """The one-step version. The target is the climber's own row, which is
    also the row the hierarchy check reads the actor's rank from, so an
    implementation that compared the actor to the TARGET's rank rather than
    refusing self-management outright would let this through."""
    refused = client.http.patch(
        f"{STAFF}/{world.recruiter}/permissions",
        json={"overrides": {caps.EDIT_ROLE_PERMISSIONS: True}},
    )
    assert refused.status_code in (403, 404), refused.text
    stored = _run(_stored_overrides(world.recruiter))
    assert caps.EDIT_ROLE_PERMISSIONS not in stored


def test_a_recruiter_cannot_edit_a_peer(client: Caller, world: World) -> None:
    """SHAPE 2. Equal rank, same tenant, and the actor holds MANAGE_STAFF, so
    the only thing that can refuse this is the strictness of the comparison.
    A `>=` where the code has `<` admits exactly this request."""
    refused = client.http.put(
        f"{STAFF}/{world.peer_recruiter}",
        json={"full_name": "Renamed By A Peer", "role": "hiring_manager"},
    )
    assert refused.status_code in (403, 404), refused.text
    assert _run(_stored_role(world.peer_recruiter)) == "recruiter"


def test_a_recruiter_cannot_edit_a_superior(client: Caller, world: World) -> None:
    """The same comparison in the other direction. Demoting your own manager
    to Hiring Manager is the most direct route to the top of a tenant."""
    refused = client.http.put(
        f"{STAFF}/{world.superior}",
        json={"full_name": "Demoted", "role": "hiring_manager"},
    )
    assert refused.status_code in (403, 404), refused.text
    assert _run(_stored_role(world.superior)) == "recruitment_manager"


def test_a_recruiter_cannot_promote_itself(client: Caller, world: World) -> None:
    """SHAPE 3, through the edit route rather than the permissions route.

    `update_staff` checks BOTH the role held now and the role being moved to,
    and this exercises the first: the Recruiter's own row is not strictly
    beneath them, so the request dies before the target role is considered.
    """
    refused = client.http.put(
        f"{STAFF}/{world.recruiter}",
        json={"full_name": "Climbing Recruiter", "role": "recruitment_manager"},
    )
    assert refused.status_code in (403, 404), refused.text
    assert _run(_stored_role(world.recruiter)) == "recruiter"


def test_a_recruiter_cannot_promote_a_subordinate_past_itself(
    client: Caller, world: World
) -> None:
    """The indirect promotion, and the reason `update_staff` checks the TARGET
    role as well as the current one. The Hiring Manager IS beneath the
    Recruiter and is therefore editable; what must be refused is the
    destination."""
    refused = client.http.put(
        f"{STAFF}/{world.subordinate}",
        json={"full_name": "A Hiring Manager", "role": "recruitment_manager"},
    )
    assert refused.status_code == 403, refused.text
    assert _run(_stored_role(world.subordinate)) == "hiring_manager"


def test_a_recruiter_cannot_create_a_super_admin(
    client: Caller, world: World
) -> None:
    """Creation is the fourth way up, and the cheapest: no existing row to
    edit, no hierarchy comparison against a target that exists yet."""
    for role in ("client", "super_admin", "recruitment_manager"):
        refused = client.http.post(
            STAFF,
            json={
                "email": f"minted-{uuid.uuid4().hex[:8]}@sarkarcorp.com",
                "full_name": "Minted",
                "role": role,
            },
        )
        assert refused.status_code in (400, 403), (role, refused.text)


# The xfail that stood here is gone because the bug it recorded is fixed:
# `app/api/companies.py` bound the local name `dispatch` to the STRING from
# `_email_dispatch_state()` and then called it, so this route 500'd from
# 2026-09-05 until 2026-09-18. The local is `dispatch_state` now.
def test_a_crafted_body_cannot_move_a_new_hire_into_another_tenant(
    client: Caller, world: World
) -> None:
    """Shape 4 end to end: the schema drops the field, and the row that is
    actually written carries the ACTOR's tenant.

    The assertion is on the stored row rather than on the response, because a
    serializer that simply never returns `tenant_id` would make a
    response-only assertion pass while the row sat in the wrong customer.
    """
    victim_tenant = uuid.uuid4()
    email = f"crafted-{uuid.uuid4().hex[:8]}@sarkarcorp.com"
    created = client.http.post(
        STAFF,
        json={
            "email": email,
            "full_name": "Crafted Body",
            "role": "hiring_manager",
            "tenant_id": str(victim_tenant),
            "permissions_json": {caps.MANAGE_BILLING: True},
        },
    )
    assert created.status_code == 201, created.text

    async def _row() -> tuple:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    return (
                        await session.execute(
                            sa.text(
                                "SELECT tenant_id, role, permissions_json "
                                "FROM users WHERE email = :email"
                            ),
                            {"email": email},
                        )
                    ).one()

    tenant_id, role, overrides = _run(_row())
    assert str(tenant_id) == str(world.tenant), "the body chose the tenant"
    assert role == "hiring_manager"
    assert not (overrides or {}), "the body granted a capability"


def test_the_recruiter_can_still_do_the_thing_it_is_allowed_to_do(
    client: Caller, world: World
) -> None:
    """THE DIRECTION THAT MATTERS MOST.

    Every assertion above is a refusal, and a handler that refused everything
    would satisfy all of them. This is the one that fails if the hierarchy is
    tightened into an outage: a Recruiter holding MANAGE_STAFF may edit a
    Hiring Manager, who is strictly beneath them.
    """
    allowed = client.http.put(
        f"{STAFF}/{world.subordinate}",
        json={"full_name": "Renamed Legitimately", "role": "hiring_manager"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["full_name"] == "Renamed Legitimately"


# ── A FINDING, recorded rather than fixed ───────────────────────────────────


# Same fix as above. This test is the one that found it, by being the first
# thing in the repository's history ever to POST this route.
def test_inviting_a_staff_member_is_not_a_500(client: Caller, world: World) -> None:
    """The route has never been called by a test, and it does not work.

    `app/api/companies.py`:

        dispatch = _email_dispatch_state()   # returns "queued" | "not_configured"
        dispatch("pickready.send_email", args=[...])   # calls the string

    A local name shadows the imported `dispatch` function, so every request
    raises `TypeError: 'str' object is not callable` and answers 500. The
    invite row and the user row are written first and the transaction is then
    rolled back by the error handler, so the caller sees a server error and
    the team member is not created.

    HOW IT SURVIVED. It was introduced on 2026-09-05 in the commit that
    replaced Celery with `dispatch`: the previous line called a task object,
    the rename collided with a local variable of the same name, and Python
    binds rather than complains. `test_staff.py` exercises `ensure_can_manage`
    and `grantable_capabilities` as pure functions and never issues the
    request, so nothing in 272 test modules touches this URL. It is the exact
    failure this repository already wrote down: the logic was right and the
    wiring was not, and only a test that makes the request can see it.

    WHY IT IS HERE RATHER THAN IN A BUG REPORT ALONE. Adding a team member is
    how a customer grants anybody else access to their tenant, so while this
    is broken a tenant has exactly one usable account. That is a security
    property as much as an availability one: it pushes customers towards
    sharing the one credential that works.
    """
    created = client.http.post(
        STAFF,
        json={
            "email": f"invited-{uuid.uuid4().hex[:8]}@sarkarcorp.com",
            "full_name": "Freshly Invited",
            "role": "hiring_manager",
        },
    )
    assert created.status_code == 201, created.text
