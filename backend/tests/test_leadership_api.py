"""Leadership Intelligence over real routes and a real database (spec 34.5,
34.6, rules 37.11 and 37.12).

WHAT IS REAL HERE
-----------------
The routes, the capability gates, the organisation-wide refusal, Postgres
under Row Level Security as `deps.get_tenant_db` enters it, the `role_permissions`
rows migrations 0133 and 0135 left, and the insert-only triggers of 0135. The
principal is injected (the login flow has its own suite); the grant engine's
cache is doubled, never its decision. Writes are read back from a SECOND
connection.

THE WORLD
---------
One company with Engineering and Finance, a Super Admin, a CEO, an MD, a
Functional Head of each department and a Recruiter; and a second company whose
department id must be refused.
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
from app.core.config import get_settings
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.workers import dispatch as dispatch_mod
from tests import leadership_fixtures as lf

URL = "/api/v1/leadership"


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.other_tenant = uuid.uuid4()
        self.engineering = uuid.uuid4()
        self.finance = uuid.uuid4()
        self.foreign_department = uuid.uuid4()
        self.eng_job = uuid.uuid4()
        self.users: dict[str, uuid.UUID] = {
            key: uuid.uuid4()
            for key in ("client", "ceo", "md", "fh_engineering", "fh_finance", "recruiter")
        }


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def _reachable() -> bool:
    engine = _engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM leadership_profiles LIMIT 0"))
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
            for tenant, name in ((w.tenant, "Leadership Alpha"), (w.other_tenant, "Leadership Beta")):
                await conn.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status, industry) "
                        "VALUES (:id, :name, :domain, 'pending', 'Payments')"
                    ),
                    {"id": tenant, "name": name, "domain": f"{tenant}.lead.test"},
                )
            for dept, tenant, name in (
                (w.engineering, w.tenant, "Engineering"),
                (w.finance, w.tenant, "Finance"),
                (w.foreign_department, w.other_tenant, "Engineering"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO company_departments (id, tenant_id, name, normalized_name) "
                        "VALUES (:id, :tenant, :name, :key)"
                    ),
                    {"id": dept, "tenant": tenant, "name": name, "key": name.lower()},
                )
            for key, role, dept in (
                ("client", "client", None),
                ("ceo", "ceo", None),
                ("md", "md", None),
                ("fh_engineering", "functional_head", w.engineering),
                ("fh_finance", "functional_head", w.finance),
                ("recruiter", "recruiter", None),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, tenant_id, role, email, full_name, status, "
                        "auth_providers, department_id) VALUES (:id, :tenant, :role, :email, "
                        ":name, 'active', '[]'::jsonb, :dept)"
                    ),
                    {
                        "id": w.users[key], "tenant": w.tenant, "role": role,
                        "email": f"{w.users[key]}@lead.test",
                        "name": f"Test {key.replace('_', ' ').title()}", "dept": dept,
                    },
                )
            await conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, tenant_id, title, department, department_id, "
                    "jd_json, jd_markdown, status) VALUES (:id, :tenant, 'Platform Engineer', "
                    "'Engineering', :dept, '{}'::jsonb, :md, 'draft')"
                ),
                {
                    "id": w.eng_job, "tenant": w.tenant, "dept": w.engineering,
                    "md": "## Description\nRuns the settlement services and their on-call rota.",
                },
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


async def _rows(sql: str, params: dict) -> list[dict]:
    """Read committed state from a SECOND connection, in the bypass scope."""
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            return [dict(row) for row in (await conn.execute(sa.text(sql), params)).mappings()]
    finally:
        await engine.dispose()


def rows(sql: str, **params) -> list[dict]:
    return asyncio.run(_rows(sql, params))


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


ROLES = {
    "client": Role.client,
    "ceo": Role.ceo,
    "md": Role.md,
    "fh_engineering": Role.functional_head,
    "fh_finance": Role.functional_head,
    "recruiter": Role.recruiter,
}


class Caller:
    def __init__(self, world: World) -> None:
        self.world = world
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_(self, key: str) -> "Caller":
        self.principal = CurrentUser(
            user_id=self.world.users[key],
            tenant_id=self.world.tenant,
            role=ROLES[key],
            audience=AUDIENCE_ORG,
        )
        return self

    def get(self, path: str):
        return self.http.get(path)

    def put(self, path: str, body: dict):
        return self.http.put(path, json=body)

    def post(self, path: str, body: dict | None = None):
        return self.http.post(path, json=body or {})


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


def _profiles(world: World, role: str, department: uuid.UUID | None = None) -> list[dict]:
    if department is None:
        return rows(
            "SELECT * FROM leadership_profiles WHERE tenant_id = :t AND author_role = :r "
            "ORDER BY version",
            t=world.tenant, r=role,
        )
    return rows(
        "SELECT * FROM leadership_profiles WHERE tenant_id = :t AND author_role = :r "
        "AND department_id = :d ORDER BY version",
        t=world.tenant, r=role, d=department,
    )


# ── 34.5: the Functional Head writes their OWN department, only ──────────────


def test_a_functional_head_sees_a_fixed_department(caller: Caller, world: World) -> None:
    response = caller.as_("fh_engineering").get(f"{URL}/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_author"] is True
    assert body["author_label"] == "Functional Head"
    assert body["department"] == {"id": str(world.engineering), "name": "Engineering"}
    assert body["departments"] == []


def test_the_engineering_head_saves_versions_of_the_engineering_stream(
    caller: Caller, world: World
) -> None:
    fh = caller.as_("fh_engineering")
    first = fh.put(f"{URL}/me", {"department_requirements": lf.FH_LINE})
    assert first.status_code == 200, first.text
    second = fh.put(
        f"{URL}/me",
        {"department_requirements": lf.FH_LINE, "ideal_employee_expectations": lf.IDEAL_LINE},
    )
    assert second.status_code == 200, second.text
    assert second.json()["profile"]["version"] == first.json()["profile"]["version"] + 1

    saved = _profiles(world, "functional_head", world.engineering)
    assert [row["version"] for row in saved][-2:] == [
        first.json()["profile"]["version"], second.json()["profile"]["version"]
    ]
    assert all(row["author_user_id"] == world.users["fh_engineering"] for row in saved)
    assert saved[-1]["ideal_employee_expectations"] == lf.IDEAL_LINE
    # The earlier version is still there, untouched (spec 18.3).
    assert saved[-2]["ideal_employee_expectations"] is None
    audits = rows(
        "SELECT action FROM audit_log WHERE tenant_id = :t AND action LIKE 'leadership_profile_%'",
        t=world.tenant,
    )
    actions = [row["action"] for row in audits]
    assert "leadership_profile_saved" in actions
    assert "leadership_profile_superseded" in actions


def test_a_functional_head_cannot_write_another_department_by_payload(
    caller: Caller, world: World
) -> None:
    """Rule 37.12, both ways a payload could try: a department field is refused
    by the schema, and a per-department expectation is refused by the service.
    Neither writes anything."""
    fh = caller.as_("fh_engineering")
    before = len(_profiles(world, "functional_head", world.finance))

    smuggled = fh.put(
        f"{URL}/me",
        {"department_requirements": lf.FH_LINE, "department_id": str(world.finance)},
    )
    assert smuggled.status_code == 422
    as_expectation = fh.put(
        f"{URL}/me",
        {"department_expectations": {str(world.finance): lf.FH_LINE}},
    )
    assert as_expectation.status_code == 422
    assert "Functional Head" in as_expectation.json()["detail"]
    assert len(_profiles(world, "functional_head", world.finance)) == before


def test_the_finance_head_writes_finance_not_engineering(caller: Caller, world: World) -> None:
    response = caller.as_("fh_finance").put(
        f"{URL}/me", {"department_requirements": lf.MD_LINE}
    )
    assert response.status_code == 200, response.text
    assert response.json()["department"]["id"] == str(world.finance)
    assert _profiles(world, "functional_head", world.finance)[-1]["department_requirements"] == lf.MD_LINE


# ── 34.6: the CEO and the MD, each their own stream ──────────────────────────


def test_the_ceo_saves_company_wide_and_per_department(caller: Caller, world: World) -> None:
    ceo = caller.as_("ceo")
    view = ceo.get(f"{URL}/me").json()
    assert {d["name"] for d in view["departments"]} == {"Engineering", "Finance"}
    response = ceo.put(
        f"{URL}/me",
        {
            "company_requirements": lf.CEO_LINE,
            "department_expectations": {
                str(world.engineering): lf.FH_LINE,
                str(world.finance): lf.MD_LINE,
            },
        },
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["author_label"] == "CEO"
    assert set(profile["department_expectations"]) == {str(world.engineering), str(world.finance)}
    saved = _profiles(world, "ceo")[-1]
    children = rows(
        "SELECT department_id FROM leadership_department_expectations WHERE profile_id = :p",
        p=saved["id"],
    )
    assert {row["department_id"] for row in children} == {world.engineering, world.finance}


def test_the_ceo_and_the_md_never_overwrite_each_other(caller: Caller, world: World) -> None:
    ceo_before = _profiles(world, "ceo")
    md = caller.as_("md")
    response = md.put(f"{URL}/me", {"company_requirements": lf.MD_LINE})
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["author_label"] == "MD"
    # The CEO's stream is exactly as it was.
    assert _profiles(world, "ceo") == ceo_before
    md_saved = _profiles(world, "md")
    ceo = caller.as_("ceo")
    again = ceo.put(f"{URL}/me", {"company_requirements": lf.CEO_LINE})
    assert again.status_code == 200
    # ...and the MD's stream is exactly as it was after the CEO saved again.
    assert _profiles(world, "md") == md_saved
    assert all(row["author_user_id"] == world.users["md"] for row in md_saved)


def test_a_ceo_cannot_write_a_department_head_field_or_a_foreign_department(
    caller: Caller, world: World
) -> None:
    ceo = caller.as_("ceo")
    before = len(_profiles(world, "ceo"))
    wrong_field = ceo.put(f"{URL}/me", {"department_requirements": lf.FH_LINE})
    assert wrong_field.status_code == 422
    foreign = ceo.put(
        f"{URL}/me",
        {"department_expectations": {str(world.foreign_department): lf.FH_LINE}},
    )
    assert foreign.status_code == 422
    assert len(_profiles(world, "ceo")) == before


def test_unusable_lines_are_kept_out_and_shown_back(caller: Caller, world: World) -> None:
    """Spec 19.1: a protected or unobservable line never reaches the pipeline,
    and the leader is shown why after Save."""
    response = caller.as_("md").put(
        f"{URL}/me",
        {"company_requirements": f"{lf.MD_LINE} {lf.UNSAFE_LINE} {lf.VAGUE_LINE}"},
    )
    assert response.status_code == 200, response.text
    excluded = {line["reason"] for line in response.json()["profile"]["lines_not_used"]}
    assert excluded == {"protected_attribute", "not_observable"}
    compiled = _profiles(world, "md")[-1]["compiled_json"]
    kept = [entry["text"] for entry in compiled["provenance"]]
    assert kept == [lf.MD_LINE]


def test_an_empty_save_is_refused(caller: Caller) -> None:
    assert caller.as_("ceo").put(f"{URL}/me", {}).status_code == 422


# ── Who may read and write at all ────────────────────────────────────────────


def test_a_role_without_the_grant_cannot_author(caller: Caller) -> None:
    for key in ("client", "recruiter"):
        assert caller.as_(key).get(f"{URL}/me").status_code == 403, key
        assert caller.as_(key).put(
            f"{URL}/me", {"company_requirements": lf.CEO_LINE}
        ).status_code == 403, key


def test_the_company_overview_is_for_the_company_wide_readers(
    caller: Caller, world: World
) -> None:
    for key in ("client", "ceo", "md"):
        response = caller.as_(key).get(f"{URL}/company")
        assert response.status_code == 200, (key, response.text)
    body = caller.as_("client").get(f"{URL}/company").json()
    assert body["ceo"]["author_label"] == "CEO"
    assert body["md"]["author_label"] == "MD"
    assert {entry["department"]["name"] for entry in body["functional_heads"]} >= {
        "Engineering", "Finance"
    }
    # A Functional Head and a Recruiter hold no company-wide read.
    assert caller.as_("fh_engineering").get(f"{URL}/company").status_code == 403
    assert caller.as_("recruiter").get(f"{URL}/company").status_code == 403


# ── Insert-only, in the database ─────────────────────────────────────────────


async def _as_app_role(sql: str, params: dict) -> None:
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL ROLE pickready_app"))
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            await conn.execute(sa.text(sql), params)
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE leadership_profiles SET company_requirements = 'rewritten' WHERE tenant_id = :t",
        "DELETE FROM leadership_profiles WHERE tenant_id = :t",
        "UPDATE leadership_department_expectations SET requirements = 'x' WHERE tenant_id = :t",
    ],
)
def test_a_saved_version_cannot_be_rewritten_or_deleted(world: World, sql: str) -> None:
    before = rows("SELECT count(*) AS n FROM leadership_profiles WHERE tenant_id = :t", t=world.tenant)
    with pytest.raises(Exception) as raised:  # noqa: PT011 - privilege or trigger, both refuse
        asyncio.run(_as_app_role(sql, {"t": world.tenant}))
    assert "insert-only" in str(raised.value) or "permission denied" in str(raised.value)
    after = rows("SELECT count(*) AS n FROM leadership_profiles WHERE tenant_id = :t", t=world.tenant)
    assert after == before


# ── The AI draft: dispatched, polled, never saved ────────────────────────────


def _run_recorded(name: str) -> str:
    from app.workers import runtime
    from app.workers.registry import resolve

    item = [entry for entry in dispatch_mod.recorded() if entry.name == name][-1]
    runtime.run_task(dispatch_mod.payload_for(item.id, resolve(name), item.args, item.kwargs))
    return item.id


def test_the_ai_draft_is_polled_by_its_owner_and_never_saved(
    caller: Caller, world: World, monkeypatch
) -> None:
    import json

    from app.services import llm_router

    answer = {
        "company_requirements": "",
        "department_requirements": lf.FH_LINE,
        "ideal_employee_expectations": lf.IDEAL_LINE,
        "department_expectations": [],
    }
    calls: list[str] = []

    async def _router(task_type, messages, response_format_json=False, session=None):
        calls.append(task_type)
        return json.dumps(answer)

    monkeypatch.setattr(llm_router, "chat_completion", _router)
    before = lf_counts(world)

    started = caller.as_("fh_engineering").post(f"{URL}/me/draft")
    assert started.status_code == 202, started.text
    task_id = started.json()["task_id"]
    pending = caller.get(f"{URL}/me/draft/{task_id}")
    assert pending.json()["status"] == "pending"

    ran = _run_recorded("pickready.draft_leadership")
    assert ran == task_id
    assert calls == ["leadership_draft"]

    drafted = caller.as_("fh_engineering").get(f"{URL}/me/draft/{task_id}")
    assert drafted.status_code == 200, drafted.text
    body = drafted.json()
    assert body["status"] == "drafted" and body["generated_by_ai"] is True
    assert body["fields"]["department_requirements"] == lf.FH_LINE
    assert "department_jobs" in {source["key"] for source in body["sources"]}
    # Somebody else polling the same id is told nothing exists.
    assert caller.as_("fh_finance").get(f"{URL}/me/draft/{task_id}").status_code == 404
    # Rule 37.11: not one leadership row was written by the draft.
    assert lf_counts(world) == before
    events = rows(
        "SELECT metadata_json AS metadata FROM audit_log WHERE tenant_id = :t "
        "AND action = 'leadership_draft_generated'",
        t=world.tenant,
    )
    assert events and events[-1]["metadata"]["status"] == "drafted"


def test_a_role_that_cannot_author_cannot_ask_for_a_draft(caller: Caller) -> None:
    assert caller.as_("recruiter").post(f"{URL}/me/draft").status_code == 403


def lf_counts(world: World) -> dict[str, int]:
    return {
        table: rows(f"SELECT count(*) AS n FROM {table} WHERE tenant_id = :t", t=world.tenant)[0]["n"]  # noqa: S608
        for table in ("leadership_profiles", "leadership_department_expectations", "job_leadership_contexts")
    }
