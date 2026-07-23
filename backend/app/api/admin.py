"""Owner console (contract rev 2, formerly "Super Admin"). Every request runs
through `get_superadmin_db`, which enforces the super_admin audience, uses the
RLS bypass scope, and writes an audit_log row for the cross-tenant access.

The Owner ONLY onboards tenants, lists them, edits permission templates, and
reads the audit log. Staff creation lives in the client portal
(/companies/me/staff) — the whole staff hierarchy belongs to the client
organization, not the platform (Pickready.docx §2)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_superadmin_db
from app.models.enums import Role, UserStatus
from app.models.tenant import AuditLog, RolePermission, Tenant
from app.models.user import User
from app.schemas.admin import (
    AdminUserOut,
    AuditLogOut,
    PermissionOut,
    PermissionsUpdateIn,
    TenantCreateIn,
    TenantCreateOut,
    TenantOut,
)
from app.services.audit import audit
from app.services.capabilities import DEFAULT_PERMISSION_MATRIX
from app.services.owner import OwnerRoleViolation, ensure_owner_invariant
from app.workers.celery_app import celery_app

router = APIRouter()


def _seed_permissions(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Copy the default template (PRD §6) into tenant rows so Super Admin can
    tune this tenant independently later."""
    for role, caps in DEFAULT_PERMISSION_MATRIX.items():
        for capability, allowed in caps.items():
            session.add(RolePermission(
                tenant_id=tenant_id, role=role, capability=capability, allowed=allowed
            ))


@router.post("/tenants", response_model=TenantCreateOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> TenantCreateOut:
    existing = (
        await session.execute(select(Tenant).where(Tenant.domain == body.domain))
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A tenant with this domain already exists")

    # Defensive owner invariant: no user-creating path may ever mint a
    # super_admin identity other than settings.owner_email (contract rev 2).
    try:
        ensure_owner_invariant(Role.client, str(body.client_email))
    except OwnerRoleViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    tenant = Tenant(name=body.name, domain=body.domain)
    session.add(tenant)
    await session.flush()

    client_user = User(
        tenant_id=tenant.id,
        role=Role.client,
        email=str(body.client_email),
        phone=body.client_phone,
        full_name=body.name,
        status=UserStatus.invited,
    )
    session.add(client_user)
    _seed_permissions(session, tenant.id)
    await session.flush()

    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="tenant_created", target_type="tenant", target_id=tenant.id,
        metadata={"name": body.name, "domain": body.domain},
    )
    # Invite the client via the OTP-based flow (email delivery is async).
    celery_app.send_task(
        "pickready.send_email",
        args=[str(tenant.id), str(body.client_email), "client_invite",
              {"tenant_name": body.name}],
    )
    return TenantCreateOut(
        tenant=TenantOut.model_validate(tenant),
        client_user=AdminUserOut.model_validate(client_user),
    )


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[TenantOut]:
    rows = (await session.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    return [TenantOut.model_validate(t) for t in rows]


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(
    tenant_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[PermissionOut]:
    """Tenant rows when tenant_id given; the global template otherwise."""
    stmt = select(RolePermission)
    if tenant_id is not None:
        stmt = stmt.where(RolePermission.tenant_id == tenant_id)
    else:
        stmt = stmt.where(RolePermission.tenant_id.is_(None))
    rows = (await session.execute(stmt.order_by(RolePermission.role, RolePermission.capability))).scalars().all()
    return [PermissionOut.model_validate(r) for r in rows]


@router.put("/permissions", response_model=list[PermissionOut])
async def update_permissions(
    body: PermissionsUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[PermissionOut]:
    """Upsert role_permissions rows — per tenant, or the global template when
    tenant_id is omitted (FR-11.2). Every change is audit-logged."""
    if body.tenant_id is not None and await session.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    out: list[RolePermission] = []
    for entry in body.entries:
        cond = (
            RolePermission.tenant_id == body.tenant_id
            if body.tenant_id is not None
            else RolePermission.tenant_id.is_(None)
        )
        row = (
            await session.execute(
                select(RolePermission).where(
                    cond,
                    RolePermission.role == entry.role,
                    RolePermission.capability == entry.capability,
                )
            )
        ).scalars().first()
        if row is None:
            row = RolePermission(
                tenant_id=body.tenant_id, role=entry.role,
                capability=entry.capability, allowed=entry.allowed,
            )
            session.add(row)
        else:
            row.allowed = entry.allowed
        out.append(row)
    await session.flush()

    await audit(
        session, tenant_id=body.tenant_id, actor_user_id=user.user_id,
        action="permissions_updated", target_type="role_permissions",
        metadata={"entries": [e.model_dump(mode="json") for e in body.entries]},
    )
    return [PermissionOut.model_validate(r) for r in out]


@router.get("/audit-log", response_model=list[AuditLogOut])
async def list_audit_log(
    tenant_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[AuditLogOut]:
    stmt = select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]
