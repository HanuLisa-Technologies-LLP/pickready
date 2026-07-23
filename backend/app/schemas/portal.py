"""Candidate portal schemas (API_CONTRACT.md `/portal`)."""
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import JobStatus, PipelineStatus


class AspectOut(BaseModel):
    id: int
    prompt: str


class OutreachInfoOut(BaseModel):
    """What the outreach link asks the candidate to provide (FR-5.1/6.1)."""
    job_title: str | None
    company_name: str | None
    already_submitted: bool
    # Personal fields (FR-5.1 a-d) still missing on the candidate record
    personal_fields: list[str]
    # The 40 aspects minus any covered by the personal fields (FR-5.1)
    aspects: list[AspectOut]
    resume_required: bool = True
    max_employer_emails: int = 3


class OutreachSubmitOut(BaseModel):
    profile_id: uuid.UUID
    aspects_received: int
    verification_requests_created: int
    parse_task: str = "queued"


class PortalJobOut(BaseModel):
    id: uuid.UUID
    title: str
    department: str | None
    level: str | None
    company_name: str | None
    status: JobStatus


class PortalJobsOut(BaseModel):
    jobs: list[PortalJobOut]


class ApplyOut(BaseModel):
    link_id: uuid.UUID
    job_id: uuid.UUID
    parse_task: str = "queued"


class ApplicationOut(BaseModel):
    link_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    company_name: str | None
    applied_at: datetime
    # Latest pipeline status; None means still in review
    stage: PipelineStatus | None


class ApplicationsOut(BaseModel):
    applications: list[ApplicationOut]
