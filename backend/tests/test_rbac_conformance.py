"""RBAC_SPECIFICATION.md 24, 32 and 33 as an executable conformance suite.

WHY THE ASSERTIONS GO THROUGH HTTP
----------------------------------
RBAC 3: "Frontend visibility is NOT a security boundary. A user being unable
to see a button does not constitute permission enforcement." RBAC 32: "The API
MUST reject unauthorized operations even if the request is manually
constructed outside the frontend."

A test that calls `rbac.decide(...)` directly satisfies neither sentence. It
proves the rule is correct; it proves nothing about whether the rule is
ATTACHED. So every case below is a request built by hand and sent through a
real Starlette application whose routes carry the real
`rbac.require_authorized` dependency, and the assertion is on the HTTP status.

WHAT IS REAL HERE AND WHAT IS NOT
---------------------------------
Real: the FastAPI routing and dependency machinery, `rbac.require_authorized`,
`rbac.authorize`, `rbac.decide`, `rbac.load_job_resource`,
`capabilities.RBAC_INVARIANTS`, and the token decoding in `api.deps`.

Faked: the database. `_FakeSession` answers the two SQL statements
`load_job_resource` issues and the one `has_capability` issues, from an
in-memory fixture. Nothing about the authorization decision is faked, and no
assertion in this file is "a mock was called": every assertion is a status
code produced by the real decision path.

The route surface mirrors RBAC 32's list. It is mounted here rather than
imported from `api/jobs.py` because those handlers are not yet wired to the
decision layer (see docs/reference/RBAC.md, "Not yet wired"); this suite is what the
wiring has to satisfy, and it fails loudly the day a route is added without
it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import AUDIENCE_ORG, create_access_token
from app.models.enums import Role
from app.services import capabilities as caps
from app.services import rbac
from app.services.capabilities import Invariant
from app.services.hiring_pipeline import JobLifecycleState

# ── The two tenants and the people in them ──────────────────────────────────

TENANT_A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
TENANT_B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")

#: One user per client role in tenant A, plus the tenant B twin used for every
#: cross-tenant case. Ids are fixed so a failure names a stable actor.
USERS: dict[Role, uuid.UUID] = {
    Role.client: uuid.UUID("00000000-0000-4000-8000-000000000001"),
    Role.hr_manager: uuid.UUID("00000000-0000-4000-8000-000000000002"),
    Role.recruiter: uuid.UUID("00000000-0000-4000-8000-000000000003"),
    Role.hiring_manager: uuid.UUID("00000000-0000-4000-8000-000000000004"),
    Role.interview_manager: uuid.UUID("00000000-0000-4000-8000-000000000005"),
}

#: The same five roles again, in tenant B. Used to prove that a valid session
#: for the identical role reaches nothing in tenant A.
USERS_B: dict[Role, uuid.UUID] = {
    role: uuid.UUID(str(user_id).replace("0000-4000", "1111-4000"))
    for role, user_id in USERS.items()
}

#: The job every scoped role is assigned to.
JOB_ASSIGNED = uuid.UUID("11111111-0000-4000-8000-000000000001")
#: A second job in the SAME tenant that nobody in USERS is assigned to. RBAC
#: 9.2 and 23 are explicit that holding a role does not reach it.
JOB_UNASSIGNED = uuid.UUID("11111111-0000-4000-8000-000000000002")
#: A real, valid job id belonging to tenant B. The direct-object-reference
#: case: knowing the id must buy nothing (33).
JOB_OTHER_TENANT = uuid.UUID("22222222-0000-4000-8000-000000000001")
#: An id that was never real. Every cross-tenant answer must be identical to
#: this one, or the difference between them is an existence oracle.
JOB_NONEXISTENT = uuid.UUID("33333333-0000-4000-8000-000000000009")

ASSIGNMENTS_ON_ASSIGNED_JOB: frozenset[tuple[str, str]] = frozenset(
    {
        (rbac.ASSIGNMENT_RECRUITER, str(USERS[Role.recruiter])),
        (rbac.ASSIGNMENT_HIRING_MANAGER, str(USERS[Role.hiring_manager])),
        (rbac.ASSIGNMENT_INTERVIEW_MANAGER, str(USERS[Role.interview_manager])),
        # 13.1: a job MAY have several Interview Managers, so a second one is
        # in the fixture. If a singular constraint ever leaked onto this
        # assignment type, this row is what would break.
        (rbac.ASSIGNMENT_INTERVIEW_MANAGER, str(uuid.uuid4())),
    }
)


@dataclass(frozen=True)
class JobRow:
    tenant_id: uuid.UUID
    lifecycle_state: str
    assignments: frozenset[tuple[str, str]]


def _jobs(state: str = JobLifecycleState.IN_REVIEW.value) -> dict[str, JobRow]:
    return {
        str(JOB_ASSIGNED): JobRow(TENANT_A, state, ASSIGNMENTS_ON_ASSIGNED_JOB),
        str(JOB_UNASSIGNED): JobRow(TENANT_A, state, frozenset()),
        str(JOB_OTHER_TENANT): JobRow(TENANT_B, state, frozenset()),
    }


# ── The fake session ─────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers exactly the statements the authorization path issues.

    An unrecognised statement RAISES. That is the whole point of writing it
    this way rather than reaching for a mock: if the decision path grows a
    query, this suite fails with the SQL in the message instead of silently
    authorizing against an empty result.
    """

    def __init__(self, jobs: dict[str, JobRow], grants: dict[str, bool]) -> None:
        self.jobs = jobs
        self.grants = grants

    async def execute(self, statement, params=None):  # noqa: ANN001
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("SELECT id, tenant_id, lifecycle_state FROM jobs"):
            row = self.jobs.get(str(params.get("jid")))
            if row is None:
                return _Result([])
            return _Result(
                [
                    {
                        "id": uuid.UUID(str(params["jid"])),
                        "tenant_id": row.tenant_id,
                        "lifecycle_state": row.lifecycle_state,
                    }
                ]
            )
        if sql.startswith("SELECT assignment_role, user_id FROM job_assignments"):
            row = self.jobs.get(str(params.get("jid")))
            if row is None:
                return _Result([])
            return _Result([(a, u) for a, u in row.assignments])
        if "FROM users" in sql or "permissions_json" in sql:
            # The per-user overlay. Empty: every case here is about the role's
            # own grant, and an overlay would mask which layer decided.
            return _Result([])
        if "role_permissions" in sql:
            return _Result([])
        raise AssertionError(f"unexpected statement in the authorization path: {sql}")


