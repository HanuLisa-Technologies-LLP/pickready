"""The ONE implementation of permanently removing a tenant.

Two callers: the Provider's irreversible `DELETE /admin/tenants/{id}` (which
asks for the company name retyped first) and the operator's
`app.scripts.cleanup_test_accounts` (which is a dry run unless told otherwise
and refuses outside the environment it is told it is in). Both need exactly
the same semantics, so they share this rather than each keeping a copy:

* Rows with a foreign key to `tenants` cascade at the database level: users,
  staff invitations, the company page, jobs, applications, credit purchases,
  the ledger, role permissions and the rest.
* Two references carry NO foreign key on purpose and are RELEASED rather than
  deleted: `candidates.tenant_id` and `profiles.source_tenant_id`. Databank
  rows may be shared across tenants, and deleting them would destroy another
  tenant's matches.
* `audit_log.tenant_id` has no foreign key either, and one row is written
  BEFORE the delete, so the trail survives the tenant.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate, Profile
from app.models.job import Job
from app.models.tenant import Tenant
from app.models.user import User
from app.services.audit import audit

__all__ = ["delete_tenant"]


async def delete_tenant(
    session: AsyncSession,
    tenant: Tenant,
    *,
    actor_user_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Delete `tenant` inside the caller's transaction; what went, by kind.

    The caller owns the transaction and the bypass scope, so a failure
    anywhere rolls the whole removal back.
    """
    tenant_id = tenant.id
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

    # Audit BEFORE the delete so the row is written while the tenant still
    # exists; audit_log has no FK to tenants, so the trail survives.
    await audit(
        session, tenant_id=tenant_id, actor_user_id=actor_user_id,
        action="tenant_deleted", target_type="tenant", target_id=tenant_id,
        metadata={
            "name": tenant.name,
            "domain": tenant.domain,
            "removed": removed,
            **(metadata or {}),
        },
    )
    await session.delete(tenant)
    await session.flush()
    return removed
