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

THE SENTENCES NOW COME FROM THE ACTIVITY CATALOGUE
---------------------------------------------------
Each stage declares WHICH ACTIVITY EVENT it is, and its description is the
sentence `services/activity/phrasing` holds for the pair (matching, that event).
There is one place a sentence about this work is written, so the stage list and
the AI activity line cannot drift into two different descriptions of the same
step. This module owns the pipeline's stage MACHINE; the catalogue owns the
words. `ai-upgrade-spec-doc.md` "Case 3" is the specification for the second
half, and its section 8 is why the key is a pair rather than an event alone:
the identical event under a different task is a different sentence.

TRANSPORT
---------
The task's own run status. `TaskContext.publish` writes the payload to the same
Redis record that carries the terminal state, and `app/api/matching` reads it
back through `workers.status.read`. No new table, no new schema, and no second
thing to keep in step with the task's real lifecycle -- the progress and the
outcome come from one place, so a finished task cannot still be showing a stage
as active. The activity block travels in that same payload under `activity`,
for the same reason.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.services.activity import events as activity_events
from app.services.activity import phrasing as activity_phrasing
from app.services.activity.stream import ActivityStream

logger = logging.getLogger(__name__)

#: The state name for a run that is under way. Deliberately NOT one of the two
#: TERMINAL states: `RunStatus.done` must keep answering False while this is
#: set, or the client would stop polling at the first stage. The name is
#: unchanged from the Celery state it replaced, because the frontend's state
#: union and the response schema were both written against it.
STATE_PROGRESS = "PROGRESS"

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

#: Which AI workflow this module reports. One constant rather than a literal per
#: lookup, because it is half of every catalogue key below.
TASK = activity_events.TASK_JOB_CANDIDATE_MATCHING


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    #: The activity event this stage IS. It is what makes the stage's sentence
    #: and the AI activity line the same string rather than two hand-written
    #: descriptions of one step that can disagree after an edit.
    event: str

    @property
    def detail(self) -> str:
        """One plain sentence, from the activity catalogue.

        Written for a recruiter, not an engineer: it says what is being done to
        their data, not which module is running. Raises for an event the
        catalogue has no phrase for, which is a build-time failure rather than a
        blank row on a running screen.
        """
        return activity_phrasing.plain_text(TASK, self.event)


