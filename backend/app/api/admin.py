"""Owner console (contract rev 2, formerly "Super Admin"). Every request runs
through `get_superadmin_db`, which enforces the super_admin audience, uses the
RLS bypass scope, and writes an audit_log row for the cross-tenant access.

The Owner onboards tenants, manages them (edit / delete), invites staff INTO a
chosen tenant, edits permission templates, and reads the audit log. The
company-side staff UI (`/companies/me/staff`) and the `/join` acceptance page
remain the client organization's own flow — this module reuses the same
`staff_invites` row and token helpers rather than inventing a parallel one.

Auth is Firebase (claude.md rule 2): no OTP is ever generated here. Email is
SMTP via the `pickready.send_email` Celery task (rules 4 and 5).

One endpoint pair here is deliberately ORG-scoped rather than Owner-scoped —
`GET/PUT /admin/my-tenant`. It exposes the caller's own company profile
(the Tenant row this module owns) to the org portal. It lives in this router
because the Tenant profile is this module's model; the `/admin` path prefix is
an artifact of router mounting, not an authorization statement — the
dependency (`get_tenant_db` + `require_capability`) is.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_superadmin_db,
    get_tenant_db,
    require_capability,
)
from app.core.config import get_settings
from app.models.candidate import Candidate, Profile
from app.models.enums import Role, UserStatus
from app.models.invite import (
    StaffInvite,
    build_invite_link,
    generate_invite_token,
    hash_invite_token,
    invite_expiry,
    invite_state,
)
from app.models.job import Job
from app.models.tenant import AuditLog, RolePermission, Tenant
from app.models.user import User
from app.schemas.admin import (
    AdminUserOut,
    AuditLogOut,
    PermissionOut,
    PermissionsUpdateIn,
    BDUserCreateIn,
    BDUserOut,
    BDUserUpdateIn,
    OwnerStaffOut,
    StaffInviteIn,
    StaffInviteOut,
    TenantCreateIn,
    TenantCreateOut,
    TenantDeleteOut,
    TenantOut,
    TenantProfileIn,
    TenantProfileOut,
    TenantUpdateIn,
    derive_tenant_domain,
)
from app.services import capabilities as caps
from app.services import rbac
from app.services.audit import audit
from app.services.capabilities import DEFAULT_PERMISSION_MATRIX
from app.services.owner import OwnerRoleViolation, ensure_owner_invariant
from app.workers.celery_app import celery_app

router = APIRouter()

# Roles the Owner may invite into a tenant. Mirrors companies.STAFF_ROLES; the
# schema Literal is the first gate and the owner invariant the last.
STAFF_ROLES: frozenset[Role] = frozenset(
    {Role.hr_manager, Role.recruiter, Role.hiring_manager}
)

# FR-2.2 — at most 5 ACTIVE Hiring Managers per tenant. Enforced identically on
# the client-side path (api/companies.py) and by a DB trigger; repeated here so
# the Owner console cannot be used to route around the cap.
MAX_HIRING_MANAGERS = 5


def _seed_permissions(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Copy the default template (PRD §6) into tenant rows so the Owner can
    tune this tenant independently later."""
    for role, caps_map in DEFAULT_PERMISSION_MATRIX.items():
        for capability, allowed in caps_map.items():
            session.add(RolePermission(
                tenant_id=tenant_id, role=role, capability=capability, allowed=allowed
            ))


# ── Pure helpers (DB-free, unit-testable) ────────────────────────────────────

def next_free_domain(preferred: str, taken: set[str]) -> str:
    """`tenants.domain` is UNIQUE but is no longer user-supplied, so a
    collision (two companies whose owners share a mail host) must not surface
    as a 409 the Owner cannot resolve. Suffix until free."""
    if preferred not in taken:
        return preferred
    stem, _, tld = preferred.partition(".")
    for n in range(2, 1000):
        nth = f"{stem}-{n}.{tld}" if tld else f"{preferred}-{n}"
        if nth not in taken:
            return nth
    raise HTTPException(status_code=409, detail="Could not allocate a tenant key")


