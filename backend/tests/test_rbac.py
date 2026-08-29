"""RBAC resolution (ESD §6): tenant override > global template > deny."""
from app.models.enums import Role
from app.services.capabilities import (
    ALL_CAPABILITIES,
    CREATE_JOB,
    DEFAULT_PERMISSION_MATRIX,
    EDIT_ROLE_PERMISSIONS,
    MANAGE_STAFF,
    SEND_OUTREACH,
    VIEW_DATABANK,
    _STAFF_OPERATIONAL,
)
from app.services.rbac import resolve_capability_set, resolve_permission


def test_tenant_override_beats_global_allow() -> None:
    # Global template says yes; the tenant explicitly revoked it.
    assert resolve_permission({VIEW_DATABANK: False}, {VIEW_DATABANK: True}, VIEW_DATABANK) is False


def test_tenant_override_beats_global_deny() -> None:
    # Global template says no; the tenant explicitly granted it.
    assert resolve_permission({SEND_OUTREACH: True}, {SEND_OUTREACH: False}, SEND_OUTREACH) is True


def test_global_fallback_when_no_tenant_row() -> None:
    assert resolve_permission({}, {VIEW_DATABANK: True}, VIEW_DATABANK) is True
    assert resolve_permission({}, {VIEW_DATABANK: False}, VIEW_DATABANK) is False


def test_missing_rows_deny() -> None:
    assert resolve_permission({}, {}, VIEW_DATABANK) is False


def test_unrelated_rows_do_not_grant() -> None:
    assert resolve_permission({SEND_OUTREACH: True}, {SEND_OUTREACH: True}, VIEW_DATABANK) is False


def test_capability_constants_are_unique_strings() -> None:
    assert len(ALL_CAPABILITIES) == len(set(ALL_CAPABILITIES))
    assert all(isinstance(c, str) and c for c in ALL_CAPABILITIES)


def test_default_matrix_uses_known_capabilities_only() -> None:
    for role, caps in DEFAULT_PERMISSION_MATRIX.items():
        assert isinstance(role, Role)
        for capability in caps:
            assert capability in ALL_CAPABILITIES, capability


def test_hierarchy_roles_begin_with_the_same_operational_template() -> None:
    """The flat model still holds for the OPERATIONAL set, and stops there.

    PRD v1.0 4 (FINAL) made HR Manager, Recruiter and Hiring Manager equal, and
    this test asserted `rm == hr == rec` on the whole grant dict. That equality
    stopped being true on 2026-08-29, when `docs/spec/RBAC_SPECIFICATION.md`
    arrived as precedence rank 1 and 25.1 drew the distinction it calls
    fundamental: the Recruiter DRAFTS the JD and the Hiring Manager OWNS the
    role definition. Two roles that hold identical grants cannot express that.

    So the assertion is narrowed rather than deleted, and narrowed to the thing
    the flat model was actually about: every staff role still reaches the whole
    operational pipeline, and nobody lost a capability they use daily. What
    diverges is exactly the set 10.4 hands to the Hiring Manager and 9.4 takes
    from the Recruiter, and `test_the_flat_model_diverges_only_where_24_says_so`
    below pins that the divergence is that set and nothing else.
    """
    rm = DEFAULT_PERMISSION_MATRIX[Role.recruitment_manager]
    hr = DEFAULT_PERMISSION_MATRIX[Role.hr_manager]
    rec = DEFAULT_PERMISSION_MATRIX[Role.recruiter]
    hm = DEFAULT_PERMISSION_MATRIX[Role.hiring_manager]
    # The operational half is still identical across all four.
    for grants in (rm, hr, rec, hm):
        assert _STAFF_OPERATIONAL.items() <= grants.items()
    # ...and the two organisation-wide roles are still identical to each other.
    assert rm == hr


def test_the_flat_model_diverges_only_where_24_says_so() -> None:
    """The divergence is the Hiring-Manager-controlled set, and nothing else.

    Written as a difference rather than as two lists, because the value is in
    catching an UNINTENDED divergence: a capability that quietly stopped being
    shared would otherwise look exactly like this one, which was deliberate.
    """
    from app.services.capabilities import (
        ASSIGN_ROLES,
        HIRING_MANAGER_CONTROLLED,
        INTEGRITY_DISPOSITION,
        REJECT_JD,
        SEND_JD_TO_HIRING_MANAGER,
        VIEW_COMPANY_JOBS,
    )


    hr = DEFAULT_PERMISSION_MATRIX[Role.hr_manager]
    rec = DEFAULT_PERMISSION_MATRIX[Role.recruiter]
    differing = {c for c in hr if hr.get(c) != rec.get(c)}
    expected = set(HIRING_MANAGER_CONTROLLED) | {
        # 24: "Reject JD" is the HR Manager's and the Super Admin's.
        REJECT_JD,
        # 7.3 / 24: staff and role administration is the Super Admin's, and
        # the HR Manager's cell is the conservative NO*.
        ASSIGN_ROLES,
        # spec-doc6 C7: HR Manager by right, Super Admin by audited override.
        INTEGRITY_DISPOSITION,
    }
    assert differing == expected, (
        "the Recruiter and HR Manager grants diverge somewhere RBAC 24 does "
        f"not sanction: {sorted(differing ^ expected)}"
    )
    # The Recruiter keeps the one hand-off 9.3 gives them.
    assert rec[SEND_JD_TO_HIRING_MANAGER] is True
    # Job visibility does NOT diverge at the grant layer, and that is the
    # design: both hold it, and the SCOPED cell in RBAC_INVARIANTS is what
    # narrows the Recruiter to their assigned jobs (9.2, 23). Expressing the
    # scope as a missing grant would leave a Recruiter unable to see the job
    # they own.
    assert hr[VIEW_COMPANY_JOBS] is rec[VIEW_COMPANY_JOBS] is True


