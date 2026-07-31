"""Business Development Portal business logic.

Everything here is deliberately callable WITHOUT a FastAPI request: the query
builders are pure, the mutators take a row and a clock, and only the two
functions that genuinely need the database take a session. That is what makes
the boundary rules (search runs in SQL, agreement promotes a lead to a tenant,
a company name containing a comma survives the CSV) testable directly.

THE FOUR THINGS THAT MUST NOT DRIFT
-----------------------------------
1. Search, filtering and pagination run in SQL, BEFORE the page is cut. This is
   the same rule the Provider Portal follows: filter a fetched page in the
   browser and "3 of 108 match" starts depending on which page was loaded.
2. Ticking a progress box stamps its timestamp the FIRST time only. Unticking
   clears the flag and keeps the stamp.
3. `agreement = true` promotes the lead to a CUSTOMER, and a customer IS a
   `tenants` row (CLAUDE.md hard rule). Un-setting it never deletes the tenant.
4. The CSV is written by `csv.writer`, never by string concatenation, so a
   company called "Acme, Inc." does not silently become two columns.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

from sqlalchemy import Select, Text, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bd import (
    CHANNEL_SOCIAL,
    PROGRESS_FLAGS,
    TENANT_PROSPECT,
    BDLead,
)
from app.models.job import Job
from app.models.tenant import CUSTOMER_ACTIVE, CUSTOMER_ARCHIVED, Tenant
from app.schemas.bd import CSV_COLUMNS, BDCustomerOut, JobCardOut

#: How many "similar to our customers" cards the segment returns. A dashboard
#: panel, not a search results page.
SIMILAR_LIMIT = 12


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


# ── Lead listing (all filtering in SQL) ──────────────────────────────────────

def lead_predicates(
    *,
    channel: str | None = None,
    search: str | None = None,
    agreement: bool | None = None,
    include_archived: bool = False,
    owner_user_id: uuid.UUID | None = None,
    agreement_is_set: bool = False,
) -> list:
    """The WHERE clauses for the lead list, as SQLAlchemy expressions.

    `agreement_is_set` distinguishes "the caller asked for undecided leads"
    (`agreement=None` WAS sent) from "the caller did not filter on agreement"
    (`agreement` absent). Without it, `?agreement=` could not express "show me
    the leads nobody has decided yet", which is the BD team's working queue.
    """
    predicates = []
    if channel:
        predicates.append(BDLead.channel == channel)
    if not include_archived:
        predicates.append(BDLead.archived_at.is_(None))
    if owner_user_id is not None:
        predicates.append(BDLead.owner_user_id == owner_user_id)
    if agreement_is_set:
        predicates.append(
            BDLead.agreement.is_(None) if agreement is None
            else BDLead.agreement.is_(agreement)
        )

    needle = (search or "").strip()
    if needle:
        pattern = f"%{needle}%"
        predicates.append(
            or_(
                BDLead.company_name.ilike(pattern),
                BDLead.industry.ilike(pattern),
                BDLead.location.ilike(pattern),
                BDLead.contact_name.ilike(pattern),
                BDLead.contact_email.ilike(pattern),
                BDLead.contact_phone.ilike(pattern),
                BDLead.website.ilike(pattern),
            )
        )
    return predicates


def lead_list_query(predicates: Sequence, page: int, page_size: int) -> Select:
    """One page of leads.

    The order must be TOTAL. `created_at` alone is not: two leads entered in
    the same second could swap between page 1 and page 2, so one duplicates and
    another is never seen. The trailing `id` is what closes that.
    """
    return (
        select(BDLead)
        .where(*predicates)
        .order_by(BDLead.created_at.desc(), BDLead.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )


# ── Progress checkboxes ──────────────────────────────────────────────────────

def apply_progress(
    lead: object, changes: dict[str, bool], now: datetime | None = None
) -> list[str]:
    """Tick or untick boxes. Returns the flags that actually changed.

    Ticking stamps `<flag>_at` the FIRST time only. Unticking clears the flag
    and KEEPS the stamp: a rep correcting a mis-click should not erase the fact
    that the company was contacted, and re-ticking should not pretend the
    contact happened today.
    """
    at = _now(now)
    changed: list[str] = []
    for flag in PROGRESS_FLAGS:
        if flag not in changes:
            continue
        value = bool(changes[flag])
        if bool(getattr(lead, flag, False)) == value:
            continue
        setattr(lead, flag, value)
        if value and getattr(lead, f"{flag}_at", None) is None:
            setattr(lead, f"{flag}_at", at)
        changed.append(flag)
    return changed


# ── Promotion: a signed lead becomes a customer ──────────────────────────────

_DOMAIN_SAFE = re.compile(r"[^a-z0-9.-]+")

#: Reserved suffix for a tenant that exists only because a lead signed. It
#: keeps a synthesized key obviously synthetic, so nobody mistakes it for a
#: real mail domain (the Owner console replaces it at onboarding).
PROSPECT_DOMAIN_SUFFIX = "prospect.pickready.local"


def derive_tenant_domain(
    *, website: str | None, contact_email: str | None, lead_id: uuid.UUID
) -> str:
    """`tenants.domain` is NOT NULL and UNIQUE, so a promoted lead needs one.

    Preference order: the company website host, then the contact email host,
    then a synthetic key derived from the lead id. Pure, so the fallback chain
    is unit-testable without a database.
    """
    candidate = (website or "").strip().lower()
    for prefix in ("https://", "http://"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    candidate = candidate.split("/")[0].strip()
    if candidate.startswith("www."):
        candidate = candidate[4:]

    if not candidate and contact_email and "@" in contact_email:
        candidate = contact_email.rsplit("@", 1)[1].strip().lower()

    candidate = _DOMAIN_SAFE.sub("", candidate).strip(".-")
    if not candidate or "." not in candidate:
        return f"lead-{str(lead_id)[:8]}.{PROSPECT_DOMAIN_SUFFIX}"
    return candidate[:255]


async def _unique_domain(session: AsyncSession, base: str, lead_id: uuid.UUID) -> str:
    """Two leads for the same company must not collide on the UNIQUE key.

    The suffix is the lead id rather than a counter: a counter needs a read to
    decide the next value and two concurrent promotions would pick the same one.
    """
    taken = (
        await session.execute(select(Tenant.id).where(Tenant.domain == base))
    ).first()
    if taken is None:
        return base
    short = str(lead_id)[:8]
    host, _, tld = base.partition(".")
    return f"{host}-{short}.{tld}" if tld else f"{host}-{short}"


async def set_agreement(
    session: AsyncSession,
    lead: BDLead,
    agreement: bool | None,
    *,
    now: datetime | None = None,
) -> tuple[str, Tenant | None]:
    """Apply the yes / no / undecided decision and run the promotion.

    Returns `(outcome, tenant)` where outcome is one of `unchanged`,
    `promoted`, `repromoted`, `demoted`, `set`.

    PROMOTION (`true`): creates the `tenants` row this lead becomes, in the
    `prospect` status so it does not appear in the Provider Portal's live
    customer list, and links it. A lead that was promoted before reuses and
    unarchives its original tenant rather than minting a duplicate company.

    DEMOTION (`false` or `null`): unlinks and ARCHIVES. It never deletes the
    tenant, because by then the customer may already have users, jobs and
    applications hanging off it, and because archiving is the reversible
    counterpart the rest of the platform already uses.
    """
    at = _now(now)
    if lead.agreement is agreement:
        return "unchanged", None

    previous = lead.agreement
    lead.agreement = agreement
    lead.agreement_at = at if agreement is not None else None

    if agreement is True:
        if lead.promoted_tenant_id is not None:
            tenant = await session.get(Tenant, lead.promoted_tenant_id)
            if tenant is not None:
                tenant.status = TENANT_PROSPECT
                tenant.archived_at = None
                lead.tenant_id = tenant.id
                await session.flush()
                return "repromoted", tenant
        tenant = await _create_tenant_from_lead(session, lead)
        lead.tenant_id = tenant.id
        lead.promoted_tenant_id = tenant.id
        await session.flush()
        return "promoted", tenant

    # false or None: unlink, and archive the customer the lead had created.
    tenant: Tenant | None = None
    if lead.tenant_id is not None:
        tenant = await session.get(Tenant, lead.tenant_id)
        if tenant is not None and tenant.status != CUSTOMER_ARCHIVED:
            tenant.status = CUSTOMER_ARCHIVED
            tenant.archived_at = at
        lead.tenant_id = None
    await session.flush()
    return ("demoted" if previous is True else "set"), tenant


async def _create_tenant_from_lead(session: AsyncSession, lead: BDLead) -> Tenant:
    base = derive_tenant_domain(
        website=lead.website, contact_email=lead.contact_email, lead_id=lead.id
    )
    tenant = Tenant(
        id=uuid.uuid4(),
        name=lead.company_name,
        domain=await _unique_domain(session, base, lead.id),
        industry=lead.industry,
        website_domain=lead.website,
        status=TENANT_PROSPECT,
        notes=(
            "Created from a Business Development lead. Not onboarded yet."
            if not lead.notes
            else f"Created from a Business Development lead. {lead.notes}"[:5000]
        ),
    )
    session.add(tenant)
    await session.flush()
    return tenant


# ── The BD Customers page ────────────────────────────────────────────────────

def customer_predicates(*, search: str | None = None) -> list:
    """A customer is a lead whose agreement was signed. Archived leads are
    excluded: archiving a lead hides the whole relationship, and a hidden lead
    reappearing on the Customers page would be a surprising leak."""
    predicates = [BDLead.agreement.is_(True), BDLead.archived_at.is_(None)]
    needle = (search or "").strip()
    if needle:
        pattern = f"%{needle}%"
        predicates.append(
            or_(
                BDLead.company_name.ilike(pattern),
                BDLead.industry.ilike(pattern),
                BDLead.location.ilike(pattern),
                BDLead.contact_name.ilike(pattern),
                BDLead.contact_email.ilike(pattern),
                BDLead.contact_phone.ilike(pattern),
            )
        )
    return predicates


def customer_list_query(
    predicates: Sequence, page: int | None = None, page_size: int | None = None
) -> Select:
    """Customers, newest agreement first, with a TOTAL order.

    `page` is optional so the CSV export can reuse this builder unpaginated and
    therefore honour exactly the same filters as the table above it.
    """
    stmt = select(BDLead).where(*predicates).order_by(
        BDLead.agreement_at.desc().nullslast(), BDLead.company_name, BDLead.id
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return stmt


def customer_out(lead: BDLead) -> BDCustomerOut:
    return BDCustomerOut(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        company_name=lead.company_name,
        location=lead.location,
        industry=lead.industry,
        contact_name=lead.contact_name,
        contact_email=lead.contact_email,
        contact_phone=lead.contact_phone,
        website=lead.website,
        channel=lead.channel,  # type: ignore[arg-type]
        social_source=lead.social_source,  # type: ignore[arg-type]
        agreement_at=lead.agreement_at,
    )


# ── CSV export ───────────────────────────────────────────────────────────────

def _csv_line(values: Iterable[object]) -> str:
    """One correctly quoted CSV line.

    `csv.writer` rather than ",".join: a company called "Acme, Inc." must stay
    one field, and a name containing a quote must be escaped rather than
    breaking the row. `\\r\\n` is the RFC 4180 terminator Excel expects.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["" if value is None else str(value) for value in values])
    return buffer.getvalue()


