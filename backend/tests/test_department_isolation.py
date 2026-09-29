"""The department boundary and the leadership seats, over real routes and a
real database (the leadership release, 2026-09-29, spec 34.5 and 34.6).

WHAT IS REAL HERE
-----------------
Everything except WHO is calling: real FastAPI routing, the real capability
gates, the real `rbac.authorize` chain, real Postgres with Row Level Security
entered exactly as `deps.get_tenant_db` enters it, and the real
`role_permissions` rows migration 0133 seeded (the grant engine's Redis cache
is doubled, never the decision). The principal is injected because minting a
Firebase session per case would test the login flow, which has its own suite.

THE WORLD
---------
One company with two departments, Engineering and Finance, one published job
in each, one applicant on each job, and one evaluation on each application.
The people: a Super Admin, a CEO, an MD, and a Functional Head of
Engineering. A second company exists only to prove a department id from
another tenant is refused.

What spec 34.5 asks, case by case: the Engineering job is visible, the Finance
job is not listed, a direct Finance URL is refused, the Finance candidate and
report are refused, and the dashboard figures exclude Finance. What 34.6 asks:
the CEO and MD see every department and read billing, invoices, compliance and
the team, and every write beside them is refused. The Leadership Intelligence
writes (34.5's "Engineering leadership write", 34.6's "CEO cannot overwrite
MD") belong to the package that builds those routes.
"""
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services import departments, department_access, prism_view

V1 = "/api/v1"


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.other_tenant = uuid.uuid4()
        self.engineering = uuid.uuid4()
        self.finance = uuid.uuid4()
        self.foreign_department = uuid.uuid4()
        self.eng_job = uuid.uuid4()
        self.fin_job = uuid.uuid4()
        self.eng_link = uuid.uuid4()
        self.fin_link = uuid.uuid4()
        self.eng_candidate = uuid.uuid4()
        self.fin_candidate = uuid.uuid4()
        self.users: dict[str, uuid.UUID] = {
            "client": uuid.uuid4(),
            "ceo": uuid.uuid4(),
            "md": uuid.uuid4(),
            "fh_engineering": uuid.uuid4(),
        }


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def _reachable() -> bool:
    engine = _engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT department_id FROM users LIMIT 0"))
            await conn.execute(sa.text("SELECT 1 FROM company_departments LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 - the reason is reported by the skip
        return False
    finally:
        await engine.dispose()


async def _seed(w: World) -> None:
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            for tenant, name in ((w.tenant, "Alpha Leadership Co"), (w.other_tenant, "Beta Co")):
                await conn.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, :name, :domain, 'pending')"
                    ),
                    {"id": tenant, "name": name, "domain": f"{tenant}.dept.test"},
                )
            for dept, tenant, name in (
                (w.engineering, w.tenant, "Engineering"),
                (w.finance, w.tenant, "Finance"),
                (w.foreign_department, w.other_tenant, "Engineering"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO company_departments "
                        "(id, tenant_id, name, normalized_name) "
                        "VALUES (:id, :tenant, :name, :key)"
                    ),
                    {"id": dept, "tenant": tenant, "name": name, "key": name.lower()},
                )
            for key, role, dept in (
                ("client", "client", None),
                ("ceo", "ceo", None),
                ("md", "md", None),
                ("fh_engineering", "functional_head", w.engineering),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, tenant_id, role, email, full_name, "
                        "status, auth_providers, department_id) VALUES "
                        "(:id, :tenant, :role, :email, :name, 'active', "
                        "'[]'::jsonb, :dept)"
                    ),
                    {
                        "id": w.users[key],
                        "tenant": w.tenant,
                        "role": role,
                        "email": f"{w.users[key]}@dept.test",
                        "name": f"Test {key.replace('_', ' ').title()}",
                        "dept": dept,
                    },
                )
            for job, link, candidate, dept, title in (
                (w.eng_job, w.eng_link, w.eng_candidate, w.engineering, "Platform Engineer"),
                (w.fin_job, w.fin_link, w.fin_candidate, w.finance, "Finance Controller"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO jobs (id, tenant_id, title, department, "
                        "department_id, jd_json, status, lifecycle_state, ratified_at) "
                        "VALUES (:id, :tenant, :title, :dname, :dept, '{}'::jsonb, "
                        "'ratified', 'CANDIDATE_APPLICATIONS', now())"
                    ),
                    {
                        "id": job,
                        "tenant": w.tenant,
                        "title": title,
                        "dname": "Engineering" if dept == w.engineering else "Finance",
                        "dept": dept,
                    },
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, "
                        "consent_databank) VALUES (:id, :tenant, :name, :email, false)"
                    ),
                    {
                        "id": candidate,
                        "tenant": w.tenant,
                        "name": f"Test Applicant {title}",
                        "email": f"{candidate}@dept.test",
                    },
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO job_candidate_links "
                        "(id, tenant_id, job_id, candidate_id, source, status) "
                        "VALUES (:id, :tenant, :job, :cand, 'fresh', 'applied')"
                    ),
                    {"id": link, "tenant": w.tenant, "job": job, "cand": candidate},
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO evaluations (id, tenant_id, job_id, link_id, "
                        "aggregate_json, gate_results_json) VALUES "
                        "(:id, :tenant, :job, :link, "
                        "CAST('{\"overall_grade\": \"Matching\"}' AS jsonb), "
                        "CAST('[]' AS jsonb))"
                    ),
                    {"id": uuid.uuid4(), "tenant": w.tenant, "job": job, "link": link},
                )
    finally:
        await engine.dispose()