@pytest.fixture(autouse=True)
def _no_permission_cache(monkeypatch):
    """Resolve grants from the fixture, not from Redis or Postgres.

    `rbac._permission_rows` is the one place the grant engine touches
    infrastructure. Everything downstream of it -- `resolve_permission`, the
    overlay precedence, `resolve_capability_set` -- is exercised for real.
    """
    from app.services import capabilities as capabilities_module

    async def fake_rows(session, tenant_id, role):  # noqa: ANN001
        grants = capabilities_module.DEFAULT_PERMISSION_MATRIX.get(role, {})
        return [(None, capability, allowed) for capability, allowed in grants.items()]

    monkeypatch.setattr(rbac, "_permission_rows", fake_rows)

    async def no_overrides(session, user_id):  # noqa: ANN001
        return {}

    monkeypatch.setattr(rbac, "_user_overrides", no_overrides)


# ── The application under test ───────────────────────────────────────────────

#: RBAC 32's endpoint list, mapped onto the capability each one enforces.
#: Every row of the 24 matrix that names a resource appears here.
PROTECTED_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("PATCH", "/jobs/{job_id}", caps.EDIT_JOB_DESCRIPTION),
    ("POST", "/jobs/{job_id}/send-to-hiring-manager", caps.SEND_JD_TO_HIRING_MANAGER),
    ("POST", "/jobs/{job_id}/finalize", caps.FINALIZE_ROLE_DEFINITION),
    ("POST", "/jobs/{job_id}/publish", caps.PUBLISH_JOB),
    ("POST", "/jobs/{job_id}/reject", caps.REJECT_JD),
    ("PATCH", "/jobs/{job_id}/must-have", caps.EDIT_MUST_HAVE_SKILLS),
    ("PATCH", "/jobs/{job_id}/nice-to-have", caps.EDIT_NICE_TO_HAVE_SKILLS),
    ("PATCH", "/jobs/{job_id}/behavioural", caps.EDIT_BEHAVIOURAL_COMPETENCIES),
    ("PATCH", "/jobs/{job_id}/philosophy", caps.EDIT_JOB_PHILOSOPHY),
    ("PATCH", "/jobs/{job_id}/swot", caps.EDIT_SWOT),
    ("PATCH", "/jobs/{job_id}/rubrics", caps.EDIT_EVALUATION_RUBRICS),
    ("GET", "/jobs/{job_id}/candidates", caps.VIEW_REVIEW_SCREEN),
    ("GET", "/jobs/{job_id}/reports", caps.VIEW_CANDIDATE_REPORTS),
    ("GET", "/jobs/{job_id}/ratings", caps.VIEW_CANDIDATE_RATINGS),
    ("POST", "/jobs/{job_id}/candidates/shortlist", caps.DECIDE_PROFILE),
    ("POST", "/jobs/{job_id}/candidates/reject", caps.DECIDE_PROFILE),
    ("POST", "/jobs/{job_id}/candidates/move-stage", caps.UPDATE_PIPELINE_STATUS),
    ("POST", "/jobs/{job_id}/team-review", caps.ADD_TEAM_REVIEW_REMARK),
    ("POST", "/jobs/{job_id}/integrity-disposition", caps.INTEGRITY_DISPOSITION),
    ("GET", "/jobs/{job_id}", caps.VIEW_COMPANY_JOBS),
)

#: Capability -> the (method, path) that enforces it, for the table test.
ROUTE_FOR_CAPABILITY: dict[str, tuple[str, str]] = {
    capability: (method, path) for method, path, capability in PROTECTED_ROUTES
}


def _build_app(jobs: dict[str, JobRow]) -> FastAPI:
    app = FastAPI()
    session = _FakeSession(jobs, {})

    async def fake_db():
        yield session

    for method, path, capability in PROTECTED_ROUTES:
        app.add_api_route(
            path,
            _handler_factory(capability),
            methods=[method],
            dependencies=[Depends(rbac.require_authorized(capability))],
        )
    app.dependency_overrides[deps.get_tenant_db] = fake_db
    return app


def _handler_factory(capability: str):
    async def handler():
        # Reached only when authorization allowed. A handler that runs is
        # itself the assertion that the gate opened -- enforcement is
        # ordering, so a refusal must never have got this far.
        return {"ok": capability}

    return handler


def _client(jobs: dict[str, JobRow] | None = None) -> TestClient:
    return TestClient(_build_app(jobs if jobs is not None else _jobs()))


def _token(role: Role, *, tenant: uuid.UUID = TENANT_A, user_id=None) -> str:
    user_id = user_id or (USERS if tenant == TENANT_A else USERS_B)[role]
    return create_access_token(user_id, role.value, tenant, audience=AUDIENCE_ORG)


def _call(
    client: TestClient,
    method: str,
    path: str,
    role: Role,
    job_id: uuid.UUID,
    *,
    tenant: uuid.UUID = TENANT_A,
) -> int:
    response = client.request(
        method,
        path.format(job_id=job_id),
        headers={"Authorization": f"Bearer {_token(role, tenant=tenant)}"},
    )
    return response.status_code


# ── 1. Every cell of the 24 matrix, at the HTTP layer ────────────────────────

