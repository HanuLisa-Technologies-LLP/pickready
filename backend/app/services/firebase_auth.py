"""Firebase identity verification; database roles and permissions remain authoritative."""
import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FirebaseIdentity:
    uid: str
    email: str | None
    phone: str | None
    name: str | None
    provider: str
    email_verified: bool


def firebase_client():
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
        if not firebase_admin._apps:
            raw = get_settings().firebase_service_account_json
            if not raw:
                raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not configured")
            firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
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
    try:
        claims = client.verify_id_token(
            id_token, check_revoked=True, clock_skew_seconds=CLOCK_SKEW_SECONDS
        )
    except HTTPException:
        raise
    except TypeError:
        # firebase-admin < 6.2 has no clock_skew_seconds keyword. Fall back
        # rather than 500 — the deployment simply keeps the stricter check.
        try:
            claims = client.verify_id_token(id_token, check_revoked=True)
        except Exception as exc:  # noqa: BLE001 - normalized to a 401 below
            log.warning("firebase_id_token_rejected", exc_info=exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=_reject_detail(exc)
            ) from exc
    except Exception as exc:
        # Operators need the verification cause; callers get only the mapped
        # response and no token/claims are ever written to the log.
        log.warning("firebase_id_token_rejected", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_reject_detail(exc)
        ) from exc
    data = claims.get("firebase") or {}
    return FirebaseIdentity(claims["uid"], claims.get("email"), claims.get("phone_number"), claims.get("name"), data.get("sign_in_provider", "unknown"), bool(claims.get("email_verified")))


def assert_provider_allowed(identity: FirebaseIdentity, role: str) -> None:
    if identity.provider not in {"password", "google.com"}:
        raise HTTPException(status_code=403, detail="Unsupported sign-in provider")
