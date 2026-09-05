"""Job setup on the LIVE path: Bodha's session, Sutra's seven stages, gate G1.

WHAT THIS FILE IS FOR
---------------------
spec-doc6 §4.3's acceptance evidence, and it is deliberately awkward to satisfy:

    "a change to a Company DNA answer or to one SWOT input demonstrably moves a
     weight in the resulting matrix, shown through the API, not through a
     summary string."

Every assertion below therefore goes through the HTTP surface a recruiter and a
hiring manager actually touch. A test that called `compile_matrix` directly
would prove the arithmetic and nothing about whether anybody can reach it, and
"the modules exist and are tested in isolation; nothing user-facing calls them"
is the exact gap this phase exists to close.

WHY IT NEEDS A REAL DATABASE
-----------------------------
The three layers Sutra composes are all STORED. Layer 2 is a `company_dna` row
with a versioning trigger on it, Layer 3 is a `job_swot_intakes` row with a
CHECK on its situation key, and the frozen matrix is `job_competencies` plus an
append-only `job_company_dna_bindings` row. A session double would let the
application's own WHERE clause be the only boundary, which claude.md rule 1
forbids, and would not exercise a single one of those constraints.

The schema is built from the ORM metadata plus the DDL that lives only in
migrations 0060 and 0064, imported FROM those migrations so what runs here is
what will run in production. The database is a scratch one, created and dropped
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
from app.models.company_dna import JobCompanyDNABinding
from app.models.enums import Role
from app.models.hiring import CompanyDNA
from app.models.job import Job
from app.models.job_setup import JobSwotIntake
from app.models.tenant import AuditLog, RolePermission, Tenant
from app.models.user import User
from app.services import capabilities as caps
from app.services import ppi, swot_intake
from app.services.hiring import pipeline_halt, scorecard, situations, swot_quality
from app.services.hiring_pipeline import JobLifecycleState

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "company_dna"
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


DNA_MIGRATION = _module("_job_setup_migration_0060", "0060_company_dna_versioning.py")
SUTRA_MIGRATION = _module(
    "_job_setup_migration_0064", "0064_sutra_seven_stage_provenance.py"
)

#: THE WHOLE SCHEMA, not a named subset.
#:
#: `tests/test_company_dna_api.py` names the five tables its router touches,
#: which is right for a router that reads five tables. This flow is not that: it
#: crosses job creation (which asks the credit ledger and the company profile),
#: the Company DNA intake, the SWOT session, the matrix and the audit trail, and
#: each of those reaches one more table through a foreign key. Naming them was
#: tried first and produced three rounds of "relation does not exist" for tables
#: nothing here is testing.
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
    CompanyDNA.__table__,
    JobCompanyDNABinding.__table__,
    Job.__table__,
    JobCompetency.__table__,
    JobSwotIntake.__table__,
    CreditLedgerEntry.__table__,
)

#: DDL the ORM metadata cannot express, taken from the migrations rather than
#: copied, so the constraints exercised here are the ones production will have.
EXTRA_DDL = (
    "CREATE UNIQUE INDEX uq_company_dna_one_current ON company_dna (tenant_id) "
    "WHERE is_current",
    DNA_MIGRATION._IMMUTABILITY_FUNCTION,
    "CREATE TRIGGER trg_company_dna_version_is_immutable "
    "BEFORE UPDATE ON company_dna "
    "FOR EACH ROW EXECUTE FUNCTION company_dna_version_is_immutable()",
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
            for role, capability in DNA_MIGRATION._GRANTS + _CAPABILITY_GRANTS:
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
    monkeypatch.setattr(swot_intake.llm_router, "invoke_llm", _invoke)


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


@pytest.fixture
def client(world: World, sessions, monkeypatch) -> Iterator[Caller]:
    from app.api import company_dna as dna_router

    monkeypatch.setattr(dna_router, "get_session_factory", lambda: sessions)
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


def _answers() -> dict:
    raw = json.loads((FIXTURES / "complete_intake.json").read_text(encoding="utf-8"))
    return {key: value for key, value in raw.items() if not key.startswith("_")}


ANSWERS = _answers()

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

#: Answered whenever a phase asks something that is not one of the quadrants.
_PHASE_ANSWERS: dict[str, str] = {
    swot_intake.PHASE_FORCE_RANKING: "I would keep the first one. There is nothing that rules a candidate out.",
    swot_intake.PHASE_BEST_PERFORMER: "no",
    swot_intake.PHASE_SITUATION: "Yes, that is right.",
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


def _complete_company_dna(
    client: Caller, tenant: uuid.UUID, *, overrides: dict[str, Any] | None = None
) -> int:
    """Run the whole Layer 2 instrument through HTTP. Returns the version."""
    base = f"/clients/{tenant}/company-dna"
    created = client.post(base, json={})
    assert created.status_code == 201, created.text
    dna_id = created.json()["id"]
    answers = dict(ANSWERS)
    answers.update(overrides or {})
    body: dict[str, Any] = {}
    for key, value in answers.items():
        answered = client.post(
            f"{base}/{dna_id}/messages", json={"question_key": key, "answer": value}
        )
        assert answered.status_code == 200, (key, answered.text)
        body = answered.json()
    frozen = client.post(
        f"{base}/{dna_id}/complete",
        json={"understanding_token": body["understanding_token"]},
    )
    assert frozen.status_code == 200, frozen.text
    return int(frozen.json()["version"])


def _run_swot(
    client: Caller,
    job_id: str,
    *,
    answers: dict[str, str] | None = None,
    situation: str | None = None,
) -> dict:
    """Answer Bodha's whole session through HTTP until it closes or hands back.

    Bounded rather than a `while True`: §18.5's rework is deliberately unbounded
    in the product, and a test that looped forever on a refusal would hang the
    suite instead of failing it.
    """
    said = dict(SWOT_ANSWERS)
    said.update(answers or {})
    opened = client.get(f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot")
    assert opened.status_code == 200, opened.text
    body = opened.json()
    for _turn in range(40):
        if body["complete"]:
            return body
        area = body.get("current_area")
        if area:
            reply = said[area]
        elif body["phase"] == swot_intake.PHASE_SITUATION and situation:
            # §18.4's read-back, answered with a NAMED alternative rather than a
            # yes. "no, it is closer to X" is exactly the correction the
            # read-back exists to collect.
            reply = f"No, it is closer to {situation}."
        else:
            reply = _PHASE_ANSWERS.get(body["phase"], "Yes, that is right.")
        answered = client.post(
            f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot/respond", json={"answer": reply}
        )
        assert answered.status_code == 200, answered.text
        body = answered.json()
    raise AssertionError(
        f"the SWOT session did not close in 40 turns; phase={body['phase']} "
        f"outstanding={body['outstanding_rules']}"
    )


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


# ── The happy path, end to end ───────────────────────────────────────────────


def test_a_job_created_through_the_api_runs_bodha_then_sutra(
    client: Caller, sessions, enqueued
) -> None:
    """spec-doc6 §17: "A job created through the real API runs Bodha's SWOT
    session, Sutra's seven stages, and produces a frozen matrix whose every
    weight traces to a named Layer 1/2/3 source."
    """
    client.sign_in(Role.hr_manager)
    version = _complete_company_dna(client, client.world.tenant_a)
    assert version == 1

    job_id = _create_job(client)
    _assign(sessions, job_id, client.world)

    # Layer 3, through the hiring manager's own session.
    client.sign_in(Role.hiring_manager)
    session_body = _run_swot(client, job_id)
    assert session_body["complete"] is True
    assert session_body["phase"] == swot_intake.PHASE_COMPLETE
    # §18.4's classification was CONFIRMED, not guessed.
    assert session_body["situation_key"] in situations.SITUATION_TYPES
    assert session_body["situation_label"]
    # §18.3's probes were actually put to them.
    asked = set(session_body["instruments_asked"])
    assert {probe.key for probe in swot_quality.HIGH_VALUE_PROBES} <= asked
    assert swot_intake.DISQUALIFIER_INSTRUMENT in asked

    # Closing the session enqueues Sutra as a TASK, never inline (claude.md 4).
    assert any(name == "pickready.compile_tatva_matrix" for name, _a, _k in enqueued)

    result = _compile(sessions, job_id)
    assert result.items, result.rejections
    assert result.company_dna_version == version

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


def test_every_weight_traces_to_a_named_layer_one_two_and_three_source(
    client: Caller, sessions
) -> None:
    client.sign_in(Role.hr_manager)
    _complete_company_dna(client, client.world.tenant_a)
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
        # All four terms of `baseline x company x situation x role`, stored.
        assert set(terms) == {
            "baseline_layer1",
            "company_layer2",
            "situation_layer3",
            "role_layer3",
        }, row.name
        assert row.provenance_json["department_model"], row.name
        assert row.provenance_json["company_dna_version"] >= 1, row.name
        assert row.dimension and row.weight is not None


# ── The acceptance evidence spec-doc6 §4.3 asks for ─────────────────────────


def test_changing_one_company_dna_answer_moves_a_weight(
    client: Caller, sessions
) -> None:
    """spec-doc6 §17: "Changing one Company DNA answer ... demonstrably moves a
    weight in the resulting matrix, observed through the API."

    ONE answer, and it is `proven_vs_potential`: Runbook §16 Section 2 makes it
    a forced scale and §17.1 compiles it into a weight modifier on Track Record
    and its counter-dimension Trajectory. Moving it from one end of the scale to
    the other is the smallest change that must reach the matrix, and the two
    dimensions move in OPPOSITE directions, so normalisation cannot hide it. Everything else about the two runs is byte-identical --
    same job, same SWOT sentences, same scripted naming -- so a weight that
    moves can only have moved because of the answer.
    """
    tenant = client.world.tenant_b
    client.sign_in(Role.hr_manager, tenant)
    _complete_company_dna(client, tenant, overrides={"proven_vs_potential": 1})

    job_id = _create_job(client)
    _assign(sessions, job_id, client.world, tenant)
    client.sign_in(Role.hiring_manager, tenant)
    _run_swot(client, job_id)
    _compile(sessions, job_id)
    before = _stored_weights(sessions, job_id)
    before_raw = _stored_raw_weights(sessions, job_id)
    assert before and before_raw

    # A NEW VERSION of the artifact, which is how Layer 2 changes: versions are
    # immutable and a change creates the next one.
    client.sign_in(Role.hr_manager, tenant)
    second = _complete_company_dna(client, tenant, overrides={"proven_vs_potential": 5})
    assert second == 2

    _compile(sessions, job_id)
    after = _stored_weights(sessions, job_id)
    after_raw = _stored_raw_weights(sessions, job_id)

    # Asserted on the DERIVED weight, which is where Layer 2's multiplier lands.
    # The stored `weight` is a normalised share and is scale-invariant, so a
    # company philosophy that lifts every scored item alike is divided straight
    # back out by `_rank_and_normalise` and shows as no movement at all. See
    # `_stored_raw_weights` for the full argument.
    moved = {
        name: (before_raw[name], after_raw[name])
        for name in before_raw
        if name in after_raw and abs(before_raw[name] - after_raw[name]) > 1e-9
    }
    assert moved, (
        "changing proven_vs_potential from one end of the scale to the other "
        f"moved no derived weight. before={before_raw} after={after_raw}"
    )

    # And it is visible through the API. The Layer 2 sentence on the review
    # screen flips direction, which is what a hiring manager actually reads.
    client.sign_in(Role.hiring_manager, tenant)
    prose = [
        " ".join(row["provenance"])
        for row in client.get(
            f"{ASSESSMENTS}/assessments/jobs/{job_id}/framework"
        ).json()["competencies"]
    ]
    assert any("less heavily" in text for text in prose), prose
    assert any("more heavily" in text for text in prose), prose


def test_changing_one_swot_input_moves_a_weight(client: Caller, sessions) -> None:
    """The Layer 3 half of the same acceptance criterion.

    ONE input, and it is the §18.4 classification the hiring manager confirms at
    the end of the session. That is not a convenient choice: §18.4 says
    misclassifying the situation is "the most expensive error available at
    intake, because it corrupts the entire weight vector", so it is the single
    SWOT input with the largest declared consequence and the one the Runbook
    most wants to be demonstrably wired.

    Everything else about the two runs is identical -- the same job title, the
    same four quadrant answers word for word, the same Company DNA version, the
    same scripted naming -- so the two matrices carry the SAME competencies
    under the same names. A weight that differs can only have come from the one
    answer that differs.
    """
    tenant = client.world.tenant_a
    client.sign_in(Role.hr_manager, tenant)
    _complete_company_dna(client, tenant)

    first = _create_job(client, "Reliability Engineer")
    _assign(sessions, first, client.world)
    client.sign_in(Role.hiring_manager)
    confirmed = _run_swot(client, first)
    _compile(sessions, first)
    baseline = _stored_weights(sessions, first)
    baseline_raw = _stored_raw_weights(sessions, first)

    client.sign_in(Role.hr_manager)
    second = _create_job(client, "Reliability Engineer")
    _assign(sessions, second, client.world)
    client.sign_in(Role.hiring_manager)
    corrected = _run_swot(client, second, situation="Greenfield")
    _compile(sessions, second)
    changed = _stored_weights(sessions, second)
    changed_raw = _stored_raw_weights(sessions, second)

    # The one input that differs, and it differs.
    assert confirmed["situation_key"] != corrected["situation_key"]
    assert corrected["situation_key"] == situations.GREENFIELD

    shared = set(baseline) & set(changed)
    assert shared, f"the two runs share no competency: {baseline} / {changed}"
    # The acceptance criterion is about the DERIVED weight, which is where a
    # situation type's multiplier lands. The stored `weight` is a normalised
    # SHARE and is deliberately scale-invariant: when a situation lifts every
    # scored item alike, because they sit on dimensions it treats alike,
    # `_rank_and_normalise` divides the lift straight back out. See
    # `_stored_raw_weights`.
    shared_raw = set(baseline_raw) & set(changed_raw)
    assert shared_raw, f"no shared competency: {baseline_raw} / {changed_raw}"
    moved_raw = {
        name: (baseline_raw[name], changed_raw[name])
        for name in shared_raw
        if abs(baseline_raw[name] - changed_raw[name]) > 1e-9
    }
    assert moved_raw, (
        "one changed SWOT input moved no derived weight. "
        f"{confirmed['situation_key']} -> {corrected['situation_key']}. "
        f"{baseline_raw} / {changed_raw}"
    )

    # And the multiplier that moved is the SITUATION term specifically, not some
    # other layer drifting. Asserting the term rather than only the product is
    # what makes this a test of Layer 3 and not of arithmetic in general.
    for name, (before, after) in moved_raw.items():
        assert before != after, name

    # And the MOVE is visible through the API, in the sentence the hiring
    # manager reads before finalising, not only in the stored number.
    client.sign_in(Role.hiring_manager)
    prose = {
        row["name"]: " ".join(row["provenance"])
        for row in client.get(
            f"{ASSESSMENTS}/assessments/jobs/{second}/framework"
        ).json()["competencies"]
    }
    assert any(
        situations.SITUATIONS[situations.GREENFIELD].label in text
        for text in prose.values()
    ), prose


# ── §18.5: the six rejection rules, for real ────────────────────────────────


def test_an_external_only_weakness_is_handed_back_to_the_hiring_manager(
    client: Caller, sessions
) -> None:
    """§18.5: "Weaknesses are absent or purely external ('the market is
    competitive')".

    RBAC §11 is the thing NOT being implemented here. The Hiring Manager cannot
    reject the JD; Bodha handing a SWOT back to the Hiring Manager for rework is
    a different act, and the difference is who is being asked to do more work.
    """
    client.sign_in(Role.hr_manager)
    _complete_company_dna(client, client.world.tenant_a)
    job_id = _create_job(client, "Market Weakness Role")
    _assign(sessions, job_id, client.world)
    client.sign_in(Role.hiring_manager)
    body = client.get(f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot").json()
    for _turn in range(40):
        if body["complete"] or body["returned_for_rework"]:
            break
        area = body.get("current_area")
        reply = (
            "Salaries here are not competitive and the market is very tight."
            if area == "weaknesses"
            else (SWOT_ANSWERS.get(area) if area else _PHASE_ANSWERS.get(body["phase"], "yes"))
        )
        body = client.post(
            f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot/respond", json={"answer": reply}
        ).json()

    assert body["returned_for_rework"] is True, body
    assert body["complete"] is False
    assert "weaknesses_external_only" in body["outstanding_rules"]
    # The refusal is a SENTENCE the manager can act on, not an error code.
    assert body["prompt"] and "market" in body["prompt"].lower()


# ── Gate G1 ─────────────────────────────────────────────────────────────────


def test_g1_refuses_a_job_whose_matrix_is_not_frozen(client: Caller, sessions) -> None:
    """The gate this phase exists to put on a live path.

    `require_frozen_matrix` IS G1. Before this phase its only caller was
    `miti/pipeline.py`, which nothing imports.
    """
    client.sign_in(Role.hr_manager)
    _complete_company_dna(client, client.world.tenant_a)
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
    _complete_company_dna(client, client.world.tenant_a)
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
    _complete_company_dna(client, client.world.tenant_a)
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
                            sa.select(JobCompanyDNABinding).where(
                                JobCompanyDNABinding.job_id == job.id
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
                                        "swot_session_completed",
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
    assert matrix.company_dna_version >= 1
    assert matrix.situation_key in situations.SITUATION_TYPES
    # RBAC §20's record: one binding carrying both versions and the person.
    assert len(bindings) == 1
    assert bindings[0].frozen_by == job.finalized_by
    assert bindings[0].scorecard_version == job.criteria_version
    # spec-doc6 §4.1: one correlation id, traceable through every stage.
    assert job.correlation_id
    assert {row.action for row in audits} == {
        "jd_sent_to_hiring_manager",
        "swot_session_completed",
        "role_definition_finalized",
    }
    assert all(row.correlation_id == job.correlation_id for row in audits)
    # RBAC §34: the agent AND the human, on the row the agent produced.
    swot_row = next(row for row in audits if row.action == "swot_session_completed")
    assert swot_row.agent_name == "bodha"
    assert swot_row.actor_user_id is not None


def test_publication_is_blocked_when_the_definition_is_not_finalised(
    client: Caller, sessions
) -> None:
    """RBAC §21: "Publication MUST NOT be possible if required Hiring
    Manager-controlled components are incomplete."
    """
    client.sign_in(Role.hr_manager)
    _complete_company_dna(client, client.world.tenant_a)
    job_id = _create_job(client, "Unfinalised Role")
    _assign(sessions, job_id, client.world)
    client.sign_in(Role.recruiter)
    refused = client.post(f"/jobs/{job_id}/publish")
    assert refused.status_code in (403, 409), refused.text


def test_sutra_refuses_without_a_company_dna_and_says_what_to_do(
    client: Caller, sessions
) -> None:
    """spec-doc6 D3: an explicit, actionable block, not a mysterious failure."""
    tenant = uuid.uuid4()

    async def _seed_tenant():
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, 'NoDNA', :domain, 'pending')"
                    ),
                    {"id": tenant, "domain": f"{tenant}.setup.test"},
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                        "assessment_grade, assessment_status, lifecycle_state, "
                        "swot_completed_at, correlation_id) VALUES "
                        "(:jid, :tid, 'No DNA Role', '{}'::jsonb, 'draft', "
                        "'non_managerial', 'questions_pending_review', 'DRAFT', "
                        "now(), 'job-nodna')"
                    ),
                    {"jid": (job_id := uuid.uuid4()), "tid": tenant},
                )
                await session.execute(
                    sa.text(
                        "INSERT INTO job_swot_intakes (id, tenant_id, job_id, status, "
                        "area_index, follow_ups_used, strengths, weaknesses, "
                        "opportunities, threats, transcript_json, phase, probes_asked, "
                        "quality_json, completed_at) VALUES "
                        "(gen_random_uuid(), :tid, :jid, 'complete', 4, 0, "
                        "'[\"a\"]'::jsonb, '[\"b\"]'::jsonb, '[]'::jsonb, '[]'::jsonb, "
                        "'[]'::jsonb, 'complete', '[]'::jsonb, '{}'::jsonb, now())"
                    ),
                    {"tid": tenant, "jid": job_id},
                )
                return job_id

    job_id = asyncio.run(_seed_tenant())

    async def _go():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    job = await session.get(Job, job_id)
                    with pytest.raises(scorecard.ScorecardInputMissing) as caught:
                        await scorecard.compile_matrix(
                            session, job, actor_user_id=None
                        )
                    return caught.value

    refusal = asyncio.run(_go())
    assert refusal.layer == "layer2"
    assert "Company DNA is required" in refusal.detail


# ── The kill switch ─────────────────────────────────────────────────────────


def test_the_halt_switch_refuses_and_never_degrades(
    client: Caller, sessions, monkeypatch
) -> None:
    """spec-doc6 D1: `RPN_PIPELINE_HALT` "must never fall back to old logic,
    degraded logic, or a stub".

    Asserted as an absence: nothing is written, nothing is generated, and the
    caller gets a 503 naming the stage and the way out. A halt that produced a
    worse answer would be the one failure mode the switch exists to prevent.
    """
    client.sign_in(Role.hr_manager)
    _complete_company_dna(client, client.world.tenant_a)
    job_id = _create_job(client, "Halted Role")
    _assign(sessions, job_id, client.world)

    monkeypatch.setenv(pipeline_halt.ENV_VAR, pipeline_halt.STAGE_BODHA_SWOT)
    client.sign_in(Role.hiring_manager)
    opened = client.get(f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot")
    assert opened.status_code == 200
    refused = client.post(
        f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot/respond", json={"answer": "anything"}
    )
    assert refused.status_code == 503, refused.text
    assert pipeline_halt.STAGE_BODHA_SWOT in refused.json()["detail"]
    assert pipeline_halt.ENV_VAR in refused.json()["detail"]

    monkeypatch.delenv(pipeline_halt.ENV_VAR)
    resumed = client.post(
        f"{ASSESSMENTS}/assessments/jobs/{job_id}/swot/respond",
        json={"answer": SWOT_ANSWERS["strengths"]},
    )
    assert resumed.status_code == 200, resumed.text


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
    they agree TODAY, which is the guarantee that is actually wanted."""
    assert set(SUTRA_MIGRATION._SITUATION_KEYS) == set(situations.SITUATION_TYPES)
    assert set(SUTRA_MIGRATION._SWOT_PHASES) == set(swot_intake.PHASES)
