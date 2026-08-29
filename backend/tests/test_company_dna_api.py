"""The Company DNA intake, driven through real HTTP against real Postgres.

WHY THIS RUNS AGAINST A DATABASE AND NOT A DOUBLE
---------------------------------------------------
Three of the things this phase promises cannot be checked without one:

  * the version IMMUTABILITY trigger, which is a `BEFORE UPDATE` function in
    migration 0060 and refuses a rewrite that application code would allow;
  * `uq_company_dna_one_current`, a PARTIAL unique index, which is the only way
    to say "exactly one current version per client" and the thing that catches
    a double-submitted intake;
  * the ROW LEVEL SECURITY policy, which is the real tenant boundary. A fake
    session would let the app's own WHERE clause be the only protection, which
    is exactly what CLAUDE.md rule 1 forbids.

The schema is built here from the ORM metadata plus the three pieces of DDL
that live only in migration 0060, IMPORTED FROM THE MIGRATION so the trigger
this exercises is the trigger that will run in production. It is deliberately
not a full `alembic upgrade`: the revision graph has two heads today (see the
final report), so `upgrade head` would fail for a reason that has nothing to do
with Company DNA.

The database is a scratch one, created and dropped by this module. It never
touches the development database.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG, AUDIENCE_OWNER
from app.main import app
from app.models.base import Base
from app.models.company_dna import JobCompanyDNABinding
from app.models.enums import Role
from app.models.hiring import CompanyDNA
from app.models.tenant import AuditLog, RolePermission, Tenant
from app.models.user import User

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "company_dna"
MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0060_company_dna_versioning.py"
)
#: Scoped to the process, so two runners on one cluster cannot drop each
#: other's database halfway through. A fixed name looked tidier and produced
#: exactly that failure: one run reported twenty unrelated errors because
#: another had just finished and taken the database with it.
SCRATCH_DB = f"readypick_company_dna_{os.getpid()}"
BASE = "/api/v1"

#: The containerised test Postgres from `docker-compose.test.yml`, overridable
#: for a runner that sits inside the compose network. The same env-with-a-
#: default shape `tests/test_object_storage.py` uses for MinIO.
#:
#: NOT the development database. This module creates and drops a scratch
#: database, and doing that against the database somebody is developing on
#: would be a surprise nobody asked for.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://readypick_test:readypick_test@localhost:55432/readypick_test",
)

#: `core.db.tenant_scope` drops to this role so the RLS policy, and not the
#: application's WHERE clause, is the tenant boundary. It has to exist in the
#: cluster for the scope to work, and creating it here means the test exercises
#: the real path rather than the owner-bypasses-everything one.
RLS_ROLE = get_settings().postgres_rls_app_role


def _migration():
    spec = importlib.util.spec_from_file_location("_dna_migration_0060", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION_MODULE = _migration()

#: The tables this router touches. A named list rather than the whole metadata:
#: creating every table would pull in the entire schema and its extensions for a
#: router that reads five of them.
ROUTER_TABLES = (
    Tenant.__table__,
    User.__table__,
    RolePermission.__table__,
    AuditLog.__table__,
    CompanyDNA.__table__,
    # Not read by any route in this phase. It is here so migration 0060's
    # CREATE TABLE is actually executed by a test rather than only read: a
    # migration nobody runs is a migration whose foreign keys nobody has
    # checked, and this one points at four tables.
    JobCompanyDNABinding.__table__,
)


def _with_dependencies(tables) -> list:
    """The named tables plus everything their foreign keys point at.

    `tenants` references `pricing_plans`, which references others, and a
    CREATE TABLE with a dangling reference fails. Computed rather than listed,
    so a new foreign key added upstream does not turn into a puzzling failure
    in this file.
    """
    wanted = {table.name: table for table in tables}
    added = True
    while added:
        added = False
        for table in list(wanted.values()):
            for key in table.foreign_keys:
                target = key.column.table
                if target.name not in wanted:
                    wanted[target.name] = target
                    added = True
    # `create_all` sorts by dependency itself, so this returns the SET rather
    # than an order. Reading `Base.metadata.sorted_tables` would warn about the
    # candidates/profiles cycle, which is unrelated to anything here.
    return list(wanted.values())


TABLES = _with_dependencies(ROUTER_TABLES)

#: The DDL migration 0060 adds that the ORM metadata cannot express: the
#: partial unique index (SQLAlchemy's UniqueConstraint has no WHERE) and the
#: immutability trigger. Both are the point of the tests below, so both come
#: from the migration module rather than from a copy in this file.
EXTRA_DDL = (
    "CREATE UNIQUE INDEX uq_company_dna_one_current ON company_dna (tenant_id) "
    "WHERE is_current",
    "ALTER TABLE company_dna ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE company_dna FORCE ROW LEVEL SECURITY",
    "CREATE POLICY company_dna_tenant_isolation ON company_dna "
    "USING ((tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) "
    "OR (current_setting('app.bypass_rls', true) = 'on')) "
    "WITH CHECK ((tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) "
    "OR (current_setting('app.bypass_rls', true) = 'on'))",
    MIGRATION_MODULE._IMMUTABILITY_FUNCTION,
    "CREATE TRIGGER trg_company_dna_version_is_immutable "
    "BEFORE UPDATE ON company_dna "
    "FOR EACH ROW EXECUTE FUNCTION company_dna_version_is_immutable()",
)


def _urls() -> tuple[str, str]:
    """(maintenance url, scratch url), derived from the TEST database."""
    url = sa.engine.make_url(TEST_DATABASE_URL)
    # `str(URL)` obfuscates the password. Rendering it out is required here and
    # is safe: this is a test-only URL for a disposable containerised database.
    return (
        url.set(database="postgres").render_as_string(hide_password=False),
        url.set(database=SCRATCH_DB).render_as_string(hide_password=False),
    )


async def _build_schema(scratch_url: str) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=TABLES)
            for statement in EXTRA_DDL:
                await conn.execute(sa.text(statement))
            if RLS_ROLE:
                # NOLOGIN and without BYPASSRLS, exactly as production. A role
                # that bypassed RLS would make every isolation assertion below
                # pass for the wrong reason.
                await conn.execute(
                    sa.text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                        f'IN SCHEMA public TO "{RLS_ROLE}"'
                    )
                )
            for role, capability in MIGRATION_MODULE._GRANTS:
                await conn.execute(
                    sa.text(
                        "INSERT INTO role_permissions (id, tenant_id, role, "
                        "capability, allowed) VALUES "
                        "(gen_random_uuid(), NULL, :role, :cap, true)"
                    ),
                    {"role": role, "cap": capability},
                )
    finally:
        await engine.dispose()


async def _admin(statement: str, url: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(statement))
    finally:
        await engine.dispose()


class World:
    """Two tenants, and one signed-in person per role in each."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        # A third client, touched only by the Super Admin override test, so
        # that test always performs a real mutation rather than colliding with
        # a draft an earlier test left open.
        self.tenant_c = uuid.uuid4()
        self.users: dict[tuple[uuid.UUID, Role], uuid.UUID] = {}
        self.bd_user = uuid.uuid4()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    """A scratch database, the schema, and the people who will call the API."""
    maintenance_url, scratch_url = _urls()
    try:
        asyncio.run(
            _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)', maintenance_url)
        )
        asyncio.run(_admin(f'CREATE DATABASE "{SCRATCH_DB}"', maintenance_url))
        if RLS_ROLE:
            asyncio.run(
                _admin(
                    f'DO $$ BEGIN CREATE ROLE "{RLS_ROLE}" NOLOGIN; '
                    "EXCEPTION WHEN duplicate_object THEN NULL; END $$",
                    maintenance_url,
                )
            )
    except Exception as exc:  # noqa: BLE001 - the reason is reported, not swallowed
        pytest.skip(
            "the containerised test Postgres is not reachable at "
            f"{sa.engine.make_url(TEST_DATABASE_URL).render_as_string()}: {exc}. "
            "Start it with `docker compose -f docker-compose.test.yml up -d "
            "postgres`, or point TEST_DATABASE_URL at another one."
        )

    asyncio.run(_build_schema(scratch_url))
    state = World()
    asyncio.run(_seed(scratch_url, state))
    yield state
    asyncio.run(
        _admin(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)', maintenance_url)
    )


