"""Firebase identity exchange, workspace selection and the session lifecycle.

Firebase proves identity; Vivekium remains authoritative for application
roles, tenant isolation, capabilities, and its portal-scoped sessions.
`services/login_context` decides which workspaces a proven identity may enter.

The retired one-time-code login handlers (candidate self-registration, code
request, code verification) sat here without a route for two months after
Firebase took over identity, and were deleted on 2026-09-24 with the schemas
only they used. `tests/test_login_otp_removed.py` keeps them gone.
"""
import uuid
from datetime import datetime, timezone

import jwt as pyjwt
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, or_, select
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import (
    REFRESH_COOKIE,
    CurrentUser,
    clear_auth_cookies,
    get_current_any,
    set_auth_cookies,
)
from app.core.config import get_settings
from app.core.db import get_identity_session, tenant_scope, superadmin_scope
from app.core.security import (
    ALGORITHM,
    AUDIENCE_CANDIDATE,
    AUDIENCE_ORG,
    AUDIENCE_OWNER,
    audience_for_role,
    create_access_token,
    create_refresh_token,
)
from app.models.enums import Role, UserStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import (
    ContextOut,
    FirebaseSessionIn,
    MeOut,
    SelectContextIn,
    SessionOut,
    UserOut,
)
from app.services import auth_sessions, login_context
from app.services import firebase_auth
from app.services import rbac
from app.services.rate_limit import rate_limit
from app.services.audit import (
    AUTH_CONTEXT_SELECTED,
    AUTH_LOGIN_REFUSED,
    AUTH_LOGIN_SUCCEEDED,
    record_auth_event,
)

router = APIRouter()

PORTAL_ROLES = {
    "candidate": {Role.candidate},
    # Every tenant role. `recruitment_manager` and `interview_manager` were
    # both absent here and in `core.security._ORG_ROLES`, which meant a portal
    # choice silently filtered those accounts out of their own sign-in.
    # Asserted against `Role` in tests/test_rbac_conformance.py so the next
    # role added fails a test rather than a login.
    "org": {
        Role.client,
        Role.recruitment_manager,
        Role.hr_manager,
        Role.recruiter,
        Role.hiring_manager,
        Role.interview_manager,
    },
    "bd": {Role.bd},
    "owner": {Role.super_admin},
}


def _filter_requested_portal(users: list[User], requested_portal: str | None) -> list[User]:
    """Apply a login-screen portal choice without ever converting roles.

    Portal intent narrows already-authorised database users. It cannot create a
    BD, owner or customer-team account and therefore cannot be used for
    privilege escalation.
    """
    if not requested_portal:
        return users
    allowed = PORTAL_ROLES[requested_portal]
    return [user for user in users if user.role in allowed]


def _is_owner_email(email: str | None, owner_email: str) -> bool:
    return bool(email) and (email or "").strip().lower() == (owner_email or "").strip().lower()


def _phone_aliases(phone: str | None) -> set[str]:
    """Return safe equivalent forms for matching legacy local phone values.

    Firebase supplies E.164 (``+919...``), while existing development rows
    may contain a ten-digit Indian national number.  This is deliberately a
    lookup aid only; a matched user is normalized to the Firebase E.164 value
    after successful sign-in.
    """
    if not phone:
        return set()
    digits = "".join(char for char in phone if char.isdigit())
    aliases = {phone.strip(), digits}
    if len(digits) == 10:
        aliases.update({f"91{digits}", f"+91{digits}"})
    elif len(digits) == 12 and digits.startswith("91"):
        aliases.update({digits[2:], f"+{digits}"})
    return {value for value in aliases if value}


