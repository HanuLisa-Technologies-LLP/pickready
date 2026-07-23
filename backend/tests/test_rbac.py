"""RBAC resolution (ESD §6): tenant override > global template > deny."""
from app.models.enums import Role
from app.services.capabilities import (
    ALL_CAPABILITIES,
    DEFAULT_PERMISSION_MATRIX,
    MANAGE_STAFF,
    SEND_OUTREACH,
    VIEW_DATABANK,
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


def test_default_matrix_matches_prd_seed() -> None:
    # Spot-check the PRD §6 template rows most of the product hangs off.
    assert DEFAULT_PERMISSION_MATRIX[Role.hr_manager][SEND_OUTREACH] is True
    assert SEND_OUTREACH not in DEFAULT_PERMISSION_MATRIX[Role.recruiter]
    assert DEFAULT_PERMISSION_MATRIX[Role.recruiter][VIEW_DATABANK] is True


def test_staff_management_is_client_owned_in_default_matrix() -> None:
    # Contract rev 2: the Client owns staff creation (grantable to HR via the
    # dynamic engine, never an Owner function).
    assert DEFAULT_PERMISSION_MATRIX[Role.client][MANAGE_STAFF] is True
    assert MANAGE_STAFF not in DEFAULT_PERMISSION_MATRIX.get(Role.hr_manager, {})


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
