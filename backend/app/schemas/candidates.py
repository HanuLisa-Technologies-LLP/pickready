"""Candidate / pipeline schemas (API_CONTRACT.md `/candidates`)."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import LinkSource, PipelineStatus, Tier, VerificationStatus


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None
    email: str
    phone: str | None
    city: str | None
    age: int | None
    gender: str | None
    consent_databank: bool


class VerificationRequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employer_seq: int
    employer_email: str
    employer_name: str | None
    status: VerificationStatus
    responded_at: datetime | None


class ProfileOut(BaseModel):
    """The Profile (PRD glossary / FR-7.2): resume + 40 aspects + employer
    verification, as shown on the HR Review Screen."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate: CandidateOut
    resume_url: str | None
    aspects_json: dict | None
    parsed_fields_json: dict | None
    aspects_completed_at: datetime | None
    verification_requests: list[VerificationRequestSummary] = []


class UploadResumeOut(BaseModel):
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    link_id: uuid.UUID
    source: LinkSource
    parse_task: str = "queued"


class DecisionIn(BaseModel):
    """Hiring Manager decision (FR-8.2). Hold requires remarks — a missing
    remark is a validation error (422)."""
    status: Literal["rejected", "shortlisted", "hold"]
    remarks: str | None = None

    @model_validator(mode="after")
    def _hold_requires_remarks(self) -> "DecisionIn":
        if self.status == "hold" and not (self.remarks and self.remarks.strip()):
            raise ValueError("remarks are mandatory when placing a profile on hold")
        return self


class StatusIn(BaseModel):
    """Mandatory pipeline status update (FR-8.4)."""
    status: Literal["rejected", "shortlisted", "offered", "joined"]
    remarks: str | None = None


class StatusOut(BaseModel):
    link_id: uuid.UUID
    status: PipelineStatus
    remarks: str | None
    at: datetime


class InterviewIn(BaseModel):
    scheduled_at: datetime
    notes: str | None = None


class InterviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_candidate_link_id: uuid.UUID
    scheduled_at: datetime
    sent_from_email: str | None
    ics_uid: str | None
    notes: str | None


class LinkOut(BaseModel):
    link_id: uuid.UUID
    candidate: CandidateOut
    profile_id: uuid.UUID | None
    source: LinkSource
    match_score: float | None
    tier: Tier | None
    breakdown: dict | None = None  # Stored 4-param ranking + comments for review UI
    hm_access_granted: bool
    current_status: PipelineStatus | None
    status_remarks: str | None = None


class GrantAccessOut(BaseModel):
    link_id: uuid.UUID
    hm_access_granted: bool = True


class JobLinksOut(BaseModel):
    job_id: uuid.UUID
    links: list[LinkOut]
