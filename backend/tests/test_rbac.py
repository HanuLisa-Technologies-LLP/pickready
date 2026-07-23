"""RBAC resolution (ESD §6): tenant override > global template > deny."""
from app.models.enums import Role
from app.services.capabilities import (
    ALL_CAPABILITIES,
    DEFAULT_PERMISSION_MATRIX,
    SEND_OUTREACH,
    VIEW_DATABANK,
)
from app.services.rbac import resolve_permission


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
