"""Drishti through the routes a functional head actually touches.

`test_drishti.py` proves the pieces: the compiler, the bounded emphasis, the
binding rule as a pure function. This one walks the SEQUENCE, because two of
the properties that matter are properties of the sequence rather than of any
one function:

* the two doors converge. A profile captured through the conversation and
  saved through the PUT produces the same stored artifact as the same five
  strings typed into the form, so nothing downstream can tell which was used;
* the functional-head binding survives a round trip. The first author becomes
  the head of record, they can keep editing, somebody else is refused with
  the SERVER's own sentence, and confirming it rebinds and says who now holds
  it.

Skips cleanly when no database is reachable, like every integration test
here. That is not a way out: `test_drishti.py` pins the binding rule itself
without one, and this file exists to prove the handler reaches that rule with
the row's real state rather than with something it assumed.
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

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.api.drishti import HEAD_CHANGE_REQUIRED
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services.hiring import drishti

DRISHTI = "/api/v1/drishti"

OBSERVABLE = (
    "We shipped the billing rewrite in two quarters and promoted the engineer "
    "who led it."
)


def _reachable_once() -> bool:
    global _REACHABLE
    if _REACHABLE is None:
        _REACHABLE = _run(_probe())
    return _REACHABLE


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


#: Probed once per session. The connect attempt costs a full timeout when
#: nothing is listening, and paying it per test turns a skipped file into a
#: minute of nothing.
_REACHABLE: bool | None = None


async def _probe() -> bool:
    """A database WITH the functional-head binding on it.

    The column probe is deliberate rather than lazy: it is added by the same
    migration as the capability this file exercises, so a database that does
    not have it is one the suite has not migrated, and every assertion below
    would fail on the schema rather than on the behaviour.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                sa.text(
                    "SELECT functional_head_user_id FROM drishti_profiles LIMIT 0"
                )
            )
        return True
    except Exception:  # noqa: BLE001 -- no database, or not migrated
        return False
    finally:
        await engine.dispose()


class World:
    """Two people in ONE tenant who both hold `author_drishti_profile`.

    THE SUCCESSOR IS NOT A SECOND SUPER ADMIN, and the reason is a constraint
    rather than a preference. RBAC's "Super Admin" IS `Role.client`, it is
    tenant-scoped, and `uq_users_one_active_super_admin_per_tenant` permits
    exactly one active holder per tenant. Seeding two would not be a stricter
    test of the binding; it would be a row the database refuses, so nothing
    below would run at all.

    `recruitment_manager` is the seat the successor sits in. It holds
    `author_drishti_profile` by the SEEDED role default (migration 0115's own
    SEED_ROWS, and `DEFAULT_PERMISSION_MATRIX` beside it), which is what makes
    the 409 below mean what the test says it means: the successor is refused
    for want of a confirmed decision, not for want of the capability. A
    Recruiter or a Hiring Manager would be refused one step earlier, with a
    403, and the binding would never be reached.
    """

    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.cto = uuid.uuid4()
        self.successor = uuid.uuid4()
        self.function = f"Engineering-{uuid.uuid4().hex[:8]}"
        #: The role each seeded user actually carries, so the principal a
        #: request is made under cannot claim a seat its row does not hold.
        #: Asking the capability engine about `client` while the row says
        #: `recruitment_manager` would answer for somebody who is not calling.
        self.roles: dict[uuid.UUID, Role] = {
            self.cto: Role.client,
            self.successor: Role.recruitment_manager,
        }


