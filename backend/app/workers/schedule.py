"""The periodic tasks, and how often each one runs.

This replaces Celery beat. Beat was a PROCESS that had to be running, singleton,
and whose failure mode was silence: nothing fires, nothing errors, and the only
symptom is a sweep that stopped repairing things. EventBridge Scheduler is the
replacement, one rule per entry, invoking the generic worker Lambda with the
same payload `dispatch` would have sent.

WHY THE SCHEDULE LIVES HERE AND NOT ONLY IN TERRAFORM
------------------------------------------------------
Because a schedule entry and a task registration have to agree, and this
codebase has already paid for them disagreeing: a beat entry fired
`pickready.probe_llm_models` every hour for a whole release after the module it
imported was deleted. Nothing in the suite touched it, because nothing in the
suite ever called it.

Keeping the list in Python means `tests/test_task_schedule.py` can assert that
every entry names a task the registry actually has. Terraform then MIRRORS this
list, and `tests/test_schedule_parity.py` reads both and fails if they drift, so
neither half can be edited alone. That is the same discipline
`test_runbook_parity.py` applies to the hiring weights, for the same reason: two
copies of one fact stay honest only when something compares them.

RATE EXPRESSIONS, NOT CRON
--------------------------
Every entry here is an interval, exactly as the beat schedule was, so the
EventBridge form is `rate(...)`. None of these sweeps care what time of day
they run: they are idempotent repairs that do nothing when there is nothing to
repair. A cron expression would add a timezone to reason about and buy nothing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduledTask:
    #: The EventBridge Scheduler rule name. Kept stable across deploys: the
    #: rule is addressed by name, so renaming one creates a second rule and
    #: leaves the first firing.
    rule: str
    task: str
    interval_minutes: int
    why: str

    @property
    def rate_expression(self) -> str:
        unit = "minute" if self.interval_minutes == 1 else "minutes"
        return f"rate({self.interval_minutes} {unit})"


SCHEDULE: tuple[ScheduledTask, ...] = (
    ScheduledTask(
        rule="readypick-refresh-dashboard-views",
        task="pickready.refresh_dashboard_views",
        interval_minutes=5,
        why="The dashboard reads materialised views (ESD section 14).",
    ),
    ScheduledTask(
        rule="readypick-remind-unapproved-framework",
        task="pickready.remind_unapproved_technical_questions",
        interval_minutes=60,
        why=(
            "A job whose Tatva matrix nobody approved keeps taking applications "
            "and can invite nobody, and nothing on the screen says why. The task "
            "keeps its old name because renaming a task and its schedule "
            "atomically is not something a rolling deploy can guarantee."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-job-setup",
        task="pickready.reconcile_job_setup",
        interval_minutes=15,
        why=(
            "Repairs jobs whose matrix generation stamped a timestamp and wrote "
            "no rows. Measured live at 19 of 35 jobs across three tenants. "
            "Every fifteen minutes rather than hourly because a broken job "
            "blocks its whole candidate pipeline, and the sweep is a cheap "
            "EXISTS scan that does nothing when there is nothing to fix."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-assessment-credits",
        task="pickready.reconcile_assessment_credits",
        interval_minutes=60,
        why=(
            "Settles abandoned assessments. Hourly rather than daily so a "
            "reminder goes out near its 24h and 72h marks."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-project-intake",
        task="pickready.reconcile_project_intake",
        interval_minutes=60,
        why=(
            "Retries verified deletion of temporary project originals and "
            "re-dispatches projects whose processing was lost. Deletion must be "
            "observable and retryable, never assumed."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-proctoring-sessions",
        task="pickready.reconcile_proctoring_sessions",
        interval_minutes=60,
        why=(
            "A browser that closed mid-assessment leaves a session active with "
            "no heartbeat. Settles it as abandoned on the SAME clock the credit "
            "reconciler uses, so the two never disagree about whether an "
            "assessment is over."
        ),
    ),
    ScheduledTask(
        rule="readypick-purge-proctoring-events",
        task="pickready.purge_proctoring_events",
        interval_minutes=60,
        why=(
            "Deletes nothing while `proctoring_event_retention_days` is zero, "
            "which is the platform's current posture."
        ),
    ),
)

RULE_NAMES: tuple[str, ...] = tuple(entry.rule for entry in SCHEDULE)


def by_rule(rule: str) -> ScheduledTask:
    for entry in SCHEDULE:
        if entry.rule == rule:
            return entry
    raise KeyError(rule)
