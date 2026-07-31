"""Matching pipeline schemas (API_CONTRACT.md `/matching`)."""
import uuid

from pydantic import BaseModel

from app.models.enums import LinkSource, Tier
from app.schemas.candidates import CandidateOut


class RunMatchingOut(BaseModel):
    task: str = "queued"
    job_id: uuid.UUID
    task_id: str
    candidate_count: int


class MatchingTaskStatusOut(BaseModel):
    task_id: str
    state: str
    done: bool


class MatchResultOut(BaseModel):
    link_id: uuid.UUID
    candidate: CandidateOut
    source: LinkSource
    #: Type of procurement: applied | sourced | databank (2026-07-28). Display
    #: and filtering only. `source` above is the older databank|fresh retrieval
    #: marker and answers a different question, so both are returned.
    source_type: str = "applied"
    source_type_label: str = "Applied"
    tier: Tier | None
    # LLM rationale — HR-visible only, never exposed to the candidate (ESD §8.2)
    rationale: str | None
    # 4-parameter breakdown (rev 2): 4 params + overall, each {score, comment}.
    # The score is retained for matching/audit; the UI renders comments only.
    breakdown: dict | None = None
    # Comments-only projection the review UI consumes — always present, always
    # 25-30 words each. "not_scored" means matching has not run for this link
    # yet (comments null); "ready" means all five comments are populated.
    ranking_status: str = "not_scored"
    skills_match_comment: str | None = None
    experience_comment: str | None = None
    role_alignment_comment: str | None = None
    education_comment: str | None = None
    overall_comment: str | None = None


class MatchResultsOut(BaseModel):
    job_id: uuid.UUID
    results: list[MatchResultOut]
    # Pagination. Defaults describe a single full page so an older client that
    # ignores these fields still reads a coherent response.
    total: int = 0
    page: int = 1
    page_size: int = 25
    total_pages: int = 1
    has_next: bool = False