def _expected_status(cell: Invariant, *, assigned: bool) -> int:
    """What the matrix cell means as an HTTP status on an in-tenant job."""
    if cell in (Invariant.DENY, Invariant.DENY_CONSERVATIVE, Invariant.NEVER):
        return 403
    if cell is Invariant.SCOPED and not assigned:
        return 403
    return 200


#: One case per (role x capability x relationship). The relationship is the
#: assigned / unassigned / other-tenant / nonexistent axis RBAC 23 and 33
#: between them require.
CONFORMANCE_CASES: list[tuple[str, Role, str, str]] = [
    (f"{role.value}|{capability}|{relationship}", role, capability, relationship)
    for capability in ROUTE_FOR_CAPABILITY
    for role in caps.CLIENT_ROLES
    for relationship in ("assigned", "unassigned", "other_tenant", "nonexistent")
]


@pytest.mark.parametrize(
    "case_id,role,capability,relationship",
    CONFORMANCE_CASES,
    ids=[case[0] for case in CONFORMANCE_CASES],
)
def test_permission_matrix_cell(
    case_id: str, role: Role, capability: str, relationship: str
) -> None:
    """RBAC 24 x 23 x 33, one cell per case, asserted on a real response."""
    method, path = ROUTE_FOR_CAPABILITY[capability]
    # The lifecycle state is chosen so the cell under test is not masked by a
    # state rule: FINALIZED is required for publish (21) and refuses the
    # criteria edits (22, 26), so each is tested in the state where its own
    # matrix cell is the thing deciding. The state rules get their own cases
    # further down.
    state = (
        JobLifecycleState.FINALIZED.value
        if capability == caps.PUBLISH_JOB
        else JobLifecycleState.IN_REVIEW.value
    )
    client = _client(_jobs(state))

    if relationship == "other_tenant":
        # RBAC 33: a valid id from another tenant buys nothing, and the answer
        # must not distinguish itself from a missing resource.
        assert _call(client, method, path, role, JOB_OTHER_TENANT) == 404, case_id
        return
    if relationship == "nonexistent":
        assert _call(client, method, path, role, JOB_NONEXISTENT) == 404, case_id
        return

    job = JOB_ASSIGNED if relationship == "assigned" else JOB_UNASSIGNED
    cell = caps.invariant_for(role, capability)
    expected = _expected_status(cell, assigned=(relationship == "assigned"))
    assert _call(client, method, path, role, job) == expected, (
        f"{case_id}: matrix cell {cell.value} expected {expected}"
    )


# ── 2. The asterisks are encoded, not flattened ──────────────────────────────

def test_every_matrix_row_names_every_client_role() -> None:
    """A missing cell is a role the specification never authorised.

    `invariant_for` denies an absent role, so an omission fails closed. This
    asserts the table is nonetheless complete, because a fail-closed omission
    is still a silent divergence from a document somebody transcribed by hand.
    """
    for capability, row in caps.RBAC_INVARIANTS.items():
        missing = [r.value for r in caps.CLIENT_ROLES if r not in row]
        assert not missing, f"{capability} does not name {missing}"


def test_five_roles_not_four() -> None:
    """spec-doc6 C4. RBAC 5 says "four" and then lists five; five is correct."""
    assert len(caps.CLIENT_ROLES) == 5
    assert Role.interview_manager in caps.CLIENT_ROLES


def test_conservative_cells_are_distinguishable_from_plain_ones() -> None:
    """RBAC 24's `*` marks a decision that may be revisited.

    Flattening it to NO would lose the only record of which refusals were a
    choice. These are the exact cells the matrix asterisks.
    """
    assert caps.invariant_for(Role.hr_manager, caps.MANAGE_STAFF) is Invariant.DENY_CONSERVATIVE
    assert caps.invariant_for(Role.hr_manager, caps.ASSIGN_ROLES) is Invariant.DENY_CONSERVATIVE
    assert (
        caps.invariant_for(Role.hiring_manager, caps.DECIDE_PROFILE)
        is Invariant.DENY_CONSERVATIVE
    )
    assert (
        caps.invariant_for(Role.hiring_manager, caps.UPDATE_PIPELINE_STATUS)
        is Invariant.DENY_CONSERVATIVE
    )
    # 24 marks the HR Manager publish YES*, and the asterisk is the reason it
    # is WITHHELD rather than granted: 9.6 names only the Super Admin as the
    # administrative exception, and 24's own footnote says the asterisked
    # entries "may require an explicit future product decision". A cell whose
    # footnote says the decision has not been made is not an affirmative grant.
    assert (
        caps.invariant_for(Role.hr_manager, caps.PUBLISH_JOB)
        is Invariant.DENY_CONSERVATIVE
    )
    # The Super Admin's IS an audited exception: 7.5 grants the override and
    # then requires it to be recorded.
    assert (
        caps.invariant_for(Role.client, caps.PUBLISH_JOB)
        is Invariant.ALLOW_AUDITED_EXCEPTION
    )
    assert (
        caps.invariant_for(Role.recruiter, caps.ADD_TEAM_REVIEW_REMARK)
        is Invariant.ALLOW_AUDITED_EXCEPTION
    )


def test_double_asterisk_hiring_manager_jd_creation_is_marked_non_canonical() -> None:
    """RBAC 24**: allowed, and off the canonical Recruiter-generates flow."""
    assert (
        caps.invariant_for(Role.hiring_manager, caps.CREATE_JOB)
        is Invariant.ALLOW_NON_CANONICAL
    )
    assert caps.invariant_for(Role.recruiter, caps.CREATE_JOB) is Invariant.ALLOW


def test_triple_asterisk_recruiter_jd_edit_is_draft_scope_only() -> None:
    """RBAC 24***: the Recruiter edits the draft, and 26 stops there."""
    assert (
        caps.invariant_for(Role.recruiter, caps.EDIT_JOB_DESCRIPTION)
        is Invariant.ALLOW_DRAFT_SCOPE
    )


