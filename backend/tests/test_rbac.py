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
    # ...and each grants the full operational set (all True).
    assert hr == _STAFF_OPERATIONAL
    assert all(v is True for v in hr.values())


def test_flat_staff_all_create_and_share_operational_caps() -> None:
    # Every staff role can create+publish jobs and reach the shared pipeline.
    for role in (Role.hr_manager, Role.recruiter, Role.hiring_manager):
        grants = DEFAULT_PERMISSION_MATRIX[role]
        assert grants[CREATE_JOB] is True
        assert grants[SEND_OUTREACH] is True   # was recruiter-denied pre-flatten
        assert grants[VIEW_DATABANK] is True


def test_staff_management_is_client_owned_in_default_matrix() -> None:
    # Contract rev 2: the Client (Company Admin) owns staff creation — never a
    # staff-role capability even under the flat model.
    assert DEFAULT_PERMISSION_MATRIX[Role.client][MANAGE_STAFF] is True
    assert MANAGE_STAFF not in DEFAULT_PERMISSION_MATRIX.get(Role.hr_manager, {})
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
