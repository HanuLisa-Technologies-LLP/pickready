"""OTP auth endpoints (FR-1.x / ESD §5 / contract rev 2). No passwords,
anywhere. Unified login: one identifier may resolve to several users across
roles/tenants — verify then returns a workspace chooser finalized by
/auth/select-context."""
import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
    create_access_token,
    create_refresh_token,
)
from app.models.enums import OTPChannel
from app.models.user import User
from app.schemas.auth import (
    CandidateRegisterIn,
    CandidateRegisterOut,
    ContextOut,
    MeOut,
    OTPRequestIn,
    OTPRequestOut,
    OTPVerifyIn,
    OTPVerifyOut,
    SelectContextIn,
    UserOut,
)
from app.services import otp as otp_service
from app.services import rbac
from app.services.audit import (
    AUTH_CONTEXT_SELECTED,
    AUTH_LOGIN_SUCCEEDED,
    AUTH_OTP_FAILED,
    record_auth_event,
)

router = APIRouter()


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