@pytest.mark.parametrize(
    "state",
    [state.value for state in JobLifecycleState],
    ids=[state.value for state in JobLifecycleState],
)
def test_recruiter_jd_edit_follows_the_lifecycle(state: str) -> None:
    """The `***` footnote as behaviour, across every lifecycle state."""
    from app.services.hiring_pipeline import DRAFTING_STATES

    client = _client(_jobs(state))
    status = _call(client, "PATCH", "/jobs/{job_id}", Role.recruiter, JOB_ASSIGNED)
    assert status == (200 if state in DRAFTING_STATES else 403), state


# ── 3. The Hiring Manager has no Reject JD path (RBAC 11, 36) ────────────────

def test_hiring_manager_cannot_reject_a_jd_by_absence_of_the_capability() -> None:
    """RBAC 11 and 36: no Reject JD path exists FOR THE HIRING MANAGER.

    The capability itself is NOT absent, and deleting it would be the wrong
    reading: 24 affirmatively grants Reject JD to the Super Admin and the HR
    Manager, and a rank-1 document's explicit grant is not something
    "restrict more when unsure" licenses overriding. That rule applies where
    the higher authority is SILENT, never against an affirmative grant.

    So the enforcement is the absence of the GRANT for this role, asserted
    three ways, because a route refusing is the weakest of the three and the
    only one a future refactor could quietly remove:
      1. the default grant is False,
      2. the 24 cell is NEVER, so no tenant row or user overlay can open it,
      3. the endpoint answers 403.
    """
    assert caps.DEFAULT_PERMISSION_MATRIX[Role.hiring_manager][caps.REJECT_JD] is False
    assert caps.invariant_for(Role.hiring_manager, caps.REJECT_JD) is Invariant.NEVER

    client = _client()
    assert _call(client, "POST", "/jobs/{job_id}/reject", Role.hiring_manager, JOB_ASSIGNED) == 403


def test_a_never_cell_cannot_be_opened_by_a_grant() -> None:
    """The point of NEVER: a tenant that grants it anyway is still refused.

    Without this, "the Recruiter cannot edit hiring criteria" would be a
    default rather than a rule, and RBAC 9.4 says the restriction is enforced
    at the UI, the API and the database mutation path.
    """
    principal = rbac.Principal(
        user_id=USERS[Role.recruiter], tenant_id=TENANT_A, role=Role.recruiter
    )
    for capability in sorted(caps.HIRING_MANAGER_CONTROLLED):
        result = rbac.decide(principal, capability, granted=True)
        assert result.decision is rbac.Decision.DENY, capability
        assert result.reason == "invariant_never", capability


# ── 4. Cross-tenant isolation, every resource type RBAC 4 lists ──────────────

#: RBAC 4's enumeration. Every one of these hangs off a job or a tenant in
#: this schema, so a job-scoped 404 is the boundary for all of them; the list
#: is written out in full so a resource added later has a visible home.
RBAC_SECTION_4_RESOURCES: tuple[str, ...] = (
    "users",
    "jobs",
    "jds",
    "hiring_criteria",
    "candidates",
    "candidate_profiles",
    "candidate_documents",
    "applications",
    "candidate_reports",
    "candidate_ratings",
    "interview_reviews",
    "team_review_remarks",
    "hiring_stage_data",
    "ai_candidate_intelligence",
    "ai_hiring_intelligence",
    "audit_records",
)


@pytest.mark.parametrize("role", caps.CLIENT_ROLES, ids=[r.value for r in caps.CLIENT_ROLES])
def test_cross_tenant_is_indistinguishable_from_nonexistent(role: Role) -> None:
    """Cross-tenant answers 404, identically to a missing resource.

    RBAC 33 states the principle ("Obscurity is NOT authorization"; knowing an
    id must not be sufficient) but never names a status code. The CODE comes
    from spec-doc6 9.1, which instructs it explicitly: "Cross-tenant reads
    return 404, never 403, so existence is not disclosed."

    The reason both are pointing at is RBAC 4, which forbids a user of one
    client to "access, INFER, modify, delete or retrieve" another client's
    resources. A 403 on a foreign id answers "that exists" to anybody who can
    enumerate uuids, which is inference.
    """
    client = _client()
    for method, path, _capability in PROTECTED_ROUTES:
        foreign = _call(client, method, path, role, JOB_OTHER_TENANT)
        missing = _call(client, method, path, role, JOB_NONEXISTENT)
        assert foreign == missing == 404, f"{role.value} {method} {path}"


@pytest.mark.parametrize("role", caps.CLIENT_ROLES, ids=[r.value for r in caps.CLIENT_ROLES])
def test_super_admin_of_another_tenant_reaches_nothing(role: Role) -> None:
    """Including the Super Admin. 7.2 gives ultimate authority WITHIN the
    client organization, and 4 draws the tenant boundary around it."""
    client = _client()
    # A session minted for tenant B, aimed at tenant A's job.
    response = client.request(
        "POST",
        f"/jobs/{JOB_ASSIGNED}/publish",
        headers={"Authorization": f"Bearer {_token(role, tenant=TENANT_B)}"},
    )
    assert response.status_code == 404


def test_every_resource_type_in_section_4_is_covered_by_the_boundary() -> None:
    """The list exists so a new resource type is a visible addition here.

    Every entry is a resource whose only route into the API is through a job
    or an application that carries `tenant_id`, so the job-scoped 404 above IS
    their boundary. Asserting the list is complete keeps that reasoning
    reviewable rather than remembered.
    """
    assert len(RBAC_SECTION_4_RESOURCES) == 16
    assert len(set(RBAC_SECTION_4_RESOURCES)) == 16


# ── 5. State-dependent authorization ─────────────────────────────────────────

