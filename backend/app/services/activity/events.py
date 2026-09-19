"""The typed AI activity event, and the two closed vocabularies it names.

PROVENANCE
----------
`ai-upgrade-spec-doc.md` "Case 3", sections 7, 8 and 24: a workflow emits a
structured event, the renderer turns it into one user-facing sentence, and the
sentence depends on the TASK as well as the event. Section 8 is the load-bearing
one: `REQUIREMENTS_IDENTIFIED` under `job_candidate_matching` and the same kind
under `assessment_report` are different sentences, so the pair is the key and
neither half alone is.

WHY BOTH VOCABULARIES ARE CLOSED
--------------------------------
An unknown kind or an unknown task would fall through to the generic line, and
the generic line is specified as the LAST resort rather than the normal
experience. A fall-through that happens at runtime, on a recruiter's screen, is
indistinguishable from the rotating-loading-message system this feature exists
to replace. So both are frozensets, `tests/test_ai_activity.py` asserts that
every declared workflow has a phrase for every kind it declares, and the build
fails rather than a screen degrading.

WHAT AN EVENT MAY CARRY
-----------------------
`facts` are values the workflow ACTUALLY computed. Nothing else may become a
number in the rendered line (Case 3 section 18, and claude.md's "a timestamp is
not evidence that work happened" applied to UI copy). `note` is the workflow's
own sentence about what it found, passed through verbatim and kept in its own
field so it can never be mistaken for catalogue copy.

There is no timestamp on an event and that is deliberate. A clock reading proves
only that a clock was read; `sequence` records the order the workflow actually
reached these points in, which is the thing a reader is being told.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# ── Task kinds: one per AI workflow that reports activity ────────────────────
#
# The name is the workflow, not the module. It is what makes the same event kind
# render differently, so it is chosen for what the user is having done for them.

TASK_JOB_CANDIDATE_MATCHING = "job_candidate_matching"
TASK_ASSESSMENT_REPORT = "assessment_report"

TASK_KINDS: frozenset[str] = frozenset(
    {
        TASK_JOB_CANDIDATE_MATCHING,
        TASK_ASSESSMENT_REPORT,
    }
)


# ── Event kinds ──────────────────────────────────────────────────────────────
#
# The seven names Case 3 section 8 lists verbatim, plus the ones this product's
# real pipelines actually reach. A kind exists only where a workflow genuinely
# passes through it: `SEMANTIC_SEARCH_COMPLETED` is here because
# `matching.run_matching` really does run a vector stage and really does skip it
# when the embedding service is down, and `STEP_UNAVAILABLE` is here so that
# skip can be reported as a skip rather than shown as a completed step.

TASK_STARTED = "TASK_STARTED"
INPUT_PREPARED = "INPUT_PREPARED"
DOCUMENT_PARSED = "DOCUMENT_PARSED"
REQUIREMENTS_IDENTIFIED = "REQUIREMENTS_IDENTIFIED"
SEMANTIC_SEARCH_COMPLETED = "SEMANTIC_SEARCH_COMPLETED"
KEYWORD_SEARCH_COMPLETED = "KEYWORD_SEARCH_COMPLETED"
CANDIDATE_POOL_ASSEMBLED = "CANDIDATE_POOL_ASSEMBLED"
EVIDENCE_READ = "EVIDENCE_READ"
SKILLS_COMPARED = "SKILLS_COMPARED"
GAPS_IDENTIFIED = "GAPS_IDENTIFIED"
RECOMMENDATIONS_GENERATED = "RECOMMENDATIONS_GENERATED"
RESULTS_RECORDED = "RESULTS_RECORDED"
STEP_UNAVAILABLE = "STEP_UNAVAILABLE"
TASK_COMPLETED = "TASK_COMPLETED"
TASK_FAILED = "TASK_FAILED"
TASK_CANCELLED = "TASK_CANCELLED"

EVENT_KINDS: frozenset[str] = frozenset(
    {
        TASK_STARTED,
        INPUT_PREPARED,
        DOCUMENT_PARSED,
        REQUIREMENTS_IDENTIFIED,
        SEMANTIC_SEARCH_COMPLETED,
        KEYWORD_SEARCH_COMPLETED,
        CANDIDATE_POOL_ASSEMBLED,
        EVIDENCE_READ,
        SKILLS_COMPARED,
        GAPS_IDENTIFIED,
        RECOMMENDATIONS_GENERATED,
        RESULTS_RECORDED,
        STEP_UNAVAILABLE,
        TASK_COMPLETED,
        TASK_FAILED,
        TASK_CANCELLED,
    }
)

#: The three kinds that END an operation. A stream that has emitted one of these
#: accepts nothing further, which is what stops "analysing your document" from
#: staying on screen after the request failed (Case 3 section 25).
TERMINAL_KINDS: frozenset[str] = frozenset(
    {TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED}
)


# ── Stream states ────────────────────────────────────────────────────────────

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

TERMINAL_STATES: frozenset[str] = frozenset(
    {STATE_DONE, STATE_ERROR, STATE_CANCELLED}
)


# ── Where a rendered sentence came from ──────────────────────────────────────
#
# Carried on every line and serialised to the client. The point is that the
# fallback is OBSERVABLE: a screen showing `generic` is a screen the catalogue
# had nothing for, and that is a defect somebody can see and fix rather than a
# silent degradation to a loading message.

SOURCE_EVENT = "event"
SOURCE_TASK_DEFAULT = "task_default"
SOURCE_GENERIC = "generic"


class UnknownTask(KeyError):
    """A task kind not in `TASK_KINDS`.

    Refused rather than rendered generically. An unregistered workflow has no
    declared stages, no completion phrase and no failure phrase, so what it
    would produce is exactly the generic rotation this feature replaces.
    """


class UnknownEvent(KeyError):
    """An event kind not in `EVENT_KINDS`. Refused for the same reason."""


@dataclass(frozen=True)
class ActivityEvent:
    """One meaningful milestone a workflow actually reached.

    `facts` hold values the workflow COMPUTED. A fact that is absent is not
    defaulted: the renderer drops to the phrase that needs no facts, so a count
    can never appear unless the pipeline supplied it.

    `note` is the workflow's own sentence about what it found. It is kept
    separate from the rendered text for a provenance reason: catalogue copy is
    swept by the test suite for numbers, grade words and em dashes, and a note
    is computed at the call site from real values. Merging them would make the
    sweep look like it covered text it never saw.
    """

    task: str
    kind: str
    facts: Mapping[str, int | str] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if self.task not in TASK_KINDS:
            raise UnknownTask(self.task)
        if self.kind not in EVENT_KINDS:
            raise UnknownEvent(self.kind)

    @property
    def terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS


@dataclass(frozen=True)
class ActivityLine:
    """One rendered activity sentence, ready to cross the API boundary."""

    operation_id: str
    task: str
    kind: str
    sequence: int
    text: str
    #: The workflow's own fact statement, verbatim, or "" when it gave none.
    detail: str
    source: str
    terminal: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "task": self.task,
            "kind": self.kind,
            "sequence": self.sequence,
            "text": self.text,
            "detail": self.detail,
            "source": self.source,
            "terminal": self.terminal,
        }
