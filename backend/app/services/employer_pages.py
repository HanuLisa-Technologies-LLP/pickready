"""Slug assignment for public employer pages.

Provenance: the 2026-09-05 add-features spec ("Employer Page & Content").
Every client company gets a persistent public page at /employers/{slug}; the
slug lives on `tenants.public_slug` (migration 0084 explains why `tenants`
and not `companies`).

This module owns the ONE slug rule, mirrored by migration 0084's SQL backfill
-- change them together. It is deliberately distinct from
`services/matching_categories.slugify`, which produces underscore-joined
SCORE KEYS with a different alphabet and bound; a URL path segment and a
score-filing key are two concepts, not one.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant
from app.models.tenant import CUSTOMER_ACTIVE

__all__ = [
    "slugify",
    "assign_slug",
    "visible_tenant_conditions",
    "visible_slug",
]

#: Bound below the column's 140 so a collision suffix always fits.
_MAX_BASE = 130
_COLLISION_SUFFIX_CHARS = 6
_FALLBACK_SUFFIX_CHARS = 8


def slugify(name: str) -> str:
    """A URL slug from a company name: casefolded, runs of anything outside
    [a-z0-9] collapsed to single hyphens, trimmed, bounded. Deterministic, so
    the migration backfill and this function agree on every existing name.
    Returns the empty string for a wholly non-alphanumeric name; the caller
    substitutes the id-derived fallback."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")
    return slug[:_MAX_BASE].rstrip("-")


def _id_suffix(tenant_id: uuid.UUID, length: int) -> str:
    """Deterministic per-tenant suffix, identical to the SQL backfill's
    substr(md5(id::text), 1, n). Not a secret and not security-relevant: it
    only has to be stable and distinct enough to break a name collision."""
    return hashlib.md5(str(tenant_id).encode("utf-8")).hexdigest()[:length]


async def assign_slug(session: AsyncSession, tenant: Tenant) -> str:
    """Set `tenant.public_slug` from its name, uniquified, and return it.

    Idempotent: a tenant that already carries a slug keeps it -- a slug is a
    published URL, and regenerating one because the name changed would break
    every link already shared. Called from the two tenant-creation paths
    (Provider onboarding in api/admin.py, BD promotion in services/bd_leads.py)
    after the row has an id.
    """
    if tenant.public_slug:
        return tenant.public_slug
    base = slugify(tenant.name) or (
        "employer-" + _id_suffix(tenant.id, _FALLBACK_SUFFIX_CHARS)
    )
    taken = (
        await session.execute(
            select(Tenant.id).where(
                Tenant.public_slug == base, Tenant.id != tenant.id
            )
        )
    ).first()
    slug = base if taken is None else (
        f"{base}-{_id_suffix(tenant.id, _COLLISION_SUFFIX_CHARS)}"
    )
    tenant.public_slug = slug
    return slug


# ── Visibility: ONE rule, two query forms ────────────────────────────────────
# "This company has a public employer page" means: it carries a slug, it has
# not been hidden, and it is an active customer (prospects and archived
# customers never appear). The SQL form filters listings; the row form decides
# whether a portal payload may link to the page. Change them together.

def visible_tenant_conditions() -> tuple:
    """SQLAlchemy conditions selecting tenants whose page is publicly served."""
    return (
        Tenant.public_slug.is_not(None),
        Tenant.is_public.is_(True),
        Tenant.status == CUSTOMER_ACTIVE,
    )


def visible_slug(tenant: Tenant | None) -> str | None:
    """The tenant's page slug when the page is publicly served, else None --
    so a caller building a link can never point at a URL that would 404."""
    if tenant is None:
        return None
    if (
        tenant.public_slug
        and tenant.is_public
        and tenant.status == CUSTOMER_ACTIVE
    ):
        return tenant.public_slug
    return None