def test_every_hierarchy_role_has_operational_defaults() -> None:
    # Every staff role can create+publish jobs and reach the shared pipeline.
    for role in (
        Role.recruitment_manager,
        Role.hr_manager,
        Role.recruiter,
        Role.hiring_manager,
    ):
        grants = DEFAULT_PERMISSION_MATRIX[role]
        assert grants[CREATE_JOB] is True
        assert grants[SEND_OUTREACH] is True   # was recruiter-denied pre-flatten
        assert grants[VIEW_DATABANK] is True


def test_staff_management_stops_at_the_bottom_of_the_hierarchy() -> None:
    """MANAGE_STAFF belongs to the Company Admin AND to the staff roles.

    This test previously asserted the opposite — that MANAGE_STAFF was
    client-ONLY. Migration 0031 (deployed) seeds it for hr_manager, recruiter
    and hiring_manager as well, which is the product decision that the four
    customer-side roles run the customer's hiring and are functionally
    identical. The live `role_permissions` rows have said so since that
    migration ran, so the old assertion described a contract production was
    already not honouring. It is inverted here rather than deleted, so the
    grant stays deliberate and a future narrowing is a visible test change.
    """
    for role in (
        Role.client,
        Role.recruitment_manager,
        Role.hr_manager,
        Role.recruiter,
    ):
        assert DEFAULT_PERMISSION_MATRIX[role][MANAGE_STAFF] is True
    assert DEFAULT_PERMISSION_MATRIX[Role.hiring_manager][MANAGE_STAFF] is False
    # It is still NOT part of the bare operational set — a role added later
    # inherits _STAFF_OPERATIONAL without silently inheriting staff management.
    assert MANAGE_STAFF not in _STAFF_OPERATIONAL


def test_edit_role_permissions_stays_owner_only() -> None:
    # No default-matrix role may hold EDIT_ROLE_PERMISSIONS (Super Admin only).
    for grants in DEFAULT_PERMISSION_MATRIX.values():
        assert EDIT_ROLE_PERMISSIONS not in grants


# ── Bulk resolver (contract rev 2: capabilities in auth responses) ───────────

def test_bulk_resolver_agrees_with_single_resolution() -> None:
    tenant_rows = {VIEW_DATABANK: False, SEND_OUTREACH: True}
    global_rows = {VIEW_DATABANK: True, MANAGE_STAFF: True}
    resolved = resolve_capability_set(tenant_rows, global_rows)
    for cap in ALL_CAPABILITIES:
        assert (cap in resolved) == resolve_permission(tenant_rows, global_rows, cap), cap


def test_bulk_resolver_applies_tenant_override_both_ways() -> None:
    resolved = resolve_capability_set(
        {VIEW_DATABANK: False, SEND_OUTREACH: True},
        {VIEW_DATABANK: True, SEND_OUTREACH: False},
    )
    assert VIEW_DATABANK not in resolved  # tenant revoke beats global allow
    assert SEND_OUTREACH in resolved      # tenant grant beats global deny


def test_bulk_resolver_empty_rows_deny_everything() -> None:
    assert resolve_capability_set({}, {}) == []


def test_bulk_resolver_preserves_canonical_order() -> None:
    global_rows = {c: True for c in ALL_CAPABILITIES}
    assert resolve_capability_set({}, global_rows) == ALL_CAPABILITIES


def test_bulk_resolver_ignores_unknown_capabilities_in_rows() -> None:
    # Stray/legacy rows (e.g. a retired capability name) never leak into the
    # resolved list — only ALL_CAPABILITIES entries are considered.
    resolved = resolve_capability_set({}, {"create_hiring_managers": True})
    assert resolved == []


def test_default_matrix_agrees_with_the_seed_migration() -> None:
    """The code template and migration 0031 must grant the same thing.

    `api/admin._seed_permissions` copies DEFAULT_PERMISSION_MATRIX into
    TENANT-SCOPED `role_permissions` rows for every customer created from the
    Owner console, and a tenant row beats the global template in
    `rbac.resolve_permission`. So when the migration grants a capability
    globally and this dict omits it, the migration is silently undone for
    exactly those customers — which is how a Company Admin ended up unable to
    read their own dashboard.
    """
    import importlib.util
    from pathlib import Path

    from app.models.enums import Role
    from app.services.capabilities import DEFAULT_PERMISSION_MATRIX

    seed_path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0031_seed_full_team_access.py"
    )
    spec = importlib.util.spec_from_file_location("seed_0031", seed_path)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)

    for role_name in seed.GRANTED_ROLES:
        granted = {
            capability
            for capability, allowed in DEFAULT_PERMISSION_MATRIX[Role(role_name)].items()
            if allowed
        }
        expected = set(seed.GRANTED_CAPABILITIES)
        # Migration 0052 retires the duplicate Company Page surface and its
        # permission. Company Profile (`edit_company_profile`) is the only
        # company-information capability that remains.
        expected.discard("create_company_page")
        # Migration 0051 reverses this one flat-model grant: Hiring Manager is
        # the bottom hierarchy tier and has no subordinate staff to manage.
        if role_name == Role.hiring_manager.value:
            expected.discard(MANAGE_STAFF)
        missing = sorted(expected - granted)
        assert not missing, (
            f"role {role_name}: migration 0031 grants {missing} but the code "
            "template does not, so a console-created tenant would lose them"
        )
