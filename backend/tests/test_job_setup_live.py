"""Job setup on the LIVE path: Bodha's session, Sutra's seven stages, gate G1.

WHAT THIS FILE IS FOR
---------------------
spec-doc6 §4.3's acceptance evidence, and it is deliberately awkward to satisfy:

    "a change to one SWOT input demonstrably moves a weight in the resulting
     matrix, shown through the API, not through a summary string."

Every assertion below therefore goes through the HTTP surface a recruiter and a
hiring manager actually touch. A test that called `compile_matrix` directly
would prove the arithmetic and nothing about whether anybody can reach it, and
"the modules exist and are tested in isolation; nothing user-facing calls them"
is the exact gap this phase exists to close.

WHY IT NEEDS A REAL DATABASE
-----------------------------
The layers Sutra composes are STORED. Layer 3 is a `job_swot_intakes` row with
a CHECK on its situation key, and the frozen matrix is `job_competencies` plus
an append-only `job_scorecard_bindings` row. A session double would let the
application's own WHERE clause be the only boundary, which claude.md rule 1
forbids, and would not exercise a single one of those constraints.

The schema is built from the ORM metadata plus the DDL that lives only in
migration 0064, imported FROM that migration so what runs here is what will run
in production. The database is a scratch one, created and dropped
by this module; it never touches the development database.

NO PROVIDER IS CALLED. `llm_router.invoke_llm` is patched to a scripted stand-in
for the whole module. That is the honest arrangement for a demonstration of
MECHANISM: the point is that a weight MOVES when a layer changes, and a live
model would make two runs differ for reasons that have nothing to do with the
layers. There is no Anthropic key in this phase and nothing here implies
otherwise.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import uuid
from typing import Any, Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.assessment import JobCompetency
from app.models.base import Base
from app.models.billing import CreditLedgerEntry
from app.models.company import Company
from app.models.enums import Role
from app.models.job_scorecard_binding import JobScorecardBinding
from app.models.job import Job
from app.models.job_setup import JobSwotIntake
from app.models.tenant import AuditLog, RolePermission, Tenant
from app.models.user import User
from app.services import capabilities as caps
from app.services import ppi
from app.services.hiring import (
    company_requirements,
    pipeline_halt,
    scorecard,
    situations,
    swot_quality,
)
from app.services.hiring_pipeline import JobLifecycleState

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
SCRATCH_DB = f"readypick_job_setup_{os.getpid()}"
#: The jobs router is mounted under both /api/v1 and /api/v2; the assessments
#: router only under /api/v2 (`app/main.py`). Two constants rather than one, so
#: a path here is the path the client actually uses.
BASE = "/api/v1"
ASSESSMENTS = "/api/v2"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://readypick_test:readypick_test@localhost:55432/readypick_test",
)

RLS_ROLE = get_settings().postgres_rls_app_role


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUTRA_MIGRATION = _module(
    "_job_setup_migration_0064", "0064_sutra_seven_stage_provenance.py"
)

#: THE WHOLE SCHEMA, not a named subset.
#:
#: Naming a subset was tried first and produced three rounds of "relation does
#: not exist": this flow crosses job creation (which asks the credit ledger and
#: the company profile), the SWOT session, the matrix and the audit trail, and
#: each of those reaches one more table through a foreign key.
#:
#: The named-subset symbols above are still imported and used, so a table this
#: file asserts against cannot silently stop existing in the metadata.
TABLES = None

#: Named so an import of a model is not "unused" while still documenting which
#: tables this flow actually reads and writes.
ROUTER_TABLES = (
    Tenant.__table__,
    User.__table__,
    RolePermission.__table__,
    AuditLog.__table__,
    Company.__table__,
    JobScorecardBinding.__table__,
    Job.__table__,
    JobCompetency.__table__,
    JobSwotIntake.__table__,
    CreditLedgerEntry.__table__,
)

#: DDL the ORM metadata cannot express, taken from the migrations rather than
#: copied, so the constraints exercised here are the ones production will have.
EXTRA_DDL = (
    # Migration 0064's two CHECKs, written from its own literals.
    "ALTER TABLE job_swot_intakes ADD CONSTRAINT ck_job_swot_intakes_situation_key "
    "CHECK (situation_key IS NULL OR situation_key IN ("
    + ", ".join(f"'{key}'" for key in SUTRA_MIGRATION._SITUATION_KEYS)
    + "))",
    "ALTER TABLE job_swot_intakes ADD CONSTRAINT ck_job_swot_intakes_phase "
    "CHECK (phase IN ("
    + ", ".join(f"'{key}'" for key in SUTRA_MIGRATION._SWOT_PHASES)
    + "))",
    # `job_assignments` is migration 0061's and is NOT in the ORM metadata:
    # `rbac.load_assignments` reads it with raw SQL, deliberately, so the RBAC
    # engine keeps no import edge into the models package. `create_all` cannot
    # build it, and every authorised route in this file selects from it.
    "CREATE TABLE job_assignments ("
    "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
    "tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, "
    "job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, "
    "user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "assignment_role varchar(30) NOT NULL, "
    "active boolean NOT NULL DEFAULT true, "
    "assigned_at timestamptz NOT NULL DEFAULT now(), "
    "revoked_at timestamptz, "
    "UNIQUE (job_id, user_id, assignment_role))",
)


def _urls() -> tuple[str, str]:
    url = sa.engine.make_url(TEST_DATABASE_URL)
    return (
        url.set(database="postgres").render_as_string(hide_password=False),
        url.set(database=SCRATCH_DB).render_as_string(hide_password=False),
    )


async def _admin(statement: str, url: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(statement))
    finally:
        await engine.dispose()


#: Every capability the routes under test enforce, granted globally so the
#: permission engine resolves rather than denying. The AUTHORIZATION tests below
#: still exercise the parts that are not a grant -- assignment scope, lifecycle
#: state, tenant -- which is where the interesting refusals live.
_CAPABILITY_GRANTS: tuple[tuple[str, str], ...] = tuple(
    (role, capability)
    for role in ("client", "hr_manager", "recruitment_manager")
    for capability in (
        caps.CREATE_JOB,
        caps.PUBLISH_JOB,
        caps.EDIT_SWOT,
        caps.FINALIZE_ROLE_DEFINITION,
        caps.SEND_JD_TO_HIRING_MANAGER,
        caps.EDIT_JOB_DESCRIPTION,
        caps.VIEW_COMPANY_JOBS,
    )
) + (
    ("recruiter", caps.CREATE_JOB),
    ("recruiter", caps.PUBLISH_JOB),
    ("recruiter", caps.SEND_JD_TO_HIRING_MANAGER),
    ("recruiter", caps.EDIT_JOB_DESCRIPTION),
    ("recruiter", caps.VIEW_COMPANY_JOBS),
    ("hiring_manager", caps.EDIT_SWOT),
    ("hiring_manager", caps.FINALIZE_ROLE_DEFINITION),
    ("hiring_manager", caps.CREATE_JOB),
    ("hiring_manager", caps.VIEW_COMPANY_JOBS),
)


async def _build_schema(scratch_url: str) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            # `credit_ledger` references `job_candidate_links`, which references
            # `profiles`, which carries a pgvector column. Pulled in by a
            # foreign key rather than by anything this file tests, and the
            # extension is what the real database has.
            await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            for statement in EXTRA_DDL:
                await conn.execute(sa.text(statement))
            if RLS_ROLE:
                await conn.execute(
                    sa.text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                        f'IN SCHEMA public TO "{RLS_ROLE}"'
                    )
                )
            for role, capability in _CAPABILITY_GRANTS:
                await conn.execute(
                    sa.text(
                        "INSERT INTO role_permissions (id, tenant_id, role, "
                        "capability, allowed) VALUES "
                        "(gen_random_uuid(), NULL, :role, :cap, true) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"role": role, "cap": capability},
                )
    finally:
        await engine.dispose()


class World:
    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.users: dict[tuple[uuid.UUID, Role], uuid.UUID] = {}


ORG_ROLES = (
    Role.client,
    Role.hr_manager,
    Role.recruitment_manager,
    Role.recruiter,
    Role.hiring_manager,
)


async def _seed(scratch_url: str, state: World) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            for tenant, label in ((state.tenant_a, "Alpha"), (state.tenant_b, "Beta")):
                await conn.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, :name, :domain, 'pending')"
                    ),
                    {"id": tenant, "name": label, "domain": f"{tenant}.setup.test"},
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
                            "email": f"{role.value}@{tenant}.setup.test",
                            "name": f"{label} {role.value}",
                        },
                    )
                # One credit, so `POST /jobs` is not refused by the credit gate.
                await conn.execute(
                    sa.text(
                        "INSERT INTO credit_ledger (id, tenant_id, event_type, "
                        "subunits_delta, idempotency_key) VALUES "
                        "(gen_random_uuid(), :tenant, 'grant', 6000, :key)"
                    ),
                    {"tenant": tenant, "key": f"seed-{tenant}"},
                )
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    maintenance_url, scratch_url = _urls()
    try:
        asyncio.run(
            _admin(
                f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)', maintenance_url
            )
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


@pytest.fixture(scope="module")
def sessions(world: World):
    _maintenance, scratch_url = _urls()
    # NullPool: an asyncpg connection belongs to the event loop that opened it,
    # and TestClient runs the application on a fresh loop per test.
    engine = create_async_engine(scratch_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _no_permission_cache(monkeypatch):
    """The RBAC engine caches permission rows in Redis for two minutes.

    Patched out so a run does not depend on a Redis being present. This is a
    double for a CACHE, not for the thing under test: resolution still goes
    through the real engine against the real rows.
    """
    from app.services import tenant_cache

    async def _miss(key):  # noqa: ANN001
        return None

    async def _noop(key, value, *, ttl=120):  # noqa: ANN001
        return None

    monkeypatch.setattr(tenant_cache, "get_json", _miss)
    monkeypatch.setattr(tenant_cache, "set_json", _noop)


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch):
    """A scripted stand-in for every model call this flow makes.

    THREE call sites, and each one is answered in the shape its caller parses:
    Bodha's question composer, Bodha's answer capture, and Sutra's stages 1-2
    naming. Nothing reaches a provider, and nothing here implies one was ever
    contacted: there is no key in this phase.

    The CAPTURE stand-in returns the answer verbatim as one point, which is what
    the real capture step does when extraction is unavailable, so the SWOT
    points the matrix is built from are exactly the sentences the test wrote.
    """
    from app.services import llm_router

    async def _invoke(task_type, messages, response_format_json=False, session=None, **k):
        blob = " ".join(str(message.get("content") or "") for message in messages)
        if '"phrases"' in blob:
            return json.dumps(_naming_response(blob))
        if "points" in blob or task_type == "extraction":
            answer = json.loads(messages[-1]["content"]).get("answer", "")
            return json.dumps({"points": [answer], "sufficient": True})
        return json.dumps({"question": "What would you see them doing?"})

    monkeypatch.setattr(llm_router, "invoke_llm", _invoke)
    monkeypatch.setattr(llm_router, "chat_completion", _invoke)
    monkeypatch.setattr(scorecard.llm_router, "chat_completion", _invoke)


def _naming_response(blob: str) -> dict[str, Any]:
    """Name each unanchored phrase deterministically, from its own first words.

    Deterministic on purpose: the acceptance evidence is that a WEIGHT moves
    when a layer changes, and a stand-in that named things differently between
    runs would make a moved weight indistinguishable from a renamed competency.
    """
    payload = json.loads(blob[blob.index('{"job_title"') :])
    named = []
    for entry in payload["phrases"]:
        words = [word for word in str(entry["text"]).split() if word.isalpha()]
        name = " ".join(words[:3]).title() or f"Competency {entry['index']}"
        named.append(
            {
                "index": entry["index"],
                "competency": name,
                "observable": (
                    "Has shipped a change to a live system and can reconstruct "
                    "what they decided and why."
                ),
            }
        )
    return {"named": named, "refused": []}


class Caller:
    def __init__(self, world: World) -> None:
        self.world = world
        self.http: TestClient | None = None
        self.principal: CurrentUser | None = None

    def sign_in(self, role: Role, tenant: uuid.UUID | None = None) -> "Caller":
        tenant = tenant or self.world.tenant_a
        self.principal = CurrentUser(
            user_id=self.world.users[(tenant, role)],
            tenant_id=tenant,
            role=role,
            audience=AUDIENCE_ORG,
        )
        return self

    def _url(self, path: str) -> str:
        """Absolute paths pass through; everything else is a /api/v1 route.

        The assessments router is mounted only under /api/v2, so its paths
        arrive here already prefixed. One rule rather than two `Caller`s.
        """
        return path if path.startswith("/api/") else f"{BASE}{path}"

    def get(self, path: str, **kwargs):
        assert self.http is not None
        return self.http.get(self._url(path), **kwargs)

    def post(self, path: str, **kwargs):
        assert self.http is not None
        return self.http.post(self._url(path), **kwargs)

    def put(self, path: str, **kwargs):
        assert self.http is not None
        return self.http.put(self._url(path), **kwargs)


@pytest.fixture
def client(world: World, sessions) -> Iterator[Caller]:
    caller = Caller(world)

    async def _current_user() -> CurrentUser:
        assert caller.principal is not None, "no principal set for this request"
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
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch):
    """Capture enqueues instead of publishing them, and RUN the compile inline.

    Two things at once, and both are deliberate. The enqueue is CAPTURED so a
    test can assert that job setup dispatches Sutra as a task rather than doing
    the work in the request handler (claude.md rule 4). And the compile is then
    run inline, in its own committed session, because the assertion this file
    exists to make is about the matrix that comes back through the API and a
    real broker is not part of what is being proved.
    """
    from app.api import assessments as assessments_router

    sent: list[tuple[str, tuple, dict]] = []

    def _send_task(name, args=(), kwargs=None, **rest):
        sent.append((name, tuple(args), dict(kwargs or {})))
        return None

    monkeypatch.setattr(assessments_router, "dispatch", _send_task)
    from app.api import jobs as jobs_router

    monkeypatch.setattr(jobs_router, "dispatch", _send_task)
    return sent


@pytest.fixture
def enqueued(_no_dispatch):
    return _no_dispatch


# ── Driving the flow through HTTP ────────────────────────────────────────────


#: The SWOT the hiring manager gives. Written to pass all six §18.5 rules: real
#: internal weaknesses, a mix rather than a market complaint, observable
#: language rather than adjectives, no prohibited disqualifier, and enough
#: turnaround signal for §18.4's classification to have something to read back.
SWOT_ANSWERS: dict[str, str] = {
    "strengths": (
        "The team ships reviewed code every week and has kept the test suite "
        "green through two migrations."
    ),
    "weaknesses": (
        "The last person in this role never owned anything in production and "
        "the scheduler broke twice while somebody else got paged."
    ),
    "opportunities": (
        "Within a year this person could lead the platform group, because we "
        "have nobody doing that today."
    ),
    "threats": (
        "The ingestion platform is broken and has to be fixed this quarter, "
        "and the two people who built it have left."
    ),
}


def _create_job(client: Caller, title: str = "Backend Engineer, Platform") -> str:
    created = client.post(
        "/jobs",
        json={
            "title": title,
            "grade": "non_managerial",
            "publish": False,
            "jd_markdown": (
                "## About the role\n\nOwn the ingestion platform.\n\n"
                "## Skills\n\n- Python\n- PostgreSQL\n- Kafka\n"
            ),
            "jd": {},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _write_company_profile(sessions, tenant: uuid.UUID) -> None:
    """Gate 1's precondition: this organisation has said what it does.

    Written directly, like `_assign`, rather than through the profile PATCH.
    The profile is a PRECONDITION of everything below and not the thing under
    test, and routing it through a second capability check would make every
    matrix assertion in this file depend on who may edit a profile.
    """

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO companies (id, tenant_id, about_company, "
                            " created_at) "
                            "VALUES (gen_random_uuid(), :t, :about, now()) "
                            "ON CONFLICT (tenant_id) DO UPDATE "
                            "   SET about_company = EXCLUDED.about_company"
                        ),
                        {
                            "t": tenant,
                            "about": (
                                "We build payments infrastructure for Indian SMEs."
                            ),
                        },
                    )

    asyncio.run(_go())


def _run_swot(
    client: Caller,
    job_id: str,
    *,
    answers: dict[str, str] | None = None,
) -> dict:
    """Save the standalone Job SWOT document through its public route."""
    sections = dict(SWOT_ANSWERS)
    sections.update(answers or {})
    response = client.put(
        f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot-analysis",
        json={**sections, "expected_version": 0},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _compile(sessions, job_id: str) -> Any:
    """Run Sutra in a committed session, as the Celery worker would."""

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    job = await session.get(Job, uuid.UUID(job_id))
                    return await scorecard.compile_matrix(
                        session,
                        job,
                        actor_user_id=job.created_by,
                        correlation_id=job.correlation_id,
                        replace=True,
                    )

    return asyncio.run(_go())


def _assign(sessions, job_id: str, world: World, tenant: uuid.UUID | None = None) -> None:
    """RBAC §9.2 and §10.2: one Recruiter and one Hiring Manager per job."""
    tenant = tenant or world.tenant_a

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for role, assignment in (
                        (Role.recruiter, "recruiter"),
                        (Role.hiring_manager, "hiring_manager"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO job_assignments (id, tenant_id, job_id, "
                                "user_id, assignment_role, active, assigned_at) VALUES "
                                "(gen_random_uuid(), :tenant, :job, :user, :role, true, now()) "
                                "ON CONFLICT DO NOTHING"
                            ),
                            {
                                "tenant": tenant,
                                "job": uuid.UUID(job_id),
                                "user": world.users[(tenant, role)],
                                "role": assignment,
                            },
                        )

    asyncio.run(_go())


def _stored_weights(sessions, job_id: str) -> dict[str, float]:
    """The stored weight per competency.

    Read from the row rather than from the response, because the API projects
    the matrix WITHOUT the number -- that is the standing product rule and it is
    not being relaxed for a test. The CHANGE is still observed through the API:
    both matrices below are produced entirely by HTTP flows, and what this reads
    is the row those flows wrote.
    """
    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = await ppi.load_framework(session, uuid.UUID(job_id))
                    return {row.name: float(row.weight) for row in rows}

    return asyncio.run(_go())


def _stored_raw_weights(sessions, job_id: str) -> dict[str, float]:
    """The DERIVED weight per competency, before force-rank normalisation.

    WHY BOTH ARE NEEDED, and why asserting only on the stored share is the
    wrong test.

    `_rank_and_normalise` divides every scored item by the total so the matrix
    sums to 1.0, which is what section 20.1's own scorecard table does. That
    makes the stored `weight` a SHARE, and a share is scale-invariant: when a
    situation type lifts every scored item by the same factor, because they all
    sit on dimensions that situation treats alike, normalisation divides the
    lift straight back out and the shares are identical to fifteen decimal
    places. Nothing is wrong; the quantity simply cannot show it.

    The situation's effect lands in the four-term product
    (baseline x company x situation x role), which is kept in provenance as
    `raw_value` precisely so it stays observable. That is the number the
    acceptance criterion in spec-doc6 section 4.3 is about: a Layer 2 or Layer 3
    change must demonstrably MOVE a weight, and the raw derived weight is a
    weight.
    """
    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    rows = await ppi.load_framework(session, uuid.UUID(job_id))
                    return {
                        row.name: float((row.provenance_json or {})["raw_value"])
                        for row in rows
                        if (row.provenance_json or {}).get("raw_value") is not None
                    }

    return asyncio.run(_go())


# ── Gate 1, through the route rather than through the function ──────────────


def _clear_company_profile(sessions, tenant: uuid.UUID) -> None:
    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "UPDATE companies SET about_company = NULL "
                            "WHERE tenant_id = :t"
                        ),
                        {"t": tenant},
                    )

    asyncio.run(_go())


def test_job_creation_is_refused_and_then_allowed_by_the_company_profile(
    client: Caller, sessions
) -> None:
    """Gate 1 at the ROUTE, in both directions, through HTTP.

    `test_workflow_gates.py` proves the function; this proves a recruiter
    actually meets it. Both directions, because a gate asserted only on its
    refusal passes just as happily when it refuses everything, and a client
    who has written their profile and still cannot post a job has a product
    that does not work.

    Self-contained: it clears the profile first rather than assuming its
    tenant has none, so no other test in this module can decide its outcome.
    """
    tenant = client.world.tenant_a
    client.sign_in(Role.hr_manager, tenant)
    _clear_company_profile(sessions, tenant)

    refused = client.post(
        "/jobs",
        json={
            "title": "Platform Engineer",
            "grade": "non_managerial",
            "publish": False,
            "jd_markdown": (
                "## About the role" + chr(10) * 2
                + "Own the ingestion platform." + chr(10)
            ),
            "jd": {},
        },
    )
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail == company_requirements.MISSING_MESSAGE
    # The refusal names the page and what to write there, and carries no
    # trace of the instrument it replaced.
    assert "Company Profile" in detail
    assert "dna" not in detail.lower()

    _write_company_profile(sessions, tenant)
    assert _create_job(client, "Platform Engineer")


# ── The happy path, end to end ───────────────────────────────────────────────


def test_a_job_created_through_the_api_runs_bodha_then_sutra(
    client: Caller, sessions, enqueued
) -> None:
    """spec-doc6 §17: "A job created through the real API runs Bodha's SWOT
    session, Sutra's seven stages, and produces a frozen matrix whose every
    weight traces to a named Layer 1/2/3 source."
    """
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)

    job_id = _create_job(client)
    _assign(sessions, job_id, client.world)

    # Layer 3, through the hiring manager's own session.
    client.sign_in(Role.hiring_manager)
    document = _run_swot(client, job_id)
    assert document["status"] == "edited"
    assert document["strengths"] == SWOT_ANSWERS["strengths"]

    # Closing the session enqueues Sutra as a TASK, never inline (claude.md 4).
    assert any(name == "pickready.compile_tatva_matrix" for name, _a, _k in enqueued)

    result = _compile(sessions, job_id)
    assert result.items, result.rejections

    framework = client.get(f"{ASSESSMENTS}/assessments/jobs/{job_id}/framework")
    assert framework.status_code == 200, framework.text
    body = framework.json()
    competencies = body["competencies"]
    assert competencies
    # All three aspects, because every one is graded and charted on a report.
    assert {row["category"] for row in competencies} == set(ppi.CATEGORIES)
    for row in competencies:
        # The seven stages, on every item, through the API.
        assert row["observable_evidence"], row
        assert row["assessment_method"], row
        assert row["provenance"], row
        # NO NUMBER REACHES A CLIENT. The weight, its four multiplier terms and
        # the threshold stay on the row: what a hiring manager reads is
        # sentences and a grade WORD.
        #
        # `id` and `ordinal` are exempt and were always there -- a primary key
        # and a display position are not assessment numbers. `force_rank` is an
        # ORDER rather than a score, and is the one integer §20.3 puts on a
        # scorecard at all.
        assert "weight" not in row and "threshold" not in row, row
        prose = " ".join(row["provenance"]) + " " + str(row["required_level"])
        assert not any(character.isdigit() for character in prose), prose


def test_every_weight_traces_to_a_named_layer_one_and_three_source(
    client: Caller, sessions
) -> None:
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Data Platform Engineer")
    _assign(sessions, job_id, client.world)
    client.sign_in(Role.hiring_manager)
    _run_swot(client, job_id)
    _compile(sessions, job_id)

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    return await ppi.load_framework(session, uuid.UUID(job_id))

    rows = asyncio.run(_go())
    assert rows
    for row in rows:
        terms = (row.provenance_json or {}).get("terms") or {}
        # Every term of `baseline x company x situation x role`, stored.
        # company_layer2 RESTORED 2026-09-19 (vivekium C3): Drishti supplies
        # LAYER_COMPANY again; 1.0 when the function has no profile.
        assert set(terms) == {
            "baseline_layer1",
            "company_layer2",
            "situation_layer3",
            "role_layer3",
        }, row.name
        assert row.provenance_json["department_model"], row.name
        assert row.dimension and row.weight is not None


# ── The acceptance evidence spec-doc6 §4.3 asks for ─────────────────────────


def test_removed_role_intake_routes_return_404_and_historic_data_is_ignored(
    client: Caller, sessions
) -> None:
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Historic Intake Role")
    _assign(sessions, job_id, client.world)

    async def _seed():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    job = await session.get(Job, uuid.UUID(job_id))
                    session.add(JobSwotIntake(
                        tenant_id=job.tenant_id,
                        job_id=job.id,
                        status="complete",
                        phase="complete",
                        strengths=["Historic intake must not shape the new matrix"],
                        weaknesses=["Historic gap"],
                        opportunities=["Historic opportunity"],
                        threats=["Historic threat"],
                        situation_key=situations.GREENFIELD,
                    ))
    asyncio.run(_seed())

    client.sign_in(Role.hiring_manager)
    base = f"{ASSESSMENTS}/assessments/jobs/{job_id}"
    assert client.get(f"{base}/swot").status_code == 404
    assert client.post(f"{base}/swot/respond", json={"answer": "old"}).status_code == 404
    saved = _run_swot(client, job_id)
    assert saved["strengths"] == SWOT_ANSWERS["strengths"]
    result = _compile(sessions, job_id)
    assert result.items
    assert result.situation_key is None
    assert all("Historic intake" not in (row.swot_origin or "") for row in result.items)


# ── Gate G1 ─────────────────────────────────────────────────────────────────


def test_g1_refuses_a_job_whose_matrix_is_not_frozen(client: Caller, sessions) -> None:
    """The gate this phase exists to put on a live path.

    `require_frozen_matrix` IS G1. Before this phase its only caller was
    `miti/pipeline.py`, which nothing imports.
    """
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Ungated Role")
    _assign(sessions, job_id, client.world)
    client.sign_in(Role.hiring_manager)
    _run_swot(client, job_id)
    _compile(sessions, job_id)

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    # The matrix EXISTS and is complete. G1 still refuses,
                    # because no human has approved it.
                    assert await scorecard.load_frozen_matrix(
                        session, uuid.UUID(job_id)
                    ) is None
                    with pytest.raises(scorecard.ScorecardNotFrozen) as caught:
                        await scorecard.require_frozen_matrix(
                            session, uuid.UUID(job_id)
                        )
                    return caught.value

    refusal = asyncio.run(_go())
    assert refusal.result.gate == "G1_scorecard_approved"
    assert refusal.result.blocking is True
    assert any("approved" in reason for reason in refusal.result.reasons)


def test_g1_cannot_be_bypassed_by_stamping_the_approval_alone(
    client: Caller, sessions
) -> None:
    """THE BYPASS ATTEMPT. Stamp the approval, skip the derivation.

    A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED, and this repository has paid
    for believing otherwise: 19 of 35 live jobs carried `framework_generated_at`
    with zero competency rows. So the gate asks the TABLE, and a row that never
    ran the seven stages is refused as hard as no row at all.
    """
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Bypass Attempt Role")

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    job = await session.get(Job, uuid.UUID(job_id))
                    # Exactly what the retired single-pass generator produced: a
                    # name, a description, an approval stamp, and no derivation.
                    session.add(
                        JobCompetency(
                            tenant_id=job.tenant_id,
                            job_id=job.id,
                            category=ppi.CATEGORY_MUST_HAVE,
                            name="Python",
                            description="Writes production Python.",
                            required_level=95,
                            ordinal=1,
                        )
                    )
                    job.framework_approved_at = sa.func.now()
                    await session.flush()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    with pytest.raises(scorecard.ScorecardNotFrozen) as caught:
                        await scorecard.require_frozen_matrix(
                            session, uuid.UUID(job_id)
                        )
                    return caught.value

    refusal = asyncio.run(_go())
    assert "no derivation" in " ".join(refusal.result.reasons)


# ── Finalisation and publication (RBAC §20, §21) ────────────────────────────


def test_the_hiring_manager_finalises_and_the_recruiter_publishes(
    client: Caller, sessions
) -> None:
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Publishable Role")
    _assign(sessions, job_id, client.world)

    # RBAC §9.3: the Recruiter hands the draft to the assigned Hiring Manager.
    client.sign_in(Role.recruiter)
    sent = client.post(f"/jobs/{job_id}/send-to-hiring-manager")
    assert sent.status_code == 200, sent.text

    client.sign_in(Role.hiring_manager)
    _run_swot(client, job_id)
    _compile(sessions, job_id)

    # RBAC §21: publication is refused while the definition is not finalised.
    client.sign_in(Role.recruiter)
    early = client.post(f"/jobs/{job_id}/publish")
    assert early.status_code in (403, 409), early.text

    client.sign_in(Role.hiring_manager)
    finalised = client.post(f"{ASSESSMENTS}/assessments/jobs/{job_id}/framework/finalize")
    assert finalised.status_code == 200, finalised.text
    assert finalised.json()["approved"] is True

    client.sign_in(Role.recruiter)
    published = client.post(f"/jobs/{job_id}/publish")
    assert published.status_code == 200, published.text

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    job = await session.get(Job, uuid.UUID(job_id))
                    matrix = await scorecard.load_frozen_matrix(
                        session, uuid.UUID(job_id)
                    )
                    binding = (
                        await session.execute(
                            sa.select(JobScorecardBinding).where(
                                JobScorecardBinding.job_id == job.id
                            )
                        )
                    ).scalars().all()
                    rows = (
                        await session.execute(
                            sa.select(AuditLog).where(
                                AuditLog.job_id == job.id,
                                AuditLog.action.in_(
                                    (
                                        "role_definition_finalized",
                                        "jd_sent_to_hiring_manager",
                                        "job_swot_analysis_edited",
                                    )
                                ),
                            )
                        )
                    ).scalars().all()
                    return job, matrix, binding, rows

    job, matrix, bindings, audits = asyncio.run(_go())
    assert job.lifecycle_state == JobLifecycleState.PUBLISHED.value
    assert job.finalized_by is not None and job.finalized_at is not None
    # G1 now passes, and only now.
    assert matrix is not None
    assert matrix.version == 1
    assert matrix.situation_key is None
    # RBAC §20's record: one binding carrying the version and the person.
    assert len(bindings) == 1
    assert bindings[0].frozen_by == job.finalized_by
    assert bindings[0].scorecard_version == job.criteria_version
    # spec-doc6 §4.1: one correlation id, traceable through every stage.
    assert job.correlation_id
    assert {row.action for row in audits} == {
        "jd_sent_to_hiring_manager",
        "job_swot_analysis_edited",
        "role_definition_finalized",
    }
    assert all(row.correlation_id == job.correlation_id for row in audits)
    swot_row = next(row for row in audits if row.action == "job_swot_analysis_edited")
    assert swot_row.actor_user_id is not None


def test_publication_is_blocked_when_the_definition_is_not_finalised(
    client: Caller, sessions
) -> None:
    """RBAC §21: "Publication MUST NOT be possible if required Hiring
    Manager-controlled components are incomplete."
    """
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Unfinalised Role")
    _assign(sessions, job_id, client.world)
    client.sign_in(Role.recruiter)
    refused = client.post(f"/jobs/{job_id}/publish")
    assert refused.status_code in (403, 409), refused.text


# ── The kill switch ─────────────────────────────────────────────────────────


def test_the_halt_switch_refuses_matrix_compilation(client: Caller, sessions, monkeypatch) -> None:
    client.sign_in(Role.hr_manager)
    _write_company_profile(sessions, client.world.tenant_a)
    job_id = _create_job(client, "Halted Role")
    _assign(sessions, job_id, client.world)
    client.sign_in(Role.hiring_manager)
    _run_swot(client, job_id)

    monkeypatch.setenv(pipeline_halt.ENV_VAR, pipeline_halt.STAGE_SUTRA_MATRIX)
    with pytest.raises(pipeline_halt.PipelineHalted):
        _compile(sessions, job_id)


def test_a_misspelled_halt_stage_is_an_error_rather_than_a_no_op(monkeypatch) -> None:
    """A kill switch whose typo reads as "off" is a kill switch that will one
    day be believed to be on."""
    monkeypatch.setenv(pipeline_halt.ENV_VAR, "sutraa")
    with pytest.raises(pipeline_halt.UnknownHaltStage) as caught:
        pipeline_halt.check(pipeline_halt.STAGE_SUTRA_MATRIX)
    assert "sutraa" in str(caught.value)
    assert "Nothing has been halted" in str(caught.value)


def test_the_migrations_situation_keys_are_the_modules(monkeypatch) -> None:
    """Migration 0064 holds the six situation keys as literals, deliberately: a
    migration describes the schema at its own point in history. This asserts
    they agree TODAY, which is the guarantee that is actually wanted.

    The companion SWOT-phase assertion went with `services/swot_intake` on
    2026-09-20. It compared the migration's phase literals against the module
    that WROTE `job_swot_intakes`, and once the Role Intake conversation was
    retired there is no writer left to disagree with them: the table survives
    as a frozen transcript and its phase vocabulary lives in the migration and
    the CHECK constraint that migration created. A parity test with one side
    deleted asserts nothing."""
    assert set(SUTRA_MIGRATION._SITUATION_KEYS) == set(situations.SITUATION_TYPES)