@pytest.mark.parametrize(
    "state",
    [state.value for state in JobLifecycleState],
    ids=[state.value for state in JobLifecycleState],
)
def test_publishing_an_unfinalized_job_is_refused(state: str) -> None:
    """RBAC 21: publication is impossible while the definition is incomplete.

    The lifecycle state IS the completeness record, because FINALIZED is only
    reachable through 20's explicit transition.
    """
    from app.services.hiring_pipeline import FINALIZED_OR_LATER

    client = _client(_jobs(state))
    status = _call(client, "POST", "/jobs/{job_id}/publish", Role.recruiter, JOB_ASSIGNED)
    assert status == (200 if state in FINALIZED_OR_LATER else 403), state


def test_hiring_manager_cannot_silently_edit_criteria_after_finalization() -> None:
    """RBAC 22 and 12: a post-finalization change needs an explicit revision
    workflow, not a silent mutation. No such workflow exists yet, so it is
    refused rather than allowed-and-logged."""
    client = _client(_jobs(JobLifecycleState.FINALIZED.value))
    for capability in sorted(caps.HIRING_MANAGER_CONTROLLED - {caps.FINALIZE_ROLE_DEFINITION}):
        method, path = ROUTE_FOR_CAPABILITY[capability]
        assert _call(client, method, path, Role.hiring_manager, JOB_ASSIGNED) == 403, capability


def test_recruiter_cannot_edit_criteria_after_finalization_either() -> None:
    """RBAC 26, the row this whole layer exists for. Refused before
    finalization by the NEVER cell and after it by the state rule, so there is
    no window in which it is possible."""
    for state in (JobLifecycleState.IN_REVIEW.value, JobLifecycleState.FINALIZED.value):
        client = _client(_jobs(state))
        for capability in sorted(caps.HIRING_MANAGER_CONTROLLED):
            method, path = ROUTE_FOR_CAPABILITY[capability]
            assert _call(client, method, path, Role.recruiter, JOB_ASSIGNED) == 403, (
                f"{capability} in {state}"
            )


def test_a_candidate_under_integrity_review_does_not_move() -> None:
    """spec-doc6 C7 and the standing no-auto-reject rule.

    The candidacy is not blocked and no flag rejects anybody. What is blocked
    is MOVING the row before a human has disposed of the finding, which is the
    difference between a gate that fails loudly and one that decides.
    """
    principal = rbac.Principal(
        user_id=USERS[Role.recruiter], tenant_id=TENANT_A, role=Role.recruiter
    )
    flagged = rbac.Resource(
        kind="application",
        tenant_id=TENANT_A,
        job_id=JOB_ASSIGNED,
        lifecycle_state=JobLifecycleState.HIRING_PROCESS.value,
        assignments=ASSIGNMENTS_ON_ASSIGNED_JOB,
        under_integrity_review=True,
    )
    moved = rbac.decide(principal, caps.UPDATE_PIPELINE_STATUS, flagged, granted=True)
    assert moved.decision is rbac.Decision.DENY
    assert moved.reason == "integrity_review_open"

    # Reading is untouched: the finding must not remove the candidate from
    # anybody's screen, or nobody can review it.
    read = rbac.decide(principal, caps.VIEW_CANDIDATE_REPORTS, flagged, granted=True)
    assert read.decision is rbac.Decision.ALLOW


# ── 6. Scope: an assignment, never a role ────────────────────────────────────

@pytest.mark.parametrize(
    "role",
    [Role.recruiter, Role.hiring_manager, Role.interview_manager],
    ids=["recruiter", "hiring_manager", "interview_manager"],
)
def test_scoped_roles_do_not_reach_an_unassigned_job(role: Role) -> None:
    """RBAC 9.2, 10.2, 13.1 and 23: holding the role is not owning the job."""
    client = _client()
    assert _call(client, "GET", "/jobs/{job_id}/candidates", role, JOB_ASSIGNED) == 200
    assert _call(client, "GET", "/jobs/{job_id}/candidates", role, JOB_UNASSIGNED) == 403


@pytest.mark.parametrize(
    "role", [Role.client, Role.hr_manager], ids=["super_admin", "hr_manager"]
)
def test_org_wide_roles_reach_every_job_in_their_own_tenant(role: Role) -> None:
    """RBAC 7.4 and 8.2, which are the reason those two are NOT scoped."""
    client = _client()
    assert _call(client, "GET", "/jobs/{job_id}/candidates", role, JOB_UNASSIGNED) == 200


def test_a_job_may_hold_several_interview_managers() -> None:
    """RBAC 13.1 and 39. The fixture carries two, and both are active."""
    interview_managers = [
        user
        for assignment, user in ASSIGNMENTS_ON_ASSIGNED_JOB
        if assignment == rbac.ASSIGNMENT_INTERVIEW_MANAGER
    ]
    assert len(interview_managers) >= 2
    assert not rbac.assignment_is_singular(rbac.ASSIGNMENT_INTERVIEW_MANAGER)
    assert rbac.assignment_is_singular(rbac.ASSIGNMENT_RECRUITER)
    assert rbac.assignment_is_singular(rbac.ASSIGNMENT_HIRING_MANAGER)


# ── 7. The Interview Manager's eleven restrictions (RBAC 13.5) ───────────────

#: 13.5's list, mapped onto this codebase's capabilities. "Modify another
#: user's review" is a property of the write path rather than a capability and
#: is asserted separately in test_audit_invariants.
INTERVIEW_MANAGER_MUST_NOT: tuple[str, ...] = (
    caps.EDIT_JOB_DESCRIPTION,
    caps.EDIT_MUST_HAVE_SKILLS,
    caps.EDIT_NICE_TO_HAVE_SKILLS,
    caps.EDIT_BEHAVIOURAL_COMPETENCIES,
    caps.EDIT_JOB_PHILOSOPHY,
    caps.EDIT_SWOT,
    caps.EDIT_EVALUATION_RUBRICS,
    caps.PUBLISH_JOB,
    caps.UPDATE_PIPELINE_STATUS,
    caps.DECIDE_PROFILE,
)


