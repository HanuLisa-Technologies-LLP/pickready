# CLAUDE.md section draft: Phase 3 WP1 (invitations, credits, after-commit)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
No migration.

## ONE AFTER-COMMIT MECHANISM, AND `realtime` RIDES IT

`core/after_commit.on_commit` (foundation) is the one way to run work after a
transaction commits. `services/realtime.publish_after_commit` used its own
`event.listens_for(..., "after_commit", once=True)` per call, and that
listener was NOT removed by a rollback: a publish registered in a transaction
that rolled back fired on the NEXT commit of the same session, announcing a
message that was never stored. It is an `on_commit` callback now, discarded
when the outermost transaction ends without committing. The guard inside
`_fire` (a closed loop logs `realtime.schedule_failed` and closes the
coroutine) stays: `on_commit` would log an exception rather than raise it, so
`test_realtime_hub` asserts that the guard ran, not merely that `commit()` did
not raise.

## THE INVITATION HAS ONE WRITER, AND APPLYING IS NOT IT

`services/assessment_invitations.invite_batch` is the only product code that
writes an `assessment_conversations` row, which IS the invitation. Applying
writes none (Phase 6; pinned from a second connection by `test_portal.py`,
`test_public_apply_unified.py` and the harness scenario
`regression.applying_never_creates_an_assessment`).
`tests/test_apply_creates_no_assessment.py` sweeps `app/` by AST with
whitespace-normalised SQL and fails on a second writer; the dev repair seed
`scripts/seed_mock_data.py` is allowlisted by reason and the allowlist is
checked for staleness.

Two routes reach it:

- **`select-candidates`**, the batch.
- **The hand move to `assessment_invited` on `change-status`.** It used to
  call `apply_transition` directly: the stage was written with NO row, an
  invitation was drafted inline and mailed with a link the start route then
  refused, no credit question was asked, and `select-candidates` could never
  invite that applicant afterwards because they were no longer at `applied`.
  It is an invitation of one now, requires `send_outreach` as well as the
  route's `decide_profile`, and a repeat answers 409 with the skip reason.
  The invitation email is always sent (it carries the candidate's only way
  in), so `send_email=false` cannot withhold it and `email_queued` says true.

## INVITING ASKS ONE CREDIT QUESTION FOR THE WHOLE BATCH

`credits.can_start_assessment(..., count=N)` asks for `count x cost`. The
batch used to ask about ONE report and then invite everybody ticked, so a
balance holding one assessment let a recruiter invite two hundred people,
each charged at completion into a deficit nobody chose. A shortfall is ONE
402 naming the count, the per-report cost, the balance and the gap, and
nothing is written. The separate "balance above zero" gate is folded into it.
`count < 1` raises: an empty batch asks nothing.

- **Three passes, nothing written until the last.** Read and lock the ticked
  applications `FOR UPDATE OF l` in id order (two recruiters inviting the
  same applicant used to both see `applied`, and the second batch 500'd on
  `InvalidTransition`; now it waits, reads the new stage and skips that
  applicant with a reason), ask the credit question, then write.
- **A closed job invites nobody** (409), and a job whose skills are not saved
  invites nobody (409). Another tenant's application reads as "Application
  not found", the same as an id that does not exist.
- **An invitation is not a reservation.** Two batches that each fit the
  balance are not jointly covered, so the START of each assessment asks again
  with `count=1` (`api/assessment_conversation`, Phase 3 WP3), which is the
  last moment the work is still a choice.

## THE INVITATION EMAIL IS DRAFTED BY A WORKER, NEVER BY THE CLICK

`pickready.send_assessment_invitation` (`workers/tasks_invitations.py`,
Route.LAMBDA, three attempts) is dispatched after the invitation commits,
together with `pickready.generate_candidate_questions`. It drafts through
`email_outbox.queue_transition_email`, so the row carries the tenant's
default sender and the dedupe key `assessment_invitation:<link>`, and the
send is dispatched after the TASK's commit. It is idempotent (an existing
invitation row of any status, checked before the model call) and skips,
with a logged reason, an application that moved on, was never invited, or
has no address.

`api/pipeline._queue_transition_email` is gone. The stage-change email is
`email_outbox.queue_transition_email` on the one candidate email writer,
unthreaded (machine copy nobody read is not posted into the recruiter's
thread as their words), and `api/pipeline` is off the dispatch-before-commit
allowlist. `change_status` still drafts a single stage-change email inline
and tells the recruiter whether it was queued; that is one bounded draft per
click, not a batch.

## A LOST INVITATION IS REPAIRED BY ASKING THE TABLE

`credit_reconciliation.reconcile` (hourly) re-dispatches the invitation task
for applications at `assessment_invited`, with an address, no invitation
email of ANY status, invited more than fifteen minutes and less than the
first reminder (24h) ago, after its own commit. A failed or bounced email is
a delivery problem with its own record and is never re-sent from here. Past
the first reminder nothing is sent (the reminder carries the same link and an
invitation after it reads as a mistake): the count is logged at ERROR.

## SUPERSESSIONS

- 2026-07-28 "subscriptions + the credit ledger": "the NEXT invitation is
  what gets blocked" now reads "the next BATCH is asked for its whole cost".
- 2026-07-27 "job posting lifecycle": the `assessment_conversations` row is
  written only by `services/assessment_invitations`.
- 2026-09-12 "conversations": `publish_after_commit` no longer "hangs off
  SQLAlchemy's `after_commit`" directly; it is an `on_commit` callback.
