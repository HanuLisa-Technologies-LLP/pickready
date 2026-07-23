"""Platform Owner invariant (contract rev 2).

Exactly ONE account may ever hold the owner (super_admin) role:
`settings.owner_email` (manjuchro@gmail.com). There is no endpoint that
creates super_admin users today — this guard is defense in depth, called from
every user-creating code path (tenant onboarding, staff creation) so the
invariant survives future endpoints and refactors. A data migration removes
any pre-existing violating rows.
"""
from app.core.config import get_settings
from app.models.enums import Role


class OwnerRoleViolation(Exception):
    """Attempt to create/update a user into super_admin with a non-owner
    email. API layer maps this to 403."""


def violates_owner_invariant(role: Role | str, email: str, owner_email: str) -> bool:
    """Pure predicate: True when this (role, email) pair must be rejected."""
    if Role(role) != Role.super_admin:
        return False
    return (email or "").strip().lower() != (owner_email or "").strip().lower()


def ensure_owner_invariant(role: Role | str, email: str) -> None:
    """Raise OwnerRoleViolation unless the (role, email) pair is permitted.
    Call from ANY code path that creates a user or changes a user's role."""
    if violates_owner_invariant(role, email, get_settings().owner_email):
        raise OwnerRoleViolation(
            "the owner (super_admin) role is reserved for the platform owner account"
        )