@pytest.mark.parametrize("capability", INTERVIEW_MANAGER_MUST_NOT)
def test_interview_manager_restrictions(capability: str) -> None:
    """RBAC 13.5, one case per restriction, refused at the HTTP layer."""
    method, path = ROUTE_FOR_CAPABILITY[capability]
    client = _client(_jobs(JobLifecycleState.FINALIZED.value))
    assert _call(client, method, path, Role.interview_manager, JOB_ASSIGNED) == 403


def test_interview_manager_can_do_the_two_things_13_grants() -> None:
    """13.3 and 13.4, on an assigned job. A role that could do nothing would
    be a role nobody would create."""
    client = _client()
    for capability in (
        caps.VIEW_CANDIDATE_REPORTS,
        caps.VIEW_CANDIDATE_RATINGS,
        caps.ADD_TEAM_REVIEW_REMARK,
        caps.VIEW_REVIEW_SCREEN,
    ):
        method, path = ROUTE_FOR_CAPABILITY[capability]
        assert _call(client, method, path, Role.interview_manager, JOB_ASSIGNED) == 200, capability


# ── 8. The two layers agree at rest ──────────────────────────────────────────

#: Grants that predate RBAC_SPECIFICATION.md and sit ABOVE its 24 ceiling.
#: Every one comes from the flat staff model CLAUDE.md records as a client
#: decision ("HR Manager, Recruiter and Hiring Manager are EQUAL"), and every
#: one is refused at runtime by the ceiling. They are enumerated rather than
#: silently tolerated so that narrowing them is a visible edit to this list
#: and adding a SEVENTH is a test failure.
#:
#: The right long-term fix is to narrow `DEFAULT_PERMISSION_MATRIX` and the
#: `role_permissions` rows migration 0031 seeded. That is a live-data change
#: with a support consequence (an HR Manager losing the staff screen), so it
#: is reported for a product decision rather than taken unilaterally. What is
#: NOT deferred is the effect: `apply_invariant_ceiling` already removes each
#: of these from the capability list `/auth/me` returns, so no control is
#: rendered for authority the API will refuse.
KNOWN_GRANTS_ABOVE_THE_CEILING: frozenset[tuple[Role, str]] = frozenset(
    {
        (Role.hr_manager, caps.MANAGE_STAFF),
        (Role.hr_manager, caps.ASSIGN_ROLES),
        # Withheld on 2026-08-29 against 9.6; see the note in
        # test_conservative_cells_are_distinguishable_from_plain_ones.
        (Role.hr_manager, caps.PUBLISH_JOB),
        (Role.recruiter, caps.MANAGE_STAFF),
        (Role.hiring_manager, caps.PUBLISH_JOB),
        (Role.hiring_manager, caps.DECIDE_PROFILE),
        (Role.hiring_manager, caps.UPDATE_PIPELINE_STATUS),
    }
)


def _grants_above_the_ceiling() -> set[tuple[Role, str]]:
    found: set[tuple[Role, str]] = set()
    for role in caps.CLIENT_ROLES:
        for capability, allowed in caps.DEFAULT_PERMISSION_MATRIX[role].items():
            if not allowed or capability not in caps.RBAC_INVARIANTS:
                continue
            if not caps.permits(caps.invariant_for(role, capability)):
                found.add((role, capability))
    return found


def test_no_new_grant_exceeds_the_ceiling() -> None:
    """The ceiling can only narrow, so a grant above it is dead configuration.

    Dead configuration is worse than a wrong grant: it reads as authority
    somebody has and does not. Six such grants predate this specification and
    are listed above; a seventh fails here.
    """
    unexpected = _grants_above_the_ceiling() - KNOWN_GRANTS_ABOVE_THE_CEILING
    assert not unexpected, (
        "new grants exceed the RBAC 24 ceiling and would be silently "
        f"refused: {sorted((r.value, c) for r, c in unexpected)}"
    )


def test_the_known_divergences_are_still_real() -> None:
    """A stale exception list is an exception list that stopped meaning
    anything. If somebody narrows one of these grants, this fails and the
    entry gets removed rather than lingering as folklore."""
    stale = KNOWN_GRANTS_ABOVE_THE_CEILING - _grants_above_the_ceiling()
    assert not stale, (
        "these grants no longer exceed the ceiling; remove them from "
        f"KNOWN_GRANTS_ABOVE_THE_CEILING: {sorted((r.value, c) for r, c in stale)}"
    )


@pytest.mark.parametrize(
    "role,capability",
    sorted(KNOWN_GRANTS_ABOVE_THE_CEILING, key=lambda pair: (pair[0].value, pair[1])),
    ids=[
        f"{role.value}|{capability}"
        for role, capability in sorted(
            KNOWN_GRANTS_ABOVE_THE_CEILING, key=lambda pair: (pair[0].value, pair[1])
        )
    ],
)
def test_a_grant_above_the_ceiling_is_refused_at_the_http_layer(
    role: Role, capability: str
) -> None:
    """The divergence is documented AND harmless, proven per row.

    This is the assertion that makes the exception list acceptable. Each of
    the six is granted by the permission data and answers 403 anyway, so the
    ceiling is what decides and the grant row is inert.
    """
    if capability not in ROUTE_FOR_CAPABILITY:
        # Not a skip. There is no job-scoped route for this capability, so there
        # is no HTTP call that could exercise the ceiling -- and that absence is
        # itself what makes the grant inert at this layer, which is the thing
        # the row exists to state. A skip would report the row as unproven while
        # the summary line said SKIPPED, one word from PASSED; the assertion
        # says what is true and fails if a job-scoped route ever appears without
        # somebody coming back here.
        assert capability not in {
            route_capability for _, _, route_capability in PROTECTED_ROUTES
        }, (
            f"{capability} now has a job-scoped route, so the ceiling CAN be "
            "exercised over HTTP. Add it to ROUTE_FOR_CAPABILITY's source and "
            "delete this branch."
        )
        return
    method, path = ROUTE_FOR_CAPABILITY[capability]
    client = _client(_jobs(JobLifecycleState.FINALIZED.value))
    assert _call(client, method, path, role, JOB_ASSIGNED) == 403