ORG_ROLES = (
    Role.client,
    Role.hr_manager,
    Role.recruitment_manager,
    Role.recruiter,
    Role.hiring_manager,
    Role.interview_manager,
)


async def _seed(scratch_url: str, state: World) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            for tenant, label in (
                (state.tenant_a, "Alpha"),
                (state.tenant_b, "Beta"),
                (state.tenant_c, "Gamma"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, :name, :domain, 'pending')"
                    ),
                    {"id": tenant, "name": label, "domain": f"{tenant}.dna.test"},
                )
                for role in ORG_ROLES:
                    user_id = uuid.uuid4()
                    state.users[(tenant, role)] = user_id
                    await conn.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, role, email, "
                            "full_name, status, auth_providers) VALUES "
                            "(:id, :tenant, :role, :email, :name, 'active', "
                            "'{}'::jsonb)"
                        ),
                        {
                            "id": user_id,
                            "tenant": tenant,
                            "role": role.value,
                            "email": f"{role.value}@{tenant}.dna.test",
                            "name": f"{label} {role.value}",
                        },
                    )
            await conn.execute(
                sa.text(
                    "INSERT INTO users (id, tenant_id, role, email, full_name, "
                    "status, auth_providers) VALUES "
                    "(:id, NULL, 'bd', :email, 'Sales', 'active', '{}'::jsonb)"
                ),
                {"id": state.bd_user, "email": "bd@readypick.test"},
            )
            # The BD console's own grant, seeded by migration 0023 in production.
            await conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (id, tenant_id, role, "
                    "capability, allowed) VALUES "
                    "(gen_random_uuid(), NULL, 'bd', 'view_bd_customers', true)"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def sessions(world: World):
    """A session factory bound to the scratch database.

    Created but NOT connected here: asyncpg connections belong to the event
    loop that opened them, and TestClient runs the application on its own. The
    first connection is therefore made inside a request, in that loop.
    """
    _maintenance, scratch_url = _urls()
    # NullPool, deliberately. An asyncpg connection belongs to the event loop
    # that opened it, and TestClient runs the application on a fresh loop per
    # test, so a pooled connection from an earlier test is bound to a loop that
    # has already closed. Without this the first test passes and every later one
    # fails on a connection it did nothing wrong to.
    engine = create_async_engine(scratch_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _no_permission_cache(monkeypatch):
    """The RBAC engine caches permission rows in Redis for two minutes.

    Patched out so a run does not depend on a Redis being present and cannot
    read a row set left by an earlier run. This is a test double for a cache,
    not for the thing being tested: the resolution below still goes through the
    real engine against the real rows.
    """
    from app.services import tenant_cache

    async def _miss(key):  # noqa: ANN001
        return None

    async def _noop(key, value, *, ttl=120):  # noqa: ANN001
        return None

    monkeypatch.setattr(tenant_cache, "get_json", _miss)
    monkeypatch.setattr(tenant_cache, "set_json", _noop)


@pytest.fixture
def client(world: World, sessions, monkeypatch) -> Iterator["Caller"]:
    """A TestClient whose principal each test sets."""
    from app.api import company_dna as router_module

    monkeypatch.setattr(router_module, "get_session_factory", lambda: sessions)

    caller = Caller(world)

    async def _current_user() -> CurrentUser:
        assert caller.principal is not None, "no principal set for this request"
        return caller.principal

    async def _tenant_db():
        principal = caller.principal
        assert principal is not None
        async with sessions() as session:
            async with session.begin():
                if principal.audience == AUDIENCE_ORG and principal.tenant_id:
                    async with tenant_scope(session, principal.tenant_id):
                        yield session
                else:
                    async with superadmin_scope(session):
                        yield session

    # RESTORE, never clear. `dependency_overrides` is application-global, and
    # clearing it would remove anything another module installed rather than
    # only what this fixture added.
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            caller.http = http
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


class Caller:
    def __init__(self, world: World) -> None:
        self.world = world
        self.http: TestClient | None = None
        self.principal: CurrentUser | None = None

    def sign_in(self, role: Role, tenant: uuid.UUID | None = None) -> "Caller":
        tenant = tenant or self.world.tenant_a
        if role == Role.bd:
            self.principal = CurrentUser(
                user_id=self.world.bd_user,
                tenant_id=None,
                role=Role.bd,
                audience=AUDIENCE_OWNER,
            )
            return self
        self.principal = CurrentUser(
            user_id=self.world.users[(tenant, role)],
            tenant_id=tenant,
            role=role,
            audience=AUDIENCE_ORG,
        )
        return self

    def get(self, path: str, **kwargs):
        assert self.http is not None
        return self.http.get(f"{BASE}{path}", **kwargs)

    def post(self, path: str, **kwargs):
        assert self.http is not None
        return self.http.post(f"{BASE}{path}", **kwargs)


def _answers() -> dict:
    raw = json.loads((FIXTURES / "complete_intake.json").read_text(encoding="utf-8"))
    return {key: value for key, value in raw.items() if not key.startswith("_")}


ANSWERS = _answers()


def _dna(client: Caller, tenant: uuid.UUID | None = None) -> str:
    return f"/clients/{tenant or client.world.tenant_a}/company-dna"


def _open_draft_id(client: Caller, tenant: uuid.UUID) -> str:
    """The open draft's id, opening one if there is none.

    Written so each test stands on its own. A test that depended on an earlier
    one having left a draft open would pass in a full run and fail when
    somebody ran it alone, which is the shape of flakiness that trains people
    to rerun rather than to read.
    """
    existing = client.get(_dna(client, tenant)).json()["session"]
    if existing is not None:
        return existing["id"]
    created = client.post(_dna(client, tenant), json={})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _run_session_to_completion(client: Caller, tenant: uuid.UUID) -> dict:
    """Open a draft, answer the whole instrument, confirm, freeze.

    The happy path spec-doc6 §4.2 asks for, driven only through HTTP.
    """
    created = client.post(_dna(client, tenant), json={})
    assert created.status_code == 201, created.text
    dna_id = created.json()["id"]

    for key, value in ANSWERS.items():
        answered = client.post(
            f"{_dna(client, tenant)}/{dna_id}/messages",
            json={"question_key": key, "answer": value},
        )
        assert answered.status_code == 200, (key, answered.text)
        body = answered.json()

    assert body["ready_to_complete"] is True
    assert body["next_question"] is None
    token = body["understanding_token"]
    assert token

    frozen = client.post(
        f"{_dna(client, tenant)}/{dna_id}/complete",
        json={"understanding_token": token},
    )
    assert frozen.status_code == 200, frozen.text
    return {"id": dna_id, "completed": frozen.json(), "session": body}


# ── The happy path ───────────────────────────────────────────────────────────


def test_an_hr_manager_completes_the_intake_end_to_end(client: Caller) -> None:
    client.sign_in(Role.hr_manager)
    tenant = client.world.tenant_a

    before = client.get(f"{_dna(client)}/status")
    assert before.status_code == 200
    assert before.json()["status"] == "incomplete"
    assert before.json()["version"] is None

    overview = client.get(_dna(client)).json()
    assert overview["has_artifact"] is False
    assert overview["scorecard"]["blocked"] is True
    assert "Company DNA required" in overview["scorecard"]["message"]
    assert overview["permissions"]["can_author"] is True

    result = _run_session_to_completion(client, tenant)

    completed = result["completed"]
    assert completed["version"] == 1
    assert completed["status"] == "complete"
    assert completed["completed_at"]
    assert completed["authored_by"] == f"Alpha {Role.hr_manager.value}"
    assert [block["key"] for block in completed["understanding"]] == [
        "emphasis",
        "evidence",
        "good",
        "risks",
        "constraints",
        "reach",
        "reporting",
        "context",
    ]

    after = client.get(f"{_dna(client)}/status").json()
    assert after["status"] == "complete"
    assert after["version"] == 1
    assert after["draft_open"] is False

    overview = client.get(_dna(client)).json()
    assert overview["has_artifact"] is True
    assert overview["scorecard"]["blocked"] is False
    assert overview["scorecard"]["message"] == ""
    assert overview["session"] is None, "the draft closed, so there is none to show"


def test_the_session_is_resumable(client: Caller) -> None:
    """Save and resume falls out of persisting every accepted answer.

    A separate draft mechanism would be a second copy of the answers with its
    own staleness. Here the row IS the draft.
    """
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    tenant = client.world.tenant_b
    created = client.post(_dna(client, tenant), json={})
    dna_id = created.json()["id"]

    first_key = next(iter(ANSWERS))
    client.post(
        f"{_dna(client, tenant)}/{dna_id}/messages",
        json={"question_key": first_key, "answer": ANSWERS[first_key]},
    )
    # A completely fresh read, as a browser reopening the page would do.
    resumed = client.get(_dna(client, tenant)).json()["session"]
    assert resumed["id"] == dna_id
    assert resumed["answers"][first_key] == ANSWERS[first_key]
    assert resumed["next_question"]["key"] != first_key
    assert resumed["ready_to_complete"] is False
    assert resumed["understanding_token"] is None


def test_the_twelve_sections_each_say_why_we_are_asking(client: Caller) -> None:
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    session = client.get(_dna(client, client.world.tenant_b)).json()["session"]
    assert len(session["sections"]) == 12
    for section in session["sections"]:
        assert section["intent"].strip(), section["key"]
        assert section["title"].strip()
    evidence = [s for s in session["sections"] if s["examples"]]
    assert evidence, "the observable-evidence section shows no example pairs"
    for section in evidence:
        for example in section["examples"]:
            assert example["rejected"] and example["accepted"]


# ── The two refusals, at the API and not in the UI ───────────────────────────


def test_free_text_to_a_forced_scale_is_refused_by_the_api(client: Caller) -> None:
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    tenant = client.world.tenant_b
    dna_id = _open_draft_id(client, tenant)
    refused = client.post(
        f"{_dna(client, tenant)}/{dna_id}/messages",
        json={"question_key": "proven_vs_potential", "answer": "we value both"},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["question_key"] == "proven_vs_potential"
    assert "scale" in detail["message"].lower()


def test_an_adjective_where_evidence_was_asked_for_is_refused(client: Caller) -> None:
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    tenant = client.world.tenant_b
    dna_id = _open_draft_id(client, tenant)
    refused = client.post(
        f"{_dna(client, tenant)}/{dna_id}/messages",
        json={"question_key": "observable_behaviours", "answer": "Ownership mindset"},
    )
    assert refused.status_code == 422
    assert "ownership mindset" in refused.json()["detail"]["message"].lower()


def test_completion_without_every_answer_is_refused_and_names_what_is_missing(
    client: Caller,
) -> None:
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    tenant = client.world.tenant_b
    dna_id = _open_draft_id(client, tenant)
    refused = client.post(
        f"{_dna(client, tenant)}/{dna_id}/complete",
        json={"understanding_token": "0" * 64},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["missing"]


# ── Bodha states its understanding back, and the confirmation binds to it ────


def test_a_stale_confirmation_is_refused(client: Caller) -> None:
    """The client confirmed one understanding; an answer moved underneath it.

    Without this, "read it back for explicit confirmation" would be a screen
    somebody clicked past, and the version frozen could be one nobody ever saw.
    """
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    tenant = client.world.tenant_b
    dna_id = _open_draft_id(client, tenant)
    for key, value in ANSWERS.items():
        client.post(
            f"{_dna(client, tenant)}/{dna_id}/messages",
            json={"question_key": key, "answer": value},
        )
    shown = client.get(_dna(client, tenant)).json()["session"]
    stale_token = shown["understanding_token"]

    moved = 1 if ANSWERS["proven_vs_potential"] != 1 else 5
    client.post(
        f"{_dna(client, tenant)}/{dna_id}/messages",
        json={"question_key": "proven_vs_potential", "answer": moved},
    )
    refused = client.post(
        f"{_dna(client, tenant)}/{dna_id}/complete",
        json={"understanding_token": stale_token},
    )
    assert refused.status_code == 409
    assert "confirmed" in refused.json()["detail"]

    fresh = client.get(_dna(client, tenant)).json()["session"]
    assert fresh["understanding_token"] != stale_token
    accepted = client.post(
        f"{_dna(client, tenant)}/{dna_id}/complete",
        json={"understanding_token": fresh["understanding_token"]},
    )
    assert accepted.status_code == 200


def test_the_understanding_read_back_carries_no_number(client: Caller) -> None:
    import re

    client.sign_in(Role.hr_manager)
    compiled = client.get(_dna(client)).json()["compiled"]
    assert compiled is not None
    client_text = set()
    for block in compiled["understanding"]:
        if block["key"] in ("good", "risks", "constraints", "context"):
            client_text.update(line.strip() for line in block["lines"])
    for block in compiled["understanding"]:
        for line in block["lines"]:
            if line.strip() in client_text:
                continue
            assert not re.search(r"\d", line), (block["key"], line)


# ── Versioning ───────────────────────────────────────────────────────────────


def test_a_second_draft_is_refused_while_one_is_open(client: Caller) -> None:
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    tenant = client.world.tenant_b
    opened = client.post(_dna(client, tenant), json={})
    assert opened.status_code == 201
    second = client.post(_dna(client, tenant), json={})
    assert second.status_code == 409


def test_a_new_version_supersedes_the_old_one_and_never_edits_it(
    client: Caller,
) -> None:
    """Immutable versions, checked from the version list rather than promised.

    Every job already run under version one was scored against version one's
    configuration. Overwriting it would make "what criteria was this candidate
    graded under" unanswerable for all of them.
    """
    client.sign_in(Role.hr_manager)
    tenant = client.world.tenant_a
    first = client.get(f"{_dna(client)}/versions").json()
    assert first["items"][0]["version"] == 1
    original_checksum = first["items"][0]["checksum"]

    result = _run_session_to_completion(client, tenant)
    assert result["completed"]["version"] == 2

    versions = client.get(f"{_dna(client)}/versions").json()
    assert [item["version"] for item in versions["items"]] == [2, 1]
    assert versions["items"][0]["is_current"] is True
    assert versions["items"][0]["status"] == "complete"
    assert versions["items"][1]["is_current"] is False
    assert versions["items"][1]["status"] == "superseded"
    # The superseded version's artifact is byte-identical to what it was.
    assert versions["items"][1]["checksum"] == original_checksum


def test_a_new_version_is_seeded_from_the_one_it_replaces(client: Caller) -> None:
    """"Create a new version" means editing a few answers, not retyping thirty."""
    client.sign_in(Role.hr_manager)
    tenant = client.world.tenant_a
    created = client.post(_dna(client), json={"copy_from_version": 1})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 3
    assert body["answers"] == ANSWERS
    assert body["ready_to_complete"] is True
    # And the source is untouched.
    versions = {
        item["version"]: item for item in client.get(f"{_dna(client)}/versions").json()["items"]
    }
    assert versions[1]["status"] == "superseded"
    assert versions[2]["is_current"] is True


def test_the_database_refuses_to_rewrite_a_completed_version(
    client: Caller, sessions
) -> None:
    """The trigger, not the router.

    An application check is code somebody can route around in a hotfix at the
    end of a release. What every already-written evaluation depends on is that
    the row cannot change, so the refusal lives where no code path can miss it.
    """
    tenant = client.world.tenant_a

    async def rewrite() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "UPDATE company_dna SET artifact_json = '{}'::jsonb "
                            "WHERE tenant_id = :t AND version = 1"
                        ),
                        {"t": tenant},
                    )

    with pytest.raises(Exception) as caught:
        asyncio.run(rewrite())
    assert "cannot be rewritten" in str(caught.value)


def test_the_database_refuses_to_reopen_a_completed_version(
    client: Caller, sessions
) -> None:
    tenant = client.world.tenant_a

    async def reopen() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "UPDATE company_dna SET status = 'draft' "
                            "WHERE tenant_id = :t AND version = 1"
                        ),
                        {"t": tenant},
                    )

    with pytest.raises(Exception) as caught:
        asyncio.run(reopen())
    assert "cannot move from" in str(caught.value)