async def _teardown(w: World) -> None:
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            await conn.execute(
                sa.text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                {"ids": [w.tenant, w.other_tenant]},
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    if not asyncio.run(_reachable()):
        pytest.skip("the migrated test database is not reachable")
    state = World()
    asyncio.run(_seed(state))
    try:
        yield state
    finally:
        asyncio.run(_teardown(state))


@pytest.fixture(autouse=True)
def _no_permission_cache(monkeypatch):
    """A double for the grant engine's CACHE, never for the decision."""
    from app.services import tenant_cache

    async def _miss(key):  # noqa: ANN001
        return None

    async def _noop(key, value, *, ttl=120):  # noqa: ANN001
        return None

    monkeypatch.setattr(tenant_cache, "get_json", _miss)
    monkeypatch.setattr(tenant_cache, "set_json", _noop)


class Caller:
    def __init__(self, world: World) -> None:
        self.world = world
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_(self, key: str, role: Role) -> "Caller":
        self.principal = CurrentUser(
            user_id=self.world.users[key],
            tenant_id=self.world.tenant,
            role=role,
            audience=AUDIENCE_ORG,
        )
        return self

    def get(self, path: str):
        return self.http.get(path)

    def request(self, method: str, path: str, **kwargs):
        return self.http.request(method, path, **kwargs)


@pytest.fixture
def caller(world: World) -> Iterator[Caller]:
    factory = async_sessionmaker(_engine(), expire_on_commit=False)
    state = Caller(world)

    async def _current_user() -> CurrentUser:
        assert state.principal is not None
        return state.principal

    async def _tenant_db():
        principal = state.principal
        assert principal is not None
        async with factory() as session:
            async with session.begin():
                async with tenant_scope(session, principal.tenant_id):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            state.http = http
            yield state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


# ── 34.5: the Functional Head of Engineering ─────────────────────────────────


def _fh(caller: Caller) -> Caller:
    return caller.as_("fh_engineering", Role.functional_head)


def test_the_engineering_job_is_listed_and_the_finance_job_is_not(
    caller: Caller, world: World
) -> None:
    response = _fh(caller).get(f"{V1}/jobs")
    assert response.status_code == 200, response.text
    listed = {row["id"] for row in response.json()}
    assert str(world.eng_job) in listed
    assert str(world.fin_job) not in listed


def test_a_direct_finance_job_url_is_the_tenant_safe_404(
    caller: Caller, world: World
) -> None:
    fh = _fh(caller)
    assert fh.get(f"{V1}/jobs/{world.eng_job}").status_code == 200
    finance = fh.get(f"{V1}/jobs/{world.fin_job}")
    missing = fh.get(f"{V1}/jobs/{uuid.uuid4()}")
    assert finance.status_code == missing.status_code == 404
    assert finance.json() == missing.json()


def test_the_finance_candidates_are_refused(caller: Caller, world: World) -> None:
    fh = _fh(caller)
    assert fh.get(f"{V1}/jobs/{world.eng_job}/candidates").status_code == 200
    assert fh.get(f"{V1}/jobs/{world.fin_job}/candidates").status_code == 404
    # The candidate's own record: the Finance applicant answers 404 BEFORE the
    # full-profile grant is asked, the Engineering applicant reaches that
    # grant (which a Functional Head does not hold) and is told so.
    assert (
        fh.get(f"{V1}/candidates/{world.fin_candidate}/profile").status_code == 404
    )
    assert (
        fh.get(f"{V1}/candidates/{world.eng_candidate}/profile").status_code == 403
    )


def test_the_finance_report_is_refused(caller: Caller, world: World) -> None:
    fh = _fh(caller)
    # The Vivekium Profile on the dashboard, through `require_authorized`.
    eng = fh.get(
        f"{V1}/dashboard/jobs/{world.eng_job}/candidates/{world.eng_link}/profile"
    )
    fin = fh.get(
        f"{V1}/dashboard/jobs/{world.fin_job}/candidates/{world.fin_link}/profile"
    )
    assert eng.status_code == 200, eng.text
    assert fin.status_code == 404
    # The PRISM Report route: Engineering passes the boundary and finds no
    # report yet; Finance is refused at the boundary, before the report is
    # looked for.
    eng_report = fh.get(f"/api/v2/assessments/reports/links/{world.eng_link}")
    fin_report = fh.get(f"/api/v2/assessments/reports/links/{world.fin_link}")
    assert eng_report.status_code == 404
    assert eng_report.json()["detail"] == prism_view.REPORT_NOT_READY
    assert fin_report.status_code == 404
    assert fin_report.json()["detail"] == "Report not found"


def test_the_dashboard_figures_exclude_finance(caller: Caller, world: World) -> None:
    fh = _fh(caller)
    summary = fh.get(f"{V1}/dashboard/summary")
    assert summary.status_code == 200, summary.text
    jobs = {row["job_id"] for row in summary.json()["jobs"]}
    assert jobs == {str(world.eng_job)}
    assert summary.json()["total_jobs_worked"] == 1

    page = fh.get(f"{V1}/dashboard/candidates")
    assert page.status_code == 200, page.text
    links = {row["link_id"] for row in page.json()["rows"]}
    assert str(world.eng_link) in links
    assert str(world.fin_link) not in links
    assert page.json()["total"] == 1

    # Naming the Finance job in the filter narrows nothing open: still empty.
    filtered = fh.get(f"{V1}/dashboard/candidates?job_id={world.fin_job}")
    assert filtered.status_code == 200
    assert filtered.json()["rows"] == []


def test_the_department_picker_lists_only_their_own(
    caller: Caller, world: World
) -> None:
    listed = _fh(caller).get(f"{V1}/companies/departments")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [str(world.engineering)]


def test_company_wide_surfaces_are_refused(caller: Caller, world: World) -> None:
    fh = _fh(caller)
    for path in (
        f"{V1}/billing/overview",
        f"{V1}/companies/me/staff",
        f"{V1}/companies/me/compliance-documents",
        f"{V1}/email-senders",
        f"{V1}/intelligence/dashboards",
    ):
        assert fh.get(path).status_code == 403, path


# ── 34.6: the CEO and the MD ─────────────────────────────────────────────────


@pytest.mark.parametrize("key,role", [("ceo", Role.ceo), ("md", Role.md)])
def test_every_department_is_visible(
    caller: Caller, world: World, key: str, role: Role
) -> None:
    leader = caller.as_(key, role)
    listed = {row["id"] for row in leader.get(f"{V1}/jobs").json()}
    assert {str(world.eng_job), str(world.fin_job)} <= listed
    for job in (world.eng_job, world.fin_job):
        assert leader.get(f"{V1}/jobs/{job}").status_code == 200
        assert leader.get(f"{V1}/jobs/{job}/candidates").status_code == 200
    summary = leader.get(f"{V1}/dashboard/summary").json()
    assert {str(world.eng_job), str(world.fin_job)} <= {
        row["job_id"] for row in summary["jobs"]
    }
    departments_listed = {
        row["id"] for row in leader.get(f"{V1}/companies/departments").json()
    }
    assert {str(world.engineering), str(world.finance)} <= departments_listed


@pytest.mark.parametrize("key,role", [("ceo", Role.ceo), ("md", Role.md)])
def test_billing_invoices_compliance_and_the_team_are_readable(
    caller: Caller, world: World, key: str, role: Role
) -> None:
    leader = caller.as_(key, role)
    assert leader.get(f"{V1}/billing/overview").status_code == 200
    # The invoice list, and one invoice: an unknown purchase is a 404, which
    # proves the read grant opened the route (a missing grant is a 403).
    assert leader.get(f"{V1}/billing/purchases").status_code == 200
    assert (
        leader.get(f"{V1}/billing/purchases/{uuid.uuid4()}/invoice").status_code
        == 404
    )
    assert leader.get(f"{V1}/companies/me/compliance-documents").status_code == 200
    team = leader.get(f"{V1}/companies/me/staff")
    assert team.status_code == 200, team.text
    seen = {row["id"]: row for row in team.json()}
    # The whole team, read-only: the Functional Head is on it, and no row
    # offers a control.
    assert str(world.users["fh_engineering"]) in seen
    assert all(row["can_manage"] is False for row in seen.values())
    assert seen[str(world.users["fh_engineering"])]["department_name"] == "Engineering"
    senders = leader.get(f"{V1}/email-senders")
    assert senders.status_code == 200
    assert senders.json()["can_manage"] is False
    assert senders.json()["can_authorize"] is False


@pytest.mark.parametrize("key,role", [("ceo", Role.ceo), ("md", Role.md)])
def test_every_write_beside_the_reads_is_refused(
    caller: Caller, world: World, key: str, role: Role
) -> None:
    leader = caller.as_(key, role)
    # Billing manage.
    assert (
        leader.request("POST", f"{V1}/billing/purchase", json={}).status_code == 403
    )
    # Compliance manage.
    assert (
        leader.request(
            "DELETE", f"{V1}/companies/me/compliance-documents/gstin"
        ).status_code
        == 403
    )
    # Staff manage: the list is readable, a new seat is not creatable.
    created = leader.request(
        "POST",
        f"{V1}/companies/me/staff",
        json={
            "email": f"nobody-{uuid.uuid4().hex[:12]}@example.com",
            "full_name": "Nobody",
            "role": "recruiter",
        },
    )
    assert created.status_code == 403
    # Company profile edit, job creation and a job edit.
    assert (
        leader.request("PATCH", f"{V1}/companies/me/profile", json={}).status_code
        == 403
    )
    assert (
        leader.request(
            "POST", f"{V1}/jobs", json={"title": "X", "jd_markdown": "# X\n\nBody"}
        ).status_code
        in (403, 422)
    )
    assert (
        leader.request(
            "PATCH", f"{V1}/jobs/{world.eng_job}", json={"title": "Renamed"}
        ).status_code
        == 403
    )


# ── Staff: the department a Functional Head is invited into ──────────────────


def _invite_body(role: str, department_id: uuid.UUID | None) -> dict:
    body = {
        # A deliverable-looking domain: the schema's EmailStr refuses the
        # reserved `.test` names this module's seeded rows use.
        "email": f"invitee-{uuid.uuid4().hex[:12]}@example.com",
        "full_name": "Test Invitee",
        "phone": "+919800000000",
        "role": role,
    }
    if department_id is not None:
        body["department_id"] = str(department_id)
    return body


def test_a_functional_head_cannot_be_invited_without_a_department(
    caller: Caller,
) -> None:
    admin = caller.as_("client", Role.client)
    refused = admin.request(
        "POST", f"{V1}/companies/me/staff", json=_invite_body("functional_head", None)
    )
    assert refused.status_code == 422
    assert "department" in refused.json()["detail"]


def test_the_department_must_be_this_companys(caller: Caller, world: World) -> None:
    admin = caller.as_("client", Role.client)
    foreign = admin.request(
        "POST",
        f"{V1}/companies/me/staff",
        json=_invite_body("functional_head", world.foreign_department),
    )
    assert foreign.status_code == 422
    assert foreign.json()["detail"] == departments.DEPARTMENT_NOT_FOUND


def test_only_a_functional_head_carries_a_department(
    caller: Caller, world: World
) -> None:
    admin = caller.as_("client", Role.client)
    refused = admin.request(
        "POST",
        f"{V1}/companies/me/staff",
        json=_invite_body("recruiter", world.engineering),
    )
    assert refused.status_code == 422


def test_a_functional_head_is_invited_into_their_department(
    caller: Caller, world: World
) -> None:
    admin = caller.as_("client", Role.client)
    created = admin.request(
        "POST",
        f"{V1}/companies/me/staff",
        json=_invite_body("functional_head", world.finance),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "functional_head"
    assert body["department_name"] == "Finance"
    assert body["phone"] == "+919800000000"

    async def _read_back() -> tuple:
        engine = _engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
                return (
                    await conn.execute(
                        sa.text("SELECT role, department_id FROM users WHERE id = :id"),
                        {"id": body["id"]},
                    )
                ).one()
        finally:
            await engine.dispose()

    # From a SECOND connection, after the response: the row committed.
    role, department_id = asyncio.run(_read_back())
    assert (role, department_id) == ("functional_head", world.finance)


def test_a_recruitment_manager_cannot_seat_a_leader() -> None:
    """The leadership seats are the Super Admin's alone (spec 11.1)."""
    from app.services import role_hierarchy

    for leader in role_hierarchy.LEAF_ROLES:
        assert role_hierarchy.can_manage(Role.client, leader)
        assert not role_hierarchy.can_manage(Role.recruitment_manager, leader)
        assert not role_hierarchy.can_manage(Role.hr_manager, leader)
        assert not role_hierarchy.can_manage(leader, Role.recruiter)


# ── The database holds the rule too ──────────────────────────────────────────


@pytest.mark.parametrize(
    "role,with_department",
    [("functional_head", False), ("recruiter", True)],
    ids=["functional_head_without", "recruiter_with"],
)
def test_the_users_check_constraint(
    world: World, role: str, with_department: bool
) -> None:
    async def _insert() -> None:
        engine = _engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, tenant_id, role, email, status, "
                        "auth_providers, department_id) VALUES (:id, :tenant, :role, "
                        ":email, 'invited', '[]'::jsonb, :dept)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": world.tenant,
                        "role": role,
                        "email": f"{uuid.uuid4()}@dept.test",
                        "dept": world.engineering if with_department else None,
                    },
                )
        finally:
            await engine.dispose()

    with pytest.raises(IntegrityError) as raised:
        asyncio.run(_insert())
    assert "ck_users_department_iff_functional_head" in str(raised.value)