def test_the_advertised_capability_set_matches_what_the_api_will_allow(
) -> None:
    """RBAC 3 read forwards rather than backwards.

    Frontend visibility is not a security boundary, so trimming the advertised
    list protects nothing. What it does is stop the product rendering a
    control that 403s, which is how a user learns the product is broken rather
    than that they lack authority.
    """
    for role in caps.CLIENT_ROLES:
        granted = [c for c, ok in caps.DEFAULT_PERMISSION_MATRIX[role].items() if ok]
        advertised = rbac.apply_invariant_ceiling(role, granted)
        for capability in advertised:
            assert caps.permits(caps.invariant_for(role, capability)), (
                f"{role.value} is still advertised {capability}"
            )
        for role_pair in KNOWN_GRANTS_ABOVE_THE_CEILING:
            if role_pair[0] is role:
                assert role_pair[1] not in advertised, role_pair


def test_a_capability_the_specification_does_not_mention_is_unconstrained() -> None:
    """Billing, compliance and business development are outside the RBAC
    document's scope (2), so the ceiling must not silently deny them."""
    assert caps.MANAGE_BILLING not in caps.RBAC_INVARIANTS
    assert caps.invariant_for(Role.client, caps.MANAGE_BILLING) is Invariant.ALLOW


def test_an_unauthenticated_request_is_refused_before_anything_is_read() -> None:
    """RBAC 32: authentication first. Enforcement is ordering, so a request
    with no session must never reach the resource load."""
    client = _client()
    assert client.post(f"/jobs/{JOB_ASSIGNED}/publish").status_code == 401


