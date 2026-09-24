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
        rule="readypick-remind-unsaved-skills",
        task="pickready.remind_unsaved_skills",
        interval_minutes=60,
        why=(
            "A job whose drafted skills nobody saved keeps taking applications "
            "and can invite nobody, and nothing on the screen says why. Hourly "
            "so a job is reminded near its own threshold; once per job. "
            "Replaces readypick-remind-unapproved-framework (Vivekium release), "
            "renamed with its task in the same deploy window."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-job-setup",
        task="pickready.reconcile_job_setup",
        interval_minutes=15,
        why=(
            "Repairs a skills draft that never landed: a saved SWOT with no "
            "skill row of any kind and no draft asked for, or a draft that "
            "never reported back. Never selects a job whose skills a person "
            "emptied. Every fifteen minutes because a job without skills "
            "cannot invite anybody, and the sweep is a cheap EXISTS scan."
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
    ScheduledTask(
        rule="readypick-purge-assessment-media",
        task="pickready.purge_assessment_media",
        interval_minutes=60,
        why=(
            "Deletes nothing while `assessment_media_retention_days` is zero, "
            "which is the platform's current posture. The owner ruling of "
            "2026-09-22 requires stored assessment media to have a retention "
            "and deletion lifecycle, and a retention window with no sweep "
            "behind it is a paragraph rather than a policy."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-context-index",
        task="pickready.reconcile_context_index",
        interval_minutes=60,
        why=(
            "Finds documents that have text and no chunk rows, and indexes "
            "them. Asks the TABLE with a NOT EXISTS, never a timestamp. It "
            "covers the one failure the call sites cannot: a dispatch that "
            "never arrived leaves no trace, and an unindexed resume is "
            "invisible to retrieval forever because nothing would ever ask "
            "again."
        ),
    ),
    ScheduledTask(
        rule="readypick-release-held-assessments",
        task="pickready.release_held_assessments",
        interval_minutes=60,
        why=(
            "A completed conversation with no report is a candidate who did "
            "the work and a customer who was charged for it, with nothing to "
            "show. The task was REGISTERED and dispatched only from the two "
            "credit-grant call sites, so it repaired a hold that a top-up "
            "cleared and nothing else: a dispatch that never arrived, a "
            "container killed mid-scoring, or a run that raised past its "
            "attempts left the report missing permanently, because the only "
            "thing that would ever have asked again was the top-up that had "
            "already happened. It asks the TABLE with an outer join, never a "
            "status column. Scheduling it was UNSAFE until the scoring lock "
            "existed: with no tenant argument the sweep also matches "
            "conversations that finished seconds ago and are being scored "
            "right now, and dispatching those would have manufactured the "
            "duplicate scoring run it is supposed to repair."
        ),
    ),
    ScheduledTask(
        rule="readypick-purge-closed-job-assessments",
        task="pickready.purge_closed_job_assessments",
        interval_minutes=60,
        why=(
            "Change request 22, owner ruling 2026-09-22: closing a job no "
            "longer deletes its assessment data inline, it withholds it for "
            "thirty days. This sweep is the half that makes the thirty days "
            "real, and without it the promise made to every assessed "
            "candidate is broken SILENTLY, because a retention window with "
            "no sweep produces the same empty log as one with nothing to "
            "delete. HOURLY rather than daily, even though the window is "
            "measured in days: it deletes stored objects one network call at "
            "a time, and a store that refuses has to be retried inside the "
            "same day rather than once. Running LATE is safe and running "
            "TWICE is safe: it asks the table for jobs whose window has "
            "passed and whose data is still here, so a second pass over a "
            "finished job finds nothing."
        ),
    ),
    ScheduledTask(
        rule="readypick-sweep-consent-lifecycle",
        task="pickready.sweep_consent_lifecycle",
        interval_minutes=1440,
        why=(
            "Consent renewal, the final warning and the inactivity rule "
            "(feature 8). DAILY rather than hourly because every window it "
            "measures is counted in days or months, so twenty four more runs "
            "a day would change nobody's outcome and would only widen the "
            "blast radius of a mistake in a task that can erase a profile. "
            "Running LATE is safe by construction: a grace window starts when "
            "a letter was actually sent, so an outage delays the cycle rather "
            "than skipping somebody to deletion. The erasure half is gated on "
            "`consent_auto_deletion_enabled`, which defaults to off, and the "
            "sweep LOGS what it would have erased so the posture is visible "
            "rather than inferred from silence."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-candidate-erasures",
        task="pickready.reconcile_candidate_erasures",
        interval_minutes=60,
        why=(
            "An erasure has two halves that fail independently: the database "
            "rows, which one transaction settles, and the STORED OBJECTS, "
            "which an object store settles one network call at a time. The "
            "second half used to be missing entirely, so a resume and an "
            "assessment recording survived a deletion whose own warning "
            "screen said they had not. This finishes every "
            "`candidate_deletion_requests` row that is not complete, asking "
            "the TABLE rather than a timestamp, and it NEVER gives up: "
            "there is no terminal failure state, because 'we stopped trying "
            "to delete this person's documents' is not an outcome this "
            "product may reach. Hourly rather than daily because the window "
            "it closes is one in which a person who asked to be erased is "
            "only half erased."
        ),
    ),
    ScheduledTask(
        rule="readypick-sweep-bgv-reminders",
        task="pickready.sweep_bgv_reminders",
        interval_minutes=1440,
        why=(
            "Email 3 of the vivekium BGV flow: the day-3 chase for an "
            "employer who has not answered a verification request. DAILY "
            "because the window is measured in days; each row is chased "
            "exactly once (reminder_sent_at is the latch), so running late "
            "delays the letter rather than duplicating it. The candidate is "
            "told with the HR address partially masked, because they are the "
            "one who can nudge their own former employer."
        ),
    ),
    ScheduledTask(
        rule="readypick-expire-credit-lots",
        task="pickready.expire_credit_lots",
        interval_minutes=1440,
        why=(
            "Change request 25: a credit lot reaching its three-month expiry "
            "writes the ledger debit for whatever was left on it, so the "
            "balance stays the plain SUM of the ledger and the statement says "
            "why it fell. Every gate and the billing summary already expire "
            "on read, so an ACTIVE customer's balance is exact when they look "
            "at it; this sweep is for the account nobody is looking at, whose "
            "figure the Provider Portal's cross-tenant overview reads. DAILY, "
            "and the interval is not load-bearing: running late costs a stale "
            "number on an idle account and can never let an expired credit be "
            "spent, because the deduction path expires first."
        ),
    ),
    ScheduledTask(
        rule="readypick-sweep-subscription-usage-alerts",
        task="pickready.sweep_subscription_usage_alerts",
        interval_minutes=1440,
        why=(
            "Change request 27: the month 10 and month 11 usage summary. "
            "PURELY INFORMATIONAL, and it writes exactly one column, the "
            "once-only latch that stops the letter being sent twice. DAILY "
            "because the window is a subscription MONTH, so running late "
            "delays the letter by a day and can never duplicate it. It is "
            "keyed on the TABLE (subscription_started_at against now), never "
            "on a last-swept stamp, so a run that died between the claim and "
            "the send does not silently skip the tenant it died on."
        ),
    ),
    ScheduledTask(
        rule="readypick-reconcile-queued-emails",
        task="pickready.reconcile_queued_emails",
        interval_minutes=15,
        why=(
            "Phase 6: every candidate email is queued by `email_outbox` and "
            "its send is dispatched AFTER the request commits, so an invoke "
            "that fails leaves a durable row sitting `queued` with nothing "
            "working on it. This asks the TABLE and re-dispatches rows between "
            "ten minutes and a day old; the send worker's atomic claim makes a "
            "re-dispatch of a row that is merely slow a no-op. Rows stuck "
            "mid-send are reported, never resent. Every fifteen minutes "
            "because a confirmation or a reminder is only useful on the day."
        ),
    ),

)

RULE_NAMES: tuple[str, ...] = tuple(entry.rule for entry in SCHEDULE)


def by_rule(rule: str) -> ScheduledTask:
    for entry in SCHEDULE:
        if entry.rule == rule:
            return entry
    raise KeyError(rule)
