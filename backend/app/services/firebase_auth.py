"""Firebase identity verification; database roles and permissions remain authoritative."""
import json
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.core.config import get_settings


@dataclass(frozen=True)
class FirebaseIdentity:
    uid: str
    email: str | None
    phone: str | None
    name: str | None
    provider: str
    email_verified: bool


def verify_id_token(id_token: str) -> FirebaseIdentity:
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
        if not firebase_admin._apps:
            raw = get_settings().firebase_service_account_json
            if not raw:
                raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not configured")
            firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
        claims = auth.verify_id_token(id_token, check_revoked=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase session") from exc
    data = claims.get("firebase") or {}
    return FirebaseIdentity(claims["uid"], claims.get("email"), claims.get("phone_number"), claims.get("name"), data.get("sign_in_provider", "unknown"), bool(claims.get("email_verified")))


def assert_provider_allowed(identity: FirebaseIdentity, role: str) -> None:
    if identity.provider not in {"password", "phone", "google.com"}:
        raise HTTPException(status_code=403, detail="Unsupported sign-in provider")
    if role != "candidate" and identity.provider == "google.com":
        raise HTTPException(status_code=403, detail="Google sign-in is available to candidates only")