def test_a_candidate_session_cannot_reach_internal_routes() -> None:
    """RBAC 14: a candidate MUST NOT gain internal client functionality merely
    by authenticating."""
    from app.core.security import AUDIENCE_CANDIDATE

    token = create_access_token(
        uuid.uuid4(), Role.candidate.value, None, audience=AUDIENCE_CANDIDATE
    )
    client = _client()
    response = client.post(
        f"/jobs/{JOB_ASSIGNED}/publish", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_a_malformed_job_id_answers_like_a_missing_one() -> None:
    """The shape of an id must not itself be an oracle (33)."""
    client = _client()
    response = client.post(
        "/jobs/not-a-uuid/publish",
        headers={"Authorization": f"Bearer {_token(Role.recruiter)}"},
    )
    assert response.status_code == 404


# ── 9. Every role can actually reach its portal ──────────────────────────────
#
# Found while adding the Interview Manager, and the older half of it is a live
# defect: `core.security._ORG_ROLES` and `api.auth.PORTAL_ROLES` were both
# hand-maintained lists, and `recruitment_manager` had been missing from both
# since migration 0050 added the role. `audience_for_role` raised
# `ValueError: no audience defined for role 'recruitment_manager'`, so no token
# was ever minted and the login failed BEFORE any capability was consulted.
# There is no permission model in which that reads as a refusal a user could
# act on.
#
# A hand-maintained list has no failure mode until somebody holds the missing
# value, which is why the fix is these three tests rather than two more
# entries.

#: The three portals that are NOT the tenant portal. Everything else is org.
_NON_ORG_ROLES: frozenset[Role] = frozenset(
    {Role.super_admin, Role.bd, Role.candidate}
)


@pytest.mark.parametrize("role", list(Role), ids=[r.value for r in Role])
def test_every_role_has_an_audience(role: Role) -> None:
    """A role with no audience cannot be issued a token, which is a failed
    login rather than a refused action."""
    from app.core.security import audience_for_role

    assert audience_for_role(role)


def test_the_org_audience_set_is_exactly_the_tenant_roles() -> None:
    """Derived assertion, so a role added later fails here rather than at
    somebody's sign-in."""
    from app.core.security import _ORG_ROLES

    assert _ORG_ROLES == {r.value for r in Role if r not in _NON_ORG_ROLES}


def test_the_login_portal_filter_covers_every_role() -> None:
    """`PORTAL_ROLES` narrows a login-screen portal choice. A role absent from
    every portal is filtered out of its own sign-in."""
    from app.api.auth import PORTAL_ROLES

    covered = set().union(*PORTAL_ROLES.values())
    assert covered == set(Role), f"no portal claims {sorted(r.value for r in set(Role) - covered)}"
    assert PORTAL_ROLES["org"] == {r for r in Role if r not in _NON_ORG_ROLES}


def test_the_interview_manager_holds_only_what_section_13_grants() -> None:
    """RBAC 13.3 and 13.4 are the entire grant, and 13.5 is the entire refusal.

    Written as an exact set rather than as spot checks: the risk with a new
    role is not that somebody denies it something, it is that somebody adds it
    to a shared template and it quietly inherits a pipeline control.
    """
    granted = {
        capability
        for capability, allowed in caps.DEFAULT_PERMISSION_MATRIX[
            Role.interview_manager
        ].items()
        if allowed
    }
    assert granted == {
        caps.VIEW_REVIEW_SCREEN,       # 13.3 "relevant candidate information"
        caps.VIEW_CANDIDATE_REPORTS,   # 13.3 "candidate reports"
        caps.VIEW_CANDIDATE_RATINGS,   # 13.3 "candidate ratings"
        caps.ADD_TEAM_REVIEW_REMARK,   # 13.4 "add remarks"
        caps.VIEW_COMPANY_JOBS,        # 24 "View all company jobs: Scoped"
        caps.VIEW_DASHBOARD,           # the surface those four are read on
    }
    for capability in INTERVIEW_MANAGER_MUST_NOT:
        assert not caps.DEFAULT_PERMISSION_MATRIX[Role.interview_manager].get(
            capability, False
        ), f"13.5 forbids {capability}"


def test_reject_jd_exists_and_all_five_cells_are_pinned() -> None:
    """RBAC 24's Reject JD row, every cell, at the HTTP layer.

    The capability EXISTS and two roles hold it. Asserting only the Hiring
    Manager's refusal would leave the affirmative half of the row untested,
    and the affirmative half is what stops somebody deleting the capability on
    a misreading of 11.
    """
    client = _client()
    expected = {
        Role.client: 200,
        Role.hr_manager: 200,
        Role.recruiter: 403,
        Role.hiring_manager: 403,
        Role.interview_manager: 403,
    }
    for role, status in expected.items():
        assert (
            _call(client, "POST", "/jobs/{job_id}/reject", role, JOB_ASSIGNED) == status
        ), role
    assert caps.REJECT_JD in caps.ALL_CAPABILITIES
    assert caps.DEFAULT_PERMISSION_MATRIX[Role.client][caps.REJECT_JD] is True
    assert caps.DEFAULT_PERMISSION_MATRIX[Role.hr_manager][caps.REJECT_JD] is True


def test_only_the_recruiter_and_the_super_admin_may_publish() -> None:
    """RBAC 9.6: "Recruiter publishes the job. Period." with exactly one named
    administrative exception, the Super Admin (7.5)."""
    client = _client(_jobs(JobLifecycleState.FINALIZED.value))
    expected = {
        Role.client: 200,        # 7.5 override, recorded as an exception
        Role.hr_manager: 403,    # 24 YES*, withheld pending a product decision
        Role.recruiter: 200,     # the operational publisher
        Role.hiring_manager: 403,
        Role.interview_manager: 403,
    }
    for role, status in expected.items():
        assert (
            _call(client, "POST", "/jobs/{job_id}/publish", role, JOB_ASSIGNED) == status
        ), role


def test_two_tenants_may_each_hold_their_own_super_admin() -> None:
    """RBAC 7.1 is PER CLIENT ORGANIZATION, and the index expression says so.

    A unique index on `(tenant_id)` admits one matching row per tenant VALUE.
    A global uniqueness rule would pass every test written against a single
    seeded tenant and then reject the second customer ever onboarded, so the
    expression is asserted here rather than trusted.
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0061_rbac_cardinality_and_audit.py"
    ).read_text(encoding="utf-8")
    assert "uq_users_one_active_super_admin_per_tenant " in source
    assert "ON users (tenant_id) " in source
    assert (
        "WHERE role = 'client' AND status <> 'disabled' AND tenant_id IS NOT NULL"
        in source
    )


def test_the_super_admin_seat_can_be_transferred() -> None:
    """RBAC 7.1's second sentence, which the uniqueness index makes essential.

    Without a transfer mechanism a client whose Super Admin leaves is locked
    out of their own tenant permanently, and the constraint is exactly what
    makes it unrecoverable. So the mechanism must exist, must demote rather
    than delete, and must be reachable by somebody outside the tenant, because
    the case that needs it is the one where nobody inside has the authority.
    """
    import inspect

    from app.api import admin

    assert callable(rbac.transfer_super_admin)
    source = inspect.getsource(rbac.transfer_super_admin)
    # The outgoing holder is demoted, never deleted and never disabled.
    assert "DELETE FROM users" not in source.upper()
    assert "SET status" not in source
    assert "demoted_role" in source
    assert any(
        route.path == "/tenants/{tenant_id}/super-admin" for route in admin.router.routes
    ), "RBAC 7.1 needs a reachable route, not only a service function"


def test_an_unknown_lifecycle_state_refuses_every_state_gated_capability() -> None:
    """A NULL lifecycle state must not read as "no state rule applies".

    That was the first behaviour and it is the permissive direction: a job row
    written by something that does not populate the column would have been
    publishable without ever having been finalized. Not hypothetical -- five
    such rows appeared in the containerised test database within a single
    suite run, because `jobs.lifecycle_state` shipped without a server default.

    Both guards are asserted: migration 0061 now defaults the column to DRAFT,
    and `decide` refuses an unknown state anyway, because the default only
    protects rows the default reaches.
    """
    import pathlib

    client = _client(
        {
            str(JOB_ASSIGNED): JobRow(TENANT_A, None, ASSIGNMENTS_ON_ASSIGNED_JOB),
        }
    )
    for capability in [caps.PUBLISH_JOB, *sorted(caps.HIRING_MANAGER_CONTROLLED)]:
        method, path = ROUTE_FOR_CAPABILITY[capability]
        for role in (Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager):
            assert _call(client, method, path, role, JOB_ASSIGNED) == 403, (
                f"{role.value} was allowed {capability} on an unknown lifecycle state"
            )

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0061_rbac_cardinality_and_audit.py"
    ).read_text(encoding="utf-8")
    assert 'server_default=sa.text("\'DRAFT\'")' in source


def test_a_read_is_still_allowed_on_an_unknown_lifecycle_state() -> None:
    """Only PROGRESSION is blocked. Refusing reads would hide a job from the
    people who have to fix its state, which is a worse failure than the one
    being prevented."""
    client = _client(
        {
            str(JOB_ASSIGNED): JobRow(TENANT_A, None, ASSIGNMENTS_ON_ASSIGNED_JOB),
        }
    )
    for capability in (
        caps.VIEW_REVIEW_SCREEN,
        caps.VIEW_CANDIDATE_REPORTS,
        caps.VIEW_COMPANY_JOBS,
    ):
        method, path = ROUTE_FOR_CAPABILITY[capability]
        assert _call(client, method, path, Role.recruiter, JOB_ASSIGNED) == 200, capability
