"""Auth request/response schemas (API_CONTRACT.md `/auth`)."""
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Role


class OTPRequestIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)  # email or phone
    channel: Literal["email", "sms"]
    audience: Literal["internal", "candidate"] = "internal"


class OTPRequestOut(BaseModel):
    challenge_id: uuid.UUID
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
    email: str
    email_verified: bool
    phone_verified: bool


class OTPVerifyOut(BaseModel):
    user: UserOut
    # Non-empty on client first login when the second channel is still
    # unverified (dual OTP, FR-1.2) — no cookies are set in that case.
    pending_channels: list[str] = []


class MeOut(BaseModel):
    user: UserOut
