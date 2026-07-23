"""FastAPI auth/session dependencies.

- JWT is read from the `pr_access` httpOnly cookie or an `Authorization:
  Bearer` header (API_CONTRACT.md).
- `get_tenant_db` yields an AsyncSession wrapped in `tenant_scope` — the
  Postgres RLS policies are the real tenant boundary (claude.md rule 1).
- `require_capability(name)` is the ONLY permission gate in business logic —
  never `if role == ...` (claude.md rule 3). Backed by the RBAC engine.
- Super Admin requests go through `get_superadmin_db`, which uses the RLS
  bypass scope AND writes an audit_log row for the cross-tenant access
  (FR-11.3).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session_factory, superadmin_scope, tenant_scope
from app.core.security import (
    ALGORITHM,
    AUDIENCE_CANDIDATE,
    AUDIENCE_INTERNAL,
    decode_token,
)
from app.models.enums import Role
from app.services import rbac
from app.services.audit import audit

ACCESS_COOKIE = "pr_access"
REFRESH_COOKIE = "pr_refresh"

# Outreach links stay valid this long (candidate must respond within it).
# ASSUMPTION: 14 days — PRD sets no explicit outreach-link TTL.
OUTREACH_TOKEN_TTL_DAYS = 14


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _payload_to_user(payload: dict) -> CurrentUser:
    tenant = payload.get("tenant_id")
    return CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        tenant_id=uuid.UUID(tenant) if tenant else None,
        role=Role(payload["role"]),
    )


def _decode_or_401(token: str, audience: str) -> dict:
    try:
        payload = decode_token(token, audience=audience)
    except pyjwt.PyJWTError as exc:
        raise _unauthorized(str(exc)) from exc
    if payload.get("type") != "access":
        raise _unauthorized("not an access token")
    return payload


async def get_current_user(request: Request) -> CurrentUser:
    """Internal audience (super_admin / client / hr_manager / recruiter /
    hiring_manager)."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    return _payload_to_user(_decode_or_401(token, AUDIENCE_INTERNAL))


async def get_current_candidate(request: Request) -> CurrentUser:
    """Candidate-portal audience — distinct JWT session scope (ESD §13)."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    user = _payload_to_user(_decode_or_401(token, AUDIENCE_CANDIDATE))
    if user.role != Role.candidate:
        raise _unauthorized("candidate session required")
    return user


async def get_current_any(request: Request) -> CurrentUser:
    """Either audience — used only by /auth/me and /auth/logout."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    try:
        return _payload_to_user(_decode_or_401(token, AUDIENCE_INTERNAL))
    except HTTPException:
        return _payload_to_user(_decode_or_401(token, AUDIENCE_CANDIDATE))


# ── DB sessions ──────────────────────────────────────────────────────────────

async def get_tenant_db(
    user: CurrentUser = Depends(get_current_user),
) -> AsyncIterator[AsyncSession]:
    """Tenant-scoped session: RLS var set for the whole request transaction.
    Commits on success, rolls back on exception."""
    if user.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires a tenant-scoped account",
        )
    async with get_session_factory()() as session:
        async with session.begin():
            async with tenant_scope(session, user.tenant_id):
                yield session


async def get_superadmin_db(
    request: Request, user: CurrentUser = Depends(get_current_user)
) -> AsyncIterator[AsyncSession]:
    """Dedicated Super Admin path: RLS bypass scope + an audit_log row for
    every cross-tenant access (FR-11.3 / ESD §3). This is auth plumbing, not
    business logic — the role check here is the audience gate, not an RBAC
    shortcut."""
    if user.role != Role.super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Super Admin only")
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await audit(
                    session,
                    tenant_id=None,
                    actor_user_id=user.user_id,
                    action="superadmin_access",
                    target_type="endpoint",
                    target_id=None,
                    metadata={"method": request.method, "path": request.url.path,
                              "query": str(request.url.query or "")},
                )
                yield session


async def get_public_db() -> AsyncIterator[AsyncSession]:
    """Session for PUBLIC tokenized endpoints (employer verification form,
    candidate outreach link, inbound-email webhook).

    # ASSUMPTION: these endpoints cannot set an RLS tenant var before the
    # token's row is found (the tenant is unknown until then) — the signed,
    # single-use token itself is the authorization. They therefore use the
    # bypass scope; every handler MUST filter by the exact token and never
    # expose data beyond that one row's scope.
    """
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                yield session


async def get_candidate_db(
    user: CurrentUser = Depends(get_current_candidate),
) -> AsyncIterator[AsyncSession]:
    """Candidate-portal session. Candidates have no tenant (they span tenants
    via the Databank), so RLS-by-tenant cannot apply; handlers MUST filter by
    the authenticated candidate's identity."""
    async with get_session_factory()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                yield session


# ── Capability gate (RBAC) ───────────────────────────────────────────────────

def require_capability(capability: str):
    """Dependency factory: 403 unless the caller's (tenant, role) grants the
    capability per the role_permissions data (tenant override > global
    template > deny)."""

    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_tenant_db),
    ) -> CurrentUser:
        if not await rbac.has_capability(session, user.tenant_id, user.role, capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing capability: {capability}",
            )
        return user

    return dependency


# ── Outreach tokens (signed, stateless) ──────────────────────────────────────
# The candidate outreach link (FR-6.1) is a signed JWT carrying
# {profile_id, job_id, purpose: "outreach"} under the candidate audience.
# There is no DB column for an outreach token, so the signature is the
# integrity guarantee; single-use is enforced at submit time by rejecting a
# profile whose aspects are already completed (see portal.py).

def make_outreach_token(profile_id: uuid.UUID | str, job_id: uuid.UUID | str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "profile_id": str(profile_id),
        "job_id": str(job_id),
        "purpose": "outreach",
        "aud": AUDIENCE_CANDIDATE,
        "iat": now,
        "exp": now + timedelta(days=OUTREACH_TOKEN_TTL_DAYS),
        "type": "outreach",
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_outreach_token(token: str) -> dict:
    """Returns {profile_id, job_id} or raises HTTPException(404) — public
    endpoints must not leak why a token is invalid."""
    try:
        payload = pyjwt.decode(
            token, get_settings().jwt_secret, algorithms=[ALGORITHM],
            audience=AUDIENCE_CANDIDATE,
        )
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=404, detail="Invalid or expired link") from exc
    if payload.get("purpose") != "outreach":
        raise HTTPException(status_code=404, detail="Invalid or expired link")
    return payload
