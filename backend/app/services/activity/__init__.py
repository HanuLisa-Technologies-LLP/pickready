"""Context-aware, task-specific AI activity status.

WHAT THIS IS
------------
The one place a running AI workflow says what it is doing. A workflow emits a
typed event; this package renders it into one sentence chosen by the pair
(task, event), so the same milestone reads differently depending on which piece
of work is running. `ai-upgrade-spec-doc.md` "Case 3" is the specification.

WHAT THIS IS NOT
----------------
It is not a timer over a list of generic messages. Nothing here is scheduled,
nothing rotates, and no sentence is emitted unless a workflow actually reached
the milestone it names. It is also not a second model call: the renderer is
ordinary code, so it adds no latency, no token spend and no way for the status
to contradict the execution it is describing.

THE FOUR MODULES
----------------
  events      the typed event, and the two closed vocabularies it names
  phrasing    the fixed copy catalogue, keyed by (task, event)
  render      one event to one sentence, with the priority order and the
              fact validation that keeps counts honest
  stream      one operation: its id, its ordered lines, its terminal state
  workflows   what each AI workflow declares, and what the build holds it to

WIRED WORKFLOWS
---------------
  job_candidate_matching   `services/matching_progress` emits from inside the
                           matching pipeline's real stage transitions
  assessment_report        the assessment scoring and PRISM synthesis path
"""
from app.services.activity.events import (
    EVENT_KINDS,
    SOURCE_EVENT,
    SOURCE_GENERIC,
    SOURCE_TASK_DEFAULT,
    STATE_CANCELLED,
    STATE_DONE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RUNNING,
    TASK_ASSESSMENT_REPORT,
    TASK_JOB_CANDIDATE_MATCHING,
    TASK_KINDS,
    TERMINAL_KINDS,
    TERMINAL_STATES,
    ActivityEvent,
    ActivityLine,
    UnknownEvent,
    UnknownTask,
)
# Deliberately NOT re-exported under the bare name `render`. Binding a function
# to the package attribute that already names the `render` SUBMODULE shadows it,
# and `from app.services.activity import render` then hands the caller a
# function where it asked for a module. claude.md records this exact trap from
# the Miti test harness; here it would break `stream.py`'s own import.
from app.services.activity.render import render as render_line
from app.services.activity.stream import ActivityStream, idle_payload
from app.services.activity.workflows import WORKFLOWS, Workflow, workflow_for

__all__ = [
    "ActivityEvent",
    "ActivityLine",
    "ActivityStream",
    "EVENT_KINDS",
    "SOURCE_EVENT",
    "SOURCE_GENERIC",
    "SOURCE_TASK_DEFAULT",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_IDLE",
    "STATE_RUNNING",
    "TASK_ASSESSMENT_REPORT",
    "TASK_JOB_CANDIDATE_MATCHING",
    "TASK_KINDS",
    "TERMINAL_KINDS",
    "TERMINAL_STATES",
    "UnknownEvent",
    "UnknownTask",
    "WORKFLOWS",
    "Workflow",
    "idle_payload",
    "render_line",
    "workflow_for",
]