async def _finalize_single(
    session: AsyncSession,
    response: Response,
    user: User,
    identity: firebase_auth.FirebaseIdentity,
) -> SessionOut:
    """Link a proven Firebase identity to ONE resolved user and issue that
    user's portal-scoped session cookies. Firebase proves the identity; the
    database role/permissions stay authoritative (claude.md rule 2)."""
    if user.status == UserStatus.disabled:
        raise HTTPException(status_code=403, detail="Account unavailable")

    # A BOUND ACCOUNT IS NEVER SILENTLY REBOUND TO A DIFFERENT IDENTITY.
    #
    # This line used to be an unconditional `user.firebase_uid = identity.uid`,
    # and that was account takeover. The caller resolves a user by EMAIL as well
    # as by uid (see the match filters in `firebase_session`), and Firebase
    # email/password signup does not verify the address, so anyone who knew a
    # staff member's email could register it with Firebase, sign in, present the
    # token here, and have this line hand them that person's role and tenant.
    # The victim's uid was overwritten in the same statement, so they lost the
    # account at the moment the attacker gained it.
    #
    # Binding a NULL uid is the legitimate first-login path and still happens.
    # Rebinding a uid that is already set to a different one is not a login, it
    # is a change of who owns the account, and this product already has a
    # deliberate, audited route for that: the Provider's primary-contact rebind,
    # which CLEARS the uid, returns the row to `invited`, revokes the pending
    # invite and sends a fresh one. A silent rebind here bypassed all four of
    # those steps.
    #
    # So the refusal is not a dead end: an account whose Firebase identity
    # genuinely changed is recovered through that route.
    # THE CONDITION IS "UNVERIFIED", NOT "DIFFERENT", AND THE DISTINCTION IS
    # THE WHOLE FIX. A first draft refused every rebind, which is wrong twice:
    # a Google identity always carries a verified email, and control of the
    # address is exactly the proof the email match rests on, so refusing it
    # buys nothing; and the platform OWNER is resolved from a configured email
    # with no administrator above them, so a blanket refusal would lock them
    # out permanently the first time their Firebase uid changed, with no
    # recovery path at all.
    #
    # An UNVERIFIED password identity proves nothing about the address, and
    # that is the case this exists to refuse.
    if (
        user.firebase_uid
        and user.firebase_uid != identity.uid
        and not identity.email_verified
    ):
        await record_auth_event(
            session, action=AUTH_LOGIN_REFUSED, actor_user_id=user.id,
            tenant_id=user.tenant_id,
            metadata={"reason": "firebase_uid_rebind_refused",
                      "provider": identity.provider},
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "This account is already linked to a different sign-in. "
                "Ask your administrator to re-issue your invitation."
            ),
        )

    user.firebase_uid = identity.uid
    user.auth_providers = sorted(set((user.auth_providers or []) + [identity.provider]))
    if identity.email_verified:
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    if identity.provider == "phone" and identity.phone:
        user.phone = identity.phone
        user.phone_verified_at = user.phone_verified_at or datetime.now(timezone.utc)
    # Invited staff/candidates activate on first proven login, mirroring
    # `login_context.select_context` (proving identifier ownership is what
    # flips invited -> active).
    if user.status == UserStatus.invited:
        user.status = UserStatus.active
    await record_auth_event(
        session, action=AUTH_LOGIN_SUCCEEDED, actor_user_id=user.id,
        tenant_id=user.tenant_id,
        metadata={"provider": identity.provider, "via": "firebase"},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        # e.g. this firebase_uid is already bound to a different (filtered-out)
        # row — never surface a 500.
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="This sign-in could not be linked to an account"
        ) from exc
    audience = audience_for_role(user.role)
    await _issue_session(response, user, audience)
    return SessionOut(
        user=await _user_out(session, user),
        capabilities=await _capabilities(session, user),
    )