def test_the_router_refuses_to_answer_into_a_completed_version(
    client: Caller,
) -> None:
    client.sign_in(Role.hr_manager)
    versions = client.get(f"{_dna(client)}/versions").json()["items"]
    current = next(item for item in versions if item["is_current"])
    # Find the row id through the overview's draft, then target the frozen one
    # by hunting the version detail route, which is keyed on the version.
    detail = client.get(f"{_dna(client)}/versions/{current['version']}")
    assert detail.status_code == 200
    assert detail.json()["configuration"] is None


# ── D3, through real HTTP ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.recruiter, Role.hiring_manager])
def test_a_read_only_role_gets_the_compiled_artifact_and_no_session(
    client: Caller, role: Role
) -> None:
    client.sign_in(role)
    body = client.get(_dna(client)).json()
    assert body["has_artifact"] is True
    assert body["compiled"]["understanding"]
    assert body["session"] is None
    assert body["permissions"] == {
        "can_author": False,
        "can_view_compiled": True,
        "can_view_session": False,
    }
    # No answer text anywhere in the payload, checked against the raw body.
    raw = json.dumps(body)
    for statement in ANSWERS["observable_behaviours"].splitlines():
        assert statement in raw, "the compiled artifact legitimately carries these"
    assert ANSWERS["headcount_growth_stage"] not in raw, (
        "a raw intake answer that is not part of the compiled artifact reached "
        "a read-only role"
    )


