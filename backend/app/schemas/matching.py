"""AI Matching schemas: starting a run and reading its progress.

The per-candidate result is NOT here. It is the ranked table's row
(`schemas/ranking.py`), in words; the old `MatchResultOut` carried the retired
per-category comments and the route that served it had no caller.
"""
import uuid

from pydantic import BaseModel


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
    #: True when the run could not do everything it set out to (the embedding
    #: service was unavailable, some candidates could not be assessed), with
    #: the server's own sentences saying what. A degraded run shown as a full
    #: one is the failure mode the stage list exists to prevent.
    degraded: bool = False
    degraded_reasons: list[str] = []


# The Matching category list schemas are DELETED with the editor and its
# routes (Vivekium release). What matching reads is Phase 2's concern.
