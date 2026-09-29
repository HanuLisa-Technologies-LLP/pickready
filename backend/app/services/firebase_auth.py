"""Firebase identity verification; database roles and permissions remain authoritative."""
import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FirebaseIdentity:
    """What a verified Firebase ID token proves, and nothing more.

    There is no phone field. Phone sign-in was removed (owner ruling, Vivekium
    release): `assert_provider_allowed` refuses the provider, no screen ever
    offered it, and a phone claim read here would be an identifier nothing is
    allowed to match on. `users.phone` survives as CONTACT data only.
    """
    uid: str
    email: str | None
    name: str | None
    provider: str
    email_verified: bool


class IdentityDeletionFailed(RuntimeError):
    """Firebase could not confirm the sign-in identity is gone.

    Its own type so the account-deletion route can roll its whole transaction
    back on exactly this, and answer with a sentence rather than a 500.
    """


# firebase-admin's default HTTP timeout is 120 SECONDS
# (`firebase_admin._http_client.DEFAULT_TIMEOUT_SECONDS`), which on a sign-in
# request is indistinguishable from no timeout at all. `verify_id_token` makes
# up to two network calls the caller is blocked on: google-auth fetches
# Google's JWT signing certificates (cached, but cold on a fresh task and after
# every rotation), and `check_revoked=True` adds a user lookup against the
# Identity Toolkit API. Every other vendor call in this repo bounds its attempt
# explicitly, and an unreachable dependency that HANGS defeats the try/except
# around it, because nothing is ever raised for the handler to catch.
#
# `httpTimeout` is the one knob firebase-admin exposes for this. It is a member
# of `_CONFIG_VALID_KEYS`, and BOTH paths above read it: `_token_gen`'s
# `CertificateFetchRequest` and `_auth_client`'s JSON client each resolve
# `app.options.get('httpTimeout', DEFAULT_TIMEOUT_SECONDS)`. It is applied per
# HTTP request, so the worst case is one timeout per call, not one for the pair.
#
# Ten seconds: a sign-in is interactive, and this sits inside the 15s
# interactive ceiling the model router already holds itself to.
HTTP_TIMEOUT_SECONDS = 10


def firebase_client():
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
        if not firebase_admin._apps:
            raw = get_settings().firebase_service_account_json
            if not raw:
                raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not configured")
            firebase_admin.initialize_app(
                credentials.Certificate(json.loads(raw)),
                options={"httpTimeout": HTTP_TIMEOUT_SECONDS},
            )
        return auth
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Firebase Admin could not be initialized") from exc


# Container clocks drift. A Docker Desktop VM whose clock sits one second behind
# Google's signing servers makes EVERY freshly minted ID token look like it was
# "used too early" (iat in the future), and firebase-admin rejects it outright —
# which is exactly how email/password and Google sign-in both 401 on a perfectly
# valid token. Firebase's own SDKs allow a skew window for this reason; 60s is
# the maximum firebase-admin accepts and is what the official docs recommend.
# It only widens the iat/nbf tolerance: signature, audience, issuer and the
# expiry check are all still enforced.
CLOCK_SKEW_SECONDS = 60


def _reject_detail(exc: Exception) -> str:
    """Map a verification failure to a message the caller can act on.

    A bare "Invalid Firebase session" for every cause is what turned a one-line
    clock-skew bug into an afternoon of guessing. The text below names the
    CATEGORY of failure only; it never echoes the token, the claims, or the
    project identifiers back to the caller.
    """
    name = type(exc).__name__
    text = str(exc).lower()
    if "too early" in text or "clock" in text:
        return "Sign-in rejected because the server clock is out of sync. Try again."
    if name == "ExpiredIdTokenError" or "expired" in text:
        return "This sign-in has expired. Sign in again."
    if name == "RevokedIdTokenError" or "revoked" in text:
        return "This sign-in was revoked. Sign in again."
    if name == "CertificateFetchError":
        return "Sign-in could not be verified right now. Try again in a moment."
    if "service_account" in text or "credential" in text or "default credentials" in text:
        return "Sign-in is not configured on this server."
    return "Invalid Firebase session"


