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
    AUDIENCE_ORG,
    AUDIENCE_OWNER,
    decode_token,
)
from app.models.enums import Role
from app.services import rbac
from app.services.audit import audit

ACCESS_COOKIE = "pr_access"
REFRESH_COOKIE = "pr_refresh"
# The refresh cookie is scoped to the auth router so it is never sent to (and
# can't leak from) ordinary API calls.
REFRESH_COOKIE_PATH = "/api/v1/auth"

# ── Session-presence hint ────────────────────────────────────────────────────
# `pr_session` carries NO token material. Its only job is to answer one
# question, for anything that can see cookies at path "/": "is there still a
# refresh token behind this browser?"
#
# It exists because of a real defect. The access cookie is deleted by the
# browser the moment its 15-minute Max-Age lapses, and the refresh cookie is
# path-scoped to /api/v1/auth, so it is NOT sent to a page request like
# /org/jobs. The Next.js middleware, which gates every portal route on cookie
# presence, therefore saw an idle-but-perfectly-refreshable user as signed out
# and redirected them to /login before a single API call was made. No amount of
# silent refresh in the API client can rescue that, because the bounce happens
# during navigation, above the API client.
#
# The hint cookie lives exactly as long as the refresh token, is path "/", stays
# HttpOnly (Next middleware reads request cookies server-side, so it never needs
# JavaScript access), and its value is a constant. Knowing it grants nothing.
SESSION_HINT_COOKIE = "pr_session"
SESSION_HINT_VALUE = "1"

# Outreach links stay valid this long (candidate must respond within it).
# ASSUMPTION: 14 days — PRD sets no explicit outreach-link TTL.
OUTREACH_TOKEN_TTL_DAYS = 14


# ── Cookie hardening (single source of truth) ────────────────────────────────
# SameSite=Strict + httponly + secure-in-prod for both auth cookies.
# ASSUMPTION: secure is disabled in development so the cookies work over plain
# http://localhost; it is forced on in production. SameSite=Strict is acceptable
# because this is a first-party SPA (no cross-site POST-back flow needs the
# cookie). Access token lives `jwt_access_ttl_minutes` (15), refresh lives
# `jwt_refresh_ttl_days`. Refresh tokens MUST be rotated on every use — the
# refresh handler should mint a NEW refresh token and call set_auth_cookies,
# not just reissue the access cookie.


def _cookie_kwargs() -> dict:
    """Shared attributes for every auth cookie, from settings (one source)."""
    from app.core.config import get_settings  # local: avoid import cycle at module load

    settings = get_settings()
    kwargs = {
        "httponly": True,
        "secure": settings.is_production,
        "samesite": settings.cookie_samesite,
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs


def set_access_cookie(response, access: str) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    response.set_cookie(
        ACCESS_COOKIE, access, max_age=settings.jwt_access_ttl_minutes * 60,
        path="/", **_cookie_kwargs(),
    )


def set_auth_cookies(response, access: str, refresh: str) -> None:
    """Set all THREE cookies. Call on login and on every refresh (rotation).

    The hint cookie is rewritten alongside the refresh token so its lifetime
    slides forward exactly as the refresh token's does. If it ever outlived the
    refresh token the middleware would let a genuinely dead session through, and
    the user would land on a portal page that immediately fails to load.
    """
    from app.core.config import get_settings

    settings = get_settings()
    set_access_cookie(response, access)
    refresh_max_age = settings.jwt_refresh_ttl_days * 86400
    response.set_cookie(
        REFRESH_COOKIE, refresh, max_age=refresh_max_age,
        path=REFRESH_COOKIE_PATH, **_cookie_kwargs(),
    )
    response.set_cookie(
        SESSION_HINT_COOKIE, SESSION_HINT_VALUE, max_age=refresh_max_age,
        path="/", **_cookie_kwargs(),
    )


def clear_auth_cookies(response) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    # The deletion must repeat the ORIGINAL attributes. A browser matches a
    # cookie by name + domain + path, so those three have to be identical;
    # samesite/secure are carried through so the deletion is not itself dropped
    # by a policy the original cookie satisfied.
    common = {
        "domain": settings.cookie_domain or None,
        "secure": settings.is_production,
        "samesite": settings.cookie_samesite,
        "httponly": True,
    }
    response.delete_cookie(ACCESS_COOKIE, path="/", **common)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, **common)
    response.delete_cookie(SESSION_HINT_COOKIE, path="/", **common)


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role
    # The audience the presented token carried — the portal it was minted for.
    # The DB-session dependencies gate on this so a token minted for one portal
    # can't be replayed against another (owner vs org vs candidate).
    audience: str | None = None


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
        audience=payload.get("aud"),
    )


def _decode_or_401(token: str, audience: str | list[str]) -> dict:
    """Decode against one audience, or a list (PyJWT accepts any match)."""
    try:
        payload = decode_token(token, audience=audience)
    except pyjwt.PyJWTError as exc:
        raise _unauthorized(str(exc)) from exc
    if payload.get("type") != "access":
        raise _unauthorized("not an access token")
    return payload


# The two "internal" (staff-facing) audiences. get_current_user authenticates
# either; the DB-session dependencies below then gate the specific portal so an
# owner token can't act on org endpoints and vice versa.
_INTERNAL_AUDIENCES = [AUDIENCE_OWNER, AUDIENCE_ORG]


async def get_current_user(request: Request) -> CurrentUser:
    """Staff-facing authentication: accepts an owner (super_admin) OR an org
    token. This only proves the token is a valid internal access token — it does
    NOT decide the portal. Portal separation is enforced downstream:
    `get_superadmin_db` requires the OWNER audience, `get_tenant_db` requires the
    ORG audience. Candidate tokens are rejected here (different audience)."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    return _payload_to_user(_decode_or_401(token, _INTERNAL_AUDIENCES))


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
    """Any portal — used only by /auth/me and /auth/logout."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    try:
        return _payload_to_user(
            _decode_or_401(token, [AUDIENCE_OWNER, AUDIENCE_ORG, AUDIENCE_CANDIDATE])
        )
    except HTTPException:
        raise _unauthorized("invalid session")


# ── DB sessions ──────────────────────────────────────────────────────────────

async def get_tenant_db(
    user: CurrentUser = Depends(get_current_user),
) -> AsyncIterator[AsyncSession]:
    """Tenant-scoped session: RLS var set for the whole request transaction.
    Commits on success, rolls back on exception.

    Org portal only: an owner (or any non-org) token is rejected here so a
    cross-portal token can't reach tenant data (returns 403, never 500)."""
    if user.audience != AUDIENCE_ORG:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org-portal session required",
        )
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
    shortcut.

    Owner portal only: requires BOTH the owner audience (so an org/candidate
    token can never reach the RLS-bypass scope) AND the super_admin role."""
    if user.audience != AUDIENCE_OWNER or user.role != Role.super_admin:
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
    """Dependency factory: 403 unless the caller grants the capability per the
    permission data (user overlay > tenant override > global template > deny).

    Resolved on EVERY privileged request, not cached from login: an HR Head who
    revokes someone's access expects it to take effect now, not whenever that
    person's session happens to expire.
    """

    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_tenant_db),
    ) -> CurrentUser:
        if not await rbac.has_capability(
            session, user.tenant_id, user.role, capability, user.user_id
        ):
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
