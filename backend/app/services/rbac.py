"""Dynamic RBAC engine (ESD §6): permissions are data, not code.

Resolution order for (user, tenant_id, role, capability), most specific first:
  1. the USER's own `users.permissions_json` overlay (HR Head per-person grant,
     spec §7.1) — a sparse {capability: bool} object
  2. tenant-specific row in `role_permissions` (Super Admin per-tenant override)
  3. global template row (tenant_id IS NULL)
  4. deny (missing rows never grant anything)

The user overlay is SPARSE on purpose. A capability the HR Head never touched
is absent from the object and therefore keeps tracking its role default, so a
later change to the role matrix still reaches everyone it should. Only the
capabilities someone deliberately pinned for one person are frozen — which is
what makes "grant Priya publish_job, leave everything else alone" expressible
without snapshotting the whole matrix onto her row.

Never branch on role in business logic — use `require_capability(...)`
(app/api/deps.py), which calls `has_capability` below.
"""
import uuid
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Role
from app.models.tenant import RolePermission
from app.models.user import User
from app.services.capabilities import ALL_CAPABILITIES
from app.services import tenant_cache


def _role_cache_key(tenant_id: uuid.UUID | None, role: Role) -> str:
    return f"pickready:tenant:{tenant_id or 'global'}:role_permissions:{role.value}"


async def _permission_rows(
    session: AsyncSession, tenant_id: uuid.UUID | None, role: Role
) -> list[tuple[str | None, str, bool]]:
    key = _role_cache_key(tenant_id, role)
    cached = await tenant_cache.get_json(key)
    if isinstance(cached, list):
        return [(row[0], row[1], bool(row[2])) for row in cached]
    conditions: list[ColumnElement[bool]] = [RolePermission.tenant_id.is_(None)]
    if tenant_id is not None:
        conditions.append(RolePermission.tenant_id == tenant_id)
    rows = (
        await session.execute(
            select(
                RolePermission.tenant_id,
                RolePermission.capability,
                RolePermission.allowed,
            ).where(RolePermission.role == role, or_(*conditions))
        )
    ).all()
    serializable = [
        [str(row.tenant_id) if row.tenant_id else None, row.capability, row.allowed]
        for row in rows
    ]
    await tenant_cache.set_json(key, serializable, ttl=120)
    return [(row[0], row[1], row[2]) for row in serializable]


async def invalidate_role_permissions(
    tenant_id: uuid.UUID | None, roles: list[Role] | None = None
) -> None:
    if tenant_id is None:
        await tenant_cache.delete_pattern("pickready:tenant:*:role_permissions:*")
        return
    for role in roles or list(Role):
        await tenant_cache.delete(_role_cache_key(tenant_id, role))


def resolve_permission(
    tenant_rows: dict[str, bool],
    global_rows: dict[str, bool],
    capability: str,
    user_overrides: dict[str, bool] | None = None,
) -> bool:
    """Pure resolution, most specific layer first.

    A per-user override (even `False`) beats the tenant row; a tenant-specific
    row (even `allowed=False`) beats the global template; absent everywhere ->
    deny. An explicit False at any layer is a real revocation, not a gap —
    which is why every layer is tested for KEY PRESENCE rather than truthiness.
    """
    if user_overrides and capability in user_overrides:
        return bool(user_overrides[capability])
    if capability in tenant_rows:
        return tenant_rows[capability]
    return global_rows.get(capability, False)


def sanitize_overrides(raw: object) -> dict[str, bool]:
    """Coerce a stored/incoming overlay into {known capability: bool}.

    Unknown capability names are DROPPED rather than stored: a typo must not
    sit in the database looking like a grant, and it must not survive a later
    rename of the real capability. Non-boolean values are coerced, so a JSON
    `"true"` written by hand still behaves.

    Pure and side-effect free; unit-tested in tests/test_rbac.py.
    """
    if not isinstance(raw, dict):
        return {}
    known = set(ALL_CAPABILITIES)
    out: dict[str, bool] = {}
    for key, value in raw.items():
        if key not in known:
            continue
        if isinstance(value, str):
            out[key] = value.strip().lower() in ("true", "1", "yes", "on")
        else:
            out[key] = bool(value)
    return out


async def _user_overrides(
    session: AsyncSession, user_id: uuid.UUID | str | None
) -> dict[str, bool]:
    """The user's sanitized permission overlay ({} when they have none)."""
    if user_id is None:
        return {}
    row = (
        await session.execute(
            select(User.permissions_json).where(User.id == uuid.UUID(str(user_id)))
        )
    ).scalar_one_or_none()
    return sanitize_overrides(row)


async def has_capability(
    session: AsyncSession,
    tenant_id: uuid.UUID | str | None,
    role: Role | str,
    capability: str,
    user_id: uuid.UUID | str | None = None,
) -> bool:
    """Resolve one capability through the user -> tenant -> global chain.

    `user_id` is optional so every existing caller keeps working unchanged; when
    it is supplied (as `require_capability` now does), that person's own
    permission overlay is consulted first.
    """
    role = Role(role)
    tid = uuid.UUID(str(tenant_id)) if tenant_id else None
    overrides = await _user_overrides(session, user_id)

    rows = await _permission_rows(session, tid, role)

    tenant_rows = {r[1]: r[2] for r in rows if r[0] is not None}
    global_rows = {r[1]: r[2] for r in rows if r[0] is None}
    return resolve_permission(tenant_rows, global_rows, capability, overrides)


