"""Client company endpoints (FR-2.x): company page, Hiring Manager accounts
(max 5), approval-level configuration, and per-tenant email templates."""
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
    HiringManagerCreateIn,
    HiringManagerOut,
)
from app.services import capabilities as caps
from app.services.audit import audit
from app.workers.celery_app import celery_app

router = APIRouter()

MAX_HIRING_MANAGERS = 5  # FR-2.2


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


@router.get("/me/hiring-managers", response_model=list[HiringManagerOut])
async def list_hiring_managers(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[HiringManagerOut]:
    rows = (
        await session.execute(
            select(HiringManager, User)
            .join(User, User.id == HiringManager.user_id)
            .where(HiringManager.tenant_id == user.tenant_id)
            .order_by(HiringManager.created_at)
        )
    ).all()
    return [
        HiringManagerOut(
            id=hm.id, user_id=u.id, email=u.email, full_name=u.full_name,
            phone=u.phone, approval_level=hm.approval_level, status=u.status.value,
        )
        for hm, u in rows
    ]


@router.post(
    "/me/hiring-managers",
    response_model=HiringManagerOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_hiring_manager(
    body: HiringManagerCreateIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_HIRING_MANAGERS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> HiringManagerOut:
    count = (
        await session.execute(
            select(func.count()).select_from(HiringManager)
            .where(HiringManager.tenant_id == user.tenant_id)
        )
    ).scalar_one()
    if count >= MAX_HIRING_MANAGERS:
        raise HTTPException(
            status_code=409,
            detail=f"At most {MAX_HIRING_MANAGERS} Hiring Manager accounts per tenant (FR-2.2)",
        )

    dup = (
        await session.execute(
            select(User).where(
                User.tenant_id == user.tenant_id,
                User.email == str(body.email),
                User.role == Role.hiring_manager,
            )
        )
    ).scalars().first()
    if dup is not None:
        raise HTTPException(status_code=409, detail="A Hiring Manager with this email already exists")

    hm_user = User(
        tenant_id=user.tenant_id, role=Role.hiring_manager, email=str(body.email),
        phone=body.phone, full_name=body.full_name, status=UserStatus.invited,
    )
    session.add(hm_user)
    await session.flush()
    hm = HiringManager(
        tenant_id=user.tenant_id, user_id=hm_user.id, approval_level=body.approval_level
    )
    session.add(hm)
    await session.flush()

    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="hiring_manager_created", target_type="user", target_id=hm_user.id,
                metadata={"email": str(body.email)})
    # OTP-based invite (FR-2.2): the invite email points them at OTP login.
    celery_app.send_task(
        "pickready.send_email",
        args=[str(user.tenant_id), str(body.email), "hiring_manager_invite",
              {"full_name": body.full_name}],
    )
    return HiringManagerOut(
        id=hm.id, user_id=hm_user.id, email=hm_user.email, full_name=hm_user.full_name,
        phone=hm_user.phone, approval_level=hm.approval_level, status=hm_user.status.value,
    )


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
