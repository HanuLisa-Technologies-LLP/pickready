"""Job + approval FSM schemas (API_CONTRACT.md `/jobs`)."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ApprovalDecision, JobStatus


class JDIn(BaseModel):
    """Structured JD fields (FR-3.1)."""
    reporting_to: str | None = None
    reportees: int | None = None
    role: str | None = None
    responsibilities: list[str] | str | None = None
    accountabilities: list[str] | str | None = None
    education: str | None = None
    skills: list[str] = []
    experience_years: float | None = None


class JobCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    level: str | None = Field(default=None, max_length=100)
    requirement_period: str | None = Field(default=None, max_length=100)
    jd: JDIn


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    department: str | None
    level: str | None
    status: JobStatus
    requirement_period: str | None
    created_by: uuid.UUID | None
    ratified_at: datetime | None
    created_at: datetime


class JobDetailOut(JobOut):
    jd_json: dict
    compensation_json: dict | None


class ApproveIn(BaseModel):
    decision: Literal["approved", "rejected"]
    remarks: str | None = None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level: JobStatus
    approver_user_id: uuid.UUID | None
    decision: ApprovalDecision
    remarks: str | None
    decided_at: datetime


class CompensationIn(BaseModel):
    compensation: dict

    @field_validator("compensation")
    @classmethod
    def _not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("compensation must not be empty")
        return v


class JDUpdateIn(BaseModel):
    jd: JDIn
    title: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    level: str | None = Field(default=None, max_length=100)