def confirmation_matches(typed: str | None, tenant_name: str) -> bool:
    """Destructive-delete guard: the caller must retype the company name.
    Case- and whitespace-insensitive so the check is about intent, not typing
    precision. A literal "DELETE" is also accepted."""
    value = (typed or "").strip().casefold()
    if not value:
        return False
    return value in {tenant_name.strip().casefold(), "delete"}


# ── Serialization ────────────────────────────────────────────────────────────

async def _client_users(
    session: AsyncSession, tenant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, User]:
    """The `client` (Client Company Admin) user per tenant — the owner/POC
    shown in the tenant list. Oldest wins if a tenant somehow has two."""
    if not tenant_ids:
        return {}
    rows = (
        await session.execute(
            select(User)
            .where(User.tenant_id.in_(tenant_ids), User.role == Role.client)
            .order_by(User.created_at)
        )
    ).scalars().all()
    out: dict[uuid.UUID, User] = {}
    for row in rows:
        if row.tenant_id is not None:
            out.setdefault(row.tenant_id, row)
    return out


async def _staff_counts(
    session: AsyncSession, tenant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not tenant_ids:
        return {}
    rows = (
        await session.execute(
            select(User.tenant_id, func.count())
            .where(User.tenant_id.in_(tenant_ids), User.role.in_(STAFF_ROLES),
                   User.status != UserStatus.disabled)
            .group_by(User.tenant_id)
        )
    ).all()
    return {tid: n for tid, n in rows if tid is not None}


def _tenant_out(tenant: Tenant, client: User | None, staff_count: int = 0) -> TenantOut:
    return TenantOut(
        id=tenant.id,
        name=tenant.name,
        domain=tenant.domain,
        spf_dkim_status=tenant.spf_dkim_status,
        created_at=tenant.created_at,
        industry=tenant.industry,
        culture=tenant.culture,
        details=tenant.details,
        client_email=client.email if client else None,
        client_name=client.full_name if client else None,
        client_phone=client.phone if client else None,
        client_status=client.status.value if client else None,
        staff_count=staff_count,
    )


# ── Tenants ──────────────────────────────────────────────────────────────────

@router.post("/tenants", response_model=TenantCreateOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> TenantCreateOut:
    """Onboard a client company and its Client Company Admin account.

    The admin signs in with email/password or Google via Firebase — PickReady
    stores no password and issues no OTP (claude.md rule 2). No sending domain
    is collected: outbound mail is SMTP (rule 5).
    """
    # Defensive owner invariant: no user-creating path may ever mint a
    # super_admin identity other than settings.owner_email (contract rev 2).
    try:
        ensure_owner_invariant(Role.client, str(body.client_email))
    except OwnerRoleViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    name = body.name.strip()
    duplicate_name = (
        await session.execute(
            select(Tenant).where(func.lower(Tenant.name) == name.lower())
        )
    ).scalars().first()
    if duplicate_name is not None:
        raise HTTPException(
            status_code=409, detail=f"A company named “{name}” already exists"
        )

    preferred = (body.domain or derive_tenant_domain(name, str(body.client_email))).lower()
    taken = set(
        (await session.execute(select(Tenant.domain))).scalars().all()
    )
    domain = next_free_domain(preferred, taken)

    tenant = Tenant(
        name=name,
        domain=domain,
        industry=body.industry,
        culture=body.culture,
        details=body.details,
    )
    session.add(tenant)
    await session.flush()

    client_user = User(
        tenant_id=tenant.id,
        role=Role.client,
        email=str(body.client_email),
        phone=body.client_phone,
        full_name=name,
        status=UserStatus.invited,
    )
    session.add(client_user)
    _seed_permissions(session, tenant.id)
    await session.flush()

    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="tenant_created", target_type="tenant", target_id=tenant.id,
        metadata={"name": name, "domain": domain, "industry": body.industry},
    )
    # Firebase owns credentials: the invite is an onboarding pointer, never a
    # code. Sending is a Celery task (rule 4) over SMTP (rule 5).
    #
    # The email used to carry only the tenant's name and NO acceptance link,
    # because no StaffInvite row was ever minted for the client owner — so the
    # person the workspace was created for had no way into it. Mint the same
    # single-use invite the staff path mints (see create_staff_invite below).
    client_token = generate_invite_token()
    session.add(
        StaffInvite(
            tenant_id=tenant.id,
            user_id=client_user.id,
            email=str(body.client_email),
            role=Role.client.value,
            token_hash=hash_invite_token(client_token),
            invited_by=user.user_id,
            expires_at=invite_expiry(),
        )
    )
    await session.flush()
    celery_app.send_task(
        "pickready.send_email",
        args=[str(tenant.id), str(body.client_email), "client_invite",
              {"tenant_name": name,
               "invite_link": build_invite_link(
                   get_settings().frontend_url, client_token
               )}],
    )
    return TenantCreateOut(
        tenant=_tenant_out(tenant, client_user),
        client_user=AdminUserOut.model_validate(client_user),
    )


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[TenantOut]:
    """One page of tenants with their company profile and owner/POC.

    Paginated in SQL. "The tenant count is small by design" was true of a demo
    and stops being true the week the platform sells; more importantly, the two
    helper lookups below fan out over whatever this returns, so an unbounded
    page here is an unbounded amount of work behind it.
    """
    rows = (
        await session.execute(
            select(Tenant).order_by(Tenant.created_at, Tenant.id).offset(skip).limit(limit)
        )
    ).scalars().all()
    ids = [t.id for t in rows]
    clients = await _client_users(session, ids)
    counts = await _staff_counts(session, ids)
    return [_tenant_out(t, clients.get(t.id), counts.get(t.id, 0)) for t in rows]


async def _load_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: uuid.UUID,
    session: AsyncSession = Depends(get_superadmin_db),
) -> TenantOut:
    tenant = await _load_tenant(session, tenant_id)
    clients = await _client_users(session, [tenant.id])
    counts = await _staff_counts(session, [tenant.id])
    return _tenant_out(tenant, clients.get(tenant.id), counts.get(tenant.id, 0))


