"""What the AI matching run is doing right now, as structured stage events.

WHY THIS EXISTS
---------------
Running AI matching used to open a modal dialog that said "Scoring and writing
remarks" and blocked the page until the task finished. For a pool of forty
candidates that is minutes of a spinner over a sentence that never changes,
which tells the recruiter nothing about whether the work is progressing, stuck,
or degraded -- and it takes the rest of the page away from them while they wait.

The replacement is the reasoning shown inline on the page itself: the recruiter
watches the run walk through retrieval, fusion and scoring, and keeps the page.

WHY IT IS A FIXED VOCABULARY AND NOT MODEL TEXT
-----------------------------------------------
This is the important constraint, and it is the reason this module is a table
rather than a passthrough. What is displayed is a STAGE KEY chosen from the
list below, plus counters. It is never a model's own narration of its
reasoning, for two reasons that both matter:

  * A trace carries identifiers, counts and timings, and never content
    (claude.md). Model narration quotes the prompt, and this prompt contains a
    real candidate's resume and a real client's job description. "Now I'm
    considering whether Priya's Kafka experience is deep enough" is a sentence
    about a named person that would be rendered to whoever has the page open.
  * A narration is generated, so it can be wrong. A model that says it is
    "checking education" while the code is in the keyword stage has produced a
    convincing progress display that describes work nobody did. The stages here
    are emitted BY the pipeline at the point the pipeline reaches them, so the
    display cannot describe work that did not happen.

The stages are therefore honest about degradation too: a run whose embedding
service was unavailable marks the semantic stage `skipped` and says so, rather
than showing a green tick over a stage that did not run.

TRANSPORT
---------
Celery's own task state. `update_state(state=PROGRESS, meta=...)` writes to the
Redis result backend, which the status endpoint already reads through
`AsyncResult`. No new table, no new schema, and no second thing to keep in
step with the task's real lifecycle -- the progress and the terminal state come
from one place, so a finished task cannot still be showing a stage as active.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: The Celery state name for a run that is under way. Deliberately NOT one of
#: Celery's built-in states: `AsyncResult.ready()` must keep answering False
#: while this is set, or the client would stop polling at the first stage.
STATE_PROGRESS = "PROGRESS"

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    #: One plain sentence. Written for a recruiter, not an engineer: it says
    #: what is being done to their data, not which module is running.
    detail: str


#: The pipeline's real stages, in the order `matching.run_matching` performs
#: them. Adding a stage here without emitting it leaves a row that never
#: advances, which is worse than not showing it -- so the test suite asserts
#: every key in this list is emitted by the pipeline.
STAGES: tuple[Stage, ...] = (
    Stage(
        "understanding",
        "Understanding the role",
        "Reading the job description and the matching categories saved for this job.",
    ),
    Stage(
        "planning",
        "Planning the pass",
        "Deciding which candidates to retrieve and what evidence each category needs.",
    ),
    Stage(
        "jd_embedding",
        "Representing the JD",
        "Turning the job description into the semantic form used for retrieval.",
    ),
    Stage(
        "preparing_candidates",
        "Preparing resumes",
        "Making sure every linked resume is parsed and indexed before retrieval runs.",
    ),
    Stage(
        "semantic_retrieval",
        "Semantic search",
        "Finding resumes that mean the same thing as the role, not just resumes that repeat its words.",
    ),
    Stage(
        "keyword_retrieval",
        "Keyword and skills search",
        "Matching the role's named skills and technologies against resume text.",
    ),
    Stage(
        "fusion",
        "Merging the results",
        "Combining both searches, then adding every candidate linked to this job so retrieval never decides who gets scored.",
    ),
    Stage(
        "prescreen",
        "Reading the evidence",
        "Reading what each resume actually evidences, and how strongly, before any scoring happens.",
    ),
    Stage(
        "scoring",
        "Scoring against the categories",
        "Assessing each candidate against this job's own matching categories.",
    ),
    Stage(
        "remarks",
        "Writing the remarks",
        "Writing the rated comment for each category.",
    ),
    Stage(
        "saving",
        "Saving the results",
        "Recording each rating against the candidate's application.",
    ),
)

STAGE_KEYS: tuple[str, ...] = tuple(stage.key for stage in STAGES)
_BY_KEY: dict[str, Stage] = {stage.key: stage for stage in STAGES}


class UnknownStage(KeyError):
    """A key not in STAGES. Refused rather than displayed: a stage the pipeline
    invented at runtime is exactly the free-text narration this avoids."""


@dataclass
class Progress:
    """The live stage list for one matching run.

    Never raises into the pipeline. A progress display that can fail the work it
    is describing is a strictly worse trade than a display that goes blank: the
    recruiter still gets their ratings, and the fallback is the terminal state
    the endpoint already reports.
    """

    #: Called with the whole payload each time it changes. In the worker this
    #: is `task.update_state`; in a test it is a list append.
    publish: Callable[[dict[str, Any]], None] | None = None
    candidate_count: int = 0
    scored_count: int = 0
    _status: dict[str, str] = field(default_factory=dict)
    _note: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in STAGE_KEYS:
            self._status.setdefault(key, STATUS_PENDING)

    # ── Transitions ─────────────────────────────────────────────────────────

    def start(self, key: str, note: str = "") -> None:
        """Mark a stage active. Everything before it that is still pending is
        marked done: a stage that ran without an explicit finish would otherwise
        sit spinning forever above a stage that has already moved on."""
        self._require(key)
        for earlier in STAGE_KEYS[: STAGE_KEYS.index(key)]:
            if self._status[earlier] == STATUS_PENDING:
                self._status[earlier] = STATUS_DONE
            elif self._status[earlier] == STATUS_ACTIVE:
                self._status[earlier] = STATUS_DONE
        self._status[key] = STATUS_ACTIVE
        if note:
            self._note[key] = note
        self._emit()

    def finish(self, key: str, note: str = "") -> None:
        self._require(key)
        self._status[key] = STATUS_DONE
        if note:
            self._note[key] = note
        self._emit()

    def skip(self, key: str, note: str) -> None:
        """A stage that genuinely did not run, and WHY.

        A skipped semantic stage means the embedding service was unavailable and
        the ranking is keyword-only. Showing that as complete would present a
        degraded run as a full one, which is the failure mode the whole
        degradation posture exists to prevent.
        """
        self._require(key)
        self._status[key] = STATUS_SKIPPED
        self._note[key] = note
        self._emit()

    def fail(self, key: str, note: str) -> None:
        self._require(key)
        self._status[key] = STATUS_FAILED
        self._note[key] = note
        self._emit()

    def scored(self, done: int, total: int) -> None:
        """Progress WITHIN the scoring stage, which is the long one."""
        self.scored_count = max(0, int(done))
        self.candidate_count = max(0, int(total))
        self._emit()

    # ── Payload ─────────────────────────────────────────────────────────────

    def payload(self) -> dict[str, Any]:
        return {
            "stages": [
                {
                    "key": stage.key,
                    "label": stage.label,
                    "detail": self._note.get(stage.key) or stage.detail,
                    "status": self._status[stage.key],
                }
                for stage in STAGES
            ],
            "candidate_count": self.candidate_count,
            "scored_count": self.scored_count,
        }

    # ── Internals ───────────────────────────────────────────────────────────

    def _require(self, key: str) -> None:
        if key not in _BY_KEY:
            raise UnknownStage(key)

    def _emit(self) -> None:
        if self.publish is None:
            return
        try:
            self.publish(self.payload())
        except Exception:  # noqa: BLE001 -- see the class docstring
            logger.debug("matching_progress.publish_failed", exc_info=True)


def empty_payload() -> dict[str, Any]:
    """The stage list before the worker has picked the task up.

    Returned for a queued task so the page draws the full list immediately and
    fills in, rather than appearing one row at a time and looking like it is
    discovering the plan as it goes.
    """
    return Progress().payload()
