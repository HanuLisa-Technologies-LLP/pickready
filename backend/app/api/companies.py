"""Client company endpoints (FR-2.x / contract rev 2): company profile, staff
management (HR Manager / Recruiter / Hiring Manager accounts), invitations,
approval-level configuration, and per-tenant email templates. The whole staff
hierarchy belongs to the client organization — staff creation moved here from
the Owner console (Readypick.docx §2).

FLAT STAFF MODEL (PRD v1.0 §4): the three staff roles are equal. Nothing here
branches on which of the three a member holds; the capability grant set is
identical for all three (services/capabilities.py) and gating stays
`require_capability` (claude.md rule 3).

INVITE LIFECYCLE — there is no password anywhere (claude.md rule 2):
  create staff row (status=invited)
    -> mint a single-use, 7-day, SHA-256-hashed token
    -> dispatch `pickready.send_email` (never inline — rule 4) with a
       /join?invite=<token> link, and ALSO return the link so an admin can
       copy it when SMTP is not yet configured
    -> invitee opens /join, signs in with Firebase (Google / email+password)
    -> /auth/firebase/session links the Firebase uid to this row BY EMAIL and
       flips invited -> active
    -> /companies/invites/{token}/accept burns the token (single use).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_public_db,
    get_tenant_db,
    require_capability,
)
from app.core.config import get_settings
from app.models.company import Company, EmailTemplate, HiringManager
from app.models.compliance import (
    DOCUMENT_GROUPS,
    DOCUMENT_LABELS,
    DOCUMENT_TYPES,
    ComplianceDocument,
)
from app.models.enums import Role, UserStatus
from app.models.invite import (
    INVITE_PENDING,
    INVITE_TTL_DAYS,
    StaffInvite,
    build_invite_link,
    generate_invite_token,
    hash_invite_token,
    invite_expiry,
    invite_state,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.companies import (
    CompanyProfileResearchOut,
    ApprovalLevelsIn,
    ApprovalLevelsOut,
    CompanyProfileIn,
    CompanyProfileOut,
    EmailTemplateIn,
    EmailTemplateOut,
    InviteAcceptOut,
    PublicInviteOut,
    StaffCreateIn,
    StaffPermissionsIn,
    StaffPermissionsOut,
    StaffUpdateIn,
    StaffOut,
)
# The compliance slot shapes are shared with api/provider.py on purpose: the
# HR Head who files a document and the owner who reads it must be looking at
# the same seven-slot structure, and one schema is how that stays true.
from app.schemas.provider import (
    ComplianceDocumentOut,
    ComplianceDocumentSlot,
    document_slots,
)
from app.services import capabilities as caps
from app.services import document_storage
from app.services import rbac
from app.services import role_hierarchy
from app.services import tenant_cache
from app.services.audit import audit
from app.services.owner import OwnerRoleViolation, ensure_owner_invariant
from app.workers import agent_client
from app.workers.dispatch import dispatch

router = APIRouter()

MAX_HIRING_MANAGERS = 5  # FR-2.2

# Roles creatable via POST /companies/me/staff. `client` (the customer's Super
# Admin), super_admin and candidate are explicitly NOT creatable here: the
# Super Admin seat is minted at onboarding by the Provider, and a portal that
# could mint another one would let a Recruitment Manager promote themselves past
# every rule in `services/role_hierarchy`.
STAFF_ROLES: frozenset[Role] = role_hierarchy.MANAGEABLE_ROLES


# ── Pure staff rules (DB-free, unit-testable) ────────────────────────────────

def validate_staff_role(role: str) -> Role:
    """One of the manageable staff roles. Raises ValueError (mapped to 400) for
    anything else, including client, super_admin, and candidate."""
    try:
        parsed = Role(role)
    except ValueError as exc:
        raise ValueError(f"unknown role: {role!r}") from exc
    if parsed not in STAFF_ROLES:
        raise ValueError(
            f"role must be one of {sorted(r.value for r in STAFF_ROLES)}"
        )
    return parsed


def ensure_can_manage(actor_role: Role | str | None, target_role: Role) -> None:
    """Refuse a staff action against a peer or a superior (spec §29).

    STRICTLY above, and the strictness is the point: two Recruiters editing each
    other would make the hierarchy meaningless, because everyone at a level would
    hold everyone else's permissions.

    Raised as 403 rather than 404. The actor can see this person on their own
    team screen, so pretending the row does not exist would be a lie they can
    immediately disprove.
    """
    if not role_hierarchy.can_manage(actor_role, target_role):
        raise HTTPException(
            status_code=403,
            detail=(
                "You can only manage team members below you. A "
                f"{role_hierarchy.ROLE_LABELS.get(target_role, target_role.value)} "
                "is not below your own role."
            ),
        )


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


# ── Company Profile (spec §3.2 — the page formerly called Settings) ──────────

async def _company_profile_out(
    session: AsyncSession, tenant_id: uuid.UUID, company: Company | None
) -> CompanyProfileOut:
    tenant = await session.get(Tenant, tenant_id)
    return CompanyProfileOut(
        tenant_id=tenant_id,
        company_name=tenant.name if tenant else "ReadyPick",
        industry=tenant.industry if tenant else None,
        about_company=company.about_company if company else None,
        work_life=company.work_life if company else None,
        # `benefits_text` is the Company Profile field. The retired legacy
        # column remains only as preserved historical data.
        benefits=company.benefits_text if company else None,
    )


@router.get("/me/profile", response_model=CompanyProfileOut)
async def get_company_profile(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyProfileOut:
    """The company-wide About / Work Life / Benefits every new job snapshots.

    Readable by any signed-in org user — these are the sections that appear on
    the public JD, so there is nothing here a colleague should not see. EDITING
    is gated separately (see the PATCH below).

    Never 404s: a tenant that has not authored a profile yet gets empty
    sections rather than an error, so the page opens straight into the form.
    """
    cache_key = f"pickready:tenant:{user.tenant_id}:company_profile"
    cached = await tenant_cache.get_json(cache_key)
    if cached is not None:
        return CompanyProfileOut.model_validate(cached)
    company = await _get_company(session, user)
    output = await _company_profile_out(session, user.tenant_id, company)
    await tenant_cache.set_json(cache_key, output.model_dump(mode="json"), ttl=120)
    return output


@router.post("/me/profile/research", response_model=CompanyProfileResearchOut)
async def research_company_profile(
    user: CurrentUser = Depends(require_capability(caps.EDIT_COMPANY_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyProfileResearchOut:
    """Draft this company's profile from the web, for a human to edit.

    RETURNS A DRAFT AND WRITES NOTHING. The client decision is that the agent
    populates the profile and an EXPLICIT Edit control lets the recruiter change
    it as they need, so applying the draft is the PATCH above, made deliberately,
    by a person who has read it. A route that saved on its own would rewrite the
    sections every candidate reads without anyone approving the words.

    Gated on EDIT_COMPANY_PROFILE rather than on a read capability, because a
    draft is only useful to someone who can act on it, and this spends a web
    search and a model call per call.
    """
    tenant = await session.get(Tenant, user.tenant_id)
    company = await _get_company(session, user)
    name = (tenant.name if tenant else None) or ""
    if not name.strip():
        raise HTTPException(
            status_code=422,
            detail="This account has no company name to research.",
        )
    try:
        draft = await agent_client.research_company(
            session,
            company=name,
            website=(tenant.website_domain if tenant else None),
            industry=(tenant.industry if tenant else None),
        )
    except agent_client.AgentInvokeError as exc:
        # The research agent DEGRADES on its own when it finds nothing usable,
        # returning `degraded=True` with a reason. This is the other case: the
        # agent was never reached, so there is no draft and no reason, and an
        # empty form would read as a finished one.
        raise HTTPException(
            status_code=503,
            detail="The company research agent could not be reached. Try again in a moment.",
        ) from exc
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="company_profile_researched",
        target_type="company",
        target_id=(company.id if company else None),
        metadata={"sources": draft.sources, "degraded": draft.degraded},
    )
    return CompanyProfileResearchOut(
        about_company=draft.about_company,
        work_life=draft.work_life,
        benefits=draft.benefits,
        sources=draft.sources,
        degraded=draft.degraded,
        message=draft.message,
    )


@router.patch("/me/profile", response_model=CompanyProfileOut)
async def update_company_profile(
    body: CompanyProfileIn,
    user: CurrentUser = Depends(require_capability(caps.EDIT_COMPANY_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> CompanyProfileOut:
    """Edit the company-wide profile sections.

    PATCH semantics: a field the caller did not send is left untouched; an
    explicit null clears it. `model_fields_set` is what separates the two — a
    plain `is None` test would silently wipe the other two sections every time
    someone edited one of them.

    Editing here affects FUTURE jobs only. Jobs already created keep the
    snapshot they were seeded with, and any per-job override they carry (spec
    §3.2) — a company rewriting its benefits blurb must not silently rewrite
    the JD of a role candidates are already applying to.
    """
    company = await _get_company(session, user)
    if company is None:
        company = Company(tenant_id=user.tenant_id)
        session.add(company)

    sent = body.model_fields_set
    if "about_company" in sent:
        company.about_company = body.about_company
    if "work_life" in sent:
        company.work_life = body.work_life
    if "benefits" in sent:
        company.benefits_text = body.benefits
    await session.flush()
    await tenant_cache.delete(f"pickready:tenant:{user.tenant_id}:company_profile")
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="company_profile_updated",
        target_type="company",
        target_id=company.id,
        metadata={"sections": sorted(sent)},
    )
    return await _company_profile_out(session, user.tenant_id, company)


#: One definition, in the module that owns the hierarchy, so a label and a
#: rank can never disagree about what a role is called.
ROLE_LABELS: dict[Role, str] = role_hierarchy.ROLE_LABELS


def _staff_out(
    u: User,
    approval_level: str | None = None,
    invite: StaffInvite | None = None,
    *,
    invite_link: str | None = None,
    email_dispatch: str | None = None,
) -> StaffOut:
    return StaffOut(
        id=u.id, email=u.email or "", full_name=u.full_name, phone=u.phone,
        role=u.role.value, status=u.status.value, approval_level=approval_level,
        created_at=u.created_at,
        invite_status=(
            invite_state(
                accepted_at=invite.accepted_at,
                revoked_at=invite.revoked_at,
                expires_at=invite.expires_at,
            )
            if invite is not None
            else None
        ),
        invite_sent_at=invite.created_at if invite is not None else None,
        invite_expires_at=invite.expires_at if invite is not None else None,
        invite_link=invite_link,
        email_dispatch=email_dispatch,
    )


# ── Invitations ──────────────────────────────────────────────────────────────

def _email_dispatch_state() -> str:
    """Honest reporting for the UI: is the SMTP sender actually configured?

    The task is dispatched either way (rule 4/5 — the worker owns the
    delivery taxonomy), but the admin is told up front when nothing can leave
    the building, so the copyable invite link is understood as THE delivery
    mechanism rather than a fallback nobody notices.
    """
    settings = get_settings()
    configured = bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)
    return "queued" if configured else "not_configured"


async def _latest_invites(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[uuid.UUID, StaffInvite]:
    """Newest invite per staff user for this tenant (oldest-first scan means
    the last write wins, i.e. the newest row survives)."""
    rows = (
        await session.execute(
            select(StaffInvite)
            .where(StaffInvite.tenant_id == tenant_id)
            .order_by(StaffInvite.created_at)
        )
    ).scalars().all()
    return {row.user_id: row for row in rows}


async def _ensure_invite_template(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Seed a minimal, tenant-EDITABLE `staff_invite` template on first use.

    # ASSUMPTION: services/email_render.DEFAULT_TEMPLATES has no built-in
    # `staff_invite` entry, so rendering would raise inside the worker and the
    # invite email would never send. PRD §5 forbids *shipping fixed copy*, not
    # having a starting point — this writes a bare v1 row the tenant can edit
    # via PUT /companies/me/email-templates, and does nothing if one exists.
    """
    existing = (
        await session.execute(
            select(EmailTemplate.id).where(
                EmailTemplate.tenant_id == tenant_id,
                EmailTemplate.name == "staff_invite",
            ).limit(1)
        )
    ).first()
    if existing is not None:
        return
    session.add(
        EmailTemplate(
            tenant_id=tenant_id,
            name="staff_invite",
            subject="You've been invited to {{company_name}} on ReadyPick",
            body=(
                "Hi {{full_name}},\n\n"
                "{{invited_by}} has invited you to join {{company_name}} on "
                "ReadyPick as a {{role_label}}.\n\n"
                "Accept your invitation here:\n\n{{invite_link}}\n\n"
                "You'll sign in with Google or with an email and password, "
                "ReadyPick never asks you to set a separate password.\n\n"
                "This link expires on {{expires_on}}.\n\n"
                ", The {{company_name}} team"
            ),
            version=1,
            is_active=True,
        )
    )
    await session.flush()