async def _apply_tenant_update(
    session: AsyncSession,
    tenant: Tenant,
    changes: dict[str, str | None],
    actor_user_id: uuid.UUID,
) -> list[str]:
    """Apply a partial company-profile update in place and audit it.

    `changes` holds ONLY the keys the caller actually sent (pydantic
    `exclude_unset`), so an absent key means "leave unchanged" while an explicit
    "" clears the field. Returns the names of the fields that really changed.
    """
    changed: list[str] = []

    name = changes.get("name")
    if "name" in changes and name and name != tenant.name:
        clash = (
            await session.execute(
                select(Tenant).where(
                    func.lower(Tenant.name) == name.lower(), Tenant.id != tenant.id
                )
            )
        ).scalars().first()
        if clash is not None:
            raise HTTPException(
                status_code=409, detail=f"A company named “{name}” already exists"
            )
        tenant.name = name
        changed.append("name")

    if "industry" in changes and changes["industry"] != tenant.industry:
        tenant.industry = changes["industry"]
        changed.append("industry")

    for field in ("culture", "details"):
        if field not in changes:
            continue
        value = changes[field] or None
        if value != getattr(tenant, field):
            setattr(tenant, field, value)
            changed.append(field)

    await session.flush()
    if changed:
        await audit(
            session, tenant_id=tenant.id, actor_user_id=actor_user_id,
            action="tenant_updated", target_type="tenant", target_id=tenant.id,
            metadata={"fields": sorted(changed)},
        )
    return changed


