"""Every RBAC cell of spec-doc6 D3, and the role mapping it rests on.

D3, DERIVED FROM `docs/spec/RBAC_SPECIFICATION.md`
---------------------------------------------------
    HR Manager          creates, edits, completes and versions the artifact
    Super Admin         the same, under RBAC 7.5 override authority, recorded
                        in the audit trail as an override
    Recruiter, Hiring
    Manager             read-only, and ONLY the compiled artifact
    Interview Manager   no access
    Candidate           no access, no visibility, not even existence
    Internal BD staff   completion status and version number only

THE ROLE MAPPING IS THE TRAP, AND IT IS TESTED HERE
-----------------------------------------------------
The specification's role is titled "Client Super Admin" (5), and 7.1 scopes it
per client organisation: "Each client organization MUST have exactly one active
Super Admin". 7.2 says it is "the ultimate authority within the client
organization". That is this codebase's `Role.client`, which
`role_hierarchy.ROLE_LABELS` already labels "Super Admin".

It is NOT `Role.super_admin`, which is ReadyPick's own platform staff: that role
carries `tenant_id` NULL, sits in the OWNER token audience, and is outside every
client tenant boundary. Wiring D3's Super Admin to it would hand ReadyPick staff
authorship of tenant-owned client data, which is the exact thing D3 forbids when
it rules that Company DNA "cannot live in an internal Ready Pick Now BD portal,
which is not inside the client tenant boundary".

`test_the_platform_super_admin_role_is_granted_nothing` is that trap, closed.

WHAT IS BEING TESTED, AND WHAT IS NOT
---------------------------------------
The GRANTS are data: global `role_permissions` rows seeded by migration 0060,
resolved by `rbac.resolve_permission`. The GATES are the FastAPI dependencies on
each route. This file reads both out of source and checks that the product of
the two is D3's table. It does not need a database to do that, and it is the
check that catches a new route added without a gate, which is a missing line
rather than a bug in any component.

`tests/test_company_dna_api.py` drives the same matrix through real HTTP against
a real database.
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib

import pytest

from app.api import company_dna as router_module
from app.models.enums import Role
from app.schemas import company_dna as schemas
from app.services import rbac
from app.services import role_hierarchy

MANAGE = router_module.MANAGE_COMPANY_DNA
VIEW = router_module.VIEW_COMPANY_DNA

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0060_company_dna_versioning.py"
)


def _migration():
    """The migration module, loaded from its path.

    Alembic revisions are not importable by package name, and reading the
    grants out of the file that actually creates them is the only version of
    this test that cannot drift from the database.
    """
    spec = importlib.util.spec_from_file_location("_dna_migration_0060", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GRANTS = _migration()._GRANTS

#: The global template, as the RBAC engine will see it after 0060 runs.
GLOBAL_ROWS: dict[str, dict[str, bool]] = {}
for _role, _capability in GRANTS:
    GLOBAL_ROWS.setdefault(_role, {})[_capability] = True


def _may(role: Role, capability: str) -> bool:
    """Resolve one cell through the real engine, against the seeded rows."""
    return rbac.resolve_permission(
        tenant_rows={}, global_rows=GLOBAL_ROWS.get(role.value, {}), capability=capability
    )


# ── The role mapping ─────────────────────────────────────────────────────────


def test_the_specifications_client_super_admin_is_this_products_client_role() -> None:
    """RBAC 5 and 7.1, against `role_hierarchy` rather than against a comment."""
    assert role_hierarchy.ROLE_LABELS[Role.client] == "Super Admin"
    assert role_hierarchy.ROLE_RANK[Role.client] == 0
    assert Role.super_admin not in role_hierarchy.ROLE_RANK, (
        "the platform super_admin is not part of the client hierarchy; if it "
        "ever is, D3's Super Admin mapping has to be re-argued"
    )


def test_the_platform_super_admin_role_is_granted_nothing() -> None:
    """ReadyPick's own staff do not author a client's hiring philosophy.

    Company DNA is tenant-scoped, client-owned data. The platform role carries
    no tenant, so a grant to it would be authorship with no tenant boundary
    around it, which is precisely what D3 rules out.
    """
    assert not _may(Role.super_admin, MANAGE)
    assert not _may(Role.super_admin, VIEW)
    assert Role.super_admin.value not in GLOBAL_ROWS


# ── The D3 matrix, cell by cell ──────────────────────────────────────────────

#: (role, may author, may read the compiled artifact). Every role in the enum
#: appears, so a new role added to the product has to be placed here
#: deliberately rather than inheriting somebody else's row.
D3_MATRIX: tuple[tuple[Role, bool, bool], ...] = (
    (Role.client, True, True),                # RBAC 7.5 override authority
    (Role.hr_manager, True, True),            # the named Layer 2 owner
    (Role.recruitment_manager, True, True),   # ranks alongside hr_manager here
    (Role.recruiter, False, True),            # compiled artifact only
    (Role.hiring_manager, False, True),       # compiled artifact only
    (Role.interview_manager, False, False),   # no access
    (Role.candidate, False, False),           # no access, no visibility
    (Role.bd, False, False),                  # status only, on its own route
    (Role.super_admin, False, False),         # platform staff, not this tenant
)


def test_every_role_in_the_product_has_a_row_in_this_table() -> None:
    """A role added to the enum without a decision here would inherit deny by
    accident rather than by argument, and nobody would notice which."""
    assert {row[0] for row in D3_MATRIX} == set(Role)


@pytest.mark.parametrize(
    "role,may_author", [(row[0], row[1]) for row in D3_MATRIX], ids=lambda v: str(v)
)
def test_authorship_matches_d3(role: Role, may_author: bool) -> None:
    assert _may(role, MANAGE) is may_author


@pytest.mark.parametrize(
    "role,may_read", [(row[0], row[2]) for row in D3_MATRIX], ids=lambda v: str(v)
)
def test_reading_the_compiled_artifact_matches_d3(role: Role, may_read: bool) -> None:
    assert _may(role, VIEW) is may_read


def test_the_two_capabilities_are_separable() -> None:
    """Reading and authoring are separate grants on purpose.

    A tenant can widen who reads the compiled artifact without widening who can
    rewrite the philosophy every job is built on. One capability would make
    those the same decision.
    """
    assert MANAGE != VIEW
    readers = {role for role, _, may_read in D3_MATRIX if may_read}
    authors = {role for role, may_author, _ in D3_MATRIX if may_author}
    assert authors < readers, "every author reads, and not every reader authors"


def test_a_tenant_row_can_revoke_but_a_missing_row_never_grants() -> None:
    """The resolution order, at the cell that matters.

    A tenant that revokes authorship from its Recruitment Managers gets a
    `False` row, and `resolve_permission` tests for KEY PRESENCE, so an explicit
    False is a real revocation rather than a gap.
    """
    assert not rbac.resolve_permission(
        tenant_rows={MANAGE: False},
        global_rows=GLOBAL_ROWS["recruitment_manager"],
        capability=MANAGE,
    )
    assert not rbac.resolve_permission(
        tenant_rows={}, global_rows={}, capability=MANAGE
    )


# ── Every route is gated, and by the right thing ─────────────────────────────

#: The route table from spec-doc6 4.2, with the capability each must enforce.
#: Written out rather than derived, so the file states the contract and the
#: assertion checks the code against it.
EXPECTED_GATES: dict[tuple[str, str], str] = {
    ("POST", "/clients/{client_id}/company-dna"): MANAGE,
    ("GET", "/clients/{client_id}/company-dna"): VIEW,
    ("GET", "/clients/{client_id}/company-dna/versions"): MANAGE,
    ("GET", "/clients/{client_id}/company-dna/versions/{version}"): MANAGE,
    ("POST", "/clients/{client_id}/company-dna/{dna_id}/messages"): MANAGE,
    ("POST", "/clients/{client_id}/company-dna/{dna_id}/complete"): MANAGE,
    # Two kinds of caller, resolved inside the handler against the same engine.
    ("GET", "/clients/{client_id}/company-dna/status"): "resolved in handler",
}


def _routes() -> dict[tuple[str, str], object]:
    out = {}
    for route in router_module.router.routes:
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            out[(method, route.path)] = route
    return out


def test_the_router_exposes_exactly_the_routes_the_spec_names() -> None:
    assert set(_routes()) == set(EXPECTED_GATES)


def _capability_in_signature(route) -> str | None:
    """The capability string baked into this route's `require_capability`.

    Read off the closure the dependency factory returned, so a route gated on
    the WRONG capability fails here. Checking only that some gate is present
    would pass a route that gated authorship on the read capability.
    """
    for parameter in inspect.signature(route.endpoint).parameters.values():
        default = parameter.default
        dependency = getattr(default, "dependency", None)
        if dependency is None:
            continue
        closure = inspect.getclosurevars(dependency)
        capability = closure.nonlocals.get("capability")
        if isinstance(capability, str):
            return capability
    return None


@pytest.mark.parametrize(
    "key,expected",
    [(k, v) for k, v in EXPECTED_GATES.items() if v != "resolved in handler"],
    ids=lambda v: str(v),
)
def test_every_route_enforces_its_capability(key, expected: str) -> None:
    route = _routes()[key]
    assert _capability_in_signature(route) == expected


def test_the_gate_reader_can_tell_a_wrong_capability_from_a_right_one() -> None:
    """A guard on the guard.

    A reader that returned None for everything would make the sweep above pass
    against a completely ungated router.
    """
    from fastapi import APIRouter, Depends

    from app.api.deps import require_capability

    probe = APIRouter()

    @probe.post("/gated")
    async def _gated(_user=Depends(require_capability(VIEW))) -> dict:
        return {}

    @probe.post("/bare")
    async def _bare(body: dict) -> dict:
        return body

    found = {route.path: _capability_in_signature(route) for route in probe.routes}
    assert found["/gated"] == VIEW
    assert found["/bare"] is None


def test_the_status_route_is_the_only_one_without_a_capability_dependency() -> None:
    """And it resolves one itself, against the same engine.

    It serves two audiences with two database scopes, which a single dependency
    cannot express, so the capability check moved into the handler. What it must
    NOT have become is an ungated route: both branches call
    `rbac.has_capability`, and the response model has no field that could carry
    content.
    """
    route = _routes()[("GET", "/clients/{client_id}/company-dna/status")]
    assert _capability_in_signature(route) is None
    source = inspect.getsource(route.endpoint)
    assert source.count("rbac.has_capability") == 2
    assert "VIEW_COMPANY_DNA" in source
    assert "view_bd_customers" in source


# ── The read-only roles cannot reach the raw session ─────────────────────────


def test_the_compiled_response_model_has_no_session_field_at_all() -> None:
    """STRUCTURAL, not blanked at the call site.

    A field somebody has to remember to clear is a field the next call site
    forgets. The model a Recruiter's payload is built from simply has nowhere
    to put an answer, a transcript or a pending prompt.
    """
    fields = set(schemas.CompanyDNACompiledOut.model_fields)
    assert fields == {"version", "status", "completed_at", "authored_by", "understanding"}
    for forbidden in ("answers", "transcript", "session", "pending_prompt", "context"):
        assert forbidden not in fields


def test_the_status_response_model_carries_status_and_version_and_nothing_else() -> None:
    """D3's tightest cell: internal BD staff see completion and a version.

    Not the artifact, not the answers, not the plain-language restatement, and
    not the author. The model has no field for any of them, so there is no
    branch that could leak one.
    """
    fields = set(schemas.CompanyDNAStatusOut.model_fields)
    assert fields == {
        "client_id",
        "status",
        "version",
        "completed_at",
        "draft_open",
    }


def test_the_overview_puts_the_session_behind_an_optional_field() -> None:
    """`session` is None for a caller without authorship."""
    annotation = schemas.CompanyDNAOverviewOut.model_fields["session"].annotation
    assert "None" in str(annotation)
    assert schemas.CompanyDNAOverviewOut.model_fields["session"].default is None


def test_the_numeric_configuration_is_opt_in_and_absent_by_default() -> None:
    """spec-doc6 D8: raw internals live behind an audited view, and the product
    rule is that no number reaches a client. So the field exists, it defaults to
    absent, and the route that fills it writes an audit row."""
    field = schemas.CompanyDNAVersionDetailOut.model_fields["configuration"]
    assert field.default is None
    source = inspect.getsource(router_module.read_company_dna_version)
    assert "company_dna_configuration_read" in source


# ── The audit trail carries both principals ──────────────────────────────────


def test_every_mutation_writes_an_audit_row_with_both_principals() -> None:
    """RBAC 34: an AI-initiated mutation is attributable to the human on whose
    behalf it acted AND to the agent that executed it."""
    source = inspect.getsource(router_module._record)
    assert "actor_user_id=user.user_id" in source
    assert '"agent": AGENT_NAME' in source
    assert '"super_admin_override"' in source


@pytest.mark.parametrize(
    "handler",
    [router_module.start_company_dna, router_module.complete_company_dna],
    ids=lambda h: h.__name__,
)
def test_the_write_paths_call_the_audit_helper(handler) -> None:
    assert "_record(" in inspect.getsource(handler)


def test_the_override_flag_is_audit_annotation_and_not_authorization() -> None:
    """`_is_override` reads the role, which is the one thing a business router
    may never branch on for ACCESS. It is checked here that its only consumer
    is the audit metadata."""
    assert router_module._is_override(Role.client) is True
    assert router_module._is_override(Role.hr_manager) is False
    module_source = pathlib.Path(router_module.__file__).read_text(encoding="utf-8")
    calls = [
        line for line in module_source.splitlines() if "_is_override(" in line
    ]
    # One definition, one docstring-free call inside the audit metadata.
    assert len([line for line in calls if "def _is_override" not in line]) == 1


def test_the_router_never_branches_on_a_role_name_for_access() -> None:
    """CLAUDE.md rule 3, checked against the source rather than promised.

    `Role.client` appears exactly once, inside `_is_override`. Any other
    comparison against a role would be an authorization decision made in a
    business router instead of in the permission data.
    """
    import ast

    tree = ast.parse(pathlib.Path(router_module.__file__).read_text(encoding="utf-8"))
    # READS THE AST, NOT THE PROSE. The module docstring explains the role
    # mapping and names `Role.client` while doing so, and a line scan would
    # report that explanation as the violation. A check that flags its own
    # documentation is one somebody weakens rather than fixes.
    comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(operand, ast.Attribute)
            and isinstance(operand.value, ast.Name)
            and operand.value.id == "Role"
            for operand in [node.left, *node.comparators]
        )
    ]
    assert len(comparisons) == 1, [ast.unparse(node) for node in comparisons]
    assert ast.unparse(comparisons[0]) == "role == Role.client"

    # And it is inside `_is_override`, which decides audit wording rather than
    # access. Anywhere else it would be an access decision.
    owners = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and comparisons[0] in list(ast.walk(node))
    ]
    assert owners == ["_is_override"], owners


# ── The tenant boundary answers 404 ──────────────────────────────────────────


def test_a_cross_tenant_path_parameter_is_a_404_and_never_a_403() -> None:
    """RBAC 33: obscurity is not authorization, and a 403 confirms existence.

    Checked on the pure resolver so the rule is pinned even where no database
    is reachable; `tests/test_company_dna_api.py` drives the same case through
    real HTTP.
    """
    import uuid

    from fastapi import HTTPException

    from app.api.deps import CurrentUser

    mine = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(), tenant_id=mine, role=Role.hr_manager, audience="org"
    )
    assert router_module._resolve_client(user, mine) == mine
    with pytest.raises(HTTPException) as caught:
        router_module._resolve_client(user, uuid.uuid4())
    assert caught.value.status_code == 404
    assert "403" not in str(caught.value.detail)


def test_a_caller_with_no_tenant_is_a_404_too() -> None:
    """A platform token that somehow reached an org route must not resolve to
    the tenant it happens to be asking about."""
    import uuid

    from fastapi import HTTPException

    from app.api.deps import CurrentUser

    user = CurrentUser(
        user_id=uuid.uuid4(), tenant_id=None, role=Role.super_admin, audience="owner"
    )
    with pytest.raises(HTTPException) as caught:
        router_module._resolve_client(user, uuid.uuid4())
    assert caught.value.status_code == 404


# ── Client-facing strings ────────────────────────────────────────────────────


def test_no_em_dash_in_any_string_this_router_can_return() -> None:
    """The standing rule. Built from chr(8212) so a repo sweep cannot rewrite
    the check itself."""
    dash = chr(8212)
    source = pathlib.Path(router_module.__file__).read_text(encoding="utf-8")
    offenders = [
        line for line in source.splitlines() if dash in line and '"' in line
    ]
    assert not offenders, offenders


def test_the_scorecard_message_states_a_requirement_and_claims_no_enforcement() -> None:
    """spec-doc6 D3 asks for an actionable block rather than a mysterious
    failure. It is a REQUIREMENT: gate G1 lives in `hiring.gates`, is reached
    only from `miti.pipeline`, and nothing in the API or the workers imports
    that yet. A string promising that evaluation is prevented would be false
    today and would stop meaning anything the day it became true."""
    message = router_module.SCORECARD_BLOCK_MESSAGE
    assert "Company DNA required before this job's scorecard can be locked" in message
    for overclaim in ("nobody can be evaluated", "blocked", "prevented", "refused"):
        assert overclaim not in message.lower()


def test_the_portal_banner_says_the_same_sentence_as_the_server() -> None:
    """One sentence, two languages, read out of both.

    The Company DNA page renders the server's copy. The portal banner cannot:
    it reads the status route, whose response shape carries no message field
    on purpose, because that shape is also what internal Business Development
    staff see. So the sentence exists twice, and this reads both files and
    compares, the same way the report's section order is checked across its two
    renderers. The failure being prevented is a banner that keeps promising
    something the page has stopped saying.
    """
    frontend = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "components"
        / "company-dna"
        / "types.ts"
    )
    assert frontend.exists(), frontend
    source = frontend.read_text(encoding="utf-8")
    marker = "export const SCORECARD_BLOCK_SENTENCE ="
    assert marker in source, "the banner no longer declares the sentence"
    declared = source.split(marker, 1)[1].split(";", 1)[0]
    sentence = declared.strip().strip('"')
    assert sentence, declared
    assert sentence in router_module.SCORECARD_BLOCK_MESSAGE, (
        f"the banner says {sentence!r} and the server says "
        f"{router_module.SCORECARD_BLOCK_MESSAGE!r}"
    )
