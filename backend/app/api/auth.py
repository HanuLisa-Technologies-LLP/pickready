"""OTP auth endpoints (FR-1.x / ESD §5 / contract rev 2). No passwords,
anywhere. Unified login: one identifier may resolve to several users across
roles/tenants — verify then returns a workspace chooser finalized by
/auth/select-context."""
import uuid
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, or_, select
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
from app.core.db import get_session, tenant_scope
from app.core.security import (
    ALGORITHM,
    AUDIENCE_CANDIDATE,
    AUDIENCE_ORG,
    AUDIENCE_OWNER,
    audience_for_role,
    create_access_token,
    create_refresh_token,
)
from app.models.enums import OTPChannel, Role, UserStatus
from app.models.user import User
from app.schemas.auth import (
    CandidateRegisterIn,
    CandidateRegisterOut,
    ContextOut,
    FirebaseSessionIn,
    MeOut,
    OTPRequestIn,
    OTPRequestOut,
    OTPVerifyIn,
    OTPVerifyOut,
    SelectContextIn,
    UserOut,
)
from app.services import otp as otp_service
from app.services import firebase_auth
from app.services import rbac
from app.services.audit import (
    AUTH_CONTEXT_SELECTED,
    AUTH_LOGIN_SUCCEEDED,
    AUTH_OTP_FAILED,
    record_auth_event,
)

router = APIRouter()


def _is_owner_email(email: str | None, owner_email: str) -> bool:
    return bool(email) and (email or "").strip().lower() == (owner_email or "").strip().lower()


async def _finalize_single(
    session: AsyncSession,
    response: Response,
    user: User,
    identity: firebase_auth.FirebaseIdentity,
) -> OTPVerifyOut:
    """Link a proven Firebase identity to ONE resolved user and issue that
    user's portal-scoped session cookies. Firebase proves the identity; the
    database role/permissions stay authoritative (claude.md rule 2)."""
    if user.status == UserStatus.disabled:
        raise HTTPException(status_code=403, detail="Account unavailable")
    user.firebase_uid = identity.uid
    user.auth_providers = sorted(set((user.auth_providers or []) + [identity.provider]))
    if identity.email_verified:
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    # Invited staff/candidates activate on first proven login, mirroring the OTP
    # path (proving identifier ownership is what flips invited -> active).
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
    _set_auth_cookies(
        response,
        create_access_token(user.id, user.role.value, user.tenant_id, audience=audience),
        create_refresh_token(user.id, audience=audience),
    )
    return OTPVerifyOut(user=_user_out(user), capabilities=await _capabilities(session, user))


@router.post("/firebase/session", response_model=OTPVerifyOut)
async def firebase_session(
    body: FirebaseSessionIn, response: Response, session: AsyncSession = Depends(get_session)
) -> OTPVerifyOut:
    """Exchange a verified Firebase ID token for a portal-scoped app session.

    Firebase is identity-only; DB roles/permissions remain authoritative
    (claude.md rule 2). Behaviour matrix:

    - **owner email** (settings.owner_email) -> ALWAYS the seeded super_admin,
      owner-portal cookies, never a new candidate, not subject to the
      candidates-only Google gate (fixed, client-controlled identity);
    - **single match** -> link firebase_uid, issue that user's portal cookies;
    - **staff pre-seed match by email** -> linked in place, role preserved (no
      duplicate candidate row);
    - **multiple matches** -> workspace chooser: contexts + context_token, NO
      cookies, finalized by /auth/select-context (same as the OTP path);
    - **no match** -> create a candidate (email and/or phone), candidate cookies.

    Google sign-in is candidates-only; phone-only signup (email null) is allowed.
    Every failure is a clean 401/403/409/422 — never a 500.
    """
    settings = get_settings()
    identity = firebase_auth.verify_id_token(body.id_token)

    # ── Owner invariant (claude.md rule 2 + services/owner.py) ──────────────
    # The platform-owner email resolves ONLY to the seeded super_admin. It is
    # never created as a candidate here, and no NON-owner can reach super_admin
    # via this endpoint (the general lookup below only ever creates candidates,
    # and eligible_login_users / the ORM guard reject any impostor super_admin).
    if _is_owner_email(identity.email, settings.owner_email):
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
    matched = (await session.execute(select(User).where(or_(
        User.firebase_uid == identity.uid,
        User.email == identity.email if identity.email else False,
        User.phone == identity.phone if identity.phone else False,
    )).order_by(User.created_at, User.id))).scalars().all()

    if matched:
        eligible = otp_service.eligible_login_users(
            matched, owner_email=settings.owner_email
        )
        if not eligible:
            # Every match is disabled or an impostor super_admin — do NOT mint a
            # duplicate candidate over the top of an existing (unusable) account.
            raise HTTPException(status_code=403, detail="Account unavailable")
    else:
        # First-ever sign-in for this identity -> a fresh candidate (rule 2:
        # candidates may use Google / email / phone). Phone-only signup allowed.
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
        session.add(Candidate(
            tenant_id=None, user_id=user.id, email=user.email, phone=user.phone,
            full_name=user.full_name,
        ))
        eligible = [user]

    # ── Provider gate on every resolved context (Google = candidates only) ──
    for user in eligible:
        firebase_auth.assert_provider_allowed(identity, user.role.value)

    if len(eligible) == 1:
        return await _finalize_single(session, response, eligible[0], identity)

    # ── Multiple workspaces -> chooser, NO cookies (same as the OTP path) ───
    # firebase_uid is unique, so it can bind to only one user; it is NOT linked
    # here — the chosen workspace is finalized via /auth/select-context, which
    # (after this proven verification) mints cookies for the picked user_id.
    contexts = await otp_service._build_contexts(session, eligible)
    token = otp_service.make_context_token(
        identity.email or identity.phone, [c.user_id for c in contexts]
    )
    await session.commit()
    return OTPVerifyOut(
        contexts=[_context_out(c) for c in contexts],
        context_token=token,
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        role=user.role,
        tenant_id=user.tenant_id,
        full_name=user.full_name,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        phone_verified=user.phone_verified_at is not None,
    )


