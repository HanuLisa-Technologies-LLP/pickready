"""Owner invariant (contract rev 2): exactly one account —
settings.owner_email — may ever hold the super_admin role. DB-free."""
import pytest

from app.core.config import get_settings
from app.models.enums import Role
from app.services.owner import (
    OwnerRoleViolation,
    ensure_owner_invariant,
    violates_owner_invariant,
)

OWNER = "manjuchro@gmail.com"


# ── Pure predicate ───────────────────────────────────────────────────────────

def test_owner_email_may_hold_super_admin() -> None:
    assert violates_owner_invariant(Role.super_admin, OWNER, OWNER) is False


def test_any_other_email_may_not_hold_super_admin() -> None:
    assert violates_owner_invariant(Role.super_admin, "evil@example.com", OWNER) is True


def test_owner_check_is_case_and_whitespace_insensitive() -> None:
    assert violates_owner_invariant(Role.super_admin, " Manjuchro@GMAIL.com ", OWNER) is False


def test_non_owner_roles_are_unconstrained() -> None:
    for role in (Role.client, Role.hr_manager, Role.recruiter,
                 Role.hiring_manager, Role.candidate):
        assert violates_owner_invariant(role, "anyone@example.com", OWNER) is False


def test_accepts_role_as_string() -> None:
    assert violates_owner_invariant("super_admin", "evil@example.com", OWNER) is True
    assert violates_owner_invariant("recruiter", "evil@example.com", OWNER) is False


def test_empty_email_never_becomes_owner() -> None:
    assert violates_owner_invariant(Role.super_admin, "", OWNER) is True


# ── Settings-backed guard (called from every user-creating endpoint) ─────────

def test_settings_default_owner_email() -> None:
    assert get_settings().owner_email == OWNER


def test_ensure_raises_for_impostor_super_admin() -> None:
    with pytest.raises(OwnerRoleViolation):
        ensure_owner_invariant(Role.super_admin, "evil@example.com")


def test_ensure_allows_the_real_owner() -> None:
    ensure_owner_invariant(Role.super_admin, get_settings().owner_email)


def test_ensure_allows_ordinary_staff_creation() -> None:
    ensure_owner_invariant(Role.hr_manager, "hr@client.example")
    ensure_owner_invariant(Role.client, "boss@client.example")
