"""Dynamic RBAC engine (ESD §6): permissions are data, not code.

Resolution order for (tenant_id, role, capability):
  1. tenant-specific row in `role_permissions` (Super Admin per-tenant override)
  2. global template row (tenant_id IS NULL)
  3. deny (missing rows never grant anything)

Never branch on role in business logic — use `require_capability(...)`
(app/api/deps.py), which calls `has_capability` below.
"""
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Role
from app.models.tenant import RolePermission
from app.services.capabilities import ALL_CAPABILITIES


def resolve_permission(
    tenant_rows: dict[str, bool], global_rows: dict[str, bool], capability: str
) -> bool:
    """Pure resolution: a tenant-specific row (even `allowed=False`) always
    beats the global template row; absent everywhere -> deny."""
    if capability in tenant_rows:
        return tenant_rows[capability]
    return global_rows.get(capability, False)


async def has_capability(
    session: AsyncSession,
    tenant_id: uuid.UUID | str | None,
    role: Role | str,
    capability: str,
) -> bool:
    """Look up role_permissions for (tenant_id, role, capability), falling back
    to the global template (tenant_id IS NULL) when no tenant row exists."""
    role = Role(role)
    tid = uuid.UUID(str(tenant_id)) if tenant_id else None

    conditions = [RolePermission.tenant_id.is_(None)]
    if tid is not None:
        conditions.append(RolePermission.tenant_id == tid)

    stmt = select(
        RolePermission.tenant_id, RolePermission.capability, RolePermission.allowed
    ).where(
        RolePermission.role == role,
        RolePermission.capability == capability,
        or_(*conditions),
    )
    rows = (await session.execute(stmt)).all()

    tenant_rows = {r.capability: r.allowed for r in rows if r.tenant_id is not None}
    global_rows = {r.capability: r.allowed for r in rows if r.tenant_id is None}
    return resolve_permission(tenant_rows, global_rows, capability)


# ── Bulk resolution (contract rev 2: capabilities in login/me responses) ─────

def resolve_capability_set(
    tenant_rows: dict[str, bool],
    global_rows: dict[str, bool],
    capabilities: list[str] | None = None,
) -> list[str]:
    """Pure bulk resolver: every capability that resolves to allowed under the
    same precedence as `resolve_permission` (tenant override > global > deny).
    Order follows ALL_CAPABILITIES so responses are stable."""
    caps = capabilities if capabilities is not None else ALL_CAPABILITIES
    return [c for c in caps if resolve_permission(tenant_rows, global_rows, c)]


async def resolve_role_capabilities(
    session: AsyncSession,
    tenant_id: uuid.UUID | str | None,
    role: Role | str,
) -> list[str]:
    """Fetch the tenant + global rows for this role ONCE, then resolve every
    capability in ALL_CAPABILITIES (single round-trip, not N lookups)."""
    role = Role(role)
    tid = uuid.UUID(str(tenant_id)) if tenant_id else None

    conditions = [RolePermission.tenant_id.is_(None)]
    if tid is not None:
        conditions.append(RolePermission.tenant_id == tid)

    stmt = select(
        RolePermission.tenant_id, RolePermission.capability, RolePermission.allowed
    ).where(RolePermission.role == role, or_(*conditions))
    rows = (await session.execute(stmt)).all()

    tenant_rows = {r.capability: r.allowed for r in rows if r.tenant_id is not None}
    global_rows = {r.capability: r.allowed for r in rows if r.tenant_id is None}
    return resolve_capability_set(tenant_rows, global_rows)


async def capabilities_for_user(
    session: AsyncSession,
    *,
    role: Role | str,
    tenant_id: uuid.UUID | str | None,
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
    return await resolve_role_capabilities(session, tenant_id, role)
