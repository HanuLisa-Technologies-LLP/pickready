# CLAUDE.md section draft: Phase 4 WP-4F (the harness double, two scenarios, the removal sweep)

Draft for the orchestrator to merge into the release's CLAUDE.md section. It
covers only what `wip/p4-4f` added on top of 4C: the harness's code-execution
seam, two adversarial scenarios, the removal sweep for the read-only coding
evaluation, and two reachability pins. No migration, no product code changed.

### THE SANDBOX FAULT CONFIGURES A LIVE DEPLOYMENT, AND THE ERROR CLASS PROVES WHICH OUTAGE WAS MET

`harness.faults.code_execution_failure(kind)` (kinds `unavailable`,
`queue_full`, `credential`, `timeout`) does NOT hand the product a failing
double. It sets `CODE_EXECUTION_BACKEND=judge0`, a `JUDGE0_URL` on an RFC 2606
`.invalid` host and a token that names itself, and routes that host through the
shared HTTP seam to the authored Judge0 fixtures
(`tests/fixtures/vendor/judge0/`). So the product's own
`code_execution.get_provider()` builds the real `Judge0Provider`, and the real
`_request` classifies the fixture's status. The route goes in first and comes
out last, and all three settings are put back on unwind.

- **Why not `override_provider` for a fault**: with the backend left `disabled`
  a Run still answers the candidate 503, for `ExecutionNotConfigured`, and the
  scenario would pass having measured a different outage. The
  `coding_runs.error_class` and `coding_submissions.last_error_class` the
  scenario asserts (`ExecutionUnavailable`) are what separate the two.
  Mutation-checked: leaving the backend `disabled` inside the fault fails four
  of the seven fault tests.
- **A kind with no fixture RAISES `FixtureMissing`** naming the directory to
  author it in, the rule every vendor seam here follows. `timeout` has no
  fixture because none is possible.

### A PROGRAM THAT RUNS IS THE PRODUCT'S OWN FAKE, AND NOTHING EXECUTES

`harness.doubles.code_execution.echoing_program(source, stdins)` installs
`code_execution.fake.FakeProvider` through `code_execution.override_provider`
(refused in production), scripted so the program ECHOES every input, and the
last input also comes back in a runtime error message. That is the most hostile
program a candidate can write against a hidden test: the key's INPUT becomes
the program's OUTPUT. It is a success, not a fault, so it is not in the fault
registry; a workload step installs it for exactly its own block, and a step
that uses it says so in its name (`..._against_an_echoing_program`).

### TWO SCENARIOS, BOTH ADVERSARIAL, BOTH MUTATION-CHECKED

- **`adversarial.the_code_runner_is_unavailable`**: Run answers 503 with the
  server's sentence and the run is `unavailable` (not counted); the final
  answer is accepted and stays `pending` with one recorded attempt; the real
  `pickready.execute_coding_submission` body re-raises `ExecutionUnavailable`
  for the retry; scoring is not dispatched and `scoring_hold` holds inside the
  wait window; at the end of `coding_execution_max_wait_hours` the evidence is
  `unavailable`, the score withheld, `needs_human_review` set and the phrase
  "The code runner was unavailable, so this answer was not assessed." Three
  degradations are recorded, each from the product's own signal. Nothing waits
  and no clock moves: `scoring_hold` and `evidence.for_conversation` take `now`,
  and the step asks them at two instants measured from the submission's own
  `created_at`. Mutation: `evidence.build` returning a score of zero for an
  expired answer fails `score_withheld`.
- **`adversarial.a_program_cannot_exfiltrate_the_hidden_tests`**: the world's
  answer key is made of sentinels; Run, the final answer, two state reads and
  the real execution task with the reviewer answering at the vendor seam. Then
  no response body, no log line captured at DEBUG, no reviewer prompt and no
  dispatched argument carries any part of the key, and no stored row outside
  `coding_question_keys` does either, read from a SECOND connection
  (`coding.key_sentinel_hits == []`), while `coding.key_holds_the_sentinels`
  proves the key really holds them. The approach notes are allowed in the
  reviewer's prompt and nowhere else. Mutations: keeping hidden stdout in
  `test_results_json` fails the stored-row sweep; logging the hidden inputs at
  DEBUG fails the log-line probe.

The `coding_question_open` world writes its question and key through the
PRODUCT's `coding_generation.persist_coding_question`, deliberately, unlike the
raw-SQL seeds: the key is sealed by a digest the read path recomputes, so a key
written any other way is one the product refuses to grade with. The
proctoring seed both assessment worlds need is now one helper,
`_start_under_proctoring`.

**What the scenarios do not drive yet, said rather than implied.** The final
answer is recorded the way `respond` records it and handed over through
`coding_assessment.final_answer.accept_structured_answer` directly, because on
this branch `respond` cannot parse a v2 coding answer (4B1 types hunk) and does
not call the hand-over (4C hunk 1). And the recruiter transcript is not in the
exfiltration workload, because that route serialises `candidate_view` of the
v2 payload and answers 500 until the same types hunk lands. Both are hunks in
`hunks/p4-4f-removals-and-harness.md`.

### THE READ-ONLY CODING EVALUATION IS SWEPT, AND ITS PENDING LEDGER CAN ONLY SHRINK

`tests/test_coding_read_only_evaluation_removed.py` sweeps `backend/app`,
`backend/tests`, `backend/harness`, `frontend/app`, `frontend/components`,
`frontend/lib` and `frontend/package.json`, whitespace-normalised with offsets
mapped back to a line (the 2026-09-23 fix), for `NOT_EXECUTED_NOTE`,
`HEDGE_MARKERS`, "was read and judged, not executed",
`assessment_answer_evaluation_coding`, `assessment_format_coding`,
`codemirror` and `@lezer/`, plus "never executed" inside prompts only. A file
NAME counts, because a prompt nothing loads still ships.

- **The removal lands in three phases**, so every site still carrying a name is
  in `PENDING_REMOVAL` with its owner, and the ledger is enforced in BOTH
  directions: an unlisted hit fails, and a listed site that no longer carries
  the name fails too, so an entry must be deleted with the deletion it names.
  The same shape as `test_dispatch_after_commit_sweep.py`'s legacy call sites.
- **`PERMANENT` holds one entry**: `test_assessment_formats_live.py`
  reproduces a stored legacy coding evaluation whose `not_executed_note` is
  that sentence, and the transcript must keep rendering such rows.
- **Case sensitive on purpose**: `not_executed_note` (lower case) is the stored
  FIELD every legacy row carries; `NOT_EXECUTED_NOTE` is the constant that
  wrote it.
- Mutation-checked: a new `NOT_EXECUTED_NOTE` in `coding_assessment/phrasing.py`
  and the sentence wrapped across a docstring line in a new module both fail,
  each naming the file and line.

### TWO MORE REACHABILITY PINS

`test_ai_reachability.REQUIRED_CALLERS` gains the port's two doors,
`code_execution.provider.get_provider` (every program that reaches the sandbox
asks it) and `outputs_match` (every answer is judged in the application, never
by the sandbox). `app.services.code_execution` was already in `LIVE`.
