# CLAUDE.md section draft: Phase 4 WP-4B2 (Run, Submit, review, evidence, sweeps)

Draft for the orchestrator to merge into the release's CLAUDE.md section. It
covers only what `wip/p4-4b2` built on top of 4B1 (the answer key, the tables
and executed question generation) and 4A (the port and the Judge0 adapter).

### A FINAL CODING ANSWER IS STORED FIRST, EXECUTED AFTER COMMIT, AND NEVER TWICE

`services/coding_assessment/submissions.py` owns the flow.
`accept_final` runs in the candidate's own `respond` transaction and writes one
`coding_submissions` row per answer with `INSERT ... ON CONFLICT (answer_id) DO
NOTHING`; `pickready.execute_coding_submission` is dispatched with
`dispatch_after_commit`, so a rolled-back submit dispatches nothing and the task
never runs before the row exists. An EMPTY answer is `no_code`: an evidence gap,
never executed, never reviewed, never a zero.

- **The ticket is COMMITTED before anybody polls.** A worker killed mid-poll
  leaves `submitted` with a ticket, and the next attempt COLLECTS the same run.
  `test_the_ticket_is_committed_before_polling_and_a_retry_collects_it` is the
  pin, and it fails when the commit moves after the poll.
- **`ExecutionTicketLost` is the ONE case that resubmits.** The host was
  replaced or pruned the run; collecting harder will not find it. The ticket is
  cleared and the code submitted again, once per invocation. Correct only
  because execution is a pure function of code and input, which is also why no
  agent-action ledger entry is written for a sandbox call.
- **A sandbox fault is retried, never graded.** `INTERNAL_ERROR` on any test,
  or results that do not cover every input, is `SandboxFault`: the run is
  discarded and resubmitted.
- **A defect on OUR side is not a retry storm.** A refused request, a key that
  fails its digest, or code that no longer matches `source_sha256` is
  `SubmissionDefect`, which the task turns into `PermanentTaskFailure`. The row
  stays open, the sweep reports it as stuck, and after the wait window the
  answer reads "Not assessed" with a person in the loop.
- **Every write stage takes `pg_try_advisory_xact_lock` on the submission and
  commits at the end of the stage.** Nothing holds a lock across a sandbox poll
  or a model call.

### HIDDEN OUTPUT IS DROPPED BEFORE ANYTHING CAN STORE OR LOG IT

`execution.summarise_hidden` keeps, per hidden test, the outcome WORD, whether
it passed, and the resource figures. Never stdin, stdout or stderr: a candidate
program can echo its stdin, and a hidden test's stdin is part of the answer key.
The comparison happens in `keys.AnswerKey.passed`, so the executor never holds
an expected hidden output either. Sample (visible) tests run in the same batch,
and the first runtime-error message among THEM is kept
(`visible_error_output`), because a public input cannot carry the key. The
compiler's message is kept because compilation precedes any input.
`test_results_are_judged_and_hidden_output_is_never_stored_or_logged` runs an
echoing program and sweeps the stored row, every log record and every prompt.

### THE SCORE IS 70 HIDDEN TESTS AND 30 REVIEW, AND NONE IS NOT ZERO

`scoring.coding_question_score` is pure: `round(w * 100 * passed / total +
(1 - w) * review)` with `w = coding_score_test_weight` (0.7). It returns None
unless BOTH halves exist. A compile error is not a missing half: the tests ran
and none passed. Impossible inputs RAISE rather than clamp. The number is
internal and goes to Miti, the one grading authority.

`evidence.CodingEvidence` has four states: `complete` (score set), `pending`
(open and younger than `coding_execution_max_wait_hours`, 24), `unavailable`
(open past the window: score None, `needs_human_review`), `not_answered` (empty
answer). The age is from the submission's own `created_at`.
`submissions.scoring_hold` is the gate scoring calls before it spends anything:
hold while open work is young, proceed once it has expired.

### THE CODE-QUALITY REVIEW NEVER SEES A HIDDEN TEST

`review.review_code_quality` (task type `coding_quality_review`, Terra,
temperature 0.0, prompt `coding_quality_review.txt`) reads the public problem,
the private approach notes (which describe the solution, never a test), the
candidate's code and the OUTCOME SENTENCE, which is built from counts and words.
`ReviewInput` has no field that could hold a hidden test, asserted by field set.