async def _capabilities(session: AsyncSession, user: User) -> list[str]:
    """Capability list for auth responses. Tenant-scoped so the RLS policy on
    role_permissions exposes this tenant's override rows (claude.md rule 1)."""
    if user.tenant_id is None:
        return await rbac.capabilities_for_user(
            session, role=user.role, tenant_id=None
        )
    async with tenant_scope(session, user.tenant_id):
        return await rbac.capabilities_for_user(
            session, role=user.role, tenant_id=user.tenant_id
        )


# Cookie writing is centralized in deps.set_auth_cookies / clear_auth_cookies
# (SameSite=Strict, httponly, secure-in-prod, refresh path-scoped) — a single
# source of truth next to the cookie-name constants.
_set_auth_cookies = set_auth_cookies


@router.post(
    "/register-candidate",
    response_model=CandidateRegisterOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_candidate(
    body: CandidateRegisterIn, session: AsyncSession = Depends(get_session)
) -> CandidateRegisterOut:
    """Candidate self sign-up (FR-9.1, register first / log in later). Creates
    the account only — the candidate then signs in from the unified login via
    OTP. No password anywhere (claude.md rule 2)."""
    try:
        candidate = await otp_service.register_candidate(
            session,
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
        )
    except otp_service.AlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return CandidateRegisterOut(candidate_id=candidate.id, email=candidate.email)


@router.post("/otp/request", response_model=OTPRequestOut)
async def request_otp(
    body: OTPRequestIn, session: AsyncSession = Depends(get_session)
) -> OTPRequestOut:
    audience = AUDIENCE_CANDIDATE if body.audience == "candidate" else AUDIENCE_ORG
    try:
        result = await otp_service.request_otp(
            session,
            identifier=body.identifier,
            channel=OTPChannel(body.channel),
            audience=audience,
        )
    except otp_service.UserNotFound as exc:
        raise HTTPException(status_code=404, detail="No account found for this email or phone") from exc
    except otp_service.OTPLocked as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts — please try again in 15 minutes",
        ) from exc
    except otp_service.OTPResendThrottled as exc:
        raise HTTPException(
            status_code=429,
            detail="Please wait 30 seconds before requesting another code",
        ) from exc
    except otp_service.OTPRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many code requests — please try again later",
        ) from exc
    await session.commit()
    settings = get_settings()
    return OTPRequestOut(
        challenge_id=result.challenge.id,
        channels_sent=result.channels_sent,
        # Dev-only: expose the code in the response so local flows are testable
        # without a mail/SMS provider. NEVER in production, never logged.
        debug_code=result.code if settings.environment == "development" else None,
    )


def _context_out(ctx: otp_service.LoginContext) -> ContextOut:
    return ContextOut(
        user_id=ctx.user_id, role=ctx.role, tenant_id=ctx.tenant_id,
        tenant_name=ctx.tenant_name, portal=ctx.portal,
    )


