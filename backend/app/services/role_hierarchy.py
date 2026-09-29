"""The customer portal's four-level hierarchy (spec §29).

    Super Admin -> Recruitment Manager -> Recruiter -> Hiring Manager

Each higher role decides the permissions of the role beneath it.

WHAT THIS REVERSES, AND WHAT SURVIVES IT
----------------------------------------
CLAUDE.md rule 3 recorded a FLAT staff model: HR Manager, Recruiter and Hiring
Manager equal, all three holding the same operational capability set. That was a
client decision and this is a client decision; the rule is rewritten rather than
worked around.

What SURVIVES is the half of rule 3 that was never about flatness: permissions
are DATA, not code. There is no `if role == ...` branch anywhere in this module's
callers. A manager granting a capability writes it into the subordinate's
existing sparse `users.permissions_json` overlay, which `services/rbac` already
resolves ahead of the tenant row and the global template. The hierarchy adds two
rules on top of that machinery and nothing else:

  1. you may only manage someone STRICTLY beneath you;
  2. you may only grant a capability you HOLD.

THE SECOND RULE IS THE IMPORTANT ONE
------------------------------------
Without it the hierarchy is a privilege-escalation ladder: a Recruiter who can
edit a Hiring Manager's permissions grants them `manage_billing`, then has that
Hiring Manager grant it back. "Only what you hold" makes the set of capabilities
in a tenant monotonically non-increasing as you descend, which is what a
hierarchy is supposed to mean.

HR MANAGER IS NOT DELETED
-------------------------
`hr_manager` predates this and real customers have those accounts. It ranks
alongside Recruitment Manager rather than being migrated away: a role a customer
already assigned must not silently change what its holder can do, and stranding
those users at the bottom of a new ladder would do exactly that.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Role
from app.models.user import User

__all__ = [
    "DEPARTMENT_SCOPED_ROLES",
    "HIERARCHY",
    "LEAF_ROLES",
    "MANAGEABLE_ROLES",
    "ROLE_LABELS",
    "ROLE_RANK",
    "can_manage",
    "grantable_capabilities",
    "rank",
    "subordinate_roles",
]

#: Ordered top to bottom. The INDEX is the rank, so inserting a level is one
#: edit here and nothing else in the product changes.
HIERARCHY: tuple[tuple[Role, ...], ...] = (
    # The customer's Super Admin. `client` is the Company Admin role the
    # product has always had; the specification's name for it is Super Admin.
    (Role.client,),
    # Recruitment Manager, and the legacy HR Manager alongside it. Same rank
    # deliberately: an existing hr_manager must keep managing exactly who they
    # managed yesterday.
    (Role.recruitment_manager, Role.hr_manager),
    (Role.recruiter,),
    (Role.hiring_manager,),
    # RBAC_SPECIFICATION.md 6 and 13. The Interview Manager is an evaluation
    # participant: they read what a job's candidates produced and add Team
    # Review remarks, and 13.5 lists eleven things they must not do. Bottom
    # tier, so nobody below them exists to manage.
    #
    # 6 actually draws all four non-Super-Admin roles as siblings under the
    # Super Admin rather than as a descending chain, and says explicitly that
    # it is "an authority hierarchy, not necessarily an inheritance
    # implementation". This module's chain is retained because it is what the
    # existing staff screens enforce; what governs authorization is
    # `capabilities.RBAC_INVARIANTS`, where 24 already denies staff management
    # to everybody except the Super Admin. The chain can therefore only ever
    # be narrower than 24, never wider.
    (Role.interview_manager,),
)

ROLE_RANK: dict[Role, int] = {
    role: index for index, tier in enumerate(HIERARCHY) for role in tier
}

#: The leadership roles (2026-09-29, spec 2.4 and 12): LEAVES directly beneath
#: the Super Admin, outside the chain above. Nobody sits beneath them, and
#: nobody but the Super Admin may create, edit or re-permission them.
#:
#: WHY THEY ARE NOT A TIER OF `HIERARCHY`
#: --------------------------------------
#: A tier is a RANK, and a rank is transitive: a tier at index 1 would let a
#: CEO manage every Recruiter (rank 2) and would let nobody at rank 1 manage a
#: CEO, while a tier at the bottom would let a Recruitment Manager create a
#: CEO. Neither is a product rule. A CEO reads the organisation and writes only
#: their own Leadership Intelligence; they run nobody's seat. So the leaves are
#: kept out of the rank arithmetic entirely and `can_manage` answers them by
#: the one rule that holds: only the Super Admin.
LEAF_ROLES: tuple[Role, ...] = (Role.ceo, Role.md, Role.functional_head)

#: Roles whose holder belongs to exactly one department and reads only that
#: department (spec 2.11, 13.2). DATA, read by the staff validation (the
#: department is required) and mirrored by the database CHECK on `users`
#: (migration 0133); the READ boundary itself is `services/department_access`,
#: which asks the user's stored department rather than this set.
DEPARTMENT_SCOPED_ROLES: frozenset[Role] = frozenset({Role.functional_head})

ROLE_LABELS: dict[Role, str] = {
    Role.client: "Super Admin",
    Role.recruitment_manager: "Recruitment Manager",
    Role.hr_manager: "HR Manager",
    Role.recruiter: "Recruiter",
    Role.hiring_manager: "Hiring Manager",
    Role.interview_manager: "Interview Manager",
    Role.ceo: "CEO",
    Role.md: "MD",
    Role.functional_head: "Functional Head",
}

#: Roles a customer's own team can create. `client` is excluded: the Super
#: Admin seat is minted at onboarding by the Provider, and a portal that could
#: mint another one would let a Recruitment Manager promote themselves past
#: every rule in this module.
MANAGEABLE_ROLES: frozenset[Role] = frozenset(
    {
        Role.recruitment_manager,
        Role.hr_manager,
        Role.recruiter,
        Role.hiring_manager,
        Role.interview_manager,
        *LEAF_ROLES,
    }
)

#: Below every rank in the hierarchy. Used as the rank of a role this module
#: does not place (a candidate, a bd user), so `can_manage` refuses rather than
#: raising on a role it was never meant to see.
_UNRANKED = len(HIERARCHY) + 1


def rank(role: Role | str | None) -> int:
    try:
        parsed = role if isinstance(role, Role) else Role(str(role))
    except ValueError:
        return _UNRANKED
    return ROLE_RANK.get(parsed, _UNRANKED)


def can_manage(actor_role: Role | str | None, target_role: Role | str | None) -> bool:
    """Whether `actor_role` may create, edit or set permissions for `target_role`.

    STRICTLY above, in the same tenant. Two Recruiters cannot edit each other,
    which is the case a "greater than or equal" comparison would quietly allow
    and which makes the hierarchy meaningless: everyone at a level would hold
    everyone else's permissions.
    """
    actor_leaf = _as_role(actor_role) in LEAF_ROLES
    target_leaf = _as_role(target_role) in LEAF_ROLES
    if actor_leaf:
        # A leaf has nobody beneath it (see LEAF_ROLES).
        return False
    if target_leaf:
        return _as_role(actor_role) == Role.client
    actor = rank(actor_role)
    target = rank(target_role)
    if actor >= _UNRANKED or target >= _UNRANKED:
        return False
    return actor < target


def _as_role(role: Role | str | None) -> Role | None:
    try:
        return role if isinstance(role, Role) else Role(str(role))
    except ValueError:
        return None


def subordinate_roles(actor_role: Role | str | None) -> list[Role]:
    """Every role this actor may create, in hierarchy order."""
    return [
        role
        for role in (*(r for tier in HIERARCHY for r in tier), *LEAF_ROLES)
        if role in MANAGEABLE_ROLES and can_manage(actor_role, role)
    ]


def grantable_capabilities(actor_capabilities: set[str]) -> set[str]:
    """The capabilities this actor may grant to a subordinate.

    Exactly what they hold, and never more. See the module docstring: without
    this the hierarchy is a ladder rather than a ceiling.
    """
    return set(actor_capabilities)


# The hierarchy must place every manageable role, or a role would exist that
# nobody can be above and nobody can be below.
assert MANAGEABLE_ROLES <= set(ROLE_RANK) | set(LEAF_ROLES)
assert Role.client in ROLE_RANK and ROLE_RANK[Role.client] == 0
assert set(ROLE_LABELS) == set(ROLE_RANK) | set(LEAF_ROLES)
assert not set(LEAF_ROLES) & set(ROLE_RANK)
assert DEPARTMENT_SCOPED_ROLES <= set(LEAF_ROLES)
# Strictly descending: every tier is below the one before it.
assert all(
    ROLE_RANK[tier[0]] == index for index, tier in enumerate(HIERARCHY)
)
