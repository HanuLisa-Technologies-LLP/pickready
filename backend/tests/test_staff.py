"""Staff management rules (contract rev 2) — DB-free: role validation and
the Hiring Manager cap as pure functions."""
import pytest

from app.api.companies import (
    MAX_HIRING_MANAGERS,
    STAFF_ROLES,
    hiring_manager_cap_reached,
    validate_staff_role,
)
from app.models.enums import Role


# ── Role validation (400 on anything but the 3 staff roles) ──────────────────

def test_the_three_staff_roles_are_accepted() -> None:
    assert validate_staff_role("hr_manager") == Role.hr_manager
    assert validate_staff_role("recruiter") == Role.recruiter
    assert validate_staff_role("hiring_manager") == Role.hiring_manager


@pytest.mark.parametrize("forbidden", ["client", "super_admin", "candidate"])
def test_privileged_and_external_roles_are_rejected(forbidden: str) -> None:
    with pytest.raises(ValueError):
        validate_staff_role(forbidden)


@pytest.mark.parametrize("bogus", ["", "admin", "HR_MANAGER", "owner", "staff"])
def test_unknown_role_strings_are_rejected(bogus: str) -> None:
    with pytest.raises(ValueError):
        validate_staff_role(bogus)


def test_staff_roles_constant_is_exactly_the_three() -> None:
    assert STAFF_ROLES == {Role.hr_manager, Role.recruiter, Role.hiring_manager}


# ── Hiring Manager cap (FR-2.2: max 5 ACTIVE; HR/Recruiter uncapped) ─────────

def test_cap_not_reached_below_five() -> None:
    for count in range(MAX_HIRING_MANAGERS):
        assert hiring_manager_cap_reached(count) is False


def test_cap_reached_at_exactly_five() -> None:
    # The 6th ACTIVE Hiring Manager must be refused (409 at the API layer).
    assert hiring_manager_cap_reached(MAX_HIRING_MANAGERS) is True


def test_cap_reached_above_five() -> None:
    assert hiring_manager_cap_reached(MAX_HIRING_MANAGERS + 3) is True


def test_disabled_hms_free_up_slots() -> None:
    # The caller counts only status != disabled — 5 total with 1 disabled
    # passes 4 into the predicate, which must allow another hire.
    active_after_one_disabled = MAX_HIRING_MANAGERS - 1
    assert hiring_manager_cap_reached(active_after_one_disabled) is False


def test_max_is_five() -> None:
    assert MAX_HIRING_MANAGERS == 5
