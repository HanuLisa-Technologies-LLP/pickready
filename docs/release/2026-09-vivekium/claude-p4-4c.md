# CLAUDE.md section draft: Phase 4 WP-4C (the coding API)

Draft for the orchestrator to merge into the release's CLAUDE.md section. It
covers only what `wip/p4-4c` built on top of 4B2 (Run, Submit, review,
evidence) and 4B1 (the answer key and executed question generation).

### THE RUN BUTTON IS TWO BOUNDED CALLS, AND THE HANDLER NEVER WAITS

`api/assessment_coding.py`, mounted under `/api/v2/assessments` on the
candidate audience. `POST .../conversations/{cid}/coding/{qid}/runs` performs
ONE bounded enqueue to the sandbox and answers 202 `{run_id, status}`;
`GET .../runs/{run_id}` performs ONE bounded fetch for a queued run. That is
how Rule 4 is kept for an interactive button: the sandbox's queue IS the
dispatch, and routing a Run through the task Lambda would put a cold start in
front of it inside a twenty-minute question. The final answer is the opposite
case and IS dispatched after commit (4B2).

- **The fences run in a fixed order**: the Redis rate window
  (`coding_run_rate_per_minute`), ownership (404, never 403),
  `proctoring_gate.require_active` (409), the OPEN question (409), then the
  service's own fences in `runs.start_run`.
- **"Open" means the current base question with nothing pending.** The
  conversation is `active` and conversational, the question is
  `_conversation_prompts[next_question_index]` (the one ordering `respond`
  walks), and no follow-up is pending. A candidate cannot run code on a
  question they have answered or not yet reached. The server-side TIMER is
  not checked yet: it is Phase 3 WP3's, and the exact addition is recorded as
  a hunk.
- **THE DATABASE CAP IS THE FENCE, THE REDIS WINDOW IS NOT.** The window fails
  open by design when Redis is down; `coding_run_max_per_question` is counted
  in `coding_runs` under an advisory lock and holds anyway.
  `test_the_database_cap_holds_when_the_rate_window_fails_open` takes Redis
  away and watches the third press refused.
- **AN OUTAGE IS 503 WITH ITS ROW COMMITTED.** The handler RETURNS the 503
  rather than raising it, because the candidate session commits on a return
  and rolls back on a raise: the `unavailable` row is the record of the
  outage, and because the sandbox never took the run it does not count
  against the candidate. A replay of the same client token answers 503 again
  rather than starting a second run.
- **Every refusal is the service's own sentence.** `REFUSAL_STATUS` maps each
  `runs.RunRefused` code to a status (409 not now, 429 no more, 422 built
  wrongly) and the body is `runs.REFUSAL_SENTENCES[code]` verbatim, so the
  screen cannot promise what the server refuses. A test asserts every refusal
  code has a status.
- **A Run result is keyed, not named.** `runs.candidate_results` returns
  `{key, passed, result_word, stdout, expected_stdout, stderr,
  compile_output}` per visible sample, which is the frontend contract 4D
  built against: the screen labels a result exactly as it labels the sample
  in the problem, by position, so the server writes no second name.

### THE FINAL ANSWER IS ONE HOOK LINE IN `respond`

`coding_assessment.final_answer.accept_structured_answer(session, *,
conversation, question, auto_submitted=False)` is called unconditionally after
a structured answer's row is written. It is a no-op for every other format and
for a legacy v1 coding question; for a v2 question it reads the answer row in
the same session and calls `submissions.accept_final`, so a rolled-back turn
stores and dispatches nothing. A missing answer row RAISES: it is a routing
defect, and a silent None would leave a final answer stored and never
executed. It lives in the service layer rather than the API because Phase 3
moves the respond body into `services/assessment_conversation/`.

`GET .../coding/{qid}/submission` answers `{state_word}`: "Submitted", "Being
checked" or "Checked". Never a result: the hidden tests stay hidden, including
how the program did on them.

### THE RECRUITER READS THE SENTENCE AND THE REVIEW, NEVER THE KEY

`coding_assessment.transcript.recruiter_views` gives the transcript, per
executed answer, the outcome SENTENCE (counts spelled out), the compiler's
message when it did not compile (derived from the code alone, so it cannot
quote a hidden test), and the review's reasoning and verbatim citations.
`TranscriptAnswerDetailOut` gains `coding_outcome`, `compile_error`,
`review_reasoning` and `review_citations`. `_answer_key` answers `{}` for a v2
question: its hidden tests, reference and approach notes live only in
`coding_question_keys`. A legacy v1 answer keeps `expected_approach` and its
stored `not_executed_note`.

### THE ANSWER KEY IS SWEPT FOR, END TO END

`tests/test_hidden_tests_never_leave_server.py` runs one journey over HTTP
against a real Postgres with sentinel strings in the key: Run and poll, the
final answer through the hook, the hidden-test run against a program that
ECHOES its stdin (so a hidden input comes back as output and inside an error),
the review by a stubbed model that records its prompts, the state words, and
the recruiter transcript. Every response, every DEBUG log record, every prompt,
every dispatched argument and the stored run, submission, message and answer
rows (read from a second connection) are swept. The approach notes are allowed
in exactly one place, the review prompt, and nowhere a person can read them.
Mutation-checked twice: an approach leaked through `_answer_key`, and hidden
stdout kept in `summarise_hidden`, each fail it.

### NO SHIPPED PROCESS CAN RUN A CANDIDATE'S CODE

`tests/test_candidate_code_never_executes.py` parses every module of the
backend image, the zip Lambda and the analysis service and finds every
execution capability (a process module, an `os` process call, an asyncio
subprocess, `exec`/`eval`/`compile`/`__import__`). The set is pinned EXACTLY:
today it is one module, `services/video/processing.py`, and that module is
asserted to run one `subprocess.run` on an argv parameter with no shell,
handed only list literals whose program is `ffmpeg` or `ffprobe`, and to name
nothing that holds code. A new execution path anywhere fails the build naming
the file. The detectors are checked against planted violations in process.

### SUPERSESSIONS

- 2026-09-02 "coding is judged by READING and every evaluation and every
  recruiter view says the code was not executed": for a v2 (executed) coding
  answer the recruiter view states the executed outcome instead;
  `not_executed_note` now appears only on legacy v1 rows that were read.