def verify_id_token(id_token: str) -> FirebaseIdentity:
    # Initialization failure is a SERVER fault, not a bad credential — a 401
    # here would send the user round the login screen forever chasing a
    # missing service-account file.
    try:
        client = firebase_client()
    except RuntimeError as exc:
        log.error("firebase_admin_unavailable", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is not configured on this server",
        ) from exc
    # No keyword fallback. `clock_skew_seconds` exists from firebase-admin 6.2
    # and requirements.txt pins >= 6.5; the retry-without-the-keyword branch
    # that used to sit here could only ever have run on an image that had
    # silently downgraded, and would then have hidden that fact.
    try:
        claims = client.verify_id_token(
            id_token, check_revoked=True, clock_skew_seconds=CLOCK_SKEW_SECONDS
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Operators need the verification cause; callers get only the mapped
        # response and no token/claims are ever written to the log.
        log.warning("firebase_id_token_rejected", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_reject_detail(exc)
        ) from exc
    data = claims.get("firebase") or {}
    return FirebaseIdentity(
        uid=claims["uid"],
        email=claims.get("email"),
        name=claims.get("name"),
        provider=data.get("sign_in_provider", "unknown"),
        email_verified=bool(claims.get("email_verified")),
    )


def delete_identity(uid: str) -> bool:
    """Delete the Firebase sign-in identity `uid`. True once it is gone.

    SYNCHRONOUS, like the client it wraps: call it through
    `run_in_threadpool` from a request handler.

    IDEMPOTENT. An identity Firebase no longer has is the outcome asked for, so
    `UserNotFoundError` answers True; a retry after a lost response must not
    fail on the delete that already happened. Anything else raises
    `IdentityDeletionFailed`: an unconfigured server, a network failure and a
    refusal all mean the identity may still exist, and the caller's job is to
    keep the rows that describe it rather than orphan a sign-in that would
    silently recreate a profile on its next use.
    """
    try:
        client = firebase_client()
    except RuntimeError as exc:
        raise IdentityDeletionFailed("Firebase Admin is not available") from exc
    try:
        client.delete_user(uid)
    except client.UserNotFoundError:
        return True
    except Exception as exc:  # noqa: BLE001 - every cause means "may still exist"
        log.error("firebase_identity_delete_failed err=%s", type(exc).__name__)
        raise IdentityDeletionFailed(type(exc).__name__) from exc
    return True


# ── Which sign-in provider each role may use (auth spec section 8) ─────────
#
# ROLE AWARE, and enforced HERE, at the session exchange, because hiding a
# button is not a policy: a caller can sign in with Google through Firebase
# directly and bring that token to `/auth/firebase/session`.
#
# * A CANDIDATE may use Google or an email and password.
# * EVERY COMPANY (org) ROLE uses an email and password ONLY. The org set is
#   read from `core.security._ORG_ROLES` at call time, never copied here, so
#   a role added to the org portal is covered the moment it exists.
# * The Provider owner and Business Development keep the policy they had.
#
# Phone is not a provider for anybody and never returns without an owner
# decision: it was removed, not disabled.
CANDIDATE_PROVIDERS = frozenset({"password", "google.com"})
ORG_PROVIDERS = frozenset({"password"})
PLATFORM_PROVIDERS = frozenset({"password", "google.com"})
#: The union, for callers that ask "is this a provider we know at all".
ALLOWED_PROVIDERS = CANDIDATE_PROVIDERS | ORG_PROVIDERS | PLATFORM_PROVIDERS

GOOGLE_REFUSED_DETAIL = "Google sign-in is available to candidates only"


def providers_for_role(role: str) -> frozenset[str]:
    """The providers `role` may sign in with. An unknown role gets nothing."""
    from app.core.security import _ORG_ROLES  # read live: the org set grows

    value = getattr(role, "value", role)
    if value == "candidate":
        return CANDIDATE_PROVIDERS
    if value in _ORG_ROLES:
        return ORG_PROVIDERS
    if value in {"super_admin", "bd"}:
        return PLATFORM_PROVIDERS
    return frozenset()


def assert_provider_allowed(identity: FirebaseIdentity, role: str) -> None:
    """Refuse a sign-in provider the role may not use (403)."""
    if identity.provider in providers_for_role(role):
        return
    if identity.provider == "google.com":
        raise HTTPException(status_code=403, detail=GOOGLE_REFUSED_DETAIL)
    raise HTTPException(status_code=403, detail="Unsupported sign-in provider")


# ── Server-side credential operations (auth hardening, 2026-09-29) ──────────
#
# The invitation's password setup and the password change and reset run on
# the SERVER now, so the email a password is set for is the one the server
# resolved, never one a browser chose. Firebase still stores the credential;
# this product never stores or logs a password. Every call is the SYNCHRONOUS
# Admin SDK, so each is offloaded with `run_in_threadpool`, the rule
# `verify_id_token` learned on the sign-in path.


class IdentityOperationFailed(RuntimeError):
    """Firebase refused or could not be reached. The message names the
    operation and the exception CLASS, never an address or a password."""


class IdentityAlreadyExists(RuntimeError):
    """Firebase already holds an identity for this address."""


def _admin():
    try:
        return firebase_client()
    except RuntimeError as exc:
        log.error("firebase_admin_unavailable", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is not configured on this server",
        ) from exc


def _lookup_uid(email: str) -> str | None:
    client = _admin()
    try:
        return client.get_user_by_email(email.strip().lower()).uid
    except client.UserNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - every other cause is "could not tell"
        log.error("firebase_identity_lookup_failed err=%s", type(exc).__name__)
        raise IdentityOperationFailed(f"lookup:{type(exc).__name__}") from exc


async def identity_exists(email: str) -> str | None:
    """The Firebase uid registered for `email`, or None when there is none."""
    return await run_in_threadpool(_lookup_uid, email)


def _create(email: str, password: str, display_name: str | None) -> str:
    client = _admin()
    kwargs = {
        "email": email.strip().lower(),
        "password": password,
        # The address was proven by the flow that called this: an invitation
        # token sent to exactly that mailbox, or a security code read from it.
        "email_verified": True,
    }
    if display_name and display_name.strip():
        kwargs["display_name"] = display_name.strip()[:200]
    try:
        return client.create_user(**kwargs).uid
    except client.EmailAlreadyExistsError as exc:
        raise IdentityAlreadyExists("email already registered") from exc
    except Exception as exc:  # noqa: BLE001 - the identity may or may not exist
        log.error("firebase_identity_create_failed err=%s", type(exc).__name__)
        raise IdentityOperationFailed(f"create:{type(exc).__name__}") from exc


async def create_password_user(email: str, password: str, display_name: str | None) -> str:
    """Create an email and password identity with a VERIFIED email; its uid.

    Raises `IdentityAlreadyExists` when the address already has an identity
    (the invitation screen then offers "sign in with your existing password")
    and `IdentityOperationFailed` for anything else.
    """
    return await run_in_threadpool(_create, email, password, display_name)


def _set_password(uid: str, password: str) -> None:
    client = _admin()
    try:
        client.update_user(uid, password=password)
    except Exception as exc:  # noqa: BLE001 - the password may be unchanged
        log.error("firebase_password_update_failed err=%s", type(exc).__name__)
        raise IdentityOperationFailed(f"update:{type(exc).__name__}") from exc


async def set_password(uid: str, password: str) -> None:
    """Replace the password on `uid` (adds the password provider when the
    identity had only Google)."""
    await run_in_threadpool(_set_password, uid, password)


def _revoke(uid: str) -> None:
    client = _admin()
    try:
        client.revoke_refresh_tokens(uid)
    except Exception as exc:  # noqa: BLE001 - a revocation not confirmed is a failure
        log.error("firebase_revoke_failed err=%s", type(exc).__name__)
        raise IdentityOperationFailed(f"revoke:{type(exc).__name__}") from exc


async def revoke_refresh_tokens(uid: str) -> None:
    """Revoke every Firebase refresh token for `uid`, so an ID token minted
    before a password change fails `verify_id_token(check_revoked=True)`."""
    await run_in_threadpool(_revoke, uid)
