"""OTP auth endpoints (FR-1.x / ESD §5). No passwords, anywhere."""
import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    get_current_any,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import (
    ALGORITHM,
    AUDIENCE_CANDIDATE,
    AUDIENCE_INTERNAL,
    create_access_token,
)
from app.models.enums import OTPChannel
from app.models.user import User
from app.schemas.auth import (
    MeOut,
    OTPRequestIn,
    OTPRequestOut,
    OTPVerifyIn,
    OTPVerifyOut,
    UserOut,
)
from app.services import otp as otp_service

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


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    secure = settings.is_production
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax",
        max_age=settings.jwt_access_ttl_minutes * 60, path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=secure, samesite="lax",
        max_age=settings.jwt_refresh_ttl_days * 86400, path="/api/v1/auth",
    )


@router.post("/otp/request", response_model=OTPRequestOut)
async def request_otp(
    body: OTPRequestIn, session: AsyncSession = Depends(get_session)
) -> OTPRequestOut:
    audience = AUDIENCE_CANDIDATE if body.audience == "candidate" else AUDIENCE_INTERNAL
    try:
        result = await otp_service.request_otp(
            session,
            identifier=body.identifier,
            channel=OTPChannel(body.channel),
            audience=audience,
        )
    except otp_service.UserNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except otp_service.OTPLocked as exc:
        raise HTTPException(status_code=429, detail="Too many failed attempts — try again later") from exc
    except otp_service.OTPRateLimited as exc:
        raise HTTPException(status_code=429, detail="Too many OTP requests — try again later") from exc
    await session.commit()
    settings = get_settings()
    return OTPRequestOut(
        challenge_id=result.challenge.id,
        # Dev-only: expose the code in the response so local flows are testable
        # without a mail/SMS provider. NEVER in production, never logged.
        debug_code=result.code if settings.environment == "development" else None,
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
        raise HTTPException(status_code=429, detail="Too many failed attempts — try again later") from exc
    except (otp_service.OTPExpired, otp_service.OTPConsumed) as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except otp_service.OTPInvalid as exc:
        await session.commit()  # persist the incremented attempt count
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except otp_service.UserNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await session.commit()
    if result.authenticated:
        _set_auth_cookies(response, result.access_token, result.refresh_token)
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
    for audience in (AUDIENCE_INTERNAL, AUDIENCE_CANDIDATE):
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

    access = create_access_token(
        user.id, user.role.value, user.tenant_id, audience=payload["aud"]
    )
    settings = get_settings()
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=settings.is_production,
        samesite="lax", max_age=settings.jwt_access_ttl_minutes * 60, path="/",
    )
    return {"refreshed": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return {"logged_out": True}


@router.get("/me", response_model=MeOut)
async def me(
    current: CurrentUser = Depends(get_current_any),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    user = await session.get(User, current.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return MeOut(user=_user_out(user))
