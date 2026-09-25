"""Candidate / pipeline schemas (API_CONTRACT.md `/candidates`)."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import LinkSource

from app.schemas.pagination import PageMeta


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




class ProfileOut(BaseModel):
    """The Profile (PRD glossary / FR-7.2): resume + profile form answers +
    employer verification."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate: CandidateOut
    resume_url: str | None
    resume_public_id: str | None
    resume_original_filename: str | None
    resume_mime_type: str | None
    resume_size_bytes: int | None
    resume_uploaded_at: datetime | None
    aspects_json: dict | None
    parsed_fields_json: dict | None
    aspects_completed_at: datetime | None


class UploadResumeOut(BaseModel):
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    link_id: uuid.UUID
    source: LinkSource
    parse_task: str = "queued"
    resume_public_id: str | None = None
    resume_url: str | None = None


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


class GrantAccessOut(BaseModel):
    link_id: uuid.UUID
    hm_access_granted: bool = True


class LinkArchiveOut(BaseModel):
    link_id: uuid.UUID
    archived: bool


#: A Team Review verdict, per the Candidate Dashboard Specification Column 7.
#: This is a DECISION vocabulary and is deliberately not `rating.GRADES`, which
#: is what an agent outputs about a candidate. See `services/team_review.py` for
#: the argument and for the override-rate mapping between the two.
#:
#: The literal is spelled out rather than built from `team_review.VERDICTS`
#: because `Literal` needs static members for mypy and for the OpenAPI schema.
#: `test_team_review_vocabulary.py` asserts the two agree, so spelling it twice
#: cannot drift.
TeamRating = Literal["pass", "hold", "reject"]


class TeamReviewIn(BaseModel):
    rating: TeamRating
    remarks: str = Field(min_length=3, max_length=3000)
    ai_rewritten_remarks: str | None = Field(default=None, max_length=3000)


class TeamReviewRewriteIn(BaseModel):
    remarks: str = Field(min_length=3, max_length=3000)


class TeamReviewRewriteOut(BaseModel):
    rewritten_remarks: str
    used_ai: bool


class TeamReviewOut(BaseModel):
    id: uuid.UUID
    reviewer_user_id: uuid.UUID
    reviewer_name: str
    rating: TeamRating
    remarks: str
    ai_rewritten_remarks: str | None
    is_current_user: bool
    created_at: datetime
    updated_at: datetime


class TeamReviewsOut(BaseModel):
    reviews: list[TeamReviewOut]
    overall_rating: TeamRating | None
    overall_remarks: str | None
    review_count: int
