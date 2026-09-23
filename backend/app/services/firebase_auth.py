"""Firebase identity verification; database roles and permissions remain authoritative."""
import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException, status

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


#: The sign-in providers this product accepts, for every role. Phone is not
#: among them and never returns without an owner decision: it was removed,
#: not disabled.
ALLOWED_PROVIDERS = frozenset({"password", "google.com"})


def assert_provider_allowed(identity: FirebaseIdentity, role: str) -> None:
    if identity.provider not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=403, detail="Unsupported sign-in provider")
