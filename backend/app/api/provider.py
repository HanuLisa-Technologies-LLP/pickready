"""Provider Portal — the ReadyPick owner's console over its CUSTOMERS.

    Provider Portal   this module: the platform owner's dashboard
    Customer Portal   a client company's own dashboard (api/companies.py)
    Candidate Portal  the public candidate surface (api/portal.py)

A customer is one onboarded client company — a `tenants` row (see migration
0020 for why compliance and lifecycle hang off `tenants` rather than
`companies`). The existing `/admin` router keeps onboarding, hard delete,
permissions and the audit trail; this router is the customer-MANAGEMENT view:
list with analytics, detail, edit, archive, and read-only compliance records.

READ-ONLY IS ENFORCED BY ABSENCE. The Provider may edit exactly three fields
(industry, website, notes) plus the archive flag. There is no route here that
writes a contact detail, a team member, or a compliance document — the customer
owns all three, and the way to guarantee the Provider cannot change them is for
the endpoint not to exist rather than for a handler to check a flag.

Every request runs through `get_superadmin_db`, which enforces the owner
audience, opens the RLS-bypass scope, and writes an audit_log row for the
cross-tenant access.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import STAFF_ROLES
from app.api.deps import CurrentUser, get_current_user, get_superadmin_db
from app.models.compliance import DOCUMENT_GROUPS, DOCUMENT_LABELS, ComplianceDocument
from app.models.enums import Role, UserStatus
from app.models.tenant import CUSTOMER_ACTIVE, CUSTOMER_ARCHIVED, Tenant
from app.models.user import User
from app.schemas.provider import (
    TEAM_PREVIEW_LIMIT,
    ComplianceDocumentOut,
    ComplianceDocumentSlot,
    CustomerAnalyticsOut,
    CustomerDetailOut,
    CustomerListOut,
    CustomerOut,
    CustomerTeamMemberOut,
    CustomerUpdateIn,
    PrimaryContactOut,
    document_slots,
)
from app.services import provider_analytics
from app.services.audit import audit
from app.services.document_storage import fetch_document_bytes

router = APIRouter()

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


# ── Loading helpers ──────────────────────────────────────────────────────────

async def _load_customer(session: AsyncSession, customer_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, customer_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return tenant


async def _primary_contacts(
    session: AsyncSession, tenant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, User]:
    """The `client` (Client Company Admin / HR Head) user per customer.

    Oldest wins if a customer somehow has two — the same tie-break the Owner
    console has always used, so the two consoles never disagree about who the
    primary contact is.
    """
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


async def _team_sizes(
    session: AsyncSession, tenant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Active staff headcount per customer. Disabled accounts are excluded —
    "9 members" must mean nine people who can sign in."""
    if not tenant_ids:
        return {}
    rows = (
        await session.execute(
            select(User.tenant_id, func.count())
            .where(
                User.tenant_id.in_(tenant_ids),
                User.role.in_(STAFF_ROLES),
                User.status != UserStatus.disabled,
            )
            .group_by(User.tenant_id)
        )
    ).all()
    return {tenant_id: count for tenant_id, count in rows if tenant_id is not None}


# ── Serialization ────────────────────────────────────────────────────────────

def _contact_out(contact: User | None) -> PrimaryContactOut:
    if contact is None:
        return PrimaryContactOut()
    return PrimaryContactOut(
        user_id=contact.id,
        name=contact.full_name,
        email=contact.email,
        phone=contact.phone,
        landline=contact.landline,
        status=contact.status.value,
    )


def _customer_out(
    tenant: Tenant,
    contact: User | None,
    team_size: int,
    analytics: provider_analytics.CustomerAnalytics,
) -> CustomerOut:
    return CustomerOut(
        id=tenant.id,
        name=tenant.name,
        industry=tenant.industry,
        website_domain=tenant.website_domain,
        domain=tenant.domain,
        status=tenant.status,  # type: ignore[arg-type]
        archived_at=tenant.archived_at,
        created_at=tenant.created_at,
        notes=tenant.notes,
        primary_contact=_contact_out(contact),
        team_size=team_size,
        analytics=CustomerAnalyticsOut(**vars(analytics)),
    )