def csv_header() -> str:
    return _csv_line(label for _field, label in CSV_COLUMNS)


def csv_row(customer: BDCustomerOut) -> str:
    values = []
    for field, _label in CSV_COLUMNS:
        value = getattr(customer, field, None)
        if isinstance(value, datetime):
            # Date only: the BD team reads these in a spreadsheet, and a
            # timezone-suffixed timestamp is noise in a sales export.
            value = value.date().isoformat()
        values.append(value)
    return _csv_line(values)


def iter_csv(customers: Iterable[BDCustomerOut]) -> Iterator[str]:
    """Header first, then one line per customer. A generator so the response
    streams instead of building the whole file in memory."""
    yield csv_header()
    for customer in customers:
        yield csv_row(customer)


def csv_filename(now: datetime | None = None) -> str:
    return f"pickready-bd-customers-{_now(now).date().isoformat()}.csv"


# ── AI Reach, segment 1: our OWN customer database ───────────────────────────

def _confidence(hits: int, possible: int) -> str:
    """Turn a match count into one of the three WORD labels.

    The count never leaves this function. No score, percentage or rank reaches
    the client (CLAUDE.md hard rule), so the conversion happens server side and
    the API carries the word only.
    """
    if possible <= 0:
        return "Low"
    ratio = hits / possible
    if ratio >= 0.75:
        return "High"
    if ratio >= 0.4:
        return "Medium"
    return "Low"


