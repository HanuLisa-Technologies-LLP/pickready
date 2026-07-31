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

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Role
from app.models.tenant import RolePermission
from app.models.user import User
from app.services.capabilities import ALL_CAPABILITIES


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

    conditions = [RolePermission.tenant_id.is_(None)]
    if tid is not None:
        conditions.append(RolePermission.tenant_id == tid)

    stmt = select(
        RolePermission.tenant_id, RolePermission.capability, RolePermission.allowed
    ).where(RolePermission.role == role, or_(*conditions))
    rows = (await session.execute(stmt)).all()

    tenant_rows = {r.capability: r.allowed for r in rows if r.tenant_id is not None}
    global_rows = {r.capability: r.allowed for r in rows if r.tenant_id is None}
    return resolve_capability_set(tenant_rows, global_rows, user_overrides=overrides)


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
