# Code execution and the coding assessment

Normative for executed coding questions as built in the Vivekium
simplification release (2026-09-25, Phase 4). `claude.md`'s top section
carries the rules and their reasons; the sandbox host's topology, cost, staged
rollout and outage runbook are `docs/operations/JUDGE0_RUNBOOK.md`. Where a
coding question sits in the assessment is `ASSESSMENT_FLOW.md`.

**Status on pilot**: `CODE_EXECUTION_BACKEND=disabled`. With the backend
disabled no role is coding-eligible (`code_execution_disabled` is recorded as
the reason), no coding question is written, and every coding surface says
execution is unavailable. The switch is flipped only after the sandbox
verification task passes on pilot (runbook stage V, then C).

## 1. The port

`app/services/code_execution/` is a port and one adapter.

| Module | Role |
|---|---|
| `provider.py` | `CodeExecutionProvider` (the protocol), `get_provider()`, `is_enabled()`, `override_provider()` (tests only; refused when `is_production`), `outputs_match` |
| `judge0.py` | the ONLY module that knows Judge0's paths, headers, status ids, language ids and base64 transport |
| `fake.py` | `FakeProvider`, installed only through `override_provider` |
| `languages.py` | the language registry (label, run hint, CPU multiplier) |
| `limits.py` | `ExecutionLimits`, `for_language`, `HOST_MAXIMA` |
| `errors.py` | the four errors |

- **Five operations**: `run` (interactive convenience), `submit` (returns a
  ticket), `collect`, `discard`, `health`. A final submission persists its
  ticket BEFORE it polls.
- **A provider never receives an expected output.** A `TestInput` is a key and
  a stdin, asserted by field set. `outputs_match` compares in domain code
  (line endings normalised, trailing whitespace dropped, leading whitespace
  significant).
- **Four errors, because the caller acts differently on each**:
  `ExecutionUnavailable` (retry; 401 and 403 are `reason="credential"`, logged
  at ERROR), `ExecutionRejected` (our bug, never retried),
  `ExecutionTicketLost` (the host was replaced or pruned the run: submit
  again), `ExecutionNotConfigured`.
- **`CODE_EXECUTION_BACKEND`** is `judge0` or `disabled` (default), never a
  fallback chain. A missing URL or token is not a boot refusal:
  `is_enabled()` answers False and every surface records "unavailable".
- **No log line carries source, stdin, stdout or the token**, asserted with
  sentinels across every logger.
- `tests/test_code_execution_architecture.py` enforces the boundary over the
  AST of `app/`; `tests/test_candidate_code_never_executes.py` pins the EXACT
  set of execution capabilities in every shipped image (today one module,
  `services/video/processing.py`, running `ffmpeg` / `ffprobe` from list
  literals, no shell).

## 2. Languages and limits

`CODE_EXECUTION_LANGUAGES` (default `python,java,cpp,javascript`) chooses which
registry languages a deployment offers; `JUDGE0_LANGUAGE_IDS` maps them to the
host's ids. The frontend's labels and run hints are the backend's words
(`coding-languages-parity.test.ts`), because the Java hint (`public class
Main`) is a rule the sandbox enforces.

| Limit | Setting | Default | Host cap (`HOST_MAXIMA`) |
|---|---|---|---|
| CPU time | `code_execution_cpu_seconds` | 2.0 s | 5.0 s |
| Extra CPU | `code_execution_cpu_extra_seconds` | 0.5 s | 1.0 s |
| Wall time | `code_execution_wall_seconds` | 5.0 s | 10.0 s |
| Memory | `code_execution_memory_mb` | 256 MB | 512 MB |
| Stack | `code_execution_stack_kb` | 64 MB | 128 MB |
| Processes | `code_execution_max_processes` | 60 | 120 |
| File size | `code_execution_max_file_kb` | 1 MB | 4 MB |
| Output kept | `code_execution_max_output_chars` | 4000 | (truncation only) |

`code_execution_cpu_multipliers` (`java:2.0,javascript:1.5`) scales CPU and
wall time together. Limits are sent on every run and REFUSED above the host
caps, never clamped; `HOST_MAXIMA` mirrors
`infra/modules/code_sandbox/judge0.conf.tftpl` and a test parses the template.
**Limits are FROZEN into a question's payload at generation**; Run and Submit
read `payload.limits_for`, so a settings change never moves the limits under
an issued question.

## 3. The answer key

`coding_question_keys` (migration 0124) holds the hidden tests, the reference
solution and the reviewer's approach notes. Exactly one module names the table
or its model: `services/coding_assessment/keys.py`
(`tests/test_coding_key_confinement.py`).