# Abuse control, not authorization (services/rate_limit). This verifies a
# Firebase token and mints a session: anonymous, and the verification is a
# network call to Google. Cheapest endpoint in the product to hit, one of the
# most expensive to serve.
@router.post("/firebase/session", response_model=SessionOut,
    dependencies=[Depends(rate_limit("auth_exchange", limit=20, window=60))],
)
async def firebase_session(
    body: FirebaseSessionIn,
    response: Response,
    session: AsyncSession = Depends(get_identity_session),
) -> SessionOut:
    """Exchange a verified Firebase ID token for a portal-scoped app session.

    Firebase is identity-only; DB roles/permissions remain authoritative
    (claude.md rule 2). Behaviour matrix:

    - **owner email** (settings.owner_email) -> ALWAYS the seeded super_admin,
      owner-portal cookies, never a new candidate;
    - **single match** -> link firebase_uid, issue that user's portal cookies;
    - **staff pre-seed match by email** -> linked in place, role preserved (no
      duplicate candidate row);
    - **multiple matches** -> workspace chooser: contexts + context_token, NO
      cookies, finalized by /auth/select-context;
    - **no match** -> create a candidate (email and/or phone), candidate cookies.

    Google and email/password sign-in are available to every role; phone-only
    signup remains accepted by the legacy API for existing accounts.
    Every failure is a clean 401/403/409/422 — never a 500.
    """
    settings = get_settings()
    # `firebase_admin`'s client is SYNCHRONOUS and `check_revoked=True`
    # forces a network round trip to the Firebase Admin API on every
    # verification, so calling it directly blocked the event loop for the
    # whole trip, on the busiest path in the product. Every other blocking
    # vendor call in this tree is already offloaded (document_storage,
    # ses_service, email_senders/eligibility); this was the one that was
    # missed. `HTTPException` propagates out of the threadpool unchanged.
    identity = await run_in_threadpool(firebase_auth.verify_id_token, body.id_token)

    # ── Owner invariant (claude.md rule 2 + services/owner.py) ──────────────
    # The platform-owner email resolves ONLY to the seeded super_admin. It is
    # never created as a candidate here, and no NON-owner can reach super_admin
    # via this endpoint (the general lookup below only ever creates candidates,
    # and eligible_login_users / the ORM guard reject any impostor super_admin).
    if _is_owner_email(identity.email, settings.owner_email):
        if body.requested_portal not in (None, "owner"):
            raise HTTPException(
                status_code=403,
                detail=f"No {body.requested_portal} workspace is linked to this account",
            )
        # Owner is an internal role resolved only by the configured owner email.
        firebase_auth.assert_provider_allowed(identity, Role.super_admin.value)
        owner = (await session.execute(
            select(User).where(
                User.role == Role.super_admin,
                func.lower(User.email) == identity.email.strip().lower(),
            )
        )).scalars().first()
        if owner is None:
            # Never fabricate the owner from a Firebase login — the account is
            # provisioned by the seed, not by self-service.
            raise HTTPException(status_code=403, detail="Owner account is not provisioned")
        return await _finalize_single(session, response, owner, identity)

    # ── Resolve the identity to existing users (unified login, rev 2) ───────
    match_filters = [User.firebase_uid == identity.uid]
    if identity.email:
        match_filters.append(func.lower(User.email) == identity.email.strip().lower())
    if aliases := _phone_aliases(identity.phone):
        match_filters.append(User.phone.in_(aliases))
    matched = (await session.execute(
        select(User).where(or_(*match_filters)).order_by(User.created_at, User.id)
    )).scalars().all()

    if matched:
        eligible = login_context.eligible_login_users(
            matched, owner_email=settings.owner_email
        )
        eligible = _filter_requested_portal(eligible, body.requested_portal)
        if not eligible:
            # Every match is disabled or an impostor super_admin — do NOT mint a
            # duplicate candidate over the top of an existing (unusable) account.
            detail = (
                f"No {body.requested_portal} workspace is linked to this account"
                if body.requested_portal
                else "Account unavailable"
            )
            raise HTTPException(status_code=403, detail=detail)
    else:
        # First-ever sign-in for this identity -> a fresh candidate (rule 2:
        # candidates may use Google / email / phone). Phone-only signup allowed.
        if body.requested_portal not in (None, "candidate"):
            raise HTTPException(
                status_code=403,
                detail=f"No {body.requested_portal} workspace is linked to this account",
            )
        firebase_auth.assert_provider_allowed(identity, Role.candidate.value)
        if not identity.email and not identity.phone:
            raise HTTPException(
                status_code=422,
                detail="An email address or phone number is required to create a candidate profile",
            )
        user = User(
            role=Role.candidate, email=identity.email, phone=identity.phone,
            full_name=identity.name, tenant_id=None, status=UserStatus.active,
            firebase_uid=identity.uid, auth_providers=[identity.provider],
        )
        session.add(user)
        await session.flush()
        from app.models.candidate import Candidate
        # NO CONSENT IS WRITTEN HERE, DELIBERATELY. Vivekium feature 6 asks
        # for Stage A "at registration, before candidate profile creation
        # completes", and this is a Firebase sign-in: the candidate has seen
        # no consent wording and ticked nothing, so a row written here would
        # record an agreement that never happened. Profile creation COMPLETES
        # at PUT /portal/me/profile-form, which is where the items are shown,
        # ticked individually and stamped, and which refuses to report a
        # profile complete while either of them is outstanding.
        session.add(Candidate(
            tenant_id=None, user_id=user.id, email=user.email, phone=user.phone,
            full_name=user.full_name,
        ))
        eligible = [user]

    # ── Provider gate on every resolved context ─────────────────────────────
    for user in eligible:
        firebase_auth.assert_provider_allowed(identity, user.role.value)

    # Phone numbers are a single-person credential.  A reused phone number in
    # imported data must never become a cross-person workspace chooser.
    if identity.provider == "phone" and len(eligible) > 1:
        raise HTTPException(
            status_code=409,
            detail="This phone number is linked to multiple accounts. Sign in with email and password.",
        )

    if len(eligible) == 1:
        return await _finalize_single(session, response, eligible[0], identity)

    # ── Multiple workspaces -> chooser, NO cookies ──────────────────────────
    # firebase_uid is unique, so it can bind to only one user; it is NOT linked
    # here — the chosen workspace is finalized via /auth/select-context, which
    # (after this proven verification) mints cookies for the picked user_id.
    contexts = await login_context.build_contexts(session, eligible)
    token = login_context.make_context_token(
        identity.email or identity.phone, [c.user_id for c in contexts]
    )
    await session.commit()
    return SessionOut(
        contexts=[_context_out(c) for c in contexts],
        context_token=token,
    )


