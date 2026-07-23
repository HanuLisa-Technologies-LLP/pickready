"""Append-only audit trail writer (PRD §8 Auditability / ESD §16).

Every approval transition, permission change, profile status change, and
Super Admin cross-tenant access goes through here.
"""
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import AuditLog


async def audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str | None,
    actor_user_id: uuid.UUID | str | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert one immutable audit_log row. The application role has no
    UPDATE/DELETE grants on this table (enforced in the migration)."""
    row = AuditLog(
        tenant_id=uuid.UUID(str(tenant_id)) if tenant_id else None,
        actor_user_id=uuid.UUID(str(actor_user_id)) if actor_user_id else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        metadata_json=metadata,
    )
    session.add(row)
    await session.flush()
    return row
