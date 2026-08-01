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


def test_flat_staff_model_all_three_roles_identical() -> None:
    # PRD v1.0 §4 (FINAL): HR Manager, Recruiter, Hiring Manager are EQUAL.
    hr = DEFAULT_PERMISSION_MATRIX[Role.hr_manager]
    rec = DEFAULT_PERMISSION_MATRIX[Role.recruiter]
    hm = DEFAULT_PERMISSION_MATRIX[Role.hiring_manager]
    assert hr == rec == hm
    # ...and each grants at least the full operational set (all True).
    # `==` was too tight once the three staff roles were widened to the same
    # customer-side set migration 0031 seeds globally: the code template has to
    # be able to grow with the migration, because `_seed_permissions` copies
    # this dict into tenant rows that OVERRIDE the migration's global rows.
    assert _STAFF_OPERATIONAL.items() <= hr.items()
    assert all(v is True for v in hr.values())


def test_flat_staff_all_create_and_share_operational_caps() -> None:
    # Every staff role can create+publish jobs and reach the shared pipeline.
    for role in (Role.hr_manager, Role.recruiter, Role.hiring_manager):
        grants = DEFAULT_PERMISSION_MATRIX[role]
        assert grants[CREATE_JOB] is True
        assert grants[SEND_OUTREACH] is True   # was recruiter-denied pre-flatten
        assert grants[VIEW_DATABANK] is True


def test_staff_management_is_granted_to_the_customer_roles() -> None:
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
    for role in (Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager):
        assert DEFAULT_PERMISSION_MATRIX[role][MANAGE_STAFF] is True
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
        missing = sorted(set(seed.GRANTED_CAPABILITIES) - granted)
        assert not missing, (
            f"role {role_name}: migration 0031 grants {missing} but the code "
            "template does not, so a console-created tenant would lose them"
        )
