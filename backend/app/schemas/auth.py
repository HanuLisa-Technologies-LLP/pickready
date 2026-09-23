"""Auth request/response schemas (API_CONTRACT.md `/auth`, rev 2).

The one-time-code login schemas (request, verify, candidate self-registration)
were deleted on 2026-09-24 with the unrouted handlers that were their only
users. The session response they shared was renamed `SessionOut`, because it
is what every live sign-in route answers and it never had anything to do with
a code. Its JSON is unchanged except that the pending-channel list is gone: it only
ever carried the retired dual-channel gate, and no screen ever read it.
"""
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Role


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
    password_enabled: bool = False
    # Rendered by every authenticated shell so a legitimate multi-tenant user
    # can always see which workspace owns the current session cookie.
    workspace_name: str


class ContextOut(BaseModel):
    """One selectable workspace when an identifier matches multiple users
    (three portals, ONE login, contract rev 2)."""
    user_id: uuid.UUID
    role: Role
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    portal: Literal["owner", "org", "candidate"]


class SessionOut(BaseModel):
    """What `/auth/firebase/session`, `/auth/workspaces` and
    `/auth/select-context` answer."""

    # Exactly one matching user: `user` + `capabilities` (cookies set).
    user: UserOut | None = None
    capabilities: list[str] | None = None
    # Multiple matching users: workspace chooser, no cookies until
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
