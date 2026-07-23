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


class MatchResultsOut(BaseModel):
    job_id: uuid.UUID
    results: list[MatchResultOut]