async def _user_out(session: AsyncSession, user: User) -> UserOut:
    if user.tenant_id is not None:
        async with tenant_scope(session, user.tenant_id):
            workspace_name = (
                await session.execute(
                    select(Tenant.name).where(Tenant.id == user.tenant_id)
                )
            ).scalar_one_or_none() or "Unknown workspace"
    elif user.role == Role.candidate:
        workspace_name = "Candidate workspace"
    else:
        workspace_name = "Vivekium"
    return UserOut(
        id=user.id,
        role=user.role,
        tenant_id=user.tenant_id,
        full_name=user.full_name,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        phone_verified=user.phone_verified_at is not None,
        password_enabled="password" in (user.auth_providers or []),
        workspace_name=workspace_name,
    )


async def _capabilities(session: AsyncSession, user: User) -> list[str]:
    """Capability list for auth responses. Tenant-scoped so the RLS policy on
    role_permissions exposes this tenant's override rows (claude.md rule 1).

    `user_id` is passed so the response is the EFFECTIVE set for this person —
    role defaults with their HR Head overlay applied (spec §7.1) — rather than
    the generic set for their role. The frontend hides actions from this list,
    so a stale answer here would show buttons that then 403.
    """
    if user.tenant_id is None:
        async with superadmin_scope(session):
            return await rbac.capabilities_for_user(
                session, role=user.role, tenant_id=None, user_id=user.id
            )
    async with tenant_scope(session, user.tenant_id):
        return await rbac.capabilities_for_user(
            session, role=user.role, tenant_id=user.tenant_id, user_id=user.id
        )


# Cookie writing is centralized in deps.set_auth_cookies / clear_auth_cookies
# (SameSite=Strict, httponly, secure-in-prod, refresh path-scoped) — a single
# source of truth next to the cookie-name constants.
_set_auth_cookies = set_auth_cookies


async def _issue_session(response: Response, user: User, audience: str) -> None:
    """Create the server record before exposing its signed browser cookies."""
    sid = auth_sessions.new_id()
    access = create_access_token(
        user.id, user.role.value, user.tenant_id, audience=audience,
        session_id=sid,
    )
    refresh_token = create_refresh_token(user.id, audience=audience, session_id=sid)
    jti = pyjwt.decode(
        refresh_token, get_settings().jwt_secret,
        algorithms=[ALGORITHM], audience=audience,
    )["jti"]
    await auth_sessions.create(sid, user.id, jti, refresh_token)
    _set_auth_cookies(response, access, refresh_token)


