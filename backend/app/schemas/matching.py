"""Matching pipeline schemas (API_CONTRACT.md `/matching`)."""
import uuid

from pydantic import BaseModel, Field

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
    #: One entry per category this candidate was ACTUALLY scored on, in the
    #: job's own order (spec §3.2). This is what a client should render: the
    #: flat fields below describe only the four categories the product scored
    #: every job on before the lists became per-job, and a job that added or
    #: removed one has comments they cannot carry.
    categories: list[dict] = []
    #: DEPRECATED, see `categories`. Correct whenever the job kept the
    #: long-standing category of the same name, null when it did not.
    skills_match_comment: str | None = None
    experience_comment: str | None = None
    role_alignment_comment: str | None = None
    education_comment: str | None = None
    overall_comment: str | None = None


class MatchResultsOut(BaseModel):
    """Same shape and the same deliberate divergence as `JobLinksOut`.

    It reports a MINIMUM of one page where `PageMeta` reports zero for an empty
    result. Kept, for the same reason: the value is already rendered by a
    shipped client and Section 1's rule is extend, never replace.

    `has_previous` completes the vocabulary, so a client reads the same field
    names everywhere even where the empty-set convention differs.
    """

    job_id: uuid.UUID
    results: list[MatchResultOut]
    # Pagination. Defaults describe a single full page so an older client that
    # ignores these fields still reads a coherent response.
    total: int = 0
    page: int = 1
    page_size: int = 25
    total_pages: int = 1
    has_next: bool = False
    has_previous: bool = False


# ── The job's Matching category list (spec §3.2) ─────────────────────────────


class MatchingCategoryIn(BaseModel):
    """What the recruiter's add/edit control sends.

    No `key`. The key is derived from the name server-side and never moves once
    written: it is what a score is filed under, so letting a client set it would
    let a rename orphan every score already stored against the category.
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)


class MatchingCategoryOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    ordinal: int


class MatchingCategoriesOut(BaseModel):
    job_id: uuid.UUID
    #: True once the recruiter has saved the list. From that point the list is
    #: frozen: candidates have been ranked against it.
    finalized: bool = False
    categories: list[MatchingCategoryOut] = []
    #: Enforced at save, not merely rendered (spec §3.2).
    minimum: int = 5
    maximum: int = 8
    #: Populated when the list cannot yet be saved, so the UI can say why rather
    #: than only disabling the Save control.
    blocking_reason: str | None = None
