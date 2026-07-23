"""Client company schemas (API_CONTRACT.md `/companies`)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.enums import APPROVAL_CHAIN


class CompanyPageIn(BaseModel):
    brief: str | None = None
    culture: str | None = None
    policies: str | None = None
    benefits: str | None = None


class CompanyPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    brief: str | None
    culture: str | None
    policies: str | None
    benefits: str | None
    approval_levels_config: dict | None


class HiringManagerCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    approval_level: str | None = None  # a JobStatus value if pre-assigned

    @model_validator(mode="after")
    def _valid_level(self) -> "HiringManagerCreateIn":
        if self.approval_level is not None:
            valid = {s.value for s in APPROVAL_CHAIN}
            if self.approval_level not in valid:
                raise ValueError(f"approval_level must be one of {sorted(valid)}")
        return self


class HiringManagerOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str | None
    phone: str | None
    approval_level: str | None
    status: str


class ApprovalLevelEntry(BaseModel):
    active: bool = False
    approver_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _approver_required_when_active(self) -> "ApprovalLevelEntry":
        if self.active and self.approver_user_id is None:
            raise ValueError("an active level requires approver_user_id")
        return self


class ApprovalLevelsIn(BaseModel):
    config: dict[str, ApprovalLevelEntry]

    @model_validator(mode="after")
    def _only_known_levels(self) -> "ApprovalLevelsIn":
        valid = {s.value for s in APPROVAL_CHAIN}
        unknown = set(self.config) - valid
        if unknown:
            raise ValueError(f"unknown approval levels: {sorted(unknown)}")
        return self


class ApprovalLevelsOut(BaseModel):
    config: dict[str, ApprovalLevelEntry]


class EmailTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)


class EmailTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    subject: str
    body: str
    version: int
    is_active: bool
    created_at: datetime
