"""One AI operation's activity, from first milestone to terminal state.

PROVENANCE
----------
`ai-upgrade-spec-doc.md` "Case 3" sections 22, 24 and 25: one identifier per
operation so two runs cannot overwrite each other's status, a status that
evolves as real milestones are reached, and a terminal state that STOPS the
activity rather than leaving the last sentence on screen after a failure.

THE OPERATION ID IS THE CONCURRENCY ANSWER
-------------------------------------------
Every line carries it, and the payload carries it. A client that started
operation A ignores a payload stamped B, so a slower earlier run cannot repaint
over a newer one, a finished run cannot hide a running one, and a payload that
arrives after the user has navigated away belongs to an operation nobody is
watching. Nothing about that depends on client-side timing.

The id is supplied by the caller wherever the caller already has one. For a
dispatched task that is the run id, which `dispatch` generates client-side
before the invoke completes, so the browser is holding the id before the first
event exists. Inventing a second identifier would mean the browser had to learn
it from the first payload, and until then it could not tell whose payload it
was looking at.

TERMINAL MEANS TERMINAL
-----------------------
After `complete`, `fail` or `cancel`, `emit` records nothing. A late milestone
from work that was already abandoned is exactly the stale message section 24
lists, and the count of what was dropped is published rather than swallowed, so
a workflow emitting after its own terminal state is visible rather than silent.

NEVER RAISES INTO THE WORK IT DESCRIBES
----------------------------------------
Same rule as `matching_progress.Progress` and `workers.status.write`, for the
same reason: a status display that can fail the task it reports on is a strictly
worse trade than a display that goes blank. The one exception is a programming
error, an unregistered task or an undeclared event kind, which is refused at
construction and at `emit` because it is a bug rather than a runtime condition.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Mapping

from app.services.activity import events as ev
from app.services.activity import render as render_mod
from app.services.activity import workflows

logger = logging.getLogger(__name__)

#: How many rendered lines are kept. A matching run over a large pool emits one
#: line per stage plus one per degradation, so this is generous; the bound
#: exists because the payload is serialised into Redis on every change and read
#: by a browser, and an unbounded log would grow both without limit (Case 3
#: section 27). The OLDEST are dropped, never the newest: the reader is being
#: shown what is happening now.
MAX_LINES = 40


class ActivityStream:
    """The activity for one AI operation.

    `publish` is called with the whole payload each time it changes. In a
    dispatched task that is `workers.runtime.TaskContext.publish`; in a test it
    is a list append. It is optional in the strong sense: every method works
    with it absent, which is what lets a workflow emit unconditionally instead
    of guarding every call site.
    """

    def __init__(
        self,
        task: str,
        *,
        operation_id: str = "",
        publish: Callable[[dict[str, Any]], None] | None = None,
        context: Mapping[str, int | str] | None = None,
    ) -> None:
        # Raises on an unregistered task: a workflow with no declaration has no
        # completion phrase and no failure phrase, so its status would be the
        # generic line for its whole life.
        self.workflow = workflows.workflow_for(task)
        self.task = task
        self.operation_id = operation_id or uuid.uuid4().hex
        self.publish = publish
        #: Facts that hold for the WHOLE operation, merged under each event's
        #: own facts. The job title is the example: it is true at every
        #: milestone, and making each call site repeat it is how one of them
        #: ends up omitting it.
        self.context: dict[str, int | str] = dict(context or {})
        self.state: str = ev.STATE_IDLE
        self.lines: list[ev.ActivityLine] = []
        self._sequence = 0
        self._dropped = 0

    # ── Emission ────────────────────────────────────────────────────────────

    def emit(
        self,
        kind: str,
        *,
        facts: Mapping[str, int | str] | None = None,
        note: str = "",
    ) -> ev.ActivityLine | None:
        """Record one milestone the workflow actually reached.

        Returns the rendered line, or None when the stream is already terminal.
        """
        if kind not in ev.EVENT_KINDS:
            raise ev.UnknownEvent(kind)
        if self.state in ev.TERMINAL_STATES:
            self._dropped += 1
            logger.warning(
                "activity.emit_after_terminal task=%s operation_id=%s kind=%s state=%s",
                self.task,
                self.operation_id,
                kind,
                self.state,
            )
            return None

        merged: dict[str, int | str] = dict(self.context)
        merged.update(facts or {})
        event = ev.ActivityEvent(task=self.task, kind=kind, facts=merged, note=note)
        self._sequence += 1
        line = render_mod.render(
            event, operation_id=self.operation_id, sequence=self._sequence
        )
        self.lines.append(line)
        if len(self.lines) > MAX_LINES:
            del self.lines[0 : len(self.lines) - MAX_LINES]

        if kind == ev.TASK_COMPLETED:
            self.state = ev.STATE_DONE
        elif kind == ev.TASK_FAILED:
            self.state = ev.STATE_ERROR
        elif kind == ev.TASK_CANCELLED:
            self.state = ev.STATE_CANCELLED
        else:
            self.state = ev.STATE_RUNNING
        self._emit_payload()
        return line

    # ── Terminal states ─────────────────────────────────────────────────────

    def complete(self, *, facts: Mapping[str, int | str] | None = None) -> None:
        self.emit(ev.TASK_COMPLETED, facts=facts)

    def fail(self) -> None:
        """End the operation with the task's own failure sentence.

        Takes NO argument, which is the whole design. The exception message is
        the one thing a failure has to say and the one thing that must not be
        said: it can quote a row, a prompt or an internal path, and this payload
        is read by a recruiter's browser. `workers.status` already makes exactly
        this trade for the same reason, recording the exception CLASS NAME and
        nothing else.
        """
        self.emit(ev.TASK_FAILED)

    def cancel(self) -> None:
        self.emit(ev.TASK_CANCELLED)

    def unavailable(self, note: str) -> None:
        """A step that genuinely did not run, and why. Not terminal.

        The degraded case has to be reportable, or a run that lost half its
        pipeline looks identical to one that did everything. `matching_progress`
        already makes this argument about its `skipped` status; this is the same
        fact on the activity line.
        """
        self.emit(ev.STEP_UNAVAILABLE, note=note)

    # ── Payload ─────────────────────────────────────────────────────────────

    @property
    def current(self) -> ev.ActivityLine | None:
        return self.lines[-1] if self.lines else None

    def payload(self) -> dict[str, Any]:
        current = self.current
        return {
            "operation_id": self.operation_id,
            "task": self.task,
            "label": self.workflow.label,
            "state": self.state,
            "line": current.as_dict() if current is not None else None,
            "log": [line.as_dict() for line in self.lines],
            # Published rather than swallowed: a non-zero value means a workflow
            # kept emitting after it had already ended, which is a defect in the
            # workflow and not in this module.
            "dropped_after_terminal": self._dropped,
        }

    def _emit_payload(self) -> None:
        if self.publish is None:
            return
        try:
            self.publish(self.payload())
        except Exception:  # noqa: BLE001 -- see the module docstring
            logger.debug("activity.publish_failed", exc_info=True)


def idle_payload(task: str, *, operation_id: str = "") -> dict[str, Any]:
    """The shape before the workflow has emitted anything.

    Returned for an operation that has been accepted but not picked up, so the
    reader sees the operation exists rather than an empty region that fills in
    later and jumps the layout.
    """
    return ActivityStream(task, operation_id=operation_id).payload()
