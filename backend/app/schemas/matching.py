"""Matching pipeline schemas (API_CONTRACT.md `/matching`)."""
import uuid

from pydantic import BaseModel

from app.models.enums import LinkSource, Tier
from app.schemas.candidates import CandidateOut


class RunMatchingOut(BaseModel):
    task: str = "queued"
    job_id: uuid.UUID


class MatchResultOut(BaseModel):
    link_id: uuid.UUID
    candidate: CandidateOut
    source: LinkSource
    match_score: float | None
    tier: Tier | None
    # LLM rationale — HR-visible only, never exposed to the candidate (ESD §8.2)
    rationale: str | None
    # 4-parameter breakdown (rev 2): 4 params + overall, each {score, comment}.
    # HR Review Screen renders all 5 scores + 5 comments.
    breakdown: dict | None = None


class MatchResultsOut(BaseModel):
    job_id: uuid.UUID
    results: list[MatchResultOut]