# ── Bulk resolution (contract rev 2: capabilities in login/me responses) ─────

def resolve_capability_set(
    tenant_rows: dict[str, bool],
    global_rows: dict[str, bool],
    capabilities: list[str] | None = None,
    user_overrides: dict[str, bool] | None = None,
) -> list[str]:
    """Pure bulk resolver: every capability that resolves to allowed under the
    same precedence as `resolve_permission` (user > tenant > global > deny).
    Order follows ALL_CAPABILITIES so responses are stable."""
    caps = capabilities if capabilities is not None else ALL_CAPABILITIES
    return [
        c for c in caps if resolve_permission(tenant_rows, global_rows, c, user_overrides)
    ]


async def resolve_role_capabilities(
    session: AsyncSession,
    tenant_id: uuid.UUID | str | None,
    role: Role | str,
    user_id: uuid.UUID | str | None = None,
) -> list[str]:
    """Fetch the tenant + global rows for this role ONCE, then resolve every
    capability in ALL_CAPABILITIES (single round-trip, not N lookups).

    When `user_id` is given, that person's overlay is applied on top — this is
    what makes /auth/me return the effective set for THIS user rather than the
    generic set for their role.
    """
    role = Role(role)
    tid = uuid.UUID(str(tenant_id)) if tenant_id else None
    overrides = await _user_overrides(session, user_id)

    rows = await _permission_rows(session, tid, role)

    tenant_rows = {r[1]: r[2] for r in rows if r[0] is not None}
    global_rows = {r[1]: r[2] for r in rows if r[0] is None}
    resolved = resolve_capability_set(
        tenant_rows, global_rows, user_overrides=overrides
    )
    return apply_invariant_ceiling(role, resolved)


def apply_invariant_ceiling(role: Role | str, resolved: list[str]) -> list[str]:
    """Drop capabilities the RBAC 24 ceiling would refuse for this role.

    WHY THE ADVERTISED SET AND THE ENFORCED SET MUST BE THE SAME SET
    ----------------------------------------------------------------
    This list is what `/auth/me` returns and what the frontend renders
    controls from. A capability the grant rows allow and the ceiling refuses
    is a button that appears, is clicked, and 403s -- which is worse than a
    missing button, because it teaches a user that the product is broken
    rather than that they lack the authority.

    It also matters for the reverse reading. RBAC 3 says frontend visibility
    is not a security boundary, and it is not: the enforcement is
    `decide`. Trimming here is honesty about what the enforcement will do, not
    a second gate, and removing this function would weaken nothing.

    A SCOPED cell is KEPT. It is genuinely held; what it is not is unbounded,
    and the scope is a property of a job rather than of the person, so a
    capability list cannot express it. The per-job answer comes from
    `require_authorized` on the route that names the job.
    """
    from app.services.capabilities import RBAC_INVARIANTS, invariant_for, permits

    return [
        capability
        for capability in resolved
        if capability not in RBAC_INVARIANTS
        or permits(invariant_for(role, capability))
    ]


async def capabilities_for_user(
    session: AsyncSession,
    *,
    role: Role | str,
    tenant_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None = None,
) -> list[str]:
    """Capability list for the auth responses (/auth/me, verify,
    select-context). Owner gets the wildcard; candidates use the portal
    endpoints (separate audience) and carry no org capabilities.

    NOTE: the role checks here are auth plumbing (which permission universe
    applies), not business-logic branching — org roles always resolve through
    the data-driven engine (claude.md rule 3)."""
    role = Role(role)
    if role == Role.super_admin:
        return ["*"]
    if role == Role.candidate:
        return []
    return await resolve_role_capabilities(session, tenant_id, role, user_id)


# ═══════════════════════════════════════════════════════════════════════════
# The authorization DECISION layer (RBAC_SPECIFICATION.md 3, 4, 23, 32, 33)
# ═══════════════════════════════════════════════════════════════════════════
#
# Everything above this line answers one question: does this role hold this
# capability? RBAC 3 says a valid role alone is never sufficient, and gives the
# chain that is:
#
#     Authenticated user -> tenant -> role -> permission -> resource scope
#         -> resource state -> ALLOW / DENY
#
# The four steps after "permission" had no implementation in this codebase
# before 2026-08-29. A Recruiter holding `publish_job` could publish any job
# in their tenant, assigned to them or not (9.2 and 23 both say they may not),
# and could publish one whose Hiring-Manager-controlled criteria were never
# finalized (21 says they may not).
#
# ONE DECISION FUNCTION, USED BY BOTH SURFACES
# --------------------------------------------
# 34 requires an AI agent to operate under the SAME authorization model as the
# human it acts for, and spec-doc6 9.2 says to enforce it with the same layer
# the HTTP endpoints use rather than a parallel one. `decide` below is that
# layer. `services/tools/executor` calls it for agent tool calls and
# `require_authorized` calls it for requests. A second implementation would
# drift, and the half that drifted would be the one nobody was testing.
#
# 404, NOT 403, ACROSS A TENANT BOUNDARY
# --------------------------------------
# 33: knowing an id must not be sufficient, and obscurity is not authorization.
# A 403 on a cross-tenant id answers "that job exists" to anybody who can
# enumerate uuids, so the cross-tenant answer is NOT_FOUND, identical to the
# answer for an id that was never real. Within one tenant the distinction is
# not a leak -- the resource belongs to the caller's own company -- so an
# unassigned job is a 403, which is also what spec-doc6 8.2 expects the
# dashboard to render.

