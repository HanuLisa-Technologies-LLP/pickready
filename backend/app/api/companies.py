"""Client company endpoints (FR-2.x / contract rev 2): company page, staff
management (HR Manager / Recruiter / Hiring Manager accounts — Hiring
Managers capped at 5), approval-level configuration, and per-tenant email
templates. The whole staff hierarchy belongs to the client organization —
staff creation moved here from the Owner console (Pickready.docx §2)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_db, require_capability
from app.models.company import Company, EmailTemplate, HiringManager
from app.models.enums import Role, UserStatus
from app.models.user import User
from app.schemas.companies import (
    ApprovalLevelsIn,
    ApprovalLevelsOut,
    CompanyPageIn,
    CompanyPageOut,
    EmailTemplateIn,
    EmailTemplateOut,
    StaffCreateIn,
    StaffOut,
)
from app.services import capabilities as caps
from app.services.audit import audit
from app.services.owner import OwnerRoleViolation, ensure_owner_invariant
from app.workers.celery_app import celery_app

router = APIRouter()

MAX_HIRING_MANAGERS = 5  # FR-2.2

# Roles creatable via POST /companies/me/staff — client / super_admin /
# candidate are explicitly NOT creatable here (contract rev 2).
STAFF_ROLES: frozenset[Role] = frozenset(
    {Role.hr_manager, Role.recruiter, Role.hiring_manager}
)


# ── Pure staff rules (DB-free, unit-testable) ────────────────────────────────

def validate_staff_role(role: str) -> Role:
    """The 3 org staff roles only. Raises ValueError (mapped to 400) for
    anything else — including client, super_admin, and candidate."""
    try:
        parsed = Role(role)
    except ValueError as exc:
        raise ValueError(f"unknown role: {role!r}") from exc
    if parsed not in STAFF_ROLES:
        raise ValueError(
            f"role must be one of {sorted(r.value for r in STAFF_ROLES)}"
        )
    return parsed


def hiring_manager_cap_reached(
    active_hiring_managers: int, max_hiring_managers: int = MAX_HIRING_MANAGERS
) -> bool:
    """FR-2.2: at most 5 ACTIVE (i.e. not disabled) Hiring Managers per
    tenant. HR Managers / Recruiters are uncapped (contract rev 2)."""
    return active_hiring_managers >= max_hiring_managers


async def _get_company(session: AsyncSession, user: CurrentUser) -> Company | None:
    # Explicit tenant filter is defense in depth; RLS is the boundary.
    return (
        await session.execute(select(Company).where(Company.tenant_id == user.tenant_id))
    ).scalars().first()


@router.get("/me", response_model=CompanyPageOut)
async def get_company_page(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyPageOut:
    company = await _get_company(session, user)
    if company is None:
        raise HTTPException(status_code=404, detail="Company page not created yet")
    return CompanyPageOut.model_validate(company)


@router.put("/me", response_model=CompanyPageOut)
async def upsert_company_page(
    body: CompanyPageIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_COMPANY_PAGE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyPageOut:
    company = await _get_company(session, user)
    if company is None:
        company = Company(tenant_id=user.tenant_id)
        session.add(company)
    company.brief = body.brief
    company.culture = body.culture
    company.policies = body.policies
    company.benefits = body.benefits
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="company_page_upserted", target_type="company", target_id=company.id)
    return CompanyPageOut.model_validate(company)


def _staff_out(u: User, approval_level: str | None = None) -> StaffOut:
    return StaffOut(
        id=u.id, email=u.email, full_name=u.full_name, phone=u.phone,
        role=u.role.value, status=u.status.value, approval_level=approval_level,
    )


@router.get("/me/staff", response_model=list[StaffOut])
async def list_staff(
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[StaffOut]:
    """All staff users of the tenant (contract rev 2). approval_level comes
    from the hiring_managers mirror row where one exists."""
    rows = (
        await session.execute(
            select(User, HiringManager.approval_level)
            .outerjoin(
                HiringManager,
                (HiringManager.user_id == User.id)
                & (HiringManager.tenant_id == User.tenant_id),
            )
            .where(User.tenant_id == user.tenant_id, User.role.in_(STAFF_ROLES))
            .order_by(User.created_at)
        )
    ).all()
    return [_staff_out(u, level) for u, level in rows]


@router.post("/me/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
async def create_staff(
    body: StaffCreateIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffOut:
    try:
        role = validate_staff_role(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Defensive owner invariant (contract rev 2) — validate_staff_role already
    # excludes super_admin; this keeps the guarantee if STAFF_ROLES ever grows.
    try:
        ensure_owner_invariant(role, str(body.email))
    except OwnerRoleViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if role == Role.hiring_manager:
        # FR-2.2: server-side cap on ACTIVE (not disabled) Hiring Managers.
        active_hms = (
            await session.execute(
                select(func.count()).select_from(User).where(
                    User.tenant_id == user.tenant_id,
                    User.role == Role.hiring_manager,
                    User.status != UserStatus.disabled,
                )
            )
        ).scalar_one()
        if hiring_manager_cap_reached(active_hms):
            raise HTTPException(
                status_code=409,
                detail=f"At most {MAX_HIRING_MANAGERS} Hiring Manager accounts per tenant (FR-2.2)",
            )

    dup = (
        await session.execute(
            select(User).where(
                User.tenant_id == user.tenant_id,
                User.email == str(body.email),
                User.role == role,
            )
        )
    ).scalars().first()
    if dup is not None:
        raise HTTPException(
            status_code=409, detail="A user with this email and role already exists"
        )

    staff_user = User(
        tenant_id=user.tenant_id, role=role, email=str(body.email),
        phone=body.phone, full_name=body.full_name, status=UserStatus.invited,
    )
    session.add(staff_user)
    await session.flush()

    approval_level: str | None = None
    if role == Role.hiring_manager:
        # Mirror into hiring_managers (approval assignments key off it).
        hm = HiringManager(
            tenant_id=user.tenant_id, user_id=staff_user.id,
            approval_level=body.approval_level,
        )
        session.add(hm)
        await session.flush()
        approval_level = hm.approval_level
    # ASSUMPTION: approval_level on non-HM staff is silently ignored — only
    # Hiring Managers hold approval assignments (FR-2.2/2.3).

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="staff_created", target_type="user", target_id=staff_user.id,
                metadata={"role": role.value, "email": str(body.email)})
    # New staff activate on their first verified Firebase sign-in. The invite
    # contains onboarding guidance; PickReady never generates an app OTP.
    celery_app.send_task(
        "pickready.send_email",
        args=[str(user.tenant_id), str(body.email), "staff_invite",
              {"full_name": body.full_name, "role": role.value}],
    )
    return _staff_out(staff_user, approval_level)


@router.delete("/me/staff/{user_id}", response_model=StaffOut)
async def deactivate_staff(
    user_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffOut:
    """Deactivate (status=disabled) — never a hard delete. Only the 3 staff
    roles are targetable; the client account cannot deactivate itself here."""
    staff_user = (
        await session.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == user.tenant_id,  # defense in depth; RLS is the boundary
                User.role.in_(STAFF_ROLES),
            )
        )
    ).scalars().first()
    if staff_user is None:
        raise HTTPException(status_code=404, detail="Staff user not found")

    staff_user.status = UserStatus.disabled
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="staff_deactivated", target_type="user", target_id=staff_user.id,
                metadata={"role": staff_user.role.value})
    return _staff_out(staff_user)


@router.put("/me/approval-levels", response_model=ApprovalLevelsOut)
async def configure_approval_levels(
    body: ApprovalLevelsIn,
    user: CurrentUser = Depends(require_capability(caps.CONFIGURE_APPROVAL_LEVELS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ApprovalLevelsOut:
    """FR-2.3: choose which of the 4 levels are mandatory and who approves
    each active one. Approvers must be users of this tenant."""
    for level, entry in body.config.items():
        if entry.active:
            approver = await session.get(User, entry.approver_user_id)
            if approver is None or approver.tenant_id != user.tenant_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"approver for level '{level}' is not a user of this tenant",
                )

    company = await _get_company(session, user)
    if company is None:
        raise HTTPException(status_code=409, detail="Create the company page first (FR-2.1)")
    company.approval_levels_config = {
        level: entry.model_dump(mode="json") for level, entry in body.config.items()
    }
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="approval_levels_configured", target_type="company",
                target_id=company.id, metadata=company.approval_levels_config)
    return ApprovalLevelsOut(config=body.config)


@router.get("/me/email-templates", response_model=list[EmailTemplateOut])
async def list_email_templates(
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_TEMPLATES)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[EmailTemplateOut]:
    rows = (
        await session.execute(
            select(EmailTemplate)
            .where(EmailTemplate.tenant_id == user.tenant_id, EmailTemplate.is_active.is_(True))
            .order_by(EmailTemplate.name)
        )
    ).scalars().all()
    return [EmailTemplateOut.model_validate(r) for r in rows]


async def _upsert_template(
    session: AsyncSession, user: CurrentUser, body: EmailTemplateIn
) -> EmailTemplate:
    """Templates are versioned (ESD §12): each save creates a new active
    version and deactivates the previous one."""
    latest = (
        await session.execute(
            select(EmailTemplate)
            .where(EmailTemplate.tenant_id == user.tenant_id, EmailTemplate.name == body.name)
            .order_by(EmailTemplate.version.desc())
        )
    ).scalars().first()
    version = 1
    if latest is not None:
        version = latest.version + 1
        latest.is_active = False
    row = EmailTemplate(
        tenant_id=user.tenant_id, name=body.name, subject=body.subject,
        body=body.body, version=version, is_active=True,
    )
    session.add(row)
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="email_template_saved", target_type="email_template",
                target_id=row.id, metadata={"name": body.name, "version": version})
    return row


@router.post(
    "/me/email-templates", response_model=EmailTemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_email_template(
    body: EmailTemplateIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_TEMPLATES)),
    session: AsyncSession = Depends(get_tenant_db),
) -> EmailTemplateOut:
    return EmailTemplateOut.model_validate(await _upsert_template(session, user, body))


@router.put("/me/email-templates", response_model=EmailTemplateOut)
async def update_email_template(
    body: EmailTemplateIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_EMAIL_TEMPLATES)),
    session: AsyncSession = Depends(get_tenant_db),
) -> EmailTemplateOut:
    return EmailTemplateOut.model_validate(await _upsert_template(session, user, body))