def _context_out(ctx: login_context.LoginContext) -> ContextOut:
    return ContextOut(
        user_id=ctx.user_id, role=ctx.role, tenant_id=ctx.tenant_id,
        tenant_name=ctx.tenant_name, portal=ctx.portal,
    )


@router.post("/workspaces", response_model=SessionOut)
async def available_workspaces(
    current: CurrentUser = Depends(get_current_any),
    session: AsyncSession = Depends(get_identity_session),
) -> SessionOut:
    """Return the workspaces belonging to the current proven identity.

    This is the in-session counterpart to the login chooser. The access token
    proves the current identity; the returned short-lived, single-use context
    token is still required to finalize a selection. No tenant data is returned
    here, only identity rows and their human-readable workspace labels.
    """
    source = await session.get(User, current.user_id)
    if source is None or source.status == UserStatus.disabled:
        raise HTTPException(status_code=401, detail="Account unavailable")
    identifier = source.email or source.phone
    if not identifier:
        raise HTTPException(
            status_code=409,
            detail="This account has no verified identifier for workspace switching",
        )

    eligible = login_context.eligible_login_users(
        await login_context.find_users(session, identifier),
        owner_email=get_settings().owner_email,
    )
    contexts = await login_context.build_contexts(session, eligible)
    token = login_context.make_context_token(
        identifier,
        [context.user_id for context in contexts],
        source_user_id=source.id,
    )
    return SessionOut(
        contexts=[_context_out(context) for context in contexts],
        context_token=token,
    )


@router.post("/select-context", response_model=SessionOut)
async def select_context(
    body: SelectContextIn,
    response: Response,
    session: AsyncSession = Depends(get_identity_session),
) -> SessionOut:
    """Exchange a context_token (proof of a verified identity) for cookies as
    one of the identifier's users (contract rev 2). Single-use."""
    try:
        result = await login_context.select_context(
            session, context_token=body.context_token, user_id=body.user_id
        )
    except login_context.ContextTokenConsumed as exc:
        raise HTTPException(status_code=410, detail="Context token already used") from exc
    except login_context.ContextTokenInvalid as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except login_context.ContextUserMismatch as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except login_context.UserNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except login_context.ContextStoreUnavailable as exc:
        # FAILS CLOSED, the `auth_sessions` posture: a token that cannot be
        # marked used must not be exchanged, or it becomes replayable.
        raise HTTPException(
            status_code=503,
            detail="Sign-in is briefly unavailable. Please try again in a moment.",
        ) from exc

    user = result.user
    token_payload = login_context.decode_context_token(body.context_token)
    selected_workspace = await _user_out(session, user)
    await record_auth_event(
        session, action=AUTH_CONTEXT_SELECTED,
        actor_user_id=user.id, tenant_id=user.tenant_id,
        metadata={
            "role": user.role.value,
            "workspace_name": selected_workspace.workspace_name,
            "selected_tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "source_user_id": token_payload.get("source_user_id"),
        },
    )
    await session.commit()
    await _issue_session(response, user, audience_for_role(user.role))
    return SessionOut(
        user=selected_workspace,
        capabilities=await _capabilities(session, user),
    )


def _dead_session(detail: str) -> JSONResponse:
    """A definite "this refresh token cannot be used again" answer.

    Returned, not raised. Raising an HTTPException discards the injected
    Response and with it the Set-Cookie headers, so the clearing silently never
    happened; a plain JSONResponse carries them.

    Every auth cookie is cleared on the way out, including the `pr_session`
    presence hint. Leaving the hint behind would let the Next.js middleware keep
    admitting the browser to portal routes that can only fail, so the user would
    bounce between a blank page and a broken session instead of landing on the
    login screen once.
    """
    dead = JSONResponse(status_code=401, content={"detail": detail})
    clear_auth_cookies(dead)
    return dead


