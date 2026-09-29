"""Company self-registration: the pieces the routes and the sign-in path share.

Owner spec 2026-09-29, sections 2.2, 5 and 24. The ROUTES live in
`api/company_onboarding.py`; this module holds what more than one caller needs:

* the ONBOARDING COOKIE, a short-lived signed JWT with its own audience
  (`pickready:onboarding`) that names one registration and grants nothing a
  portal session grants. It is the only thing that lets a person who has proven
  the company mailbox continue to pricing, payment and the password;
* `has_paid`, the one answer to "is the first purchase paid", read from
  `credit_purchases` every time (claude.md rule 8: never a stored flag);
* `stage`, the next step, DERIVED from the registration row and that answer;
* the SIGN-IN REFUSAL. A company still `onboarding` has an invited `client`
  user, and the ordinary sign-in flips an invited user to active on its first
  proven login. Without the refusal, a Firebase sign-up with the registered
  address (which Firebase allows from any browser) would open the workspace
  without a purchase. `api/auth` asks `without_onboarding` BEFORE it activates
  anybody, on every path that can (the single exchange, the chooser and the
  workspace switcher);
* the phone and email normalisers the form is checked with.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

import jwt
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import ALGORITHM
from app.models.billing import PURCHASE_PAID
from app.models.company_registration import (
    REGISTRATION_ACTIVATED,
    REGISTRATION_PENDING,
    REGISTRATION_VERIFIED,
    CompanyRegistration,
)
from app.models.tenant import TENANT_ONBOARDING, Tenant

__all__ = [
    "AUDIENCE_ONBOARDING",
    "AUDIT_ACTIVATED",
    "AUDIT_CODE_SENT",
    "AUDIT_EMAIL_VERIFIED",
    "AUDIT_PAYMENT_CREATED",
    "AUDIT_PAYMENT_VERIFIED",
    "AUDIT_STARTED",
    "REGISTRATION_PENDING_DAYS",
    "COOKIE_TTL_MINUTES",
    "ONBOARDING_COOKIE",
    "ONBOARDING_LOGIN_DETAIL",
    "STAGE_CHOOSE_PACK",
    "STAGE_DETAILS",
    "STAGE_DONE",
    "STAGE_SET_PASSWORD",
    "STAGE_VERIFY_EMAIL",
    "canonical_phone",
    "clear_cookie",
    "has_paid",
    "mint_cookie",
    "normal_email",
    "read_cookie",
    "set_cookie",
    "stage",
    "without_onboarding",
]

#: The audit actions of a registration (owner spec 29). The spec writes
#: `company_registration_otp_sent`; this product calls it a security code
#: everywhere (`services/security_codes`), so the row does too. No row ever
#: carries the code, the CAPTCHA proof, the password or a payment signature.
AUDIT_STARTED = "company_registration_started"
AUDIT_CODE_SENT = "company_registration_code_sent"
AUDIT_EMAIL_VERIFIED = "company_registration_email_verified"
AUDIT_PAYMENT_CREATED = "company_registration_payment_created"
AUDIT_PAYMENT_VERIFIED = "company_registration_payment_verified"
AUDIT_ACTIVATED = "company_registration_activated"

#: A registration nobody verified inside this many days is closed as
#: `expired` the next time the same address registers or asks for a code.
REGISTRATION_PENDING_DAYS = 7

ONBOARDING_COOKIE = "pr_onboarding"
AUDIENCE_ONBOARDING = "pickready:onboarding"
COOKIE_TTL_MINUTES = 60

#: What the ordinary sign-in says to a company that has not finished. It
#: names the one way forward and nothing about the account's state beyond
#: what the person who registered already knows.
ONBOARDING_LOGIN_DETAIL = (
    "Your company registration is not finished yet. Open Register your "
    "company, enter the same email address and a new security code, then "
    "complete the purchase and set your password."
)

# The steps of `/company/register`, in order. DERIVED, never stored.
STAGE_DETAILS = "details"
STAGE_VERIFY_EMAIL = "verify_email"
STAGE_CHOOSE_PACK = "choose_pack"
STAGE_SET_PASSWORD = "set_password"
STAGE_DONE = "done"


# ── Normalisers ─────────────────────────────────────────────────────────────

def normal_email(value: str) -> str:
    return (value or "").strip().lower()


_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def canonical_phone(value: str) -> str:
    """E.164 (`+` and 8 to 15 digits), or ValueError with a sentence.

    A bare ten-digit number beginning 6 to 9 is an Indian mobile and gains
    `+91`; a leading `0` or `91` before one is dropped the same way. Anything
    else must already carry its country code.
    """
    # ASSUMPTION: the customers are Indian entities (CLAUDE.md 2026-09-10), so
    # a number typed without a country code is read as an Indian mobile. Every
    # other country is accepted with its `+` code.
    raw = _PHONE_SEPARATORS.sub("", (value or "").strip())
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not raw.startswith("+"):
        digits = raw
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        elif len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if _INDIAN_MOBILE.match(digits):
            raw = "+91" + digits
    if not _E164.match(raw):
        raise ValueError(
            "Enter a mobile number with its country code, for example "
            "+91 98765 43210."
        )
    return raw


# ── The onboarding cookie ───────────────────────────────────────────────────

def mint_cookie(registration_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(registration_id),
            "aud": AUDIENCE_ONBOARDING,
            "iat": now,
            "exp": now + timedelta(minutes=COOKIE_TTL_MINUTES),
            "type": "onboarding",
        },
        get_settings().jwt_secret,
        algorithm=ALGORITHM,
    )


def read_cookie(token: str | None) -> uuid.UUID | None:
    """The registration id a valid cookie names, else None. An expired, forged
    or other-audience token (a portal access token included) reads as None."""
    if not token:
        return None
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE_ONBOARDING,
        )
    except jwt.PyJWTError:
        return None
    if claims.get("type") != "onboarding":
        return None
    try:
        return uuid.UUID(str(claims.get("sub")))
    except ValueError:
        return None


def _cookie_kwargs() -> dict:
    settings = get_settings()
    kwargs = {
        "httponly": True,
        "secure": settings.serves_over_https,
        "samesite": "strict",
        "path": "/",
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs


def set_cookie(response, registration_id: uuid.UUID) -> None:
    response.set_cookie(
        ONBOARDING_COOKIE,
        mint_cookie(registration_id),
        max_age=COOKIE_TTL_MINUTES * 60,
        **_cookie_kwargs(),
    )


def clear_cookie(response) -> None:
    response.delete_cookie(ONBOARDING_COOKIE, **_cookie_kwargs())


# ── Derived state ───────────────────────────────────────────────────────────

async def has_paid(session: AsyncSession, tenant_id: uuid.UUID | None) -> bool:
    """Whether this tenant holds a PAID credit purchase. Read from the table
    every time; the browser's verify call and the webhook both settle through
    `credit_packs.settle_purchase`, so whichever won, this sees it."""
    if tenant_id is None:
        return False
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM credit_purchases WHERE tenant_id = :t "
                "AND status = :paid LIMIT 1"
            ),
            {"t": str(tenant_id), "paid": PURCHASE_PAID},
        )
    ).first()
    return row is not None


def stage(registration: CompanyRegistration | None, paid: bool) -> str:
    if registration is None:
        return STAGE_DETAILS
    if registration.status == REGISTRATION_PENDING:
        return STAGE_VERIFY_EMAIL
    if registration.status == REGISTRATION_VERIFIED:
        return STAGE_SET_PASSWORD if paid else STAGE_CHOOSE_PACK
    if registration.status == REGISTRATION_ACTIVATED:
        return STAGE_DONE
    # expired, cancelled or locked: the only way on is to start again.
    return STAGE_DETAILS


# ── The sign-in refusal ─────────────────────────────────────────────────────

async def onboarding_tenant_ids(
    session: AsyncSession, tenant_ids: Iterable[uuid.UUID | None]
) -> set[uuid.UUID]:
    """Which of these tenants are still onboarding. Called from the identity
    session, which already reads `tenants` under the bypass scope."""
    wanted = {tid for tid in tenant_ids if tid is not None}
    if not wanted:
        return set()
    rows = (
        await session.execute(
            select(Tenant.id).where(
                Tenant.id.in_(wanted), Tenant.status == TENANT_ONBOARDING
            )
        )
    ).scalars().all()
    return set(rows)


async def without_onboarding(session: AsyncSession, users: Sequence) -> list:
    """`users` minus every account whose company is still onboarding.

    Raises 403 with `ONBOARDING_LOGIN_DETAIL` when that removes every one of
    them, so a company that has not finished is told how to finish rather
    than told it has no workspace. Must run BEFORE anything activates an
    invited account.
    """
    blocked = await onboarding_tenant_ids(session, (u.tenant_id for u in users))
    if not blocked:
        return list(users)
    kept = [u for u in users if u.tenant_id not in blocked]
    if not kept:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ONBOARDING_LOGIN_DETAIL
        )
    return kept