from dataclasses import dataclass, field
from enum import Enum

from app.services.capabilities import (
    HIRING_MANAGER_CONTROLLED,
    EXCEPTIONAL,
    Invariant,
    invariant_for,
    permits,
)


class Decision(str, Enum):
    """What the caller must do with the request."""

    #: Proceed.
    ALLOW = "allow"
    #: 403. The resource is inside the caller's tenant and they may not act.
    DENY = "deny"
    #: 404. Say nothing about whether the resource exists (33).
    NOT_FOUND = "not_found"


#: The three per-job assignment roles of RBAC 23. Stored in `job_assignments`
#: (migration 0061) and NEVER inferred from `users.role`: holding the
#: Recruiter role is not being THE Recruiter for a job (9.2), and the same
#: sentence appears again for the Hiring Manager (10.2).
ASSIGNMENT_RECRUITER = "recruiter"
ASSIGNMENT_HIRING_MANAGER = "hiring_manager"
ASSIGNMENT_INTERVIEW_MANAGER = "interview_manager"

ASSIGNMENT_ROLES: tuple[str, ...] = (
    ASSIGNMENT_RECRUITER,
    ASSIGNMENT_HIRING_MANAGER,
    ASSIGNMENT_INTERVIEW_MANAGER,
)

#: Which assignment a SCOPED cell requires, per org role. A role absent from
#: this map is never scoped by assignment, which is why the Super Admin and
#: the HR Manager reach every job in their own tenant (7.4, 8.2).
_ASSIGNMENT_FOR_ROLE: dict[Role, str] = {
    Role.recruiter: ASSIGNMENT_RECRUITER,
    Role.hiring_manager: ASSIGNMENT_HIRING_MANAGER,
    Role.interview_manager: ASSIGNMENT_INTERVIEW_MANAGER,
}


# ── Job lifecycle (RBAC 17), as the decision layer needs to read it ──────────
# The canonical enum lives in `services/hiring_pipeline.JobLifecycleState`;
# these are the two derived sets the authorization rules actually consult, and
# they are imported lazily inside the functions so this module keeps no import
# edge to the pipeline module (which imports nothing from here either).


def _drafting_states() -> frozenset[str]:
    from app.services.hiring_pipeline import DRAFTING_STATES

    return DRAFTING_STATES


def _finalized_states() -> frozenset[str]:
    from app.services.hiring_pipeline import FINALIZED_OR_LATER

    return FINALIZED_OR_LATER


@dataclass(frozen=True)
class Principal:
    """Who is acting, and on whose authority.

    `agent` is set only for an AI-initiated action. RBAC 34 requires every
    AI-initiated mutation to be attributable to BOTH the human principal and
    the executing agent, so an agent never gets a Principal of its own: it
    borrows the human's, and the agent name rides alongside. There is no
    constructor here that produces a principal with an agent and no user, and
    that absence is the enforcement.
    """

    user_id: uuid.UUID | str | None
    tenant_id: uuid.UUID | str | None
    role: Role
    #: The executing agent, for an AI-initiated action. None for a human one.
    agent: str | None = None

    def __post_init__(self) -> None:
        if self.agent is not None and self.user_id is None:
            raise ValueError(
                "an agent action requires a human principal (RBAC 34): "
                "user_id must not be None when agent is set"
            )

    @property
    def is_agent_action(self) -> bool:
        return self.agent is not None


@dataclass(frozen=True)
class Resource:
    """The facts about the target a decision needs, already loaded.

    Deliberately a plain value object rather than an ORM row. The rules below
    are pure and unit-testable without a database, and the loader that fills
    this in is the only piece that needs one. It also means the same rules run
    for a resource loaded by a request handler and one loaded by an agent
    tool, which is what "the same authorization layer" (spec-doc6 9.2) has to
    mean in practice.
    """

    kind: str
    resource_id: uuid.UUID | str | None = None
    tenant_id: uuid.UUID | str | None = None
    #: The job this resource belongs to, for scoping. A job's own job_id.
    job_id: uuid.UUID | str | None = None
    #: RBAC 17 lifecycle state of the owning job, when known.
    lifecycle_state: str | None = None
    #: (assignment_role, user_id) pairs currently ACTIVE on the job.
    assignments: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    #: A candidate row with an open integrity finding. No flag ever
    #: auto-rejects, so this never blocks reading; it blocks MOVING the
    #: candidate until a human has disposed of the finding (spec-doc6 C7).
    under_integrity_review: bool = False