@pytest.mark.parametrize("role", [Role.recruiter, Role.hiring_manager])
def test_a_read_only_role_cannot_author_or_list_versions(
    client: Caller, role: Role
) -> None:
    client.sign_in(role)
    assert client.post(_dna(client), json={}).status_code == 403
    assert client.get(f"{_dna(client)}/versions").status_code == 403
    assert client.get(f"{_dna(client)}/versions/1").status_code == 403


def test_an_interview_manager_has_no_access_at_all(client: Caller) -> None:
    """D3: no access. Enforced by the absence of a grant, not by a branch."""
    client.sign_in(Role.interview_manager)
    assert client.get(_dna(client)).status_code == 403
    assert client.get(f"{_dna(client)}/status").status_code == 403
    assert client.post(_dna(client), json={}).status_code == 403
    assert client.get(f"{_dna(client)}/versions").status_code == 403


def test_the_super_admin_may_author_and_the_audit_records_the_override(
    client: Caller, sessions
) -> None:
    """RBAC §7.5: the same actions, recorded as an override.

    A gate that merely allowed it would leave the trail unable to distinguish a
    Super Admin acting for an HR Manager from the HR Manager acting.
    """
    client.sign_in(Role.client, client.world.tenant_c)
    tenant = client.world.tenant_c
    opened = client.post(_dna(client, tenant), json={})
    assert opened.status_code == 201, opened.text

    async def overrides() -> list:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = await session.execute(
                        sa.text(
                            "SELECT metadata_json FROM audit_log "
                            "WHERE tenant_id = :t AND action LIKE 'company_dna_%'"
                        ),
                        {"t": tenant},
                    )
                    return [row[0] for row in rows.all()]

    trail = asyncio.run(overrides())
    assert trail, "no audit row was written for a Company DNA mutation"
    assert any(entry.get("super_admin_override") is True for entry in trail)
    assert all(entry.get("agent") == "bodha" for entry in trail)