#: The pipeline's real stages, in the order `matching.run_matching` performs
#: them. Adding a stage here without emitting it leaves a row that never
#: advances, which is worse than not showing it -- so the test suite asserts
#: every key in this list is emitted by the pipeline.
STAGES: tuple[Stage, ...] = (
    Stage("understanding", "Understanding the role", activity_events.TASK_STARTED),
    Stage("planning", "Planning the pass", activity_events.REQUIREMENTS_IDENTIFIED),
    Stage("jd_embedding", "Representing the JD", activity_events.INPUT_PREPARED),
    Stage(
        "preparing_candidates",
        "Preparing resumes",
        activity_events.DOCUMENT_PARSED,
    ),
    Stage(
        "semantic_retrieval",
        "Semantic search",
        activity_events.SEMANTIC_SEARCH_COMPLETED,
    ),
    Stage(
        "keyword_retrieval",
        "Keyword and skills search",
        activity_events.KEYWORD_SEARCH_COMPLETED,
    ),
    Stage("fusion", "Merging the results", activity_events.CANDIDATE_POOL_ASSEMBLED),
    Stage(
        "validation_fit",
        "Checking application answers",
        activity_events.VALIDATION_CHECKED,
    ),
    Stage(
        "scoring",
        "Checking resumes against the skills",
        activity_events.SKILLS_COMPARED,
    ),
    Stage(
        "grounding",
        "Checking every tag against the resume",
        activity_events.EVIDENCE_GROUNDED,
    ),
    Stage("saving", "Saving the results", activity_events.RESULTS_RECORDED),
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

    It is also the matching workflow's ACTIVITY EMITTER. Every transition below
    already marks a milestone the pipeline genuinely reached, so the activity
    line is derived from the same call rather than from a second set of hooks
    somebody has to remember to add. `operation_id` should be the dispatched
    run id: the browser is holding that before the first event exists, so it can
    tell one run's payload from another's from the first poll (Case 3 §24).
    """

    #: Called with the whole payload each time it changes. In a running task
    #: this is `runtime.TaskContext.publish`; in a test it is a list append.
    publish: Callable[[dict[str, Any]], None] | None = None
    candidate_count: int = 0
    scored_count: int = 0
    #: The dispatched run id, when the caller has one.
    operation_id: str = ""
    #: Facts true for the whole run, such as the job title. Merged under every
    #: event's own facts by the stream, so a call site cannot forget one.
    context: Mapping[str, int | str] | None = None
    _status: dict[str, str] = field(default_factory=dict)
    _note: dict[str, str] = field(default_factory=dict)
    #: Why this run is not a full one, in the server's own words, in the
    #: order the pipeline found out. A non-empty list IS the degraded flag.
    _degraded: list[str] = field(default_factory=list)
    _activity: ActivityStream = field(init=False)

    def __post_init__(self) -> None:
        for key in STAGE_KEYS:
            self._status.setdefault(key, STATUS_PENDING)
        # No `publish` on the stream: this class publishes ONE payload carrying
        # both the stage list and the activity block, so the two can never be
        # read a poll apart from each other.
        self._activity = ActivityStream(
            TASK, operation_id=self.operation_id, context=self.context
        )
        self.operation_id = self._activity.operation_id

    # ── Transitions ─────────────────────────────────────────────────────────

    def start(self, key: str, note: str = "") -> None:
        """Mark a stage active. Everything before it that is still pending is
        marked done: a stage that ran without an explicit finish would otherwise
        sit spinning forever above a stage that has already moved on."""
        stage = self._require(key)
        for earlier in STAGE_KEYS[: STAGE_KEYS.index(key)]:
            if self._status[earlier] == STATUS_PENDING:
                self._status[earlier] = STATUS_DONE
            elif self._status[earlier] == STATUS_ACTIVE:
                self._status[earlier] = STATUS_DONE
        self._status[key] = STATUS_ACTIVE
        if note:
            self._note[key] = note
        # The activity line for work that is starting: what is being done now.
        self._activity.emit(stage.event, facts=self._facts(), note=note)
        self._emit()

    def finish(self, key: str, note: str = "") -> None:
        stage = self._require(key)
        self._status[key] = STATUS_DONE
        if note:
            self._note[key] = note
            # A finish that carries a note carries a FINDING the pipeline
            # computed, which is the second half of the progression Case 3 §10
            # asks for: the activity, then what it turned up. A finish with
            # nothing new to say emits nothing, so the line does not flicker
            # between two renderings of the same sentence.
            self._activity.emit(stage.event, facts=self._facts(), note=note)
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
        self._activity.unavailable(note)
        self._emit()

    def fail(self, key: str, note: str) -> None:
        """A stage that broke. Terminal for the activity line.

        The stage row keeps the pipeline's own reason, as it always has. The
        activity line gets the workflow's fixed failure sentence and no reason
        at all, because an activity line is the surface a browser renders and
        the reason a run fails is the one string most likely to quote a row, a
        path or a prompt. `workers.status` makes the same trade by recording an
        exception's class name and never its message.
        """
        self._require(key)
        self._status[key] = STATUS_FAILED
        self._note[key] = note
        self._activity.fail()
        self._emit()

    def degrade(self, reason: str) -> None:
        """Record that the run as a WHOLE is not a full one, and why.

        Separate from `skip`, which is about one stage: an embedding outage
        skips a stage AND degrades the run, while a model failure on three
        candidates skips no stage and still means three rows read "Not
        assessed". The sentence is the pipeline's own, rendered verbatim by
        the job page, and a repeated reason is recorded once.
        """
        if reason and reason not in self._degraded:
            self._degraded.append(reason)
            self._emit()

    def describe(self, **facts: int | str) -> None:
        """Record facts that hold for the whole run, such as the role title.

        Separate from `start` because they become known at a different moment:
        the pipeline opens the run before it has loaded the job. Every line from
        here on may use them, and a fact the catalogue does not declare is
        refused by the renderer rather than interpolated, so a caller cannot
        push arbitrary text into a sentence through this door.
        """
        self._activity.context.update(facts)

    def scored(self, done: int, total: int) -> None:
        """Progress WITHIN the scoring stage, which is the long one.

        Emits no activity line. It is called once per scoring batch, and a line
        per batch would be text changing under the reader without any new
        milestone behind it, which is the animation Case 3 §21 rules out. The
        counts it records are what later lines are allowed to say.
        """
        self.scored_count = max(0, int(done))
        self.candidate_count = max(0, int(total))
        self._emit()

    def complete(self) -> None:
        """The run finished. Terminal for the activity line.

        Separate from `finish("saving")` because the last stage completing and
        the operation being over are different facts: the pipeline can return
        early with no candidates to score, having completed nothing after
        fusion.
        """
        self._activity.complete(facts=self._facts())
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
            "degraded": bool(self._degraded),
            "degraded_reasons": list(self._degraded),
            "activity": self._activity.payload(),
        }

    # ── Internals ───────────────────────────────────────────────────────────

    def _facts(self) -> dict[str, int | str]:
        """The counts this run has genuinely established, and only those.

        `candidate_count` is set by `scored()` from the real pool size, so
        before the pool exists it is zero and nothing is claimed about it. A
        zero is dropped rather than spoken: "0 candidates" is a true sentence
        about a run that has not counted yet, which is exactly the false
        precision Case 3 §18 forbids.
        """
        facts: dict[str, int | str] = {}
        if self.candidate_count > 0:
            facts["candidate_count"] = self.candidate_count
        return facts

    def _require(self, key: str) -> Stage:
        stage = _BY_KEY.get(key)
        if stage is None:
            raise UnknownStage(key)
        return stage

    def _emit(self) -> None:
        if self.publish is None:
            return
        try:
            self.publish(self.payload())
        except Exception:  # noqa: BLE001 -- see the class docstring
            logger.debug("matching_progress.publish_failed", exc_info=True)


def empty_payload(operation_id: str = "") -> dict[str, Any]:
    """The stage list before the worker has picked the task up.

    Returned for a queued task so the page draws the full list immediately and
    fills in, rather than appearing one row at a time and looking like it is
    discovering the plan as it goes.

    `operation_id` should be the polled run id. The reader is already watching
    that id, so stamping it here means the very first response is attributable
    to the run they started rather than to an anonymous placeholder that a
    second run's payload could be mistaken for.
    """
    return Progress(operation_id=operation_id).payload()