@dataclass(frozen=True)
class Authorization:
    """The decision, plus everything the audit row needs about how it was made.

    `reason` names the rule rather than describing it, so a log line and a
    test assertion quote the same token. `exceptional` marks a cell RBAC 24
    asterisked: allowed, and recorded as a deviation from the canonical flow
    (7.5 requires the Super Admin's override to be recorded, and C13 requires
    the same of an HR Manager publish).
    """

    decision: Decision
    reason: str
    invariant: Invariant
    exceptional: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def http_status(self) -> int:
        if self.decision is Decision.ALLOW:
            return 200
        if self.decision is Decision.NOT_FOUND:
            return 404
        return 403


def _same_tenant(left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


def decide(
    principal: Principal,
    capability: str,
    resource: Resource | None = None,
    *,
    granted: bool,
) -> Authorization:
    """The whole of RBAC 3's chain, in RBAC 3's order.

    `granted` is the grant engine's answer (`has_capability`), passed in so
    this function stays pure and the caller does exactly one database read.

    Order matters and is not cosmetic. Tenant is checked FIRST so a
    cross-tenant probe cannot learn anything from the shape of the refusal,
    and state is checked LAST so a state-based refusal is never used to
    confirm that a resource the caller could not otherwise see exists.
    """
    invariant = invariant_for(principal.role, capability)

    # 1. Tenant. 4: a user of one client must not access, INFER, modify,
    #    delete or retrieve another client's resources. Inference is why this
    #    is a 404.
    if resource is not None and resource.tenant_id is not None:
        if not _same_tenant(principal.tenant_id, resource.tenant_id):
            return Authorization(
                Decision.NOT_FOUND, "cross_tenant", invariant
            )

    # 2. The 24 ceiling. NEVER is refused before the grant is even consulted,
    #    because a NEVER cell is precisely the one a grant must not be able to
    #    open (26, 36).
    if invariant is Invariant.NEVER:
        return Authorization(Decision.DENY, "invariant_never", invariant)
    if not permits(invariant):
        return Authorization(Decision.DENY, "invariant_denies", invariant)

    # 3. Permission, from the data-driven engine.
    if not granted:
        return Authorization(Decision.DENY, "capability_not_granted", invariant)

    # 4. Resource scope. 9.2 and 23: holding a role is not owning a job.
    if invariant is Invariant.SCOPED and resource is not None:
        required = _ASSIGNMENT_FOR_ROLE.get(principal.role)
        if required is not None:
            if resource.job_id is None:
                return Authorization(
                    Decision.DENY, "scoped_capability_needs_a_job", invariant
                )
            held = (required, str(principal.user_id))
            if held not in resource.assignments:
                return Authorization(Decision.DENY, "not_assigned", invariant)

    # 5. Resource state.
    state_refusal = _state_rules(principal, capability, resource, invariant)
    if state_refusal is not None:
        return state_refusal

    return Authorization(
        Decision.ALLOW,
        "allowed",
        invariant,
        exceptional=invariant in EXCEPTIONAL,
    )


def _state_rules(
    principal: Principal,
    capability: str,
    resource: Resource | None,
    invariant: Invariant,
) -> Authorization | None:
    """RBAC 21, 22, 26 and spec-doc6 C7. Returns None when nothing refuses."""
    if resource is None:
        return None

    state = resource.lifecycle_state

    # An UNKNOWN lifecycle state refuses every state-gated capability.
    #
    # Reading None as "no state rule applies" is the permissive direction and
    # it is wrong in the one case that matters: a job row written by something
    # that does not populate the column would be publishable without ever
    # having been finalized. That is not hypothetical -- 5 such rows appeared
    # in the containerised test database within a single suite run, before
    # migration 0061 grew its `server_default`.
    #
    # Both guards stay. The default stops the rows appearing; this stops the
    # ones that appear anyway from being progressed.
    if state is None and (
        capability in _state_gated_capabilities()
        or invariant is Invariant.ALLOW_DRAFT_SCOPE
    ):
        return Authorization(
            Decision.DENY, "lifecycle_state_unknown", invariant
        )

    # 24***: the Recruiter edits the DRAFT, and only the draft. From
    # FINALIZED onward the document is the Hiring Manager's approved role
    # definition and 26 forbids the Recruiter altering it.
    if invariant is Invariant.ALLOW_DRAFT_SCOPE:
        if state is not None and state not in _drafting_states():
            return Authorization(
                Decision.DENY, "draft_scope_only", invariant
            )

    # 21: publication must not be possible while required
    # Hiring-Manager-controlled components are incomplete. The lifecycle state
    # IS that record: FINALIZED is only reachable through 20's explicit
    # transition, which is the thing that checks completeness.
    if capability == caps_publish_job() and state is not None:
        if state not in _finalized_states():
            return Authorization(
                Decision.DENY, "publish_requires_finalized", invariant
            )

    # 26: after finalization the Recruiter may not change the
    # Hiring-Manager-controlled criteria. The NEVER cells already refuse the
    # Recruiter outright; this rule catches everyone whose cell is SCOPED,
    # which is the Hiring Manager editing their OWN finalized definition.
    # 12 and 22 both say that needs an explicit revision workflow rather than
    # a silent mutation, and no such workflow exists yet, so it is refused.
    if capability in HIRING_MANAGER_CONTROLLED and state is not None:
        if state in _finalized_states() and capability != _finalize_capability():
            return Authorization(
                Decision.DENY, "criteria_frozen_after_finalization", invariant
            )

    # spec-doc6 C7 / claude.md: no flag ever auto-rejects, and a candidate row
    # carrying an open integrity finding does not move until a human has
    # disposed of it. Blocking the MOVE rather than the candidacy is the
    # point: G3 fails loudly and blocks nothing about the person.
    if resource.under_integrity_review and capability in _pipeline_capabilities():
        return Authorization(
            Decision.DENY, "integrity_review_open", invariant
        )

    return None


# Small indirections so this module does not import capability constants at
# module scope twice under two names. They are functions rather than module
# constants because `capabilities` imports `Role` from `models.enums` and this
# module already imports both; a second constant would be a second thing to
# keep in step.

def caps_publish_job() -> str:
    from app.services import capabilities

    return capabilities.PUBLISH_JOB


def _finalize_capability() -> str:
    from app.services import capabilities

    return capabilities.FINALIZE_ROLE_DEFINITION


def _state_gated_capabilities() -> frozenset[str]:
    """Capabilities whose answer depends on the job's lifecycle state.

    Publication (21) and the Hiring-Manager-controlled criteria (22, 26). The
    Recruiter's JD edit is gated too, but by its ALLOW_DRAFT_SCOPE cell rather
    than by its name, so it is handled at the call site.
    """
    from app.services import capabilities

    return frozenset({capabilities.PUBLISH_JOB}) | capabilities.HIRING_MANAGER_CONTROLLED


def _pipeline_capabilities() -> frozenset[str]:
    from app.services import capabilities

    return frozenset({capabilities.DECIDE_PROFILE, capabilities.UPDATE_PIPELINE_STATUS})


# ── Loading the facts ────────────────────────────────────────────────────────

async def load_job_resource(
    session: AsyncSession,
    job_id: uuid.UUID | str,
    *,
    kind: str = "job",
) -> Resource | None:
    """Read one job's tenant, lifecycle state and active assignments.

    Returns None when no row exists. The caller turns that into the SAME 404 a
    cross-tenant hit produces, which is what makes the two indistinguishable
    from outside (33).

    Runs under whatever session scope the caller opened. Under the tenant
    session RLS already hides another tenant's row, so this normally returns
    None before `decide` ever sees a foreign tenant_id; the explicit
    cross-tenant branch in `decide` is defence in depth for every path that
    legitimately runs under the bypass scope (claude.md rule 1).
    """
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, lifecycle_state FROM jobs WHERE id = :jid"
            ),
            {"jid": str(job_id)},
        )
    ).mappings().first()
    if row is None:
        return None
    assignments = await load_assignments(session, job_id)
    return Resource(
        kind=kind,
        resource_id=row["id"],
        tenant_id=row["tenant_id"],
        job_id=row["id"],
        lifecycle_state=row["lifecycle_state"],
        assignments=assignments,
    )


