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
import logging
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory, superadmin_scope, tenant_scope
from app.core.security import (
    AUDIENCE_CANDIDATE,
    AUDIENCE_ORG,
    AUDIENCE_OWNER,
    decode_token,
)
from app.models.enums import Role
from app.services import auth_sessions, rbac
from app.services.audit import audit

logger = logging.getLogger(__name__)

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
# It originally covered the gap after an access cookie's Max-Age expired, while
# the path-scoped refresh cookie was invisible to the Next.js route gate. Both
# access and hint are now browser-session cookies. The hint remains for clients
# already using it, and carries no authentication authority.
#
# The hint cookie lives for the browser session, is path "/", stays
# HttpOnly (Next middleware reads request cookies server-side, so it never needs
# JavaScript access), and its value is a constant. Knowing it grants nothing.
SESSION_HINT_COOKIE = "pr_session"
SESSION_HINT_VALUE = "1"

# ── Real user activity ───────────────────────────────────────────────────────
# The ONE signal that renews the thirty-minute idle deadline. The browser sends
# `X-User-Activity: 1` only within a few seconds of a real pointer, key or touch
# event (`frontend/lib/user-activity.ts`), so a request fired by a timer, a
# poll or a refresh that repairs a poll's 401 carries no header and renews
# nothing. See `services/auth_sessions` for why "every request renews" was the
# bug. The value is a constant, not a claim about identity: forging it only
# keeps the forger's OWN session alive, which a real click would also do.
ACTIVITY_HEADER = "X-User-Activity"
ACTIVITY_HEADER_VALUE = "1"


def is_user_activity(request) -> bool:
    """Whether this request says a person just interacted with the page."""
    return request.headers.get(ACTIVITY_HEADER) == ACTIVITY_HEADER_VALUE


# ── Cookie hardening (single source of truth) ────────────────────────────────
# SameSite=Strict + httponly + secure-in-prod for both auth cookies.
# ASSUMPTION: secure is disabled in development so the cookies work over plain
# http://localhost; it is forced on in production. SameSite=Strict is acceptable
# because this is a first-party SPA (no cross-site POST-back flow needs the
# cookie). JWT access expires in fifteen minutes; Redis expires the session
# after thirty minutes idle, regardless of browser cookie lifetime. Refresh
# rotation is checked atomically by the server-side session store.


def _cookie_kwargs() -> dict:
    """Shared attributes for every auth cookie, from settings (one source)."""
    from app.core.config import get_settings  # local: avoid import cycle at module load

    settings = get_settings()
    kwargs = {
        "httponly": True,
        # `Secure` follows the ORIGIN, not the environment name. See
        # `Settings.serves_over_https`: tying it to `is_production` sent the
        # auth cookie without it from every non-production HTTPS deployment.
        "secure": settings.serves_over_https,
        "samesite": settings.cookie_samesite,
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs


def set_access_cookie(response, access: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access,
        path="/", **_cookie_kwargs(),
    )


def set_auth_cookies(response, access: str, refresh: str) -> None:
    """Set three browser-session cookies on login and refresh."""
    set_access_cookie(response, access)
    response.set_cookie(
        REFRESH_COOKIE, refresh,
        path=REFRESH_COOKIE_PATH, **_cookie_kwargs(),
    )
    response.set_cookie(
        SESSION_HINT_COOKIE, SESSION_HINT_VALUE,
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
        # `Secure` follows the ORIGIN, not the environment name. See
        # `Settings.serves_over_https`: tying it to `is_production` sent the
        # auth cookie without it from every non-production HTTPS deployment.
        "secure": settings.serves_over_https,
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


async def _authenticated_user(
    request: Request, token: str, audience: str | list[str]
) -> CurrentUser:
    payload = _decode_or_401(token, audience)
    user = _payload_to_user(payload)
    sid = payload.get("sid")
    if sid:
        if not await auth_sessions.validate(
            sid, user.user_id, touch=is_user_activity(request)
        ):
            raise _unauthorized("Session expired or revoked")
    elif request.cookies.get(ACCESS_COOKIE) == token:
        # Existing cookie JWTs without a server record cannot be revoked.
        # Explicit Authorization bearer tokens remain for service clients.
        raise _unauthorized("Session expired or revoked")
    return user


async def authenticate_socket_token(
    token: str | None, audience: str
) -> CurrentUser | None:
    """The principal behind a WebSocket's access token, or None.

    The REST dependencies above raise; a socket handler closes with a policy
    code instead, so this answers None for every refusal. It applies the SAME
    session rule as `_authenticated_user`, which the conversation socket used
    to skip: it decoded the JWT and never asked the session store, so a signed
    out or revoked session kept streaming until the fifteen-minute token ran
    out. A socket token MUST carry a `sid`: only browsers open sockets, and a
    browser token without one is the unrevocable legacy cookie the REST path
    already refuses.

    `touch=False`, always. An open socket is the most passive thing a tab can
    do, and letting it renew would be the polling bug by another door.
    """
    if not token:
        return None
    try:
        payload = decode_token(token, audience=audience)
    except pyjwt.PyJWTError:
        return None
    if payload.get("type") != "access" or not payload.get("sid"):
        return None
    try:
        user = _payload_to_user(payload)
    except (KeyError, ValueError):
        return None
    try:
        live = await auth_sessions.validate(payload["sid"], user.user_id, touch=False)
    except HTTPException as exc:
        # The session store could not answer (503). A socket REFUSES rather
        # than falling open, the posture every REST route takes; logged so an
        # outage is not read as a wave of signed-out users.
        logger.warning(
            "socket_session_check_unavailable status=%s", exc.status_code
        )
        return None
    if not live:
        return None
    return user


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
    return await _authenticated_user(request, token, _INTERNAL_AUDIENCES)


async def get_current_candidate(request: Request) -> CurrentUser:
    """Candidate-portal audience — distinct JWT session scope (ESD §13)."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    user = await _authenticated_user(request, token, AUDIENCE_CANDIDATE)
    if user.role != Role.candidate:
        raise _unauthorized("candidate session required")
    return user


async def get_optional_candidate(request: Request) -> CurrentUser | None:
    """The candidate session if there is one, otherwise None -- never a 401.

    Exactly one kind of endpoint needs this: a tokenized link that has to
    answer "is this person signed in, and are they the RIGHT person?" in a
    single call. Requiring auth would make the unauthenticated case a 401 the
    page cannot distinguish from an expired link; making it public would leave
    the wrong-account check with nothing to compare against.

    A malformed or expired cookie is treated as absent rather than as an error,
    for the same reason: the page's job is to send them to sign in, and a
    stale cookie is the most ordinary way to arrive here.
    """
    token = _extract_token(request)
    if not token:
        return None
    try:
        user = await _authenticated_user(request, token, AUDIENCE_CANDIDATE)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise
    if user.role != Role.candidate:
        return None
    return user


async def get_current_any(request: Request) -> CurrentUser:
    """Any portal — used only by /auth/me and /auth/logout."""
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    try:
        return await _authenticated_user(
            request, token, [AUDIENCE_OWNER, AUDIENCE_ORG, AUDIENCE_CANDIDATE]
        )
    except HTTPException as exc:
        if exc.status_code == 401:
            raise _unauthorized("invalid session") from exc
        raise


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

