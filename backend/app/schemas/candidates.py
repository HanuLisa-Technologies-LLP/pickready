"""Candidate / pipeline schemas (API_CONTRACT.md `/candidates`)."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import LinkSource, PipelineStatus, Tier, VerificationStatus

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
    resume_public_id: str | None
    resume_original_filename: str | None
    resume_mime_type: str | None
    resume_size_bytes: int | None
    resume_uploaded_at: datetime | None
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
    resume_public_id: str | None = None
    resume_url: str | None = None


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
    #: Type of procurement: applied | sourced | databank (2026-07-28).
    source_type: str = "applied"
    source_type_label: str = "Applied"
    tier: Tier | None
    breakdown: dict | None = None  # Stored 4-param ranking + comments for review UI
    # Comments-only projection for the review screen — always present, each
    # comment 25-30 words. ranking_status: "not_scored" | "ready".
    ranking_status: str = "not_scored"
    skills_match_comment: str | None = None
    experience_comment: str | None = None
    role_alignment_comment: str | None = None
    education_comment: str | None = None
    overall_comment: str | None = None
    hm_access_granted: bool
    archived_at: datetime | None = None
    current_status: PipelineStatus | None
    status_remarks: str | None = None


class GrantAccessOut(BaseModel):
    link_id: uuid.UUID
    hm_access_granted: bool = True


class LinkArchiveOut(BaseModel):
    link_id: uuid.UUID
    archived: bool


class JobLinksOut(BaseModel):
    """Deliberately NOT on `PageMeta`.

    It already carried the derived fields, and it reports a MINIMUM of one page
    (`max(1, ...)` in the handler) where `PageMeta` reports zero for an empty
    result. Both readings are defensible and this one is already in a shipped
    client, so it keeps its own: Section 1's rule is extend, never replace, and
    changing a number an existing UI renders is a replacement.

    `has_previous` is added so the vocabulary matches everywhere even though
    the empty-set convention does not.
    """

    job_id: uuid.UUID
    links: list[LinkOut]
    # Pagination. Defaults describe a single full page so an older client that
    # ignores these fields still reads a coherent response.
    total: int = 0
    page: int = 1
    page_size: int = 25
    total_pages: int = 1
    has_next: bool = False
    has_previous: bool = False


class RankingCommentsOut(BaseModel):
    """Comments-only ranking response. Numeric scores never cross this API."""

    skills_match_comment: str | None = None
    experience_comment: str | None = None
    role_alignment_comment: str | None = None
    education_comment: str | None = None
    overall_comment: str | None = None


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