# Abuse control, not authorization (services/rate_limit). A refresh token is a
# long random JWT and is not brute forceable in practice, so this is not about
# guessing one: it is that an unthrottled endpoint doing a database round trip
# per call is a resource-exhaustion vector, and this was the one auth route the
# sweep that added `auth_exchange` missed.
#
# The window is deliberately generous. A legitimate browser refreshes roughly
# once per access-token lifetime (fifteen minutes), and several tabs of one
# session can refresh at once after a laptop wakes, so a tight limit here would
# sign real people out. Thirty per minute is far above that and far below a
# useful flood. Now that `client_identifier` resolves a verified subject, an
# authenticated caller gets their own bucket rather than sharing their office
# address with every colleague.
@router.post(
    "/refresh",
    dependencies=[Depends(rate_limit("auth_refresh", limit=30, window=60))],
)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_identity_session),
):
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        return _dead_session("Missing refresh token")
    payload = None
    # A refresh token is minted for exactly one portal audience (owner / org /
    # candidate); try each so any valid session refreshes, but the reissued
    # tokens keep the SAME audience — no cross-portal escalation.
    for audience in (AUDIENCE_OWNER, AUDIENCE_ORG, AUDIENCE_CANDIDATE):
        try:
            payload = pyjwt.decode(
                token, get_settings().jwt_secret, algorithms=[ALGORITHM], audience=audience
            )
            break
        except pyjwt.InvalidAudienceError:
            continue
        except pyjwt.PyJWTError:
            return _dead_session("Invalid refresh token")
    if payload is None or payload.get("type") != "refresh" or not payload.get("sid") or not payload.get("jti"):
        return _dead_session("Invalid refresh token")

    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or user.status.value == "disabled":
        return _dead_session("Account unavailable")

    # Rotate BOTH tokens on every refresh (refresh-token rotation), preserving
    # the token's audience.
    access = create_access_token(
        user.id, user.role.value, user.tenant_id, audience=payload["aud"],
        session_id=payload["sid"],
    )
    refresh_candidate = create_refresh_token(
        user.id, audience=payload["aud"], session_id=payload["sid"]
    )
    new_jti = pyjwt.decode(
        refresh_candidate, get_settings().jwt_secret,
        algorithms=[ALGORITHM], audience=payload["aud"],
    )["jti"]
    refresh_token = await auth_sessions.rotate(
        payload["sid"], user.id, payload["jti"], new_jti, refresh_candidate,
    )
    if refresh_token is None:
        return _dead_session("Session expired or revoked")
    set_auth_cookies(response, access, refresh_token)
    return {"refreshed": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    for name in (REFRESH_COOKIE, "pr_access"):
        token = request.cookies.get(name)
        if not token:
            continue
        try:
            payload = pyjwt.decode(
                token, get_settings().jwt_secret, algorithms=[ALGORITHM],
                audience=[AUDIENCE_OWNER, AUDIENCE_ORG, AUDIENCE_CANDIDATE],
                options={"verify_exp": False},
            )
        except pyjwt.PyJWTError:
            continue
        if payload.get("sid") and payload.get("sub"):
            await auth_sessions.revoke(payload["sid"], payload["sub"])
            break
    clear_auth_cookies(response)
    return {"logged_out": True}


@router.post("/password-changed")
async def password_changed(
    body: FirebaseSessionIn,
    response: Response,
    session: AsyncSession = Depends(get_identity_session),
) -> dict:
    """Fresh Firebase proof revokes app sessions even if this cookie expired."""
    identity = await run_in_threadpool(firebase_auth.verify_id_token, body.id_token)
    users = (await session.execute(
        select(User.id).where(User.firebase_uid == identity.uid)
    )).scalars().all()
    if not users:
        raise HTTPException(status_code=401, detail="Unknown account")
    for user_id in users:
        await auth_sessions.revoke_all(user_id)
    clear_auth_cookies(response)
    return {"sessions_revoked": True}


@router.get("/me", response_model=MeOut)
async def me(
    current: CurrentUser = Depends(get_current_any),
    session: AsyncSession = Depends(get_identity_session),
) -> MeOut:
    user = await session.get(User, current.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return MeOut(
        user=await _user_out(session, user),
        capabilities=await _capabilities(session, user),
    )