def _document_out(
    document: ComplianceDocument, uploader_name: str | None
) -> ComplianceDocumentOut:
    return ComplianceDocumentOut(
        id=document.id,
        document_type=document.document_type,  # type: ignore[arg-type]
        label=DOCUMENT_LABELS[document.document_type],
        group=DOCUMENT_GROUPS[document.document_type],  # type: ignore[arg-type]
        file_name=document.file_name,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        uploaded_at=document.uploaded_at,
        uploaded_by_name=uploader_name,
    )


async def _slots_for(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[ComplianceDocumentSlot]:
    """All seven slots for one customer, present or not.

    The absent ones are returned too (with `document: null`) so a missing PAN
    card is a visible row rather than something the client has to notice is
    not in a short list.
    """
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
                select(User.id, User.full_name, User.email).where(User.id.in_(uploader_ids))
            )
        ).all()
        names = {row.id: (row.full_name or row.email) for row in rows}
    by_type = {
        d.document_type: _document_out(
            d, names.get(d.uploaded_by) if d.uploaded_by else None
        )
        for d in documents
    }
    return document_slots(by_type)


# ── Customers ────────────────────────────────────────────────────────────────

@router.get("/customers", response_model=CustomerListOut)
async def list_customers(
    search: str | None = Query(
        default=None,
        description="Matches company name, industry, primary contact name or "
                    "primary contact email.",
    ),
    status_filter: str = Query(
        default=CUSTOMER_ACTIVE,
        alias="status",
        description="active (default) | archived | all",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    session: AsyncSession = Depends(get_superadmin_db),
) -> CustomerListOut:
    """The Provider Portal's customer table.

    Archived customers are HIDDEN by default: `status=active` is the default
    precisely so a forgotten filter cannot quietly show archived customers as
    if they were live.

    Search and the status filter run in SQL, before pagination — filtering a
    single page in the browser would make "3 of 108 customers match" depend on
    which page happened to be loaded.
    """
    contact = User.__table__.alias("primary_contact")
    stmt = select(Tenant).outerjoin(
        contact,
        (contact.c.tenant_id == Tenant.id) & (contact.c.role == Role.client),
    )
    count_stmt = select(func.count(func.distinct(Tenant.id))).select_from(
        Tenant.__table__.outerjoin(
            contact,
            (contact.c.tenant_id == Tenant.id) & (contact.c.role == Role.client),
        )
    )

    if status_filter != "all":
        if status_filter not in (CUSTOMER_ACTIVE, CUSTOMER_ARCHIVED):
            raise HTTPException(
                status_code=422, detail="status must be active, archived or all"
            )
        stmt = stmt.where(Tenant.status == status_filter)
        count_stmt = count_stmt.where(Tenant.status == status_filter)

    needle = (search or "").strip()
    if needle:
        pattern = f"%{needle}%"
        predicate = or_(
            Tenant.name.ilike(pattern),
            Tenant.industry.ilike(pattern),
            contact.c.full_name.ilike(pattern),
            contact.c.email.ilike(pattern),
        )
        stmt = stmt.where(predicate)
        count_stmt = count_stmt.where(predicate)

    total = (await session.execute(count_stmt)).scalar_one()
    # DISTINCT is not needed — at most one `client` row joins per tenant — but
    # the order must be TOTAL or a customer could repeat across pages while
    # another never appears (the same rule the candidate table follows).
    rows = (
        await session.execute(
            stmt.order_by(Tenant.created_at.desc(), Tenant.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().unique().all()

    ids = [tenant.id for tenant in rows]
    contacts = await _primary_contacts(session, ids)
    team_sizes = await _team_sizes(session, ids)
    analytics = await provider_analytics.counts_for_tenants(session, ids)
    return CustomerListOut(
        customers=[
            _customer_out(
                tenant,
                contacts.get(tenant.id),
                team_sizes.get(tenant.id, 0),
                analytics.get(tenant.id, provider_analytics.EMPTY_ANALYTICS),
            )
            for tenant in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/customers/{customer_id}", response_model=CustomerDetailOut)
async def get_customer(
    customer_id: uuid.UUID,
    session: AsyncSession = Depends(get_superadmin_db),
) -> CustomerDetailOut:
    """The detail panel (spec §4.1): profile, primary contact, team preview,
    analytics, and all seven compliance slots in one response."""
    tenant = await _load_customer(session, customer_id)
    contacts = await _primary_contacts(session, [tenant.id])
    team_sizes = await _team_sizes(session, [tenant.id])
    analytics = await provider_analytics.counts_for_tenant(session, tenant.id)

    members = (
        await session.execute(
            select(User)
            .where(
                User.tenant_id == tenant.id,
                User.role.in_(STAFF_ROLES),
                User.status != UserStatus.disabled,
            )
            .order_by(User.created_at)
            .limit(TEAM_PREVIEW_LIMIT)
        )
    ).scalars().all()

    base = _customer_out(
        tenant, contacts.get(tenant.id), team_sizes.get(tenant.id, 0), analytics
    )
    return CustomerDetailOut(
        **base.model_dump(),
        culture=tenant.culture,
        details=tenant.details,
        team=[
            CustomerTeamMemberOut(
                id=member.id,
                name=member.full_name,
                email=member.email,
                role=member.role.value,
                status=member.status.value,
            )
            for member in members
        ],
        compliance_documents=await _slots_for(session, tenant.id),
    )


@router.get("/customers/{customer_id}/analytics", response_model=CustomerAnalyticsOut)
async def get_customer_analytics(
    customer_id: uuid.UUID,
    session: AsyncSession = Depends(get_superadmin_db),
) -> CustomerAnalyticsOut:
    """Spec §6.3. Same counters as the list row, addressable on their own so a
    panel can refresh them without re-fetching the whole customer."""
    tenant = await _load_customer(session, customer_id)
    analytics = await provider_analytics.counts_for_tenant(session, tenant.id)
    return CustomerAnalyticsOut(**vars(analytics))


async def _apply_customer_update(
    session: AsyncSession,
    tenant: Tenant,
    changes: dict[str, str | None],
    actor_user_id: uuid.UUID,
) -> list[str]:
    """Apply the Provider's partial edit in place and audit it.

    `changes` holds ONLY the keys the caller actually sent (pydantic
    `exclude_unset`), so an absent key means "leave unchanged" while an
    explicit "" clears the field.
    """
    changed: list[str] = []

    for field in ("industry", "website_domain", "notes"):
        if field not in changes:
            continue
        value = changes[field] or None
        if value != getattr(tenant, field):
            setattr(tenant, field, value)
            changed.append(field)

    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if new_status != tenant.status:
            tenant.status = new_status
            # `archived_at` is derived from the transition, never sent by the
            # client: an unarchive must clear it, or the customer would show a
            # stale archive date after being restored.
            tenant.archived_at = (
                datetime.now(timezone.utc) if new_status == CUSTOMER_ARCHIVED else None
            )
            changed.append("status")

    await session.flush()
    if changed:
        await audit(
            session,
            tenant_id=tenant.id,
            actor_user_id=actor_user_id,
            action=(
                "customer_archived"
                if tenant.status == CUSTOMER_ARCHIVED and "status" in changed
                else "customer_unarchived"
                if "status" in changed
                else "customer_updated"
            ),
            target_type="tenant",
            target_id=tenant.id,
            metadata={"fields": sorted(changed), "status": tenant.status},
        )
    return changed


async def _updated_detail(
    session: AsyncSession, tenant: Tenant
) -> CustomerDetailOut:
    return await get_customer(tenant.id, session)


@router.patch("/customers/{customer_id}", response_model=CustomerDetailOut)
async def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> CustomerDetailOut:
    """Edit the three Provider-owned fields, and/or archive & unarchive.

    Company name, primary contact, team and created date are deliberately not
    accepted: they belong to the customer and are maintained in the Customer
    Portal (spec §4.2).
    """
    tenant = await _load_customer(session, customer_id)
    await _apply_customer_update(
        session, tenant, body.model_dump(exclude_unset=True), user.user_id
    )
    return await _updated_detail(session, tenant)


@router.patch("/customers/{customer_id}/archive", response_model=CustomerDetailOut)
async def archive_customer(
    customer_id: uuid.UUID,
    archived: bool = Query(
        default=True,
        description="true archives the customer, false restores it.",
    ),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> CustomerDetailOut:
    """Archive (or unarchive) a customer — spec §2.4 / §6.1.

    A SOFT, fully reversible hide: no job, application, report, email or user
    is touched, and the customer's own portal is unaffected. It is the safe
    counterpart to `DELETE /admin/tenants/{id}`, which is irreversible and
    still requires retyping the company name.
    """
    tenant = await _load_customer(session, customer_id)
    await _apply_customer_update(
        session,
        tenant,
        {"status": CUSTOMER_ARCHIVED if archived else CUSTOMER_ACTIVE},
        user.user_id,
    )
    return await _updated_detail(session, tenant)


# ── Compliance documents (READ ONLY — no upload route exists here) ───────────

@router.get(
    "/customers/{customer_id}/compliance-documents",
    response_model=list[ComplianceDocumentSlot],
)
async def list_compliance_documents(
    customer_id: uuid.UUID,
    session: AsyncSession = Depends(get_superadmin_db),
) -> list[ComplianceDocumentSlot]:
    """All seven slots in fixed order: the four Indian tax/compliance records,
    then the three commercial ones. A slot with `document: null` has not been
    supplied yet."""
    tenant = await _load_customer(session, customer_id)
    return await _slots_for(session, tenant.id)


async def _load_document(
    session: AsyncSession, document_id: uuid.UUID
) -> ComplianceDocument:
    document = await session.get(ComplianceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get(
    "/compliance-documents/{document_id}", response_model=ComplianceDocumentOut
)
async def get_compliance_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_superadmin_db),
) -> ComplianceDocumentOut:
    """Metadata for one document (spec §6.2). The bytes are behind
    `/download`, never inlined here."""
    document = await _load_document(session, document_id)
    uploader_name: str | None = None
    if document.uploaded_by is not None:
        uploader = await session.get(User, document.uploaded_by)
        uploader_name = (uploader.full_name or uploader.email) if uploader else None
    return _document_out(document, uploader_name)


@router.get("/compliance-documents/{document_id}/download")
async def download_compliance_document(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
    inline: bool = Query(
        default=False, description="true serves for viewing, false forces a download."
    ),
) -> Response:
    """Redirect to the stored asset.

    Streaming the bytes through the API would buy nothing — the storage URL is
    unguessable and the redirect is issued only after the owner audience check
    — while doubling egress and putting a 10 MB body on the request path. The
    access itself is already recorded: `get_superadmin_db` writes an audit row
    for every request through this router.
    """
    document = await _load_document(session, document_id)
    await audit(
        session,
        tenant_id=document.tenant_id,
        actor_user_id=user.user_id,
        action="compliance_document_accessed",
        target_type="compliance_document",
        target_id=document.id,
        metadata={"document_type": document.document_type, "inline": inline},
    )
    content = await fetch_document_bytes(document.file_public_id or "")
    # 307, not 302: the method must be preserved and the redirect must not be
    # cached as permanent — the underlying asset URL can change on replace.
    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type=document.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{document.file_name.replace(chr(34), "")}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