def _company_url(tenant: Tenant) -> str:
    host = (tenant.website_domain or tenant.domain or "").strip()
    if not host:
        return ""
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"https://{host}"


def similar_query(job_role: str, city: str, industry: str, company: str | None) -> Select:
    """Jobs at PickReady's OWN customers that look like the search.

    ASSUMPTION (2026-07-28): `jobs` has no city column in this schema, so the
    city term is matched against the job's structured JD text and department
    alongside the customer's own profile prose. A job that matches on role and
    industry but cannot evidence the city still surfaces, at a LOWER confidence
    label, rather than being dropped: the BD team is looking for leads, and a
    near miss they can judge beats a silent omission.
    """
    role_pattern = f"%{(job_role or '').strip()}%"
    city_pattern = f"%{(city or '').strip()}%"
    industry_pattern = f"%{(industry or '').strip()}%"

    conditions = [
        Job.title.ilike(role_pattern),
        Tenant.industry.ilike(industry_pattern),
        Job.jd_json.cast(Text).ilike(city_pattern),
    ]
    if company:
        conditions.append(Tenant.name.ilike(f"%{company.strip()}%"))

    return (
        select(Job, Tenant)
        .join(Tenant, Tenant.id == Job.tenant_id)
        .where(
            Job.archived_at.is_(None),
            Tenant.status.in_((CUSTOMER_ACTIVE, TENANT_PROSPECT)),
            or_(*conditions),
        )
        .order_by(Job.created_at.desc(), Job.id)
        .limit(SIMILAR_LIMIT)
    )