def test_a_department_of_another_tenant_is_refused_by_the_foreign_key(
    world: World,
) -> None:
    async def _insert() -> None:
        engine = _engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, tenant_id, role, email, status, "
                        "auth_providers, department_id) VALUES (:id, :tenant, "
                        "'functional_head', :email, 'invited', '[]'::jsonb, :dept)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": world.tenant,
                        "email": f"{uuid.uuid4()}@dept.test",
                        "dept": world.foreign_department,
                    },
                )
        finally:
            await engine.dispose()

    with pytest.raises(IntegrityError) as raised:
        asyncio.run(_insert())
    assert "fk_users_department_same_tenant" in str(raised.value)


# ── The pure halves ──────────────────────────────────────────────────────────


def test_in_scope_is_restrictive_for_a_job_in_no_department() -> None:
    dept = uuid.uuid4()
    assert department_access.in_scope(None, None)
    assert department_access.in_scope(None, dept)
    assert department_access.in_scope(dept, dept)
    assert not department_access.in_scope(dept, None)
    assert not department_access.in_scope(dept, uuid.uuid4())


def test_a_department_scoped_writer_cannot_place_a_job_elsewhere() -> None:
    """`assign_job_department` refuses taking a job out of the writer's own
    department before it touches the database."""
    from fastapi import HTTPException

    class _Job:
        tenant_id = uuid.uuid4()
        department_id = None
        department = None

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            departments.assign_job_department(
                None, _Job(), department_id=None, name=None, scope=uuid.uuid4()
            )
        )
    assert raised.value.status_code == 403


@pytest.mark.parametrize(
    "raw",
    ["Engineering", "  engineering ", "ENGINEERING\t", "Sales &  Marketing", "R\nand D"],
)
def test_the_backfill_normalises_exactly_as_the_service_does(raw: str) -> None:
    """Migration 0133's SQL key and `departments.normalize` must agree, or the
    backfill and a later "add department" mint two rows for one name."""
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0133_roles_departments.py"
    )
    spec = importlib.util.spec_from_file_location("_roles_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def _sql_key() -> str:
        engine = _engine()
        try:
            async with engine.connect() as conn:
                return (
                    await conn.execute(
                        sa.text(
                            "SELECT " + module._NORMALIZED.format(col="CAST(:raw AS text)")
                        ),
                        {"raw": raw},
                    )
                ).scalar_one()
        finally:
            await engine.dispose()

    if not asyncio.run(_reachable()):
        pytest.skip("the migrated test database is not reachable")
    assert asyncio.run(_sql_key()) == departments.normalize(raw)