@pytest.fixture
def world() -> Iterator[World]:
    if not _reachable_once():
        pytest.skip("no migrated database reachable -- skipping Drishti API test")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"Drishti-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.drishti.test",
                        },
                    )
                    for uid, name in ((w.cto, "Asha Rao"), (w.successor, "Vikram Iyer")):
                        await session.execute(
                            sa.text(
                                "INSERT INTO users (id, tenant_id, email, full_name, "
                                " role, status) "
                                "VALUES (:id, :tid, :email, :name, :role, 'active')"
                            ),
                            {
                                "id": str(uid),
                                "tid": str(w.tenant),
                                "email": f"{uid.hex[:10]}@drishti.test",
                                "name": name,
                                "role": w.roles[uid].value,
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

    def acting_as(self, world: World, user_id: uuid.UUID) -> None:
        self.principal = CurrentUser(
            user_id=user_id,
            tenant_id=world.tenant,
            # Read from the world rather than fixed, so the principal and the
            # seeded row agree about which seat is calling.
            role=world.roles[user_id],
            audience=AUDIENCE_ORG,
        )


@pytest.fixture
def client(world: World) -> Iterator[Caller]:
    sessions = _sessions()
    caller = Caller()

    async def _current_user() -> CurrentUser:
        if caller.principal is None:
            raise AssertionError("the test did not say who it was acting as")
        return caller.principal

    async def _tenant_db():
        principal = caller.principal
        if principal is None or principal.tenant_id is None:
            raise AssertionError("the test did not say who it was acting as")
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
            caller.acting_as(world, world.cto)
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _save(caller: Caller, world: World, **overrides):
    body = {
        "function_name": world.function,
        "strategic_purpose": OBSERVABLE,
        "functional_head_title": "CTO",
    }
    body.update(overrides)
    return caller.http.put(f"{DRISHTI}/functions", json=body)


def test_the_first_author_becomes_the_head_of_record(client, world) -> None:
    saved = _save(client, world)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["functional_head_name"] == "Asha Rao"
    assert body["functional_head_title"] == "CTO"
    assert body["is_functional_head"] is True
    assert body["functional_head_bound_at"] is not None


def test_the_head_keeps_editing_without_a_confirmation(client, world) -> None:
    _save(client, world)
    again = _save(client, world, strategic_purpose=OBSERVABLE + " We also rebuilt CI.")
    assert again.status_code == 200, again.text
    assert again.json()["functional_head_name"] == "Asha Rao"


def test_somebody_else_is_refused_with_the_servers_own_sentence(client, world) -> None:
    """THE BINDING, end to end.

    The refusal is a 409 and not a 403: the successor holds the capability,
    what they do not have is a confirmed decision, and routing them to an
    administrator would send them to somebody who cannot help. The screen
    renders this sentence verbatim, so the rule and the sentence describing
    it cannot drift.
    """
    _save(client, world)
    client.acting_as(world, world.successor)
    refused = _save(client, world)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == HEAD_CHANGE_REQUIRED.format(name="Asha Rao")

    confirmed = _save(client, world, functional_head_change_confirmed=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["functional_head_name"] == "Vikram Iyer"
    assert confirmed.json()["is_functional_head"] is True


def test_the_conversation_writes_nothing_until_the_profile_is_saved(
    client, world
) -> None:
    """A turn is a capture, not a write.

    The artifact the matrix reads must have exactly one writer, so the
    conversation returns the accumulated sections and the profile does not
    exist until the PUT stores and compiles them.
    """
    opened = client.http.post(
        f"{DRISHTI}/conversation/turn",
        json={"function_name": world.function, "section_key": "", "sections": {}},
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["generated_by_ai"] is False
    listed = client.http.get(f"{DRISHTI}/functions").json()["profiles"]
    assert all(row["function_name"] != world.function for row in listed)


def test_the_conversations_sections_save_to_the_forms_artifact(client, world) -> None:
    """The two doors converge, through the routes rather than in a unit test.

    Whatever route the words took, `compiled` is the same dict, so no weight,
    no prompt and no stored provenance line can tell them apart.
    """
    captured = {key: OBSERVABLE for key in drishti.SECTION_KEYS}
    saved = _save(client, world, **captured)
    assert saved.status_code == 200, saved.text
    assert saved.json()["compiled"] == drishti.compile_profile(
        function_name=world.function, sections=captured
    )