def similar_cards(
    rows: Iterable[tuple[Job, Tenant]],
    *,
    job_role: str,
    city: str,
    industry: str,
    company: str | None,
) -> list[JobCardOut]:
    """Shape the customer-database rows into the same card the internet segment
    emits, so the UI renders one component twice."""
    role = (job_role or "").strip().lower()
    town = (city or "").strip().lower()
    sector = (industry or "").strip().lower()
    firm = (company or "").strip().lower()

    cards: list[JobCardOut] = []
    for job, tenant in rows:
        blob = " ".join(
            str(part or "").lower()
            for part in (job.title, job.department, job.jd_json, tenant.details)
        )
        checks = [
            bool(role) and role in (job.title or "").lower(),
            bool(sector) and sector in (tenant.industry or "").lower(),
            bool(town) and town in blob,
        ]
        possible = sum(1 for term in (role, sector, town) if term)
        if firm:
            checks.append(firm in (tenant.name or "").lower())
            possible += 1
        url = _company_url(tenant)
        if not url:
            # Every card must open a company website. A card with nowhere to go
            # is a dead click, so it is dropped rather than rendered.
            continue
        cards.append(
            JobCardOut(
                job_title=job.title,
                company=tenant.name,
                city=city or None,
                industry=tenant.industry,
                company_url=url,
                job_url=None,
                source_domain=(tenant.website_domain or tenant.domain),
                confidence_label=_confidence(sum(1 for c in checks if c), possible),  # type: ignore[arg-type]
            )
        )
    return cards


async def similar_to_customers(
    session: AsyncSession, *, job_role: str, city: str, industry: str,
    company: str | None = None,
) -> list[JobCardOut]:
    """Segment 1 of AI Reach. No external network call, so it always works even
    when the web search key is absent or the internet segment times out."""
    rows = (
        await session.execute(similar_query(job_role, city, industry, company))
    ).all()
    return similar_cards(
        [(row[0], row[1]) for row in rows],
        job_role=job_role, city=city, industry=industry, company=company,
    )