@router.put("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> TenantOut:
    """Edit the company profile. `domain` and `created_at` are immutable; the
    client account is managed from the client portal."""
    return await _update_tenant_by_id(session, tenant_id, body, user.user_id)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def patch_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> TenantOut:
    """Alias of PUT — both are partial updates over the same schema."""
    return await _update_tenant_by_id(session, tenant_id, body, user.user_id)


async def _update_tenant_by_id(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    body: TenantUpdateIn,
    actor_user_id: uuid.UUID,
) -> TenantOut:
    tenant = await _load_tenant(session, tenant_id)
    await _apply_tenant_update(
        session, tenant, body.model_dump(exclude_unset=True), actor_user_id
    )
    clients = await _client_users(session, [tenant.id])
    counts = await _staff_counts(session, [tenant.id])
    return _tenant_out(tenant, clients.get(tenant.id), counts.get(tenant.id, 0))


@router.delete("/tenants/{tenant_id}", response_model=TenantDeleteOut)
async def delete_tenant(
    tenant_id: uuid.UUID,
    confirm: str = Query(
        ...,
        description="Retype the company name (or 'DELETE') to confirm this "
                    "irreversible action.",
    ),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> TenantDeleteOut:
    """Permanently remove a tenant and everything scoped to it.

    Runs inside the request transaction (`get_superadmin_db` opens
    `session.begin()`), so a failure anywhere rolls the whole thing back.

    Rows with a FK to `tenants` cascade at the database level: users,
    staff_invites, companies, hiring_managers, email_templates, jobs,
    job_approvals, job_candidate_links, verification_requests, interviews,
    pipeline_status, role_permissions.

    Two tables carry a tenant reference WITHOUT a FK, on purpose:
      * `candidates.tenant_id` / `profiles.source_tenant_id` — Databank rows
        may be shared across tenants, so they are RELEASED (set NULL) rather
        than deleted. Deleting them would destroy other tenants' matches.
      * `audit_log.tenant_id` — append-only; history survives the tenant.
    """
    tenant = await _load_tenant(session, tenant_id)
    if not confirmation_matches(confirm, tenant.name):
        raise HTTPException(
            status_code=400,
            detail="Confirmation does not match the company name",
        )

    removed: dict[str, int] = {}
    for label, model in (("users", User), ("jobs", Job)):
        removed[label] = (
            await session.execute(
                select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
            )
        ).scalar_one()

    released_candidates = await session.execute(
        update(Candidate).where(Candidate.tenant_id == tenant_id).values(tenant_id=None)
    )
    removed["candidates_released"] = released_candidates.rowcount or 0
    released_profiles = await session.execute(
        update(Profile)
        .where(Profile.source_tenant_id == tenant_id)
        .values(source_tenant_id=None)
    )
    removed["profiles_released"] = released_profiles.rowcount or 0

    name = tenant.name
    # Audit BEFORE the delete so the row is written while the tenant still
    # exists; audit_log has no FK to tenants, so the trail survives.
    await audit(
        session, tenant_id=tenant_id, actor_user_id=user.user_id,
        action="tenant_deleted", target_type="tenant", target_id=tenant_id,
        metadata={"name": name, "domain": tenant.domain, "removed": removed},
    )
    await session.delete(tenant)
    await session.flush()
    return TenantDeleteOut(id=tenant_id, name=name, removed=removed)


# ── Owner-side team management (invite staff INTO a tenant) ──────────────────

@router.get("/staff", response_model=list[OwnerStaffOut])
async def list_all_staff(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[OwnerStaffOut]:
    """One page of the operational team across every tenant.

    This list grows as (customers x their staff), so it is the fastest-growing
    response in the Owner console and the one least able to afford being
    unbounded.
    """
    rows = (
        await session.execute(
            select(User, Tenant)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.role.in_(STAFF_ROLES))
            .order_by(Tenant.name, User.created_at, User.email, User.id)
            .offset(skip)
            .limit(limit)
        )
    ).all()
    user_ids = [staff.id for staff, _tenant in rows]
    invite_by_user: dict[uuid.UUID, StaffInvite] = {}
    if user_ids:
        invites = (
            await session.execute(
                select(StaffInvite)
                .where(StaffInvite.user_id.in_(user_ids))
                .order_by(StaffInvite.created_at)
            )
        ).scalars().all()
        for invite in invites:
            invite_by_user[invite.user_id] = invite

    out: list[OwnerStaffOut] = []
    for staff, tenant in rows:
        invite = invite_by_user.get(staff.id)
        out.append(
            OwnerStaffOut(
                id=staff.id,
                tenant_id=tenant.id,
                tenant_name=tenant.name,
                email=staff.email or "",
                full_name=staff.full_name,
                role=staff.role.value,
                status=staff.status.value,
                invite_status=(
                    invite_state(
                        accepted_at=invite.accepted_at,
                        revoked_at=invite.revoked_at,
                        expires_at=invite.expires_at,
                    )
                    if invite
                    else None
                ),
                invite_sent_at=invite.created_at if invite else None,
                invite_expires_at=invite.expires_at if invite else None,
            )
        )
    return out


@router.post(
    "/staff-invites", response_model=StaffInviteOut, status_code=status.HTTP_201_CREATED
)
async def invite_staff(
    body: StaffInviteIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> StaffInviteOut:
    """Create a staff account in the chosen tenant and issue an invite.

    Reuses the client-side invite primitives (`app.models.invite`) and the
    `/join` acceptance page, so an Owner-issued invite and a Client-issued one
    are the same object. The invite is NOT a credential — the invitee proves
    ownership of the email through Firebase, then `/auth/firebase/session`
    links the uid and flips `invited -> active`.
    """
    tenant = await _load_tenant(session, body.tenant_id)
    role = Role(body.role)
    if role not in STAFF_ROLES:  # unreachable via the schema Literal; belt and braces
        raise HTTPException(status_code=400, detail="Not an invitable staff role")
    try:
        ensure_owner_invariant(role, str(body.email))
    except OwnerRoleViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if role == Role.hiring_manager:
        active_hms = (
            await session.execute(
                select(func.count()).select_from(User).where(
                    User.tenant_id == tenant.id,
                    User.role == Role.hiring_manager,
                    User.status != UserStatus.disabled,
                )
            )
        ).scalar_one()
        if active_hms >= MAX_HIRING_MANAGERS:
            raise HTTPException(
                status_code=409,
                detail=f"At most {MAX_HIRING_MANAGERS} Hiring Manager accounts "
                       f"per company (FR-2.2)",
            )

    email = str(body.email)
    existing = (
        await session.execute(
            select(User).where(
                User.tenant_id == tenant.id, User.email == email, User.role == role
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{email} already holds this role at {tenant.name}",
        )

    staff_user = User(
        tenant_id=tenant.id, role=role, email=email, phone=body.phone,
        full_name=body.full_name, status=UserStatus.invited,
    )
    session.add(staff_user)
    await session.flush()

    # At most ONE pending invite per staff user (see StaffInvite docstring).
    token = generate_invite_token()
    invite = StaffInvite(
        tenant_id=tenant.id,
        user_id=staff_user.id,
        email=email,
        role=role.value,
        token_hash=hash_invite_token(token),
        invited_by=user.user_id,
        expires_at=invite_expiry(),
    )
    session.add(invite)
    await session.flush()

    link = build_invite_link(get_settings().frontend_url, token)
    await audit(
        session, tenant_id=tenant.id, actor_user_id=user.user_id,
        action="staff_invited", target_type="user", target_id=staff_user.id,
        # The raw token is never persisted or logged (ESD §16).
        metadata={"role": role.value, "email": email, "via": "owner_console"},
    )
    # The worker renders named tenant templates. Ensure the editable staff
    # invite template exists before the Owner enqueues the same flow used by
    # the company portal.
    from app.api.companies import ROLE_LABELS, _ensure_invite_template

    await _ensure_invite_template(session, tenant.id)
    # CurrentUser carries only the id, role and audience from the token, never
    # a name, so the inviter has to be read from the row.
    inviter = await session.get(User, user.user_id)
    inviter_label = (
        (inviter.full_name or inviter.email) if inviter is not None else None
    ) or "The PickReady team"
    # The context keys must match the PLACEHOLDERS in that template, which is
    # the same one the company portal uses. This path was passing `tenant_name`
    # and `role`, neither of which the template names, so every Owner-console
    # invitation rendered its blanks as empty strings: "You have been invited
    # to  on PickReady", " has invited you to join  as a ", "This link expires
    # on ." An unknown placeholder resolves to '' rather than raising
    # (email_render.substitute), so the email sent and looked delivered.
    celery_app.send_task(
        "pickready.send_email",
        args=[str(tenant.id), email, "staff_invite",
              {"full_name": body.full_name or email,
               "role": role.value,
               "role_label": ROLE_LABELS.get(role, role.value),
               "company_name": tenant.name,
               "tenant_name": tenant.name,
               "invited_by": inviter_label,
               "invite_link": link,
               "expires_on": invite.expires_at.strftime("%d %b %Y")}],
    )
    return StaffInviteOut(
        user=AdminUserOut.model_validate(staff_user),
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        role=body.role,
        invite_link=link,
        expires_at=invite.expires_at,
        email_queued=True,
    )


# ── Business Development team (platform staff, tenant_id NULL) ───────────────
#
# WHY THIS IS NOT THE STAFF-INVITE FLOW ABOVE. Every invite path in the product
# is tenant-scoped: `staff_invites` has a NOT NULL tenant, the invite email is
# rendered from a tenant's template, and `/join` accepts into a tenant. A BD
# user has no tenant by design, so reusing that machinery would mean inventing
# a fake one. The identity model is nonetheless IDENTICAL to the invite flow:
# the row is reserved here with status `invited` and no firebase_uid, and the
# first proven Firebase sign-in on this email binds the uid and flips the row
# to active (api/auth._finalize_single). No password is ever created or stored.
#
# Gating is `get_superadmin_db`, the same dependency as every other route in
# this module: it demands the owner audience AND the super_admin role, and
# super_admin short-circuits capability resolution, so no new capability is
# introduced. What a BD user may DO once signed in is decided by the three
# `bd` capabilities seeded in migration 0023, never by a role branch.

def _bd_user_out(user: User) -> BDUserOut:
    return BDUserOut(
        id=user.id,
        email=user.email or "",
        full_name=user.full_name,
        phone=user.phone,
        status=user.status.value,
        created_at=user.created_at,
        signed_in=bool(user.firebase_uid),
    )


async def _load_bd_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    row = (
        await session.execute(
            select(User).where(User.id == user_id, User.role == Role.bd)
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Business Development user not found")
    return row


@router.get("/bd-users", response_model=list[BDUserOut])
async def list_bd_users(
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[BDUserOut]:
    """Every Business Development account, newest last."""
    rows = (
        await session.execute(
            select(User)
            .where(User.role == Role.bd)
            .order_by(User.created_at, User.email)
        )
    ).scalars().all()
    return [_bd_user_out(row) for row in rows]


@router.post(
    "/bd-users", response_model=BDUserOut, status_code=status.HTTP_201_CREATED
)
async def create_bd_user(
    body: BDUserCreateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> BDUserOut:
    """Reserve a Business Development account for an email address.

    The account is usable as soon as its owner signs in at the normal login
    page with this email, through Google or email/password. PickReady never
    holds the credential.
    """
    email = str(body.email)
    # `uq_users_tenant_email_role` cannot catch this: NULLs do not collide under
    # a Postgres UNIQUE constraint, so two BD rows for one address would both
    # insert happily and the login lookup would then return an ambiguous pair.
    existing = (
        await session.execute(
            select(User).where(
                User.tenant_id.is_(None),
                User.role == Role.bd,
                func.lower(User.email) == email.strip().lower(),
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{email} already holds a Business Development account",
        )
    try:
        ensure_owner_invariant(Role.bd, email)
    except OwnerRoleViolation as exc:  # pragma: no cover - bd is never super_admin
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    bd_user = User(
        tenant_id=None, role=Role.bd, email=email, phone=body.phone,
        full_name=body.full_name, status=UserStatus.invited,
    )
    session.add(bd_user)
    await session.flush()
    await audit(
        session, tenant_id=None, actor_user_id=user.user_id,
        action="bd_user_created", target_type="user", target_id=bd_user.id,
        metadata={"email": email, "full_name": body.full_name},
    )
    return _bd_user_out(bd_user)


@router.patch("/bd-users/{bd_user_id}", response_model=BDUserOut)
async def update_bd_user(
    bd_user_id: uuid.UUID,
    body: BDUserUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> BDUserOut:
    """Edit a BD user's details, or disable / re-enable the account.

    Disabling is a REVERSIBLE hide: it sets `status` and nothing else, so a BD
    user who owns leads keeps owning them and can be switched back on. There is
    no delete here, exactly as there is none on the customer list.
    """
    bd_user = await _load_bd_user(session, bd_user_id)
    changes = body.model_dump(exclude_unset=True)
    if "full_name" in changes:
        bd_user.full_name = changes["full_name"]
    if "phone" in changes:
        bd_user.phone = changes["phone"]
    if "status" in changes and changes["status"] is not None:
        if changes["status"] == "disabled":
            bd_user.status = UserStatus.disabled
        else:
            # Re-enable to the truthful state: an account that has never been
            # bound to a Firebase identity is still only invited.
            bd_user.status = (
                UserStatus.active if bd_user.firebase_uid else UserStatus.invited
            )
    await session.flush()
    await audit(
        session, tenant_id=None, actor_user_id=user.user_id,
        action="bd_user_updated", target_type="user", target_id=bd_user.id,
        metadata={"changed": sorted(changes), "status": bd_user.status.value},
    )
    return _bd_user_out(bd_user)


# ── Org-portal: the caller's own company profile ─────────────────────────────

def _profile_out(tenant: Tenant, client: User | None, editable: bool) -> TenantProfileOut:
    return TenantProfileOut(
        id=tenant.id,
        name=tenant.name,
        industry=tenant.industry,
        culture=tenant.culture,
        details=tenant.details,
        created_at=tenant.created_at,
        client_email=client.email if client else None,
        client_name=client.full_name if client else None,
        client_phone=client.phone if client else None,
        editable=editable,
    )


@router.get("/my-tenant", response_model=TenantProfileOut)
async def get_my_tenant(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> TenantProfileOut:
    """ORG-scoped. The signed-in user's own company profile — name, industry,
    culture, details and the owner/POC — for the Company page."""
    if user.tenant_id is None:
        raise HTTPException(status_code=404, detail="No company for this account")
    tenant = await _load_tenant(session, user.tenant_id)
    clients = await _client_users(session, [tenant.id])
    editable = await rbac.has_capability(
        session, user.tenant_id, user.role, caps.CREATE_COMPANY_PAGE
    )
    return _profile_out(tenant, clients.get(tenant.id), editable)


@router.put("/my-tenant", response_model=TenantProfileOut)
async def update_my_tenant(
    body: TenantProfileIn,
    user: CurrentUser = Depends(require_capability(caps.CREATE_COMPANY_PAGE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TenantProfileOut:
    """ORG-scoped. Capability-gated (never `if role == ...`, claude.md rule 3)
    so the permission matrix stays the authority on who may edit."""
    if user.tenant_id is None:
        raise HTTPException(status_code=404, detail="No company for this account")
    tenant = await _load_tenant(session, user.tenant_id)
    await _apply_tenant_update(
        session, tenant, body.model_dump(exclude_unset=True), user.user_id
    )
    clients = await _client_users(session, [tenant.id])
    return _profile_out(tenant, clients.get(tenant.id), True)


# ── Permissions & audit log ──────────────────────────────────────────────────

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
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[AuditLogOut]:
    # `id` in the ORDER BY makes it total: audit rows written in the same
    # millisecond would otherwise be free to swap between pages.
    stmt = (
        select(AuditLog)
        .order_by(AuditLog.at.desc(), AuditLog.id)
        .offset(skip)
        .limit(limit)
    )
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]
