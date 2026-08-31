"""Every dashboard control x every role x every scope, at the HTTP layer.

WHY THIS IS A TABLE TEST AND WHY IT GOES THROUGH HTTP
------------------------------------------------------
spec-doc6 §8.2: "Write a table-driven test: for each of the five roles x each
column control x (assigned job / unassigned job / other tenant's job), assert
the expected enabled/disabled/absent/403 outcome."

RBAC §3 and §32 say why the assertion has to be a status code rather than a
call into `rbac.decide`: "Frontend visibility is NOT a security boundary" and
"The API MUST reject unauthorized operations even if the request is manually
constructed outside the frontend." A test of the rule proves the rule is
right. Only a request proves the rule is ATTACHED, which is the failure that
actually ships.

WHAT IS REAL HERE
-----------------
Everything except the identity of the caller. Real routes, real
`rbac.require_authorized` dependencies, real `capabilities.RBAC_INVARIANTS`,
real Postgres with real Row Level Security, real `job_assignments` rows. The
principal is injected because minting a Firebase session per case would test
the login flow, which has its own suite.

THE EXPECTED OUTCOME IS DERIVED, NOT LISTED
-------------------------------------------
`_expected` computes the status from the §24 cell in `capabilities.py`. A
hand-written expectation table would be a second copy of the matrix, and the
first time somebody corrected a cell the test would defend the old value. What
IS written by hand is the two places this surface deliberately departs from a
naive reading of the cell, and each carries its reason.
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

from app.api import dashboard as dashboard_router
from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services import capabilities as caps
from app.services import rbac
from app.services.capabilities import Invariant

BASE = "/api/v1/dashboard"

#: The five client roles RBAC §24 enumerates. `recruitment_manager` is a
#: product role the specification does not speak to and is out of this matrix
#: by construction: `invariant_for` denies a role absent from a §24 row, and
#: asserting a specification outcome for a role the specification never names
#: would be inventing one.
MATRIX_ROLES: tuple[Role, ...] = (
    Role.client,
    Role.hr_manager,
    Role.recruiter,
    Role.hiring_manager,
    Role.interview_manager,
)

#: control -> (method, path template, capability). The capability comes from
#: the router's own `DASHBOARD_CONTROLS`, so this table cannot drift from the
#: routes by restating a mapping somebody has to keep in step.
CONTROLS: dict[str, tuple[str, str]] = {
    "profile": ("GET", "/jobs/{job_id}/candidates/{link_id}/profile"),
    "team_review": ("PUT", "/jobs/{job_id}/candidates/{link_id}/team-review"),
    "stage_move": ("POST", "/jobs/{job_id}/candidates/{link_id}/stage"),
    "integrity_disposition": (
        "POST",
        "/jobs/{job_id}/candidates/{link_id}/integrity-disposition",
    ),
    "calibration": ("GET", "/jobs/{job_id}/candidates/{link_id}/calibration"),
}

SCOPES = ("assigned", "unassigned", "other_tenant")

#: Which per-job assignment each scoped role needs (RBAC §23). Mirrors
#: `rbac._ASSIGNMENT_FOR_ROLE`, read from it so the fixture cannot seed the
#: wrong assignment type and then pass.
ASSIGNMENT_FOR_ROLE = {
    Role.recruiter: rbac.ASSIGNMENT_RECRUITER,
    Role.hiring_manager: rbac.ASSIGNMENT_HIRING_MANAGER,
    Role.interview_manager: rbac.ASSIGNMENT_INTERVIEW_MANAGER,
}


class World:
    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.users: dict[tuple[uuid.UUID, Role], uuid.UUID] = {}
        self.job_assigned = uuid.uuid4()
        self.job_unassigned = uuid.uuid4()
        self.job_other_tenant = uuid.uuid4()
        self.link: dict[uuid.UUID, uuid.UUID] = {}
        self.candidate: dict[uuid.UUID, uuid.UUID] = {}
        self.evaluation: dict[uuid.UUID, uuid.UUID] = {}

    def job_for(self, scope: str) -> uuid.UUID:
        return {
            "assigned": self.job_assigned,
            "unassigned": self.job_unassigned,
            "other_tenant": self.job_other_tenant,
        }[scope]


def _sessions():
    # NullPool: an asyncpg connection belongs to the loop that opened it, and
    # TestClient runs the application on a fresh loop per test.
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM calibration_records LIMIT 0"))
            await conn.execute(
                sa.text("SELECT prescreen_grade FROM job_candidate_links LIMIT 0")
            )
        return True
    except Exception:  # noqa: BLE001 - the reason is reported by the skip
        return False
    finally:
        await engine.dispose()


async def _seed(state: World) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            for tenant, label in (
                (state.tenant_a, "Alpha Testing Co"),
                (state.tenant_b, "Beta Testing Co"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                        "VALUES (:id, :name, :domain, 'pending')"
                    ),
                    {"id": tenant, "name": label, "domain": f"{tenant}.dash.test"},
                )
                for role in MATRIX_ROLES:
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
                            "email": f"{user_id}@dash.test",
                            # Synthetic, and obviously so (spec-doc6 C14).
                            "name": f"Test {role.value.replace('_', ' ').title()}",
                        },
                    )

            for job_id, tenant in (
                (state.job_assigned, state.tenant_a),
                (state.job_unassigned, state.tenant_a),
                (state.job_other_tenant, state.tenant_b),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO jobs (id, tenant_id, title, jd_json, status, "
                        "lifecycle_state) VALUES (:id, :tenant, :title, "
                        "'{}'::jsonb, 'ratified', 'CANDIDATE_APPLICATIONS')"
                    ),
                    {"id": job_id, "tenant": tenant, "title": "Test Role"},
                )
                candidate_id = uuid.uuid4()
                link_id = uuid.uuid4()
                state.candidate[job_id] = candidate_id
                state.link[job_id] = link_id
                await conn.execute(
                    sa.text(
                        "INSERT INTO candidates (id, tenant_id, full_name, email, "
                        "consent_databank) VALUES (:id, :tenant, :name, :email, false)"
                    ),
                    {
                        "id": candidate_id,
                        "tenant": tenant,
                        "name": "Test Candidate Zero",
                        "email": f"{candidate_id}@dash.test",
                    },
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO job_candidate_links "
                        "(id, tenant_id, job_id, candidate_id, source, status) "
                        "VALUES (:id, :tenant, :job, :cand, 'fresh', 'applied')"
                    ),
                    {
                        "id": link_id,
                        "tenant": tenant,
                        "job": job_id,
                        "cand": candidate_id,
                    },
                )
                # An evaluation on every job, so a refusal is never confusable
                # with a 404 for an absent Ready Pick Profile. Every gate
                # PASSED: the integrity lock has its own test, and leaving a
                # finding open here would make the stage cases refuse for the
                # wrong reason.
                evaluation_id = uuid.uuid4()
                state.evaluation[job_id] = evaluation_id
                await conn.execute(
                    sa.text(
                        "INSERT INTO evaluations (id, tenant_id, job_id, link_id, "
                        "aggregate_json, gate_results_json) VALUES "
                        "(:id, :tenant, :job, :link, CAST(:agg AS jsonb), CAST('[]' AS jsonb))"
                    ),
                    {
                        "id": evaluation_id,
                        "tenant": tenant,
                        "job": job_id,
                        "link": link_id,
                        "agg": '{"overall_grade": "Matching", '
                        '"adjusted_composite": 78.0}',
                    },
                )

            # RBAC §23: the three per-job roles are assigned to ONE job in
            # tenant A and to nothing else. That single fact is what every
            # `unassigned` case below is testing.
            for role, assignment_role in ASSIGNMENT_FOR_ROLE.items():
                await conn.execute(
                    sa.text(
                        "INSERT INTO job_assignments "
                        "(tenant_id, job_id, user_id, assignment_role) VALUES "
                        "(:tenant, :job, :user, :assignment)"
                    ),
                    {
                        "tenant": state.tenant_a,
                        "job": state.job_assigned,
                        "user": state.users[(state.tenant_a, role)],
                        "assignment": assignment_role,
                    },
                )
    finally:
        await engine.dispose()


async def _teardown(state: World) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            # Tenants CASCADE to everything below them.
            await conn.execute(
                sa.text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                {"ids": [state.tenant_a, state.tenant_b]},
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    if not asyncio.run(_reachable()):
        pytest.skip(
            "no migrated test database reachable; run "
            "`./scripts/test.sh` or `alembic upgrade head` against it first"
        )
    state = World()
    asyncio.run(_seed(state))
    try:
        yield state
    finally:
        asyncio.run(_teardown(state))


@pytest.fixture(autouse=True)
def _no_permission_cache(monkeypatch):
    """The grant engine caches permission rows in Redis for two minutes.

    A double for the CACHE, never for the decision: resolution still runs
    through the real engine against the real `role_permissions` rows.
    """
    from app.services import tenant_cache

    async def _miss(key):  # noqa: ANN001
        return None

    async def _noop(key, value, *, ttl=120):  # noqa: ANN001
        return None

    monkeypatch.setattr(tenant_cache, "get_json", _miss)
    monkeypatch.setattr(tenant_cache, "set_json", _noop)


async def _reset(state: World) -> None:
    """Put every application back to `applied` with no reviews or dispositions.

    THREE OF THE FIVE CONTROLS MUTATE, and the matrix runs each of them 15
    times against the same three rows. Without this, the second stage move
    answers 409 ("already assessment_invited") and the assertion reads as an
    authorization failure when it is an ordering artifact. Resetting between
    cases is what keeps every one of the 75 assertions about authorization and
    nothing else.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL app.bypass_rls = 'on'"))
            links = [str(link) for link in state.link.values()]
            await conn.execute(
                sa.text(
                    "UPDATE job_candidate_links SET status = 'applied' "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": links},
            )
            await conn.execute(
                sa.text(
                    "DELETE FROM pipeline_status "
                    "WHERE job_candidate_link_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": links},
            )
            await conn.execute(
                sa.text(
                    "DELETE FROM candidate_team_reviews "
                    "WHERE job_candidate_link_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": links},
            )
            await conn.execute(
                sa.text(
                    "DELETE FROM review_dispositions WHERE link_id = "
                    "ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": links},
            )
    finally:
        await engine.dispose()


@pytest.fixture
def reset_rows(world: World):
    asyncio.run(_reset(world))
    yield


class Caller:
    def __init__(self) -> None:
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_role(self, world: World, role: Role, tenant: uuid.UUID) -> None:
        self.principal = CurrentUser(
            user_id=world.users[(tenant, role)],
            tenant_id=tenant,
            role=role,
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
                # RLS is the real boundary (claude.md rule 1). The scope is
                # entered here exactly as the production dependency enters it,
                # so a cross-tenant case is refused by Postgres and not only by
                # the application's WHERE clause.
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


# ── The expectation, derived from the matrix ─────────────────────────────────


def _expected(control: str, role: Role, scope: str) -> int:
    """The status this (control, role, scope) must produce.

    Computed from the §24 cell rather than listed, so the test tracks the
    matrix instead of freezing a snapshot of it.
    """
    capability = dashboard_router.DASHBOARD_CONTROLS[control]
    cell = caps.invariant_for(role, capability)

    # RBAC §4 and §33: a cross-tenant hit is answered like a resource that was
    # never there. 404, never 403 -- a 403 would confirm the row exists, which
    # is an inference the specification forbids.
    if scope == "other_tenant":
        return 404

    if cell in (Invariant.DENY, Invariant.DENY_CONSERVATIVE, Invariant.NEVER):
        return 403
    if cell is Invariant.SCOPED and scope == "unassigned":
        return 403

    # THE ONE DELIBERATE NARROWING. §24 marks the Recruiter and the Hiring
    # Manager YES* on "Add Team Review remarks" with no scope marker, while
    # marking "View candidates" and "View candidate ratings" scoped for the
    # same two roles. Remarking on a candidate you may not see is incoherent,
    # so the write route requires both, and an unassigned job is 404 -- the
    # answer the VIEW capability gives, since that is the check that failed.
    if control == "team_review" and scope == "unassigned":
        if caps.invariant_for(role, caps.VIEW_CANDIDATE_RATINGS) is Invariant.SCOPED:
            return 404

    return 200


CASES = [
    (f"{role.value}|{control}|{scope}", control, role, scope)
    for control in CONTROLS
    for role in MATRIX_ROLES
    for scope in SCOPES
]


def _body(control: str) -> dict | None:
    if control == "team_review":
        return {"verdict": "pass", "remarks": "Spoke to them; strong on the migration."}
    if control == "stage_move":
        return {"status": "assessment_invited"}
    if control == "integrity_disposition":
        return {"disposition": "cleared", "note": "Checked with the candidate."}
    return None


@pytest.mark.parametrize(
    "case_id,control,role,scope", CASES, ids=[case[0] for case in CASES]
)
def test_dashboard_control_matrix(
    case_id: str,
    control: str,
    role: Role,
    scope: str,
    client: Caller,
    world: World,
    reset_rows,
) -> None:
    method, template = CONTROLS[control]
    job_id = world.job_for(scope)
    tenant = world.tenant_a
    client.as_role(world, role, tenant)
    response = client.http.request(
        method,
        BASE + template.format(job_id=job_id, link_id=world.link[job_id]),
        json=_body(control),
    )
    assert response.status_code == _expected(control, role, scope), (
        f"{case_id} answered {response.status_code}: {response.text[:200]}"
    )


def test_the_case_count_covers_the_whole_table() -> None:
    """Five roles x five controls x three scopes. Stated so a silently
    shrunken parametrisation is visible in the diff."""
    assert len(CASES) == len(MATRIX_ROLES) * len(CONTROLS) * len(SCOPES) == 75


def test_a_recruiter_sees_only_their_assigned_jobs_candidates(
    client: Caller, world: World
) -> None:
    """RBAC §9.2 and §23, on the LIST rather than on one row.

    The scoped roles must not see the tenant's whole funnel. This is the
    scoping that matters most in practice: a refusal on one URL is loud, and a
    list quietly containing rows a person should not see is not.
    """
    client.as_role(world, Role.recruiter, world.tenant_a)
    body = client.http.get(f"{BASE}/candidates").json()
    seen = {row["job_id"] for row in body["rows"]}
    assert seen == {str(world.job_assigned)}
    assert body["controls"]["scoped_to_assignments"] is True


def test_an_hr_manager_sees_every_job_in_their_own_tenant_and_no_other(
    client: Caller, world: World
) -> None:
    client.as_role(world, Role.hr_manager, world.tenant_a)
    body = client.http.get(f"{BASE}/candidates").json()
    seen = {row["job_id"] for row in body["rows"]}
    assert seen == {str(world.job_assigned), str(world.job_unassigned)}
    assert str(world.job_other_tenant) not in seen
    assert body["controls"]["scoped_to_assignments"] is False


def test_the_interview_manager_view_is_read_plus_team_review(
    client: Caller, world: World
) -> None:
    """spec-doc6 C16: the Dashboard document does not mention Interview
    Managers at all, and they are core users of it. Their view is read-only
    plus Team Review."""
    client.as_role(world, Role.interview_manager, world.tenant_a)
    controls = client.http.get(f"{BASE}/candidates").json()["controls"]
    assert controls["can_team_review"] is True
    assert controls["can_move_stage"] is False
    assert controls["stage_disabled_reason"]
    assert controls["can_disposition_integrity"] is False
    assert controls["can_view_calibration"] is False


def test_a_disabled_stage_control_explains_itself(
    client: Caller, world: World
) -> None:
    """The specification asks for a tooltip on hover rather than a greyed
    control with no reason."""
    client.as_role(world, Role.hiring_manager, world.tenant_a)
    body = client.http.get(
        f"{BASE}/jobs/{world.job_assigned}/candidates/"
        f"{world.link[world.job_assigned]}/stage"
    ).json()
    assert body["can_move"] is False
    assert body["disabled_reason"]
    assert body["allowed_transitions"] == []


def test_the_calibration_view_reaches_exactly_super_admin_and_hr_manager(
    client: Caller, world: World
) -> None:
    """spec-doc6 D8, pinned as a POPULATION rather than as a capability name.

    The route borrows `INTEGRITY_DISPOSITION`, whose §24-derived cell set is
    the same population D8 names. This test is what makes that borrowing safe:
    the day the two populations diverge, this fails rather than a screen
    leaking the engine's internals.
    """
    reached = set()
    for role in MATRIX_ROLES:
        client.as_role(world, role, world.tenant_a)
        response = client.http.get(
            f"{BASE}/jobs/{world.job_assigned}/candidates/"
            f"{world.link[world.job_assigned]}/calibration"
        )
        if response.status_code == 200:
            reached.add(role)
    assert reached == {Role.client, Role.hr_manager}