async def load_assignments(
    session: AsyncSession, job_id: uuid.UUID | str
) -> frozenset[tuple[str, str]]:
    """Active (assignment_role, user_id) pairs for one job."""
    rows = (
        await session.execute(
            text(
                "SELECT assignment_role, user_id FROM job_assignments "
                "WHERE job_id = :jid AND active"
            ),
            {"jid": str(job_id)},
        )
    ).all()
    return frozenset((str(r[0]), str(r[1])) for r in rows)


async def authorize(
    session: AsyncSession,
    principal: Principal,
    capability: str,
    resource: Resource | None = None,
) -> Authorization:
    """Resolve the grant, then run `decide`. One database read, one answer."""
    granted = await has_capability(
        session,
        principal.tenant_id,
        principal.role,
        capability,
        principal.user_id,
    )
    return decide(principal, capability, resource, granted=granted)


def raise_for(authorization: Authorization, capability: str) -> None:
    """Turn a refusal into the HTTP answer RBAC 32 and 33 require.

    A 404 carries the same body as a genuinely missing resource. A 403 names
    the capability, because inside your own tenant an accurate refusal is
    useful and discloses nothing.
    """
    if authorization.allowed:
        return
    from fastapi import HTTPException

    if authorization.decision is Decision.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Not found")
    raise HTTPException(
        status_code=403, detail=f"Not permitted: {capability}"
    )