- **Judged by code**: four criteria on 0 to 1, an overall 0 to 100, reasoning
  of `assessment_evaluation_min_reasoning_words`, at least one citation, every
  citation verbatim from the code, no number in the reasoning
  (`siddhi.numbers.scan_text`). A fabricated citation is COUNTED in the loop's
  reason, never quoted, because reasons are logged.
- **Code addressed to the reviewer is a LIMITATION, not a refusal.** The code
  passes `inspect_answer`; a hit is recorded in words and sets
  `needs_human_review`, and the reviewer sees the sanitised text.
- **A failed review is `review_status='failed'`**, retried by the sweep, never
  replaced by a template; `review_model_id`/`review_prompt_version` are written
  only for a review a model wrote.

### A REPORT STATES CODING RESULTS IN SPELLED-OUT WORDS

`phrasing.outcome_sentence` ("Passed seven of the ten hidden tests; one gave a
wrong answer and two exceeded the time limit.") is a fixed catalogue assembled
from stored outcome words, the proctoring report's precedent under the number
ban (CONTRACT v2). `test_no_sentence_a_report_prints_carries_a_digit...` sweeps
every shape up to thirty tests for digits, the em dash and `scan_text`. A
sentence for failures nobody recorded RAISES. The candidate is told a STATE
about their own final answer ("Submitted", "Being checked", "Checked"), never a
result.

### RUN IS INTERACTIVE, SO IT NEVER WAITS ON THE SANDBOX

`runs.start_run` performs ONE bounded `submit` of the VISIBLE tests and returns
a `queued` row; `runs.refresh_run` performs ONE bounded `collect` under `FOR
UPDATE SKIP LOCKED`. The sandbox queue is the dispatch; routing a Run through
the task Lambda would put a cold start in front of an interactive button.

- **The per-question cap is COUNTED IN THE TABLE** (`coding_run_max_per_question`,
  40), under an advisory lock on (conversation, question), so it holds when the
  Redis rate window fails open. Runs the sandbox never took (`unavailable`) do
  not count: an outage must not spend a candidate's runs.
- **One run per `client_token`, one run in flight per question.** Refusals are
  `RunRefused` with a server-authored sentence the route renders verbatim.
- **An outage is a state**: `unavailable` with the class name, and after
  `coding_run_deadline_seconds` a queued run the sandbox never answered is
  closed rather than left spinning.

### THE SWEEP ASKS THE TABLE, THE PROBE REPORTS, THE VERIFICATION IS GREEN OR NOT

- **`pickready.reconcile_coding_submissions`, every 15 minutes**, in
  `schedule.py` and all three environments' Terraform: re-dispatches execution
  open past `coding_submission_redispatch_minutes` and reviews owed past
  `coding_review_retry_minutes`; logs `coding.submission_stuck` at ERROR past
  `ATTEMPTS_BEFORE_ALARM` and re-dispatches anyway (never gives up); hands a
  COMPLETED conversation with no report to scoring once its coding work is done
  or has waited past the window.
- **`pickready.probe_code_execution`, every 5 minutes**: `status=disabled`
  while the backend is disabled, otherwise one canary and `health()`, logged as
  status and latency only. The alarm counts the `status=failed` lines.
- **`pickready.verify_code_execution_sandbox` is registered and NOT
  scheduled.** A canary per configured language, an infinite loop, a memory
  hog, an output flood, a process bomb, a network attempt, health after them,
  and the adapter's own checks. **GREEN ONLY WHEN EVERY CHECK RAN AND PASSED**:
  the adapter checks (an unauthenticated request refused, the language ids
  offered) come from an optional `self_checks()` on the provider, and a
  provider without it records them NOT PERFORMED, which is red. A skipped check
  is not a passed check.

### EVIDENCE GOES THROUGH THE ONE LEDGER WRITER

When execution completes, the answer is filed with
`assessment_pipeline.evidence.record_answer_evidence`, the only ledger writer,
with the answer's transcript message as the locator. Idempotent there; a ledger
failure never fails the task. No second ledger path and no new source type.