- **The key JUDGES and never DISCLOSES**: `AnswerKey.test_inputs()` hands the
  sandbox a key and a stdin; `AnswerKey.passed(key, stdout)` compares inside
  the module.
- **The database refuses a coding payload that carries the key**
  (`ck_candidate_questions_coding_key_private`). The v2 payload
  (`CodingPayloadV2`) forbids extra keys; `candidate_projection` is an
  explicit field list (`CANDIDATE_FIELDS`).
- **INSERT-ONLY twice**: UPDATE revoked from `pickready_app` and the trigger
  `coding_question_key_is_immutable`.
- **A digest binds the key to its validation** (`validation_json.hidden_digest`,
  recomputed on every read; `KeyIntegrityError`).
- Every key field is `repr=False`. `coding_hidden_tests_max` may never exceed
  30 (the database CHECK; refused at boot).

## 4. Writing a coding question

`assessment_formats.coding_generation.write_coding_question`, one `agent_loop`
with background bounds (`coding_generation_deadline_seconds`, 420), called by
the question composer for each coding slot.

1. Deterministic checks first (shape, counts: `coding_visible_tests_min/max`
   2 to 3, `coding_hidden_tests_min/max` 5 to 10, sizes, duplicate inputs,
   identical hidden outputs, a hidden input echoed in the statement, `public
   class Main` for Java, the candidate-facing guards, the meta-commentary
   sweep).
2. The model's reference solution runs against every visible and hidden test
   in the sandbox; one failure rejects the question. It must finish within
   `coding_reference_cpu_headroom` (0.5) of the CPU limit.
3. Each starter program must run cleanly and must NOT pass every hidden test.

Loop reasons are content-free ("hidden test 3 (h3)"), because `agent_loop`
logs them; what the model needs to fix a failure travels only in the next
request. Disabled execution refuses before any model call
(`code_execution_disabled`); a sandbox outage LATCHES for the rest of the loop
(`code_execution_unavailable`). The result is a draft OR one refusal word,
never both; the composer turns a refusal into a prose slot and records it.
The prompt never carries compensation, the resume or anything a candidate
wrote. `persist_coding_question` writes the question and its key in the
caller's transaction.

## 5. Run: the interactive button

`api/assessment_coding.py`, candidate audience, under `/api/v2/assessments`.

- `POST .../conversations/{cid}/coding/{qid}/runs` does ONE bounded submit of
  the VISIBLE tests and answers 202 `{run_id, status}`; `GET .../runs/{run_id}`
  does ONE bounded collect under `FOR UPDATE SKIP LOCKED`. The sandbox's queue
  is the dispatch; routing a Run through the task Lambda would put a cold
  start in front of an interactive button.
- **Fences, in order**: the Redis rate window (`coding_run_rate_per_minute`,
  20), ownership (404), `proctoring_gate.require_active` (409), the OPEN
  question (the current base question, nothing pending; 409), then
  `runs.start_run`'s own fences.
- **The per-question cap is counted in the table** (`coding_run_max_per_question`,
  40) under an advisory lock on (conversation, question), so it holds when
  the Redis window fails open. One run per `client_token`, one in flight per
  question.
- **An outage is 503 with its `unavailable` row committed** (the handler
  returns the 503 rather than raising, so the candidate session commits) and
  does not count against the candidate. A queued run the sandbox never
  answered is closed after `coding_run_deadline_seconds`.
- Every refusal is `runs.REFUSAL_SENTENCES[code]` verbatim, mapped by
  `REFUSAL_STATUS` (409 not now, 429 no more, 422 built wrongly).
- A result is keyed per visible sample (`key, passed, result_word, stdout,
  expected_stdout, stderr, compile_output`); the screen names samples by
  position. No timing, memory figure or score.

## 6. Submit: the final answer

- **Stored first, executed after commit, never twice.**
  `final_answer.accept_structured_answer` is one hook line in the respond
  path; for a v2 question it calls `submissions.accept_final`, which writes
  one `coding_submissions` row per answer (`ON CONFLICT (answer_id) DO
  NOTHING`) and dispatches `pickready.execute_coding_submission` with
  `dispatch_after_commit`. An EMPTY answer is `no_code`: an evidence gap,
  never executed, never a zero.
- **The ticket is committed before anybody polls**; a retry COLLECTS the same
  run. `ExecutionTicketLost` is the ONE case that resubmits. A sandbox fault
  (`INTERNAL_ERROR`, or results that do not cover every input) is retried,
  never graded. A defect on OUR side (a refused request, a key failing its
  digest, code no longer matching `source_sha256`) is a
  `PermanentTaskFailure`, never a retry storm.