def require_authorized(
    capability: str,
    *,
    job_id_param: str = "job_id",
) -> Callable[..., Awaitable["CurrentUserProtocol"]]:
    """FastAPI dependency: the full RBAC 3 chain for a job-scoped route.

    Deliberately NOT a replacement for `require_capability`. That gate answers
    "may this role do this at all", which is still the right question for a
    route with no resource in it (creating a job, listing the databank). This
    one adds tenant, scope and state for a route that names a resource, and it
    runs BEFORE the handler, so a refusal has not already read the row it is
    refusing to show.
    """
    from fastapi import Depends, HTTPException, Request

    async def dependency(
        request: Request,
        user: "CurrentUserProtocol" = Depends(_current_user_dependency()),
        session: AsyncSession = Depends(_tenant_db_dependency()),
    ) -> "CurrentUserProtocol":
        raw = request.path_params.get(job_id_param)
        if raw is None:
            raise HTTPException(status_code=404, detail="Not found")
        try:
            job_id = uuid.UUID(str(raw))
        except ValueError:
            # An unparseable id is answered exactly like a missing one, so the
            # shape of the id is not itself an oracle.
            raise HTTPException(status_code=404, detail="Not found") from None
        principal = Principal(
            user_id=user.user_id, tenant_id=user.tenant_id, role=user.role
        )
        resource = await load_job_resource(session, job_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="Not found")
        result = await authorize(session, principal, capability, resource)
        raise_for(result, capability)
        return user

    return dependency


def _current_user_dependency() -> Callable[..., Any]:
    from app.api.deps import get_current_user

    return get_current_user


def _tenant_db_dependency() -> Callable[..., Any]:
    from app.api.deps import get_tenant_db

    return get_tenant_db


