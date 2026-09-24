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


class MatchingStageOut(BaseModel):
    """One step of the matching run, as shown inline on the job page.

    Deliberately a fixed vocabulary from `services/matching_progress`, not a
    model's own narration. A narration would quote the prompt -- which holds a
    real candidate's resume and a real client's JD -- and, being generated,
    could describe work the pipeline never did.
    """

    key: str
    label: str
    detail: str
    #: pending | active | done | skipped | failed. `skipped` is load-bearing: a
    #: run whose embedding service was down ranked on keywords alone, and
    #: showing that stage as complete would present a degraded run as a full one.
    status: str


class ActivityLineOut(BaseModel):
    """One AI activity sentence, rendered by `services/activity` from a typed
    event the workflow actually reached. Never model narration, and never a
    number the pipeline did not compute."""

    operation_id: str
    task: str
    kind: str
    sequence: int
    text: str
    #: The workflow's own statement of what it found, or "" when it had none.
    detail: str = ""
    #: `event` | `task_default` | `generic`. Serialised so a FALLBACK is
    #: visible in the payload rather than reading as a real milestone.
    source: str = "event"
    terminal: bool = False


class ActivityOut(BaseModel):
    operation_id: str
    task: str
    label: str
    state: str
    line: ActivityLineOut | None = None
    log: list[ActivityLineOut] = []
    dropped_after_terminal: int = 0


class MatchingTaskStatusOut(BaseModel):
    task_id: str
    state: str
    done: bool
    #: Always the full list, so the page draws every step immediately and fills
    #: them in, rather than appearing to discover its own plan as it goes.
    stages: list[MatchingStageOut] = []
    #: How many candidates this run is scoring, and how many are finished.
    #: Counts of rows, not ratings: no score, grade or rank is implied.
    candidate_count: int = 0
    scored_count: int = 0
    #: What the run is doing, in one sentence, for the AI activity
    #: indicator. OPTIONAL so an older worker mid-deploy simply reports no
    #: activity rather than 500ing the page that polls it.
    activity: ActivityOut | None = None


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


# The Matching category list schemas are DELETED with the editor and its
# routes (Vivekium release). What matching reads is Phase 2's concern.
