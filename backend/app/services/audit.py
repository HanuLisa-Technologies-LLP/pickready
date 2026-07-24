"""Append-only audit trail writer (PRD §8 Auditability / ESD §16).

Every approval transition, permission change, profile status change, Super
Admin cross-tenant access, and — see the AUTH_* constants below — every
auth event goes through here.

Two entry points:

* `audit(...)`  — low-level INSERT that participates in the CALLER's
  transaction and returns the row. Use it where the audit write is part of
  the unit of work being committed (approval FSM, permission edits, etc.);
  a failure here is a real error and should surface.

* `record_auth_event(...)` — hardened wrapper for the AUTH request/worker
  path. It writes inside its own SAVEPOINT and NEVER raises into the caller:
  an audit failure must not break a login (or a Celery task). It tolerates
  `tenant_id=None` (Owner / candidate — a past bug crashed on exactly this)
  and `actor_user_id=None` (a failed login where no user resolved).
"""
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import AuditLog

logger = logging.getLogger("pickready.audit")


# ── Auth action constants (single source of truth for auth audit rows) ───────
# Kept here so callers (api/auth.py, services/otp.py, workers/tasks.py,
# api/deps.py) reference names, never string literals.
AUTH_OTP_REQUESTED = "otp_requested"
AUTH_OTP_VERIFIED = "otp_verified"
AUTH_OTP_FAILED = "otp_failed"
AUTH_OTP_RATE_LIMITED = "otp_rate_limited"
AUTH_LOGIN_SUCCEEDED = "login_succeeded"
AUTH_CONTEXT_SELECTED = "context_selected"
AUTH_LOGOUT = "logout"
AUTH_EMAIL_SEND_FAILED = "email_send_failed"
AUTH_OWNER_INVARIANT_VIOLATION = "owner_invariant_violation"

# Every auth action this module knows how to record (handy for tests and for
# the validation harness to assert coverage against).
AUTH_ACTIONS: frozenset[str] = frozenset(
    {
        AUTH_OTP_REQUESTED,
        AUTH_OTP_VERIFIED,
        AUTH_OTP_FAILED,
        AUTH_OTP_RATE_LIMITED,
        AUTH_LOGIN_SUCCEEDED,
        AUTH_CONTEXT_SELECTED,
        AUTH_LOGOUT,
        AUTH_EMAIL_SEND_FAILED,
        AUTH_OWNER_INVARIANT_VIOLATION,
    }
)


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    """None / empty -> None; str/UUID -> UUID. Never raises for the None case
    (the tenant_id=None crash this module is hardened against)."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    text = str(value).strip()
    if not text:
        return None
    return uuid.UUID(text)


def _new_audit_row(
    *,
    tenant_id: uuid.UUID | str | None,
    actor_user_id: uuid.UUID | str | None,
    action: str,
    target_type: str | None,
    target_id: uuid.UUID | str | None,
    metadata: dict[str, Any] | None,
) -> AuditLog:
    return AuditLog(
        tenant_id=_coerce_uuid(tenant_id),
        actor_user_id=_coerce_uuid(actor_user_id),
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        metadata_json=metadata,
    )


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
    """Insert one immutable audit_log row inside the caller's transaction and
    return it. The application role has no UPDATE/DELETE grants on this table
    (enforced in the migration). Raises on failure — use for writes that are
    part of the committed unit of work.

    Tolerates `tenant_id=None` (platform-level / Owner / candidate events) and
    `actor_user_id=None` (unauthenticated / failed-login events)."""
    row = _new_audit_row(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )
    session.add(row)
    await session.flush()
    return row


async def record_auth_event(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: uuid.UUID | str | None = None,
    tenant_id: uuid.UUID | str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Record an auth event WITHOUT ever raising into the caller.

    Designed for the login path and Celery workers: an audit failure must not
    break a login or a task. The write happens inside a SAVEPOINT so that a
    failure rolls back only the audit insert and leaves the caller's
    transaction intact and usable. Returns True if the row was written, False
    if it was swallowed (the failure is logged, never propagated).

    Works identically whether called from a request handler (session opened via
    `get_session`) or a Celery worker (session-level `app.bypass_rls`): the
    audit_log table has no RLS policy, so no tenant var is required.

    `tenant_id=None` and `actor_user_id=None` are fully supported.
    """
    if action not in AUTH_ACTIONS:
        # Not fatal — record it anyway, but flag the drift so unknown auth
        # actions don't silently accumulate.
        logger.warning("record_auth_event called with unregistered action %r", action)
    try:
        # SAVEPOINT isolation: a failure here rolls back only this insert.
        async with session.begin_nested():
            row = _new_audit_row(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
            )
            session.add(row)
            await session.flush()
        return True
    except Exception:  # noqa: BLE001 — auth audit must never break the caller
        logger.exception(
            "audit write failed (swallowed) action=%s tenant_id=%s actor=%s",
            action,
            tenant_id,
            actor_user_id,
        )
        return False