class CurrentUserProtocol:
    """Structural stand-in for `api.deps.CurrentUser`.

    Named rather than imported so this module keeps no import edge into the
    API package: `api.deps` imports this module, and the cycle would be real.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role


# ── Cardinality (RBAC 5, 39) ─────────────────────────────────────────────────
#
# The database enforces these with partial unique indexes (migration 0061).
# The helpers below exist so the API can answer with a readable 409 instead of
# surfacing an IntegrityError, NOT so the check can be skipped: a check that
# lives only in application code is one a second writer, a backfill script or
# a background-task race walks straight past.

CARDINALITY_ONE_ACTIVE_SUPER_ADMIN = "uq_users_one_active_super_admin_per_tenant"
CARDINALITY_ONE_RECRUITER_PER_JOB = "uq_job_assignments_one_active_recruiter"
CARDINALITY_ONE_HIRING_MANAGER_PER_JOB = "uq_job_assignments_one_active_hiring_manager"

#: Assignment roles that admit exactly one active holder per job (5, 39).
SINGULAR_ASSIGNMENTS: frozenset[str] = frozenset(
    {ASSIGNMENT_RECRUITER, ASSIGNMENT_HIRING_MANAGER}
)


def assignment_is_singular(assignment_role: str) -> bool:
    """Whether a job admits exactly one active holder of this assignment.

    Interview Manager is the one that is not (13.1), and it is not an
    oversight: a job MAY have several, and the review model in 29 draws three
    of them.
    """
    return assignment_role in SINGULAR_ASSIGNMENTS


# ═══════════════════════════════════════════════════════════════════════════
# RBAC 34: an agent acts under a human principal, and holds no authority of
# its own
# ═══════════════════════════════════════════════════════════════════════════
#
# `services/tools/permissions.AGENT_TOOLS` answers "which TOOLS may this agent
# call". That is a real boundary and it stays. It is not the boundary RBAC 34
# asks for, which is a different question: on WHOSE authority, over WHICH
# tenant, and within WHICH job. The specification's own worked example is
# exact:
#
#     A Recruiter-authorized AI agent may assist with JD generation.
#     It MUST NOT use that authority to modify Hiring Manager-controlled
#     criteria.
#
# A tool grant cannot express that, because "write the must-have skills" and
# "write the JD draft" would be the same tool called with different arguments,
# and because the answer depends on the human, not on the agent.
#
# THIS IS IN `rbac` AND NOT IN `permissions` FOR TWO REASONS
# ----------------------------------------------------------
# spec-doc6 9.2 says to enforce agent authorization with the same layer the
# HTTP endpoints use rather than a parallel one, and this module is that layer.
# `authorize_agent_action` resolves through `decide`, the identical function
# `require_authorized` calls for a request, so an agent inherits for free and
# permanently: the tenant check that answers 404, the 24 ceiling including its
# NEVER cells, the per-job assignment scope of 23, and the state rules of 21,
# 22 and 26. A parallel implementation would have had to re-derive all six and
# would have drifted on the first one somebody forgot.
#
# The second reason is mechanical: `services/tools/permissions` must stay an
# import leaf, because `agents/identity.py` reads its attributes at module
# scope (`tests/test_import_graph.py`).

from app.services import capabilities as capabilities_mod
from app.services.tools import permissions

#: Every capability an agent may cause a mutation under, per agent. The
#: principal must hold the capability AND the agent must be declared for it;
#: the intersection is the agent's reach, so neither side alone can widen it.
#:
#: Read the empty sets carefully. Yukti, Vaada, Miti and Siddhi hold NO write
#: capability at all. That is not an omission: they grade, gather evidence and
#: write reports, and none of those is a mutation of the hiring definition or
#: of a candidate's status. A sensitive action requires a human at any
#: confidence, and the enforcement is the absence of the capability, which is
#: the same rule this codebase already applies to write tools.
AGENT_CAPABILITIES: dict[str, frozenset[str]] = {
    # Bodha runs the SWOT session and the Company DNA intake, both of which
    # feed Hiring-Manager-controlled fields. It may write them only when the
    # human it acts for may: a Recruiter running a SWOT session gets a
    # refusal, which is 34's worked example almost verbatim.
    permissions.AGENT_BODHA: frozenset(
        {
            capabilities_mod.EDIT_SWOT,
            capabilities_mod.EDIT_JOB_PHILOSOPHY,
        }
    ),
    # Sutra compiles the matrix from the SWOT and the Company DNA, which means
    # writing the four criteria fields. Same rule: only under a principal who
    # holds them.
    permissions.AGENT_SUTRA: frozenset(
        {
            capabilities_mod.EDIT_MUST_HAVE_SKILLS,
            capabilities_mod.EDIT_NICE_TO_HAVE_SKILLS,
            capabilities_mod.EDIT_BEHAVIOURAL_COMPETENCIES,
            capabilities_mod.EDIT_EVALUATION_RUBRICS,
        }
    ),
    permissions.AGENT_YUKTI: frozenset(),
    permissions.AGENT_VAADA: frozenset(),
    permissions.AGENT_MITI: frozenset(),
    permissions.AGENT_SIDDHI: frozenset(),
    # The pre-existing agents. `job_setup` is the surface that runs framework
    # generation today, so it carries the same criteria reach as Sutra; the
    # rest write nothing.
    permissions.AGENT_JOB_SETUP: frozenset(
        {
            capabilities_mod.EDIT_MUST_HAVE_SKILLS,
            capabilities_mod.EDIT_NICE_TO_HAVE_SKILLS,
            capabilities_mod.EDIT_BEHAVIOURAL_COMPETENCIES,
            capabilities_mod.EDIT_EVALUATION_RUBRICS,
            capabilities_mod.EDIT_SWOT,
            capabilities_mod.EDIT_JOB_PHILOSOPHY,
        }
    ),
    permissions.AGENT_RANKING: frozenset(),
    permissions.AGENT_PPI_REPORT: frozenset(),
    permissions.AGENT_EMAIL: frozenset(),
    permissions.AGENT_PROBE: frozenset(),
    permissions.AGENT_INTERVIEWER: frozenset(),
    permissions.AGENT_SCORING: frozenset(),
}


#: No agent may ever cause these, whoever authorised it. RBAC 34's list of
#: things AI agents must not bypass ends with workflow state and audit, and
#: 39's last rule closes the door on agent execution as a bypass route.
#: Finalization is a HUMAN act (20 requires it to record the user who
#: finalized it), publication is the Recruiter's operational act (9.6), and
#: rejecting a candidate is the sensitive action a human must take at any
#: confidence.
AGENT_FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset(
    {
        capabilities_mod.FINALIZE_ROLE_DEFINITION,
        capabilities_mod.PUBLISH_JOB,
        capabilities_mod.REJECT_JD,
        capabilities_mod.DECIDE_PROFILE,
        capabilities_mod.UPDATE_PIPELINE_STATUS,
        capabilities_mod.MANAGE_STAFF,
        capabilities_mod.ASSIGN_ROLES,
        capabilities_mod.INTEGRITY_DISPOSITION,
    }
)


def agent_capabilities(agent: str) -> frozenset[str]:
    """Deny by default: an unregistered agent may cause no mutation."""
    return AGENT_CAPABILITIES.get(agent, frozenset())


def authorize_agent_action(
    principal: Principal,
    agent: str,
    capability: str,
    resource: Resource | None = None,
    *,
    granted: bool,
) -> "Authorization":
    """The RBAC 34 gate, resolving through the human's own authorization.

    Returns an `Authorization`. The order is deliberate and mirrors the
    tool executor's: the agent's own declaration is checked FIRST, so a
    capability no agent should ever hold is refused before the human's
    permissions are even consulted. Enforcement is ordering, not politeness.

    `principal.agent` must name this agent. A principal built without one is
    a human acting directly, and running a human's request through the agent
    gate would attribute their action to a program.
    """
    if getattr(principal, "agent", None) != agent:
        return Authorization(
            Decision.DENY,
            "agent_principal_mismatch",
            capabilities_mod.Invariant.DENY,
        )
    # Tenant before anything about the agent. Every other check below refuses
    # for a reason that is a property of the AGENT rather than of the
    # resource, so running them first would let a caller tell "this agent
    # cannot do that" apart from "that resource is not yours" -- and 34 puts
    # tenant isolation at the top of the list agents must not bypass. Answering
    # 404 uniformly costs nothing and keeps the agent surface indistinguishable
    # from the HTTP one, which is the whole point of sharing a layer.
    if resource is not None:
        resource_tenant = getattr(resource, "tenant_id", None)
        if resource_tenant is not None and str(resource_tenant) != str(
            getattr(principal, "tenant_id", None)
        ):
            return Authorization(
                Decision.NOT_FOUND, "cross_tenant", capabilities_mod.Invariant.DENY
            )
    if capability in AGENT_FORBIDDEN_CAPABILITIES:
        return Authorization(
            Decision.DENY,
            "capability_forbidden_to_every_agent",
            capabilities_mod.Invariant.NEVER,
        )
    if capability not in agent_capabilities(agent):
        return Authorization(
            Decision.DENY,
            "agent_not_declared_for_capability",
            capabilities_mod.Invariant.DENY,
        )
    # The human's own decision, unchanged and unweakened. An agent can only
    # ever be a subset of its principal.
    return decide(principal, capability, resource, granted=granted)


# ── RBAC 7.1: transferring the Super Admin seat ──────────────────────────────
#
# WHY THIS EXISTS AT ALL
# ----------------------
# 7.1 is two sentences and both are load-bearing:
#
#     "Each client organization MUST have exactly one active Super Admin."
#     "The system MUST provide a controlled mechanism for changing/transferring
#      the Super Admin role when necessary."
#
# Migration 0061 enforces the first with a partial unique index. Shipping that
# index WITHOUT this function would be the worse half of the requirement on its
# own: a client whose Super Admin leaves the company could not appoint another
# one, because the index refuses the second row and nothing existed to
# deactivate the first. The constraint is precisely what would make that
# unrecoverable, so the escape hatch ships with it.
#
# WHY IT IS ONE FUNCTION AND NOT TWO ENDPOINTS
# --------------------------------------------
# Demote-then-promote as two requests has a window in which the tenant has no
# Super Admin, and a failure in the second half leaves it there permanently.
# This runs both writes in the CALLER's transaction, in that order, so either
# the seat moves or nothing happened. The order is not interchangeable: the
# index is checked per statement, so promoting first would be refused while the
# outgoing holder is still active.


class SuperAdminTransferError(RuntimeError):
    """The seat could not be moved, and the reason is stated rather than
    surfaced as an IntegrityError from a unique index nobody can read."""


async def transfer_super_admin(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    to_user_id: uuid.UUID | str,
    demoted_role: Role = Role.hr_manager,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move the Client Super Admin seat within one tenant, atomically.

    The outgoing holder is DEMOTED, never deleted and never disabled. Two
    reasons: 7.3 makes deactivating staff a separate, deliberate act, and a
    person who has just handed over is usually still doing the job they did
    before. `demoted_role` defaults to HR Manager, which is the nearest
    organisation-wide role in 6's hierarchy, so the handover costs them no
    operational access they were using.

    Returns the facts the caller needs for its audit row (30 wants previous and
    new state). It does NOT write the audit row itself: the caller knows the
    actor, the request metadata and the correlation id, and an audit row
    written here would have to guess all three.
    """
    now = now or datetime.now(timezone.utc)
    incoming = (
        await session.execute(
            text(
                "SELECT id, tenant_id, role, status FROM users WHERE id = :uid"
            ),
            {"uid": str(to_user_id)},
        )
    ).mappings().first()
    if incoming is None or str(incoming["tenant_id"]) != str(tenant_id):
        # Same answer for "not in this tenant" as for "does not exist", for the
        # reason every other lookup in this module gives.
        raise SuperAdminTransferError("No such user in this organization")
    if incoming["status"] == "disabled":
        raise SuperAdminTransferError(
            "A deactivated account cannot hold the Super Admin seat; "
            "reactivate them first"
        )

    outgoing = (
        await session.execute(
            text(
                "SELECT id FROM users "
                "WHERE tenant_id = :tid AND role = :role AND status <> 'disabled'"
            ),
            {"tid": str(tenant_id), "role": Role.client.value},
        )
    ).mappings().all()
    if len(outgoing) > 1:
        # The invariant is already broken, which means the index was never
        # created or was dropped. Refusing is the only honest answer: picking
        # one to demote would be this function deciding which Super Admin was
        # real, and 0061 explicitly declines to make that choice.
        raise SuperAdminTransferError(
            f"This organization has {len(outgoing)} active Super Admins, which "
            "RBAC 5 forbids. Resolve that before transferring the seat."
        )

    previous_holder = str(outgoing[0]["id"]) if outgoing else None
    if previous_holder == str(to_user_id):
        raise SuperAdminTransferError("That person already holds the seat")

    # Demote FIRST. The partial unique index is checked per statement, so
    # promoting first is refused while the outgoing holder is still active.
    if previous_holder is not None:
        await session.execute(
            text("UPDATE users SET role = :role WHERE id = :uid"),
            {"role": demoted_role.value, "uid": previous_holder},
        )
    await session.execute(
        text("UPDATE users SET role = :role WHERE id = :uid"),
        {"role": Role.client.value, "uid": str(to_user_id)},
    )
    return {
        "tenant_id": str(tenant_id),
        "previous_state": {
            "super_admin_user_id": previous_holder,
            "incoming_role": str(incoming["role"]),
        },
        "new_state": {
            "super_admin_user_id": str(to_user_id),
            "demoted_to": demoted_role.value if previous_holder else None,
        },
        "at": now,
    }