# ── Tenant isolation ─────────────────────────────────────────────────────────


#: The four read paths, as `/clients/{id}/company-dna...` suffixes.
_READ_SUFFIXES = ("", "/versions", "/versions/1", "/status")


@pytest.mark.parametrize(
    "role", [Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager]
)
def test_a_cross_tenant_read_looks_exactly_like_a_client_that_does_not_exist(
    client: Caller, role: Role
) -> None:
    """RBAC §33, stated as the property that actually matters.

    "Cross-tenant reads are 404" is the right rule for a route the caller could
    otherwise use, and `test_a_cross_tenant_read_by_an_authorised_role_is_404`
    below asserts exactly that. It is the wrong rule for a route the caller can
    never use: a Recruiter asking for a version list is refused for lacking
    authorship, and they are refused identically for their OWN client, so the
    403 says nothing about whether the other client exists.

    The property that covers both is INDISTINGUISHABILITY: the response to a
    real other tenant must be byte-identical to the response for a client id
    that has never existed. A 403 that leaked existence would differ from the
    made-up id's answer, and this catches that without forcing an authorization
    refusal to lie about which check failed.
    """
    client.sign_in(role, client.world.tenant_a)
    real_other = client.world.tenant_b
    never_existed = uuid.uuid4()
    for suffix in _READ_SUFFIXES:
        theirs = client.get(f"{_dna(client, real_other)}{suffix}")
        nobodys = client.get(f"{_dna(client, never_existed)}{suffix}")
        assert (theirs.status_code, theirs.json()) == (
            nobodys.status_code,
            nobodys.json(),
        ), (role, suffix)