@router.post("/otp/verify", response_model=OTPVerifyOut)
async def verify_otp(
    body: OTPVerifyIn, response: Response, session: AsyncSession = Depends(get_session)
) -> OTPVerifyOut:
    try:
        result = await otp_service.verify_challenge(
            session, challenge_id=body.challenge_id, code=body.code
        )
    except otp_service.ChallengeNotFound as exc:
        raise HTTPException(status_code=404, detail="Unknown challenge") from exc
    except otp_service.OTPLocked as exc:
        await session.commit()  # persist any attempt counter written before lock
        raise HTTPException(
            status_code=429,
            detail="Too many attempts — please try again in 15 minutes",
        ) from exc
    except otp_service.OTPExpired as exc:
        raise HTTPException(
            status_code=410, detail="This code has expired — request a new one"
        ) from exc
    except otp_service.OTPConsumed as exc:
        raise HTTPException(
            status_code=410, detail="This code has already been used"
        ) from exc
    except otp_service.OTPInvalid as exc:
        await record_auth_event(
            session, action=AUTH_OTP_FAILED,
            metadata={"challenge_id": str(body.challenge_id),
                      "attempts_remaining": exc.attempts_remaining},
        )
        await session.commit()  # persist the incremented attempt count
        raise HTTPException(status_code=401, detail="Invalid code") from exc
    except otp_service.UserNotFound as exc:
        raise HTTPException(
            status_code=404, detail="No account found for this email or phone"
        ) from exc

    if result.authenticated:
        await record_auth_event(
            session, action=AUTH_LOGIN_SUCCEEDED,
            actor_user_id=result.user.id, tenant_id=result.user.tenant_id,
            metadata={"role": result.user.role.value},
        )
    await session.commit()
    if result.multi_context:
        # NO cookies — the caller picks a workspace via /auth/select-context.
        return OTPVerifyOut(
            contexts=[_context_out(c) for c in result.contexts],
            context_token=result.context_token,
        )
    if result.authenticated:
        _set_auth_cookies(response, result.access_token, result.refresh_token)
        return OTPVerifyOut(
            user=_user_out(result.user),
            capabilities=await _capabilities(session, result.user),
        )
    # Client first-login dual OTP still pending — no cookies yet (FR-1.2).
    return OTPVerifyOut(
        user=_user_out(result.user), pending_channels=result.pending_channels
    )


@router.post("/select-context", response_model=OTPVerifyOut)
async def select_context(
    body: SelectContextIn, response: Response, session: AsyncSession = Depends(get_session)
) -> OTPVerifyOut:
    """Exchange a context_token (proof of OTP success) for cookies as one of
    the identifier's users (contract rev 2). Single-use."""
    try:
        result = await otp_service.select_context(
            session, context_token=body.context_token, user_id=body.user_id
        )
    except otp_service.ContextTokenConsumed as exc:
        raise HTTPException(status_code=410, detail="Context token already used") from exc
    except otp_service.ContextTokenInvalid as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except otp_service.ContextUserMismatch as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except otp_service.UserNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if result.authenticated:
        await record_auth_event(
            session, action=AUTH_CONTEXT_SELECTED,
            actor_user_id=result.user.id, tenant_id=result.user.tenant_id,
            metadata={"role": result.user.role.value},
        )
        await session.commit()
        _set_auth_cookies(response, result.access_token, result.refresh_token)
        return OTPVerifyOut(
            user=_user_out(result.user),
            capabilities=await _capabilities(session, result.user),
        )
    await session.commit()
    # Client first-login dual OTP still pending for this workspace.
    return OTPVerifyOut(
        user=_user_out(result.user), pending_channels=result.pending_channels
    )


@router.post("/refresh")
async def refresh(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
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
        except pyjwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or user.status.value == "disabled":
        raise HTTPException(status_code=401, detail="Account unavailable")

    # Rotate BOTH tokens on every refresh (refresh-token rotation), preserving
    # the token's audience.
    access = create_access_token(
        user.id, user.role.value, user.tenant_id, audience=payload["aud"]
    )
    refresh_token = create_refresh_token(user.id, audience=payload["aud"])
    set_auth_cookies(response, access, refresh_token)
    return {"refreshed": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    clear_auth_cookies(response)
    return {"logged_out": True}


@router.get("/me", response_model=MeOut)
async def me(
    current: CurrentUser = Depends(get_current_any),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    user = await session.get(User, current.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return MeOut(user=_user_out(user), capabilities=await _capabilities(session, user))
