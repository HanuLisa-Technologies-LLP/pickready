"""PUBLIC employer pages: the searchable company directory and each company's
page with its live careers list.

Provenance: the 2026-09-05 add-features spec ("Employer Page & Content"). A
persistent, publicly visible page per client company -- its profile, its
website link, and every currently-live job with a link to the existing public
apply page -- reachable by search or from a company name in the candidate
portal.

AUTH MODEL: none, deliberately. These endpoints follow the exact pattern of
`GET /jobs/public/{job_id}`: the session is `get_public_db` (bypass scope,
because an anonymous visitor has no tenant context to set), and safety comes
from the handlers whitelisting rows (is_public + active customers only, live
published jobs only) and the response schemas whitelisting fields. Both
endpoints are rate-limited with the shared abuse counter, like every other
surface an anonymous caller can reach.

VISIBILITY: a page is served only when the tenant has a slug, `is_public` is
true and the customer status is `active`. Prospects (BD-created tenants whose
agreement was signed but who are not onboarded) and archived customers never
appear. A hidden or unknown slug 404s identically -- whether a company EXISTS
on the platform is not something an anonymous visitor should be able to probe,
same rule as the expired job link.

# ASSUMPTION (spec, "Subscription-gated per Feature 14"): the spec gates the
# employer page on a Feature 14 that the document does not define. `is_public`
# already covers hiding a page; wiring the flag to a subscription state is a
# future owner decision, and no billing check is invented here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_public_db
from app.api.jobs import public_job_url
from app.models import Company, Job, Tenant
from app.schemas.employer_pages import (
    EmployerCardOut,
    EmployerOpenRoleOut,
    EmployerPageOut,
    EmployerSearchOut,
)
from app.services import job_posting
from app.services.employer_pages import visible_tenant_conditions
from app.services.rate_limit import rate_limit

router = APIRouter()

MAX_PAGE_SIZE = 50


def _escape_like(term: str) -> str:
    r"""Neutralise LIKE wildcards in a visitor-typed search term, so `%` and
    `_` match themselves instead of everything."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Abuse control, not authorization (services/rate_limit): the directory search
# does real database work for an unauthenticated visitor.
@router.get(
    "",
    response_model=EmployerSearchOut,
    dependencies=[Depends(rate_limit("employer_search", limit=30, window=60))],
)
async def search_employers(
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    session: AsyncSession = Depends(get_public_db),
) -> EmployerSearchOut:
    """Public, paginated directory of employer pages.

    The search filter runs in SQL, before pagination, over name and industry
    -- filtering a fetched page in the browser would make the match count
    depend on which page was loaded (the same rule the Provider customer list
    follows).
    """
    conditions = list(visible_tenant_conditions())
    term = (search or "").strip()
    if term:
        pattern = f"%{_escape_like(term)}%"
        conditions.append(
            or_(
                Tenant.name.ilike(pattern, escape="\\"),
                Tenant.industry.ilike(pattern, escape="\\"),
            )
        )

    total = (
        await session.execute(
            select(func.count()).select_from(Tenant).where(*conditions)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(Tenant)
            .where(*conditions)
            .order_by(func.lower(Tenant.name), Tenant.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return EmployerSearchOut(
        employers=[
            EmployerCardOut(
                slug=t.public_slug,
                name=t.name,
                industry=t.industry,
                website_domain=t.website_domain,
            )
            for t in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{slug}",
    response_model=EmployerPageOut,
    dependencies=[Depends(rate_limit("employer_page", limit=60, window=60))],
)
async def get_employer_page(
    slug: str,
    session: AsyncSession = Depends(get_public_db),
) -> EmployerPageOut:
    """One employer's public page: identity, narrative sections, and the
    careers list of every currently-live published job with its apply link.

    The narrative sections resolve exactly as the candidate portal's job
    payload does: the client-authored Company Profile first, the tenant's
    onboarding prose as the fallback for a client who has never saved one.

    The careers list applies `services/job_posting.public_link_active` per
    job -- the SAME predicate the public apply page answers with -- so a job
    outside its 30-day window (or closed early, or archived, or never
    published) can never be listed here while 404ing there.
    """
    tenant = (
        await session.execute(
            select(Tenant).where(
                Tenant.public_slug == slug, *visible_tenant_conditions()
            )
        )
    ).scalars().first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Employer page not found")

    company = (
        await session.execute(
            select(Company).where(Company.tenant_id == tenant.id)
        )
    ).scalars().first()

    jobs = (
        await session.execute(
            select(Job)
            .where(
                Job.tenant_id == tenant.id,
                Job.ratified_at.is_not(None),
                Job.archived_at.is_(None),
            )
            .order_by(Job.posting_start_date.desc(), Job.id)
        )
    ).scalars().all()
    open_roles = [
        EmployerOpenRoleOut(
            id=job.id,
            title=job.title,
            department=job.department,
            level=job.level,
            experience_min_years=job.experience_min_years,
            experience_max_years=job.experience_max_years,
            apply_path=f"/apply/{job.id}",
            apply_url=public_job_url(job.id),
        )
        for job in jobs
        if job_posting.public_link_active(
            job.posting_start_date, job.posting_end_date, closed_at=job.closed_at
        )
    ]

    return EmployerPageOut(
        slug=tenant.public_slug,
        name=tenant.name,
        industry=tenant.industry,
        website_domain=tenant.website_domain,
        about_company=(company.about_company if company else None) or tenant.details,
        work_life=(company.work_life if company else None) or tenant.culture,
        benefits=company.benefits_text if company else None,
        open_roles=open_roles,
    )