@pytest.mark.parametrize("role", [Role.client, Role.hr_manager])
def test_a_cross_tenant_read_by_an_authorised_role_is_404(
    client: Caller, role: Role
) -> None:
    """The strict form, for the roles that hold every capability involved.

    These two could read all four routes for their own client, so nothing but
    the tenant boundary is refusing them here, and it has to refuse with 404.
    """
    client.sign_in(role, client.world.tenant_a)
    other = client.world.tenant_b
    for suffix in _READ_SUFFIXES:
        response = client.get(f"{_dna(client, other)}{suffix}")
        assert response.status_code == 404, (role, suffix, response.status_code)


def test_a_cross_tenant_write_is_404_and_never_403(client: Caller) -> None:
    client.sign_in(Role.hr_manager, client.world.tenant_a)
    other = client.world.tenant_b
    assert client.post(_dna(client, other), json={}).status_code == 404
    assert (
        client.post(
            f"{_dna(client, other)}/{uuid.uuid4()}/messages",
            json={"question_key": "decider", "answer": "A panel"},
        ).status_code
        == 404
    )


def test_a_row_id_from_another_tenant_is_404(client: Caller) -> None:
    """Direct object reference protection, on the row rather than on the path.

    A caller who knows a real Company DNA id from another client and puts their
    OWN client id in the path must still get nothing.
    """
    client.sign_in(Role.hr_manager, client.world.tenant_b)
    theirs = client.get(_dna(client, client.world.tenant_b)).json()
    stolen = theirs["session"]["id"] if theirs["session"] else None
    if stolen is None:
        opened = client.post(_dna(client, client.world.tenant_b), json={})
        stolen = opened.json()["id"]

    client.sign_in(Role.hr_manager, client.world.tenant_a)
    response = client.post(
        f"{_dna(client)}/{stolen}/messages",
        json={"question_key": "decider", "answer": "A panel"},
    )
    assert response.status_code == 404


# ── Internal BD staff: status only ───────────────────────────────────────────


def test_bd_staff_see_completion_status_and_a_version_and_nothing_else(
    client: Caller,
) -> None:
    """D3's tightest cell.

    BD needs to know whether a customer they are onboarding has finished. They
    must never see what the customer said, what it compiled to, or who wrote
    it.
    """
    client.sign_in(Role.bd)
    response = client.get(f"{_dna(client)}/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "client_id",
        "status",
        "version",
        "completed_at",
        "draft_open",
    }
    assert body["status"] == "complete"
    assert body["version"] >= 1
    raw = json.dumps(body)
    for statement in ANSWERS["observable_behaviours"].splitlines():
        assert statement not in raw
    assert ANSWERS["headcount_growth_stage"] not in raw


def test_bd_staff_are_refused_every_route_that_carries_content(
    client: Caller,
) -> None:
    """Cross-tenant reach by internal staff is a tenant-isolation violation.

    Refused by the ORG-audience requirement on `get_tenant_db`, which is
    structural: there is no content route in this router a platform token can
    reach, whichever client id it puts in the path.
    """
    client.sign_in(Role.bd)
    for tenant in (client.world.tenant_a, client.world.tenant_b):
        assert client.get(_dna(client, tenant)).status_code == 403
        assert client.get(f"{_dna(client, tenant)}/versions").status_code == 403
        assert client.get(f"{_dna(client, tenant)}/versions/1").status_code == 403
        assert client.post(_dna(client, tenant), json={}).status_code == 403


