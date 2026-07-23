"""Super Admin console schemas (API_CONTRACT.md `/admin`)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import Role
from app.services.capabilities import ALL_CAPABILITIES


class TenantCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=3, max_length=255)
    client_email: EmailStr
    client_phone: str = Field(min_length=5, max_length=20)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: str
    spf_dkim_status: str
    created_at: datetime


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role
    email: str
    phone: str | None
    full_name: str | None
    status: str


class TenantCreateOut(BaseModel):
    tenant: TenantOut
    client_user: AdminUserOut


class PermissionEntry(BaseModel):
    role: Role
    capability: str
    allowed: bool

    @field_validator("capability")
    @classmethod
    def _known_capability(cls, v: str) -> str:
        if v not in ALL_CAPABILITIES:
            raise ValueError(f"unknown capability: {v}")
        return v


class PermissionsUpdateIn(BaseModel):
    tenant_id: uuid.UUID | None = None  # None -> edit the global template
    entries: list[PermissionEntry] = Field(min_length=1)


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: Role
    capability: str
    allowed: bool


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata_json: dict | None
    at: datetime
