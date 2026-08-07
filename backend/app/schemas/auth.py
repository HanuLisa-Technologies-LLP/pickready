"""Auth request/response schemas (API_CONTRACT.md `/auth`, rev 2)."""
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Role


class OTPRequestIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)  # email or phone
    # `audience` is accepted for backward compat; it no longer routes the
    # lookup (unified login, rev 2) — it only signals candidate
    # self-registration intent on unknown identifiers.
    channel: Literal["email", "sms"]
    audience: Literal["internal", "candidate"] = "internal"


class OTPRequestOut(BaseModel):
    challenge_id: uuid.UUID
    # Channels the single code was actually dispatched to. When the resolved
    # account exposes both an email and a phone, one challenge is sent to BOTH
    # in parallel and the user may enter whichever code arrives — so the UI can
    # say "Check your email and SMS". Order: requested/primary channel first.
    channels_sent: list[Literal["email", "sms"]] = []
    # Dev-only convenience: the plaintext code, returned ONLY when
    # ENVIRONMENT=development so local testing works without real email/SMS.
    # Never populated in production; never logged (ESD §16).
    debug_code: str | None = None


class OTPVerifyIn(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=4, max_length=10)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Role
    tenant_id: uuid.UUID | None
    full_name: str | None
    # Optional: a phone-only candidate (Firebase phone provider) has no email.
    email: str | None
    email_verified: bool
    phone_verified: bool
    # Rendered by every authenticated shell so a legitimate multi-tenant user
    # can always see which workspace owns the current session cookie.
    workspace_name: str


class ContextOut(BaseModel):
    """One selectable workspace when an identifier matches multiple users
    (three portals, ONE login — contract rev 2)."""
    user_id: uuid.UUID
    role: Role
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    portal: Literal["owner", "org", "candidate"]


class OTPVerifyOut(BaseModel):
    # Exactly one matching user: `user` + `capabilities` (cookies set).
    user: UserOut | None = None
    capabilities: list[str] | None = None
    # Non-empty on client first login when the second channel is still
    # unverified (dual OTP, FR-1.2) — no cookies are set in that case.
    pending_channels: list[str] = []
    # Multiple matching users: workspace chooser — no cookies until
    # /auth/select-context.
    contexts: list[ContextOut] | None = None
    context_token: str | None = None


class SelectContextIn(BaseModel):
    context_token: str = Field(min_length=10)
    user_id: uuid.UUID


class MeOut(BaseModel):
    user: UserOut
    capabilities: list[str] = []


class FirebaseSessionIn(BaseModel):
    id_token: str = Field(min_length=20)
    # Optional portal intent from the unified sign-in screen. This is a filter,
    # never an authority grant: the resolved database role must already belong
    # to the requested portal or sign-in is refused.
    requested_portal: Literal["candidate", "org", "bd", "owner"] | None = None


class CandidateRegisterIn(BaseModel):
    """Candidate self-service sign-up (register first, log in later). OTP-only —
    no password is collected; the account is verified by OTP at first login."""
    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=20)


class CandidateRegisterOut(BaseModel):
    candidate_id: uuid.UUID
    email: str
    # Guidance for the client: registration creates the account only; the
    # candidate now signs in from the unified login via OTP.
    next: Literal["login"] = "login"