def test_a_bd_status_read_is_audited(client: Caller, sessions) -> None:
    """Platform staff touching client data leaves a row, every time."""
    client.sign_in(Role.bd)
    client.get(f"{_dna(client, client.world.tenant_b)}/status")

    async def reads() -> int:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(
                        sa.text(
                            "SELECT count(*) FROM audit_log "
                            "WHERE action = 'company_dna_status_read'"
                        )
                    )
                    return int(result.scalar_one())

    assert asyncio.run(reads()) >= 1


# ── The audited numeric view (spec-doc6 D8) ──────────────────────────────────


def test_the_numeric_configuration_is_opt_in_and_audited(
    client: Caller, sessions
) -> None:
    client.sign_in(Role.hr_manager)
    plain = client.get(f"{_dna(client)}/versions/1").json()
    assert plain["configuration"] is None

    detailed = client.get(f"{_dna(client)}/versions/1?configuration=true").json()
    assert detailed["configuration"]["weight_modifiers"]

    async def reads() -> int:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(
                        sa.text(
                            "SELECT count(*) FROM audit_log "
                            "WHERE action = 'company_dna_configuration_read'"
                        )
                    )
                    return int(result.scalar_one())

    assert asyncio.run(reads()) >= 1


# ── The Layer 2 binding a frozen scorecard will reference ────────────────────


def test_a_job_can_be_bound_to_the_company_dna_version_it_was_frozen_against(
    client: Caller, sessions
) -> None:
    """spec-doc6 §4.2: every job references the exact version in force when its
    scorecard was frozen.

    The binding is written by the job-setup flow, which is a later phase. What
    this phase owes is a place to put it that answers the question correctly
    when it is asked years later, so what is tested here is the SHAPE: a second
    freeze appends rather than overwriting, and the older row still names the
    version it was frozen against.
    """
    tenant = client.world.tenant_a
    job_id = uuid.uuid4()

    async def bind() -> list[tuple[int, int]]:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                            "VALUES (:id, :t, 'Engineering Manager', '{}'::jsonb, 'draft')"
                        ),
                        {"id": job_id, "t": tenant},
                    )
                    versions = (
                        await session.execute(
                            sa.text(
                                "SELECT id, version FROM company_dna "
                                "WHERE tenant_id = :t ORDER BY version"
                            ),
                            {"t": tenant},
                        )
                    ).all()
                    assert len(versions) >= 2, "this test needs two versions"
                    for sequence, (dna_id, version) in enumerate(versions[:2], start=1):
                        await session.execute(
                            sa.text(
                                "INSERT INTO job_company_dna_bindings "
                                "(id, tenant_id, job_id, company_dna_id, "
                                " company_dna_version, freeze_sequence, "
                                " scorecard_version, frozen_at) VALUES "
                                "(:row, :t, :job, :dna, :v, :seq, :seq, now())"
                            ),
                            {
                                "row": uuid.uuid4(),
                                "t": tenant,
                                "job": job_id,
                                "dna": dna_id,
                                "v": version,
                                "seq": sequence,
                            },
                        )
                    rows = (
                        await session.execute(
                            sa.text(
                                "SELECT freeze_sequence, company_dna_version FROM "
                                "job_company_dna_bindings WHERE job_id = :job "
                                "ORDER BY freeze_sequence"
                            ),
                            {"job": job_id},
                        )
                    ).all()
                    return [(int(r[0]), int(r[1])) for r in rows]

    history = asyncio.run(bind())
    assert len(history) == 2, "the second freeze overwrote the first"
    assert history[0][0] == 1 and history[1][0] == 2
    assert history[0][1] != history[1][1], (
        "both freezes recorded the same Company DNA version, so the history "
        "cannot answer which philosophy the first one was locked against"
    )


def test_two_freezes_cannot_share_a_sequence_number(client: Caller, sessions) -> None:
    """The uniqueness that makes the sequence a history rather than a bag."""
    tenant = client.world.tenant_a
    job_id = uuid.uuid4()

    async def duplicate() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                            "VALUES (:id, :t, 'Payments Lead', '{}'::jsonb, 'draft')"
                        ),
                        {"id": job_id, "t": tenant},
                    )
                    # Its own artifact row, so the test stands alone rather
                    # than depending on an earlier one having completed a
                    # version for this tenant.
                    # Its own artifact row, so the test stands alone rather
                    # than depending on an earlier one having completed a
                    # version for this tenant. The id is supplied because this
                    # schema is built from the ORM metadata, which carries a
                    # Python-side default rather than the migration's
                    # `DEFAULT gen_random_uuid()`.
                    dna_id = uuid.uuid4()
                    await session.execute(
                        sa.text(
                            "INSERT INTO company_dna (id, tenant_id, version, "
                            "status) VALUES (:id, :t, 900, 'superseded')"
                        ),
                        {"id": dna_id, "t": tenant},
                    )
                    for _ in range(2):
                        await session.execute(
                            sa.text(
                                "INSERT INTO job_company_dna_bindings "
                                "(id, tenant_id, job_id, company_dna_id, "
                                " company_dna_version, freeze_sequence) VALUES "
                                "(:row, :t, :job, :dna, 1, 1)"
                            ),
                            {
                                "row": uuid.uuid4(),
                                "t": tenant,
                                "job": job_id,
                                "dna": dna_id,
                            },
                        )

    with pytest.raises(Exception) as caught:
        asyncio.run(duplicate())
    assert "uq_job_company_dna_binding_sequence" in str(caught.value)


# ── The migration is additive, and this is the proof ─────────────────────────
#
# A rolling deploy runs the previous image and this one against this schema at
# the same time, so "additive" is not a description of intent: it is a property
# of the statements. This reads them and checks it, because a migration whose
# safety lives only in a docstring is a migration somebody widens without
# re-reading the docstring.
#
# Needs no database, so it runs wherever the suite runs.

