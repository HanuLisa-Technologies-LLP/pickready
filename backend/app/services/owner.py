"""Platform Owner invariant (contract rev 2).

Exactly ONE account may ever hold the owner (super_admin) role:
`settings.owner_email` (manjuchro@gmail.com). There is no endpoint that
creates super_admin users today — this guard is defense in depth, enforced at
three layers so the invariant survives future endpoints and refactors:

1. `ensure_owner_invariant(...)` — called explicitly from every user-creating
   API path (tenant onboarding, staff creation).
2. An ORM mapper guard (`before_insert` / `before_update` on `User`) that
   rejects ANY session flush minting or promoting a non-owner super_admin,
   regardless of which code path built the object. This is the airtight layer:
   a new endpoint that forgets step 1 still cannot violate the invariant.
3. `otp.eligible_login_users` treats a violating row as nonexistent at login,
   and a data migration removes any pre-existing violating rows.

Every rejection is logged at ERROR level with the offending email.
"""
import logging

from sqlalchemy import event

from app.core.config import get_settings
from app.models.enums import Role

logger = logging.getLogger(__name__)


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
        logger.error(
            "owner invariant violation: refused to assign the super_admin role "
            "to %r (owner is %r)",
            (email or "").strip().lower(),
            get_settings().owner_email,
        )
        raise OwnerRoleViolation(
            "the owner (super_admin) role is reserved for the platform owner account"
        )


# ── Layer 2: ORM mapper guard ────────────────────────────────────────────────
# Registered at import time. `app.services.owner` is imported by the admin and
# companies routers (and by the dev seed), so the listeners are live for any
# process that can write users.

def _guard_user_row(user) -> None:
    """Mapper-level enforcement: any User being inserted or updated into the
    super_admin role must carry the owner email."""
    ensure_owner_invariant(user.role, user.email or "")


def _register_orm_guards() -> None:
    from app.models.user import User  # local import — models must not import services

    if getattr(User, "_owner_guard_registered", False):
        return
    event.listen(User, "before_insert", lambda _m, _c, target: _guard_user_row(target))
    event.listen(User, "before_update", lambda _m, _c, target: _guard_user_row(target))
    User._owner_guard_registered = True


_register_orm_guards()