async def _issue_invite(
    session: AsyncSession,
    actor: CurrentUser,
    staff_user: User,
    company_name: str,
    invited_by_name: str | None,
) -> tuple[StaffInvite, str, str]:
    """Revoke any live invite for this user, mint a fresh single-use token and
    enqueue the invite email. Returns (invite, raw_link, email_dispatch)."""
    now = datetime.now(timezone.utc)
    live = (
        await session.execute(
            select(StaffInvite).where(
                StaffInvite.tenant_id == actor.tenant_id,
                StaffInvite.user_id == staff_user.id,
                StaffInvite.accepted_at.is_(None),
                StaffInvite.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for previous in live:
        previous.revoked_at = now  # at most ONE pending invite per user

    token = generate_invite_token()
    invite = StaffInvite(
        tenant_id=actor.tenant_id,
        user_id=staff_user.id,
        email=staff_user.email or "",
        role=staff_user.role.value,
        token_hash=hash_invite_token(token),
        invited_by=actor.user_id,
        expires_at=invite_expiry(now),
    )
    session.add(invite)
    await session.flush()

    link = build_invite_link(get_settings().frontend_url, token)
    await _ensure_invite_template(session, actor.tenant_id)
    dispatch = _email_dispatch_state()
    # Rule 4: delivery is ALWAYS a dispatched task, never inline in the handler.
    dispatch(
        "pickready.send_email",
        args=[
            str(actor.tenant_id),
            staff_user.email,
            "staff_invite",
            {
                "full_name": staff_user.full_name or staff_user.email or "there",
                "role": staff_user.role.value,
                "role_label": ROLE_LABELS.get(staff_user.role, staff_user.role.value),
                "company_name": company_name,
                "invited_by": invited_by_name or "Your team",
                "invite_link": link,
                "expires_on": invite.expires_at.strftime("%d %b %Y"),
            },
        ],
    )
    return invite, link, dispatch


async def _tenant_name(session: AsyncSession, tenant_id: uuid.UUID | None) -> str:
    if tenant_id is None:
        return "ReadyPick"
    tenant = await session.get(Tenant, tenant_id)
    return tenant.name if tenant is not None else "ReadyPick"


async def _load_staff(
    session: AsyncSession, user: CurrentUser, user_id: uuid.UUID
) -> User:
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
        raise HTTPException(status_code=404, detail="Team member not found")
    return staff_user


@router.get("/me/staff", response_model=list[StaffOut])
async def list_staff(
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[StaffOut]:
    """Staff beneath the caller in the customer hierarchy."""
    visible_roles = role_hierarchy.subordinate_roles(user.role)
    if not visible_roles:
        return []
    rows = (
        await session.execute(
            select(User, HiringManager.approval_level)
            .outerjoin(
                HiringManager,
                (HiringManager.user_id == User.id)
                & (HiringManager.tenant_id == User.tenant_id),
            )
            .where(User.tenant_id == user.tenant_id, User.role.in_(visible_roles))
            .order_by(User.created_at)
        )
    ).all()
    invites = await _latest_invites(session, user.tenant_id)
    return [_staff_out(u, level, invites.get(u.id)) for u, level in rows]


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
    # Spec §29: a person may only create a role BELOW their own.
    ensure_can_manage(user.role, role)

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

    # Duplicate check is email-only (not email+role): in the FLAT model one
    # person holds one staff seat, and a "same email, different role" row is
    # confusing rather than useful. Message is written for a human, not a log.
    dup = (
        await session.execute(
            select(User).where(
                User.tenant_id == user.tenant_id,
                func.lower(User.email) == str(body.email).strip().lower(),
                User.role.in_(STAFF_ROLES),
            )
        )
    ).scalars().first()
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{body.email} is already on your team as a "
                f"{ROLE_LABELS.get(dup.role, dup.role.value)}"
                + (" (deactivated, reactivate them instead)"
                   if dup.status == UserStatus.disabled else "")
            ),
        )

    staff_user = User(
        tenant_id=user.tenant_id, role=role, email=str(body.email),
        phone=body.phone, full_name=body.full_name, status=UserStatus.invited,
        # The reporting line: whoever created this seat. Recorded for display
        # and for a future org chart; who may MANAGE whom is decided by rank in
        # `services/role_hierarchy`, so a missing manager never grants access.
        manager_user_id=user.user_id,
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

    # New staff activate on their first verified Firebase sign-in — ReadyPick
    # never generates a password or an app OTP for them (rule 2).
    actor = await session.get(User, user.user_id)
    invite, link, dispatch = await _issue_invite(
        session, user, staff_user,
        company_name=await _tenant_name(session, user.tenant_id),
        invited_by_name=(actor.full_name or actor.email) if actor else None,
    )
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="staff_invited", target_type="user", target_id=staff_user.id,
                metadata={"invite_id": str(invite.id), "email_dispatch": dispatch})
    return _staff_out(
        staff_user, approval_level, invite, invite_link=link, email_dispatch=dispatch
    )


@router.put("/me/staff/{user_id}", response_model=StaffOut)
async def update_staff(
    user_id: uuid.UUID,
    body: StaffUpdateIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffOut:
    """Edit a staff member without replacing their identity or invite history."""
    staff_user = await _load_staff(session, user, user_id)
    try:
        role = validate_staff_role(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # BOTH ends are checked, and both are necessary. The role they hold NOW
    # decides whether this person may be edited at all; the role they are being
    # moved to decides whether the edit is a promotion past the actor.
    ensure_can_manage(user.role, staff_user.role)
    ensure_can_manage(user.role, role)

    if (
        role == Role.hiring_manager
        and staff_user.role != Role.hiring_manager
        and staff_user.status != UserStatus.disabled
    ):
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

    hm = (
        await session.execute(
            select(HiringManager).where(
                HiringManager.tenant_id == user.tenant_id,
                HiringManager.user_id == staff_user.id,
            )
        )
    ).scalars().first()
    if role == Role.hiring_manager:
        if hm is None:
            hm = HiringManager(
                tenant_id=user.tenant_id,
                user_id=staff_user.id,
                approval_level=body.approval_level,
            )
            session.add(hm)
        else:
            hm.approval_level = body.approval_level
    elif hm is not None:
        await session.delete(hm)

    previous_role = staff_user.role
    staff_user.full_name = body.full_name.strip()
    staff_user.phone = body.phone
    staff_user.role = role
    await session.flush()
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="staff_updated",
        target_type="user",
        target_id=staff_user.id,
        metadata={"previous_role": previous_role.value, "role": role.value},
    )
    invites = await _latest_invites(session, user.tenant_id)
    return _staff_out(
        staff_user,
        body.approval_level if role == Role.hiring_manager else None,
        invites.get(staff_user.id),
    )


@router.post("/me/staff/{user_id}/resend-invite", response_model=StaffOut)
async def resend_staff_invite(
    user_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffOut:
    """Revoke the previous token and mint a fresh one (see INVITE_TTL_DAYS).
    Returns the new link so it can be copied even when SMTP is unconfigured."""
    staff_user = await _load_staff(session, user, user_id)
    ensure_can_manage(user.role, staff_user.role)
    if staff_user.status == UserStatus.disabled:
        raise HTTPException(
            status_code=409,
            detail="This account is deactivated, reactivate it before resending an invite",
        )
    if staff_user.status == UserStatus.active:
        raise HTTPException(
            status_code=409,
            detail="This team member has already signed in, they can use the normal sign-in page",
        )
    if not staff_user.email:
        raise HTTPException(
            status_code=422, detail="This account has no email address to invite"
        )

    actor = await session.get(User, user.user_id)
    invite, link, dispatch = await _issue_invite(
        session, user, staff_user,
        company_name=await _tenant_name(session, user.tenant_id),
        invited_by_name=(actor.full_name or actor.email) if actor else None,
    )
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="staff_invite_resent", target_type="user", target_id=staff_user.id,
                metadata={"invite_id": str(invite.id), "email_dispatch": dispatch})
    return _staff_out(staff_user, None, invite, invite_link=link, email_dispatch=dispatch)


@router.post("/me/staff/{user_id}/reactivate", response_model=StaffOut)
async def reactivate_staff(
    user_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffOut:
    """Undo a deactivation. Someone who already linked a Firebase identity goes
    straight back to `active`; someone who never signed in returns to `invited`
    so the invite flow still applies."""
    staff_user = await _load_staff(session, user, user_id)
    ensure_can_manage(user.role, staff_user.role)
    if staff_user.status != UserStatus.disabled:
        raise HTTPException(status_code=409, detail="This account is already active")

    staff_user.status = (
        UserStatus.active if staff_user.firebase_uid else UserStatus.invited
    )
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="staff_reactivated", target_type="user", target_id=staff_user.id,
                metadata={"role": staff_user.role.value, "status": staff_user.status.value})
    invites = await _latest_invites(session, user.tenant_id)
    return _staff_out(staff_user, None, invites.get(staff_user.id))


@router.delete("/me/staff/{user_id}", response_model=StaffOut)
async def deactivate_staff(
    user_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffOut:
    """Deactivate (status=disabled) — never a hard delete. Only the 3 staff
    roles are targetable; the client account cannot deactivate itself here.
    Any live invitation is revoked so a mailed link cannot be used later."""
    staff_user = await _load_staff(session, user, user_id)
    ensure_can_manage(user.role, staff_user.role)

    staff_user.status = UserStatus.disabled
    now = datetime.now(timezone.utc)
    for live in (
        await session.execute(
            select(StaffInvite).where(
                StaffInvite.tenant_id == user.tenant_id,
                StaffInvite.user_id == staff_user.id,
                StaffInvite.accepted_at.is_(None),
                StaffInvite.revoked_at.is_(None),
            )
        )
    ).scalars().all():
        live.revoked_at = now
    await session.flush()
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="staff_deactivated", target_type="user", target_id=staff_user.id,
                metadata={"role": staff_user.role.value})
    invites = await _latest_invites(session, user.tenant_id)
    return _staff_out(staff_user, None, invites.get(staff_user.id))


# ── Invitation acceptance (/join) ────────────────────────────────────────────
# PUBLIC + tokenized, like the employer-verification form: the tenant is
# unknown until the token resolves, so these run under get_public_db and MUST
# filter by the exact token hash and expose nothing beyond that one row.

async def _resolve_invite(session: AsyncSession, token: str) -> StaffInvite:
    invite = (
        await session.execute(
            select(StaffInvite).where(StaffInvite.token_hash == hash_invite_token(token))
        )
    ).scalars().first()
    if invite is None:
        raise HTTPException(status_code=404, detail="This invitation link is not valid")
    state = invite_state(
        accepted_at=invite.accepted_at,
        revoked_at=invite.revoked_at,
        expires_at=invite.expires_at,
    )
    if state != INVITE_PENDING:
        raise HTTPException(
            status_code=410,
            detail={
                "accepted": "This invitation has already been used, sign in as usual.",
                "revoked": "This invitation was withdrawn. Ask your admin for a new one.",
                "expired": "This invitation has expired. Ask your admin to resend it.",
            }[state],
        )
    return invite


@router.get("/invites/{token}", response_model=PublicInviteOut)
async def read_invite(
    token: str, session: AsyncSession = Depends(get_public_db)
) -> PublicInviteOut:
    """Explain an invitation on /join before any sign-in happens."""
    invite = await _resolve_invite(session, token)
    staff_user = await session.get(User, invite.user_id)
    inviter = (
        await session.get(User, invite.invited_by) if invite.invited_by else None
    )
    return PublicInviteOut(
        email=invite.email,
        full_name=staff_user.full_name if staff_user else None,
        role=invite.role,
        company_name=await _tenant_name(session, invite.tenant_id),
        invited_by_name=(inviter.full_name or inviter.email) if inviter else None,
        expires_at=invite.expires_at,
        status=INVITE_PENDING,
    )


@router.post("/invites/{token}/accept", response_model=InviteAcceptOut)
async def accept_invite(
    token: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> InviteAcceptOut:
    """Burn the token AFTER the invitee has proven their identity via Firebase.

    Firebase + /auth/firebase/session already did the real work (link uid by
    email, flip invited -> active). This only marks the invitation consumed,
    and only by the very user it was issued to — a signed-in member of the
    tenant cannot burn someone else's invite.
    """
    invite = await _resolve_invite(session, token)
    if invite.user_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You're signed in as someone else. Sign out and use the invited email address.",
        )
    invite.accepted_at = datetime.now(timezone.utc)
    await session.flush()
    await audit(session, tenant_id=invite.tenant_id, actor_user_id=user.user_id,
                action="staff_invite_accepted", target_type="user",
                target_id=invite.user_id, metadata={"invite_id": str(invite.id)})
    return InviteAcceptOut(
        accepted=True,
        role=invite.role,
        company_name=await _tenant_name(session, invite.tenant_id),
    )


# ── Per-user permission matrix (spec §7.1) ───────────────────────────────────
# The HR Head (anyone holding MANAGE_STAFF) tunes what each colleague can do.
# Grants are stored as a SPARSE overlay on `users.permissions_json`: a
# capability the HR Head never touched stays absent and keeps tracking its role
# default, so a later change to the role matrix still reaches everyone it
# should. Only deliberately pinned capabilities are frozen per person.

@router.get("/me/staff/{user_id}/permissions", response_model=StaffPermissionsOut)
async def get_staff_permissions(
    user_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffPermissionsOut:
    """What this colleague can do, and where each answer comes from.

    Returns the EFFECTIVE set alongside the role default and the explicit
    overlay, so the checkbox UI can show "on, because their role grants it"
    differently from "on, because you granted it to them".
    """
    staff_user = await _load_staff(session, user, user_id)
    ensure_can_manage(user.role, staff_user.role)
    role_defaults = await rbac.resolve_role_capabilities(
        session, user.tenant_id, staff_user.role
    )
    overrides = rbac.sanitize_overrides(staff_user.permissions_json)
    effective = await rbac.resolve_role_capabilities(
        session, user.tenant_id, staff_user.role, staff_user.id
    )
    # What the CALLER holds, which is exactly what they may grant (spec §29).
    # Returned so the screen does not offer a switch the server will refuse.
    mine = await rbac.resolve_role_capabilities(
        session, user.tenant_id, user.role, user.user_id
    )
    return StaffPermissionsOut(
        user_id=staff_user.id,
        role=staff_user.role.value,
        role_label=role_hierarchy.ROLE_LABELS.get(staff_user.role),
        full_name=staff_user.full_name,
        email=staff_user.email,
        all_capabilities=list(caps.ALL_CAPABILITIES),
        role_defaults=role_defaults,
        overrides=overrides,
        effective=effective,
        grantable=sorted(role_hierarchy.grantable_capabilities(set(mine))),
    )


@router.patch("/me/staff/{user_id}/permissions", response_model=StaffPermissionsOut)
async def update_staff_permissions(
    user_id: uuid.UUID,
    body: StaffPermissionsIn,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_STAFF)),
    session: AsyncSession = Depends(get_tenant_db),
) -> StaffPermissionsOut:
    """Grant or revoke capabilities for one colleague.

    `overrides` REPLACES the stored overlay — the UI sends the full set of
    pins it wants, and omitting a capability returns it to its role default.
    That is the behaviour a checkbox screen needs: unticking "granted" should
    mean "stop pinning this", not "leave the old pin in place".

    Four guards now, and the two new ones are what makes the hierarchy real
    (spec §29):
      * An unknown capability name is dropped, not stored (rbac.sanitize_
        overrides) — a typo must never sit in the database looking like a grant.
      * MANAGE_STAFF cannot be revoked from YOURSELF. A tenant that locks its
        last administrator out of staff management has no in-app way back.
      * STRICTLY BENEATH. A peer or a superior cannot be edited, or the
        hierarchy would mean nothing: everyone at a level would hold everyone
        else's permissions.
      * ONLY WHAT YOU HOLD. Without this the hierarchy is a privilege-escalation
        ladder: a Recruiter grants a Hiring Manager `manage_billing`, then has
        that Hiring Manager grant it back. Restricting a GRANT to the actor's own
        effective set makes the capabilities in a tenant monotonically
        non-increasing as you descend, which is what a hierarchy means.
        REVOKING is deliberately unrestricted: it can only reduce what a
        subordinate can do, and a manager who inherited a team must be able to
        close a permission they were never given the ability to open.
    """
    staff_user = await _load_staff(session, user, user_id)
    ensure_can_manage(user.role, staff_user.role)
    overrides = rbac.sanitize_overrides(body.overrides)

    mine = set(
        await rbac.resolve_role_capabilities(
            session, user.tenant_id, user.role, user.user_id
        )
    )
    escalating = sorted(
        capability
        for capability, allowed in overrides.items()
        if allowed and capability not in mine
    )
    if escalating:
        raise HTTPException(
            status_code=403,
            detail=(
                "You can only grant permissions you hold yourself. These are "
                "not yours to give: " + ", ".join(escalating)
            ),
        )

    if staff_user.id == user.user_id and overrides.get(caps.MANAGE_STAFF) is False:
        raise HTTPException(
            status_code=409,
            detail=(
                "You can't remove your own staff-management access, ask another "
                "administrator to do it."
            ),
        )

    previous = rbac.sanitize_overrides(staff_user.permissions_json)
    # Store None rather than {} when nothing is pinned: "no overlay" and "an
    # empty overlay" mean the same thing, and NULL says it unambiguously.
    staff_user.permissions_json = overrides or None
    await session.flush()

    changed = sorted(
        key
        for key in set(previous) | set(overrides)
        if previous.get(key) != overrides.get(key)
    )
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="staff_permissions_updated",
        target_type="user",
        target_id=staff_user.id,
        metadata={
            "role": staff_user.role.value,
            "changed": changed,
            "granted": sorted(k for k, v in overrides.items() if v),
            "revoked": sorted(k for k, v in overrides.items() if not v),
        },
    )
    return await get_staff_permissions(staff_user.id, user=user, session=session)


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
        raise HTTPException(status_code=409, detail="Complete the company profile first")
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


# â”€â”€ Compliance & legal documents (Customer Portal side) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# The WRITE half of the Provider Portal's compliance section (spec Â§3.3): the
# customer's HR Head files their own tax and commercial records here, and the
# ReadyPick owner reads them through api/provider.py. The split is deliberate
# and complete â€” the Provider router has no upload route, this router has no
# cross-tenant read, and RLS confines every statement below to the caller's own
# tenant regardless.
#
# Gated on MANAGE_COMPLIANCE_DOCUMENTS, granted to the Company Admin alone by
# default. This is the one place the flat staff model is not flattened: a
# recruiter has no business filing the signed agreement. It stays a capability
# rather than a role check (claude.md rule 3), so an HR Head can delegate it to
# one named person through the per-user overlay.


async def _compliance_slots(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[ComplianceDocumentSlot]:
    """All seven slots, present or not â€” the same shape the Provider sees, so
    the HR Head is looking at exactly the list the owner is looking at."""
    documents = (
        await session.execute(
            select(ComplianceDocument).where(ComplianceDocument.tenant_id == tenant_id)
        )
    ).scalars().all()
    uploader_ids = [d.uploaded_by for d in documents if d.uploaded_by is not None]
    names: dict[uuid.UUID, str | None] = {}
    if uploader_ids:
        rows = (
            await session.execute(
                select(User.id, User.full_name, User.email).where(
                    User.id.in_(uploader_ids)
                )
            )
        ).all()
        names = {row.id: (row.full_name or row.email) for row in rows}
    by_type = {
        document.document_type: ComplianceDocumentOut(
            id=document.id,
            document_type=document.document_type,
            label=DOCUMENT_LABELS[document.document_type],
            group=DOCUMENT_GROUPS[document.document_type],
            file_name=document.file_name,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            uploaded_at=document.uploaded_at,
            uploaded_by_name=(
                names.get(document.uploaded_by) if document.uploaded_by else None
            ),
        )
        for document in documents
    }
    return document_slots(by_type)


async def _own_document(
    session: AsyncSession, tenant_id: uuid.UUID, document_type: str
) -> ComplianceDocument | None:
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(
            status_code=422, detail=f"Unknown document type: {document_type}"
        )
    return (
        await session.execute(
            select(ComplianceDocument).where(
                ComplianceDocument.tenant_id == tenant_id,
                ComplianceDocument.document_type == document_type,
            )
        )
    ).scalars().first()


def _own_tenant(user: CurrentUser) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=404, detail="No company for this account")
    return user.tenant_id


@router.get("/me/compliance-documents", response_model=list[ComplianceDocumentSlot])
async def list_compliance_documents(
    user: CurrentUser = Depends(require_capability(caps.MANAGE_COMPLIANCE_DOCUMENTS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[ComplianceDocumentSlot]:
    """The customer's own seven compliance slots, in fixed spec order."""
    return await _compliance_slots(session, _own_tenant(user))


@router.post(
    "/me/compliance-documents",
    response_model=list[ComplianceDocumentSlot],
    status_code=status.HTTP_201_CREATED,
)
async def upload_compliance_document(
    document_type: str = Form(..., description="One of the seven compliance types."),
    file: UploadFile = File(..., description=document_storage.UPLOAD_LIMITS_HINT),
    user: CurrentUser = Depends(require_capability(caps.MANAGE_COMPLIANCE_DOCUMENTS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[ComplianceDocumentSlot]:
    """File (or REPLACE) one compliance document.

    One row per (customer, type) â€” uploading a second GSTIN certificate
    replaces the first rather than adding a row nobody can disambiguate. The
    existing row is updated in place so its id, and any link the Provider
    already holds, keeps resolving to the current document.

    The whole slot list comes back rather than the single row: the page renders
    the seven slots together, and returning one row would make it reconcile two
    response shapes for the same view.
    """
    tenant_id = _own_tenant(user)
    existing = await _own_document(session, tenant_id, document_type)

    # Store FIRST, write the row second. A failed upload must leave behind no
    # row claiming a document exists; store_document is content-addressed and
    # idempotent, so a retry after a lost response cannot orphan an asset.
    stored = await document_storage.store_document(file)

    row = existing or ComplianceDocument(
        tenant_id=tenant_id, document_type=document_type
    )
    row.file_url = stored.secure_url
    row.file_public_id = stored.public_id
    row.file_name = stored.original_filename
    row.mime_type = stored.mime_type
    row.size_bytes = stored.size_bytes
    row.uploaded_by = user.user_id
    row.uploaded_at = datetime.now(timezone.utc)
    if existing is None:
        session.add(row)
    await session.flush()

    await audit(
        session, tenant_id=tenant_id, actor_user_id=user.user_id,
        action="compliance_document_uploaded", target_type="compliance_document",
        target_id=row.id,
        metadata={"document_type": document_type, "replaced": existing is not None},
    )
    return await _compliance_slots(session, tenant_id)


@router.delete(
    "/me/compliance-documents/{document_type}",
    response_model=list[ComplianceDocumentSlot],
)
async def remove_compliance_document(
    document_type: str,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_COMPLIANCE_DOCUMENTS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[ComplianceDocumentSlot]:
    """Withdraw a document that was filed in error.

    The stored asset is deliberately NOT deleted. It is content-addressed, so
    an identical file filed by another customer resolves to the same object;
    deleting the bytes here would empty their record too. The slot simply
    returns to "Not Available Yet".
    """
    tenant_id = _own_tenant(user)
    row = await _own_document(session, tenant_id, document_type)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    document_id = row.id
    await session.delete(row)
    await session.flush()
    await audit(
        session, tenant_id=tenant_id, actor_user_id=user.user_id,
        action="compliance_document_removed", target_type="compliance_document",
        target_id=document_id, metadata={"document_type": document_type},
    )
    return await _compliance_slots(session, tenant_id)


@router.get("/me/compliance-documents/{document_type}/download")
async def download_own_compliance_document(
    document_type: str,
    inline: bool = False,
    user: CurrentUser = Depends(require_capability(caps.MANAGE_COMPLIANCE_DOCUMENTS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> Response:
    """View or download one of the customer's own filed documents.

    Addressed by TYPE rather than id, because the HR Head's page is a list of
    seven slots and the slot is what they click. The Provider's equivalent is
    addressed by id, since it navigates from a specific stored row.
    """
    tenant_id = _own_tenant(user)
    row = await _own_document(session, tenant_id, document_type)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    content = await document_storage.fetch_document_bytes(row.file_public_id or "")
    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type=row.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{row.file_name.replace(chr(34), "")}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