import ast as _ast
import re as _re

_ALLOWED_STATEMENT = _re.compile(
    r"^\s*(CREATE\s+TABLE|CREATE\s+UNIQUE\s+INDEX|CREATE\s+INDEX|CREATE\s+POLICY"
    r"|CREATE\s+OR\s+REPLACE\s+FUNCTION|CREATE\s+TRIGGER|INSERT\s+INTO"
    r"|ALTER\s+TABLE\s+\S+\s+(ENABLE|FORCE)\s+ROW\s+LEVEL\s+SECURITY)",
    _re.IGNORECASE,
)

#: Statement shapes that break a rolling deploy, each with what it breaks.
_FORBIDDEN = {
    r"\bDROP\s+(TABLE|COLUMN|CONSTRAINT|INDEX)\b": (
        "the previous image still reads it"
    ),
    r"\bALTER\s+COLUMN\b": "a type or nullability change is not additive",
    r"\bRENAME\b": "the previous image still writes the old name",
    r"\bADD\s+COLUMN\b[^;]*\bNOT\s+NULL\b(?![^;]*\bDEFAULT\b)": (
        "an existing row has no value for it"
    ),
    r"\bTRUNCATE\b": "it destroys rows the previous image is serving",
    r"\bUPDATE\s+\w+\s+SET\b": "it rewrites rows the previous image wrote",
}


def _upgrade_statements() -> list[str]:
    """Every SQL string `upgrade()` passes to `op.execute`, from the AST.

    Read from the source rather than by running it: running it needs a
    database, and the property being checked is a property of the text.
    """
    tree = _ast.parse(MIGRATION.read_text(encoding="utf-8"))
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, _ast.FunctionDef) and node.name == "upgrade"
    )
    constants = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, _ast.Assign)
        and isinstance(node.targets[0], _ast.Name)
        and isinstance(node.value, _ast.Constant)
        and isinstance(node.value.value, str)
    }
    found: list[str] = []
    for node in _ast.walk(upgrade):
        if not isinstance(node, _ast.Call):
            continue
        target = node.func
        if not (isinstance(target, _ast.Attribute) and target.attr == "execute"):
            continue
        argument = node.args[0]
        if isinstance(argument, _ast.Constant) and isinstance(argument.value, str):
            found.append(argument.value)
        elif isinstance(argument, _ast.Name) and argument.id in constants:
            found.append(constants[argument.id])
        elif isinstance(argument, _ast.JoinedStr):
            # An f-string, RENDERED rather than stripped. Its interpolations are
            # table names from module constants, and dropping them would leave
            # `ALTER TABLE  ENABLE ROW LEVEL SECURITY`, which no check below
            # could then attribute to a table.
            rendered = []
            for part in argument.values:
                if isinstance(part, _ast.Constant):
                    rendered.append(str(part.value))
                elif isinstance(part, _ast.FormattedValue) and isinstance(
                    part.value, _ast.Name
                ):
                    rendered.append(
                        str(getattr(MIGRATION_MODULE, part.value.id, part.value.id))
                    )
                else:
                    rendered.append(_ast.unparse(part))
            found.append("".join(rendered))
        else:
            found.append(_ast.unparse(argument))
    return found


def test_the_migration_statements_were_actually_read() -> None:
    """A sweep over an empty list passes forever."""
    statements = _upgrade_statements()
    assert len(statements) >= 6, statements
    assert any("CREATE TABLE" in s for s in statements)
    assert any("CREATE TRIGGER" in s for s in statements)
    assert any("INSERT INTO role_permissions" in s for s in statements)


def test_every_statement_in_the_migration_is_an_additive_shape() -> None:
    unexpected = [
        statement.strip()[:80]
        for statement in _upgrade_statements()
        if not _ALLOWED_STATEMENT.match(statement)
    ]
    assert not unexpected, (
        "these statements are not one of the shapes this migration claims to "
        f"be made of: {unexpected}"
    )


@pytest.mark.parametrize("pattern,why", sorted(_FORBIDDEN.items()))
def test_the_migration_contains_no_statement_that_breaks_a_rolling_deploy(
    pattern: str, why: str
) -> None:
    body = "\n".join(_upgrade_statements())
    match = _re.search(pattern, body, _re.IGNORECASE)
    assert match is None, f"{match.group(0) if match else ''}: {why}"


def test_the_migration_touches_exactly_two_existing_tables() -> None:
    """`company_dna` gains a trigger and `role_permissions` gains rows.

    Both are safe for a reason worth stating rather than assuming: NOTHING in
    the previous image writes `company_dna` at all, so a BEFORE UPDATE trigger
    on it cannot refuse a statement that image issues. And the previous image
    resolves capabilities by iterating its own list and looking each one up, so
    two rows naming capabilities it has never heard of are read into a
    dictionary and never consulted.
    """
    body = "\n".join(_upgrade_statements()).lower()
    existing = {
        table
        for table in ("jobs", "tenants", "users", "profiles", "candidates",
                      "job_candidate_links", "functional_skills_reports",
                      "evaluations", "audit_log", "company_dna",
                      "role_permissions")
        if _re.search(
            r"\b(alter table|insert into|update|drop table"
            r"|create trigger [a-z_]+ before update on"
            r"|create policy [a-z_]+ on)\s+" + table + r"\b",
            body,
        )
    }
    assert existing == {"company_dna", "role_permissions"}, existing