- **Hidden output is dropped before anything can store or log it**
  (`execution.summarise_hidden`): per hidden test, the outcome word, pass and
  resource figures only. The first runtime error among the VISIBLE tests and
  the compiler's message are kept.
- Every write stage takes `pg_try_advisory_xact_lock` on the submission and
  commits at the end of the stage; nothing holds a lock across a poll or a
  model call.
- The candidate is told a STATE only (`GET .../coding/{qid}/submission`:
  "Submitted", "Being checked", "Checked"), never a result.

## 7. Review and score

- **The code-quality review** (`coding_assessment/review.py`, task type
  `coding_quality_review`, Terra, temperature 0, prompt
  `coding_quality_review.txt`) reads the public problem, the approach notes,
  the code and the OUTCOME SENTENCE, and never a hidden test (`ReviewInput`
  has no field that could hold one). Four criteria 0 to 1, an overall 0 to
  100, reasoning of at least `assessment_evaluation_min_reasoning_words`,
  every citation verbatim from the code, no number in the reasoning. Code
  addressed to the reviewer is a recorded LIMITATION and sets
  `needs_human_review`. A failed review is `review_status='failed'`, retried,
  never templated.
- **The score** (`scoring.coding_question_score`, internal):
  `round(w * 100 * passed / total + (1 - w) * review)` with
  `w = coding_score_test_weight` (0.7). None unless BOTH halves exist.
- **Evidence** (`coding_assessment/evidence.py`): `complete`, `pending` (open
  and younger than `coding_execution_max_wait_hours`, 24), `unavailable` (open
  past the window: no score, `needs_human_review`), `not_answered`. Miti reads
  it and calls no model for a coding item.
- **Scoring waits** for owed coding work: `submissions.scoring_hold` is called
  by the scoring task after the lock and the exists check.
- **The report states results in spelled-out words**
  (`phrasing.outcome_sentence`: "Passed seven of the ten hidden tests; one gave
  a wrong answer and two exceeded the time limit."), swept for digits up to
  thirty tests. The recruiter's transcript shows the sentence, a compiler
  message, and the review's reasoning and citations; never the key. A legacy
  v1 answer keeps its stored `not_executed_note`.

## 8. Sweeps, probe and verification

| Task | Schedule | What it does |
|---|---|---|
| `pickready.reconcile_coding_submissions` | every 15 minutes | re-dispatches execution open past `coding_submission_redispatch_minutes` and reviews owed past `coding_review_retry_minutes`; logs `coding.submission_stuck` at ERROR past `ATTEMPTS_BEFORE_ALARM` and never gives up; hands a completed conversation with no report to scoring once its coding work is done or has waited out the window |
| `pickready.probe_code_execution` | every 5 minutes | `status=disabled` while disabled, otherwise one canary and `health()`, logged as status and latency only |
| `pickready.verify_code_execution_sandbox` | NOT scheduled (operator) | a canary per language, an infinite loop, a memory hog, an output flood, a process bomb, a network attempt, health after them, and the adapter's self-checks. GREEN only when every check ran and passed |

## 9. The editor

Monaco, self-hosted: `scripts/copy-monaco.mjs` copies the AMD build into
`public/monaco/vs` on `predev` and `prebuild`, `monaco-editor` 0.56.0 and
`@monaco-editor/react` 4.7.0 are pinned EXACT, and the CSP did not move.
Copy, cut, paste and drop are refused in two layers (capture-phase listeners on
the host in `lib/assessment/editor-guard.ts`, and `editor.addCommand`), each
attempt reported once with its kind; every suggestion surface is off.
Ctrl/Cmd+Enter RUNS the samples and never submits; Submit is a
`ConfirmButton` that focuses "Keep working". One draft per language.

## 10. What the harness proves

`harness.faults.code_execution_failure(kind)` configures a LIVE Judge0 backend
on an `.invalid` host routed to authored fixtures, so the real adapter
classifies the outage (`ExecutionUnavailable` is asserted on the stored rows).
`harness.doubles.code_execution.echoing_program` installs the product's fake
scripted to ECHO every input. Scenarios:
`adversarial.the_code_runner_is_unavailable` and
`adversarial.a_program_cannot_exfiltrate_the_hidden_tests`.
`tests/test_hidden_tests_never_leave_server.py` runs the whole journey over
HTTP with sentinel keys and sweeps every response, log record, prompt,
dispatched argument and stored row.
