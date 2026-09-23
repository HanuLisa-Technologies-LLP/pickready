# CLAUDE.md section draft: Phase 7 Wave A (platform fixes and scrap)

Draft for the orchestrator to fold into the release's single top section. Written
in the file's own voice; nothing here edits `claude.md` directly.

## A TRUNCATED RESPONSE IS HALF AN ANSWER, AND THE ROUTER NO LONGER RETURNS IT

`finish_reason: "length"` was outside `REFUSAL_FINISH_REASONS`, so
`llm_router._attempt` returned the cut body as a complete answer: half a JSON
document to a structured caller, half a sentence to a prose one. The harness
found it on its first fault run (2026-09-22) and pinned it as a finding.

- **`FAILURE_TRUNCATED` is a failure class in `RECOVERY_FOR_FAILURE`**, with its
  own strategy (`retry_with_a_larger_completion_budget`). It is retried ONCE
  (`MAX_TRUNCATION_RETRIES`) at double `max_completion_tokens`, capped by
  `MAX_COMPLETION_TOKENS_CEILING` (twice the largest reviewed `TASK_MAX_TOKENS`
  row, derived rather than typed). The existing cost ceiling prices the doubled
  budget BEFORE the retry starts, so a retry the ceiling cannot afford is never
  sent.
- **A second cut raises `ResponseTruncated`**, a subclass of
  `LLMUnavailableError` beside `ModelRefusal`, so every caller still degrades
  and none needed changing. `needs_human_review` is False: nothing was declined.
- **The cut text goes nowhere.** Not into `ctx.errors`, not into a log line, not
  back to the model as feedback: it is model output written from a prompt that
  carries a candidate's answers.
- **The breaker is not tripped.** The credential and the vendor both worked; a
  sizing problem is fixed in `TASK_MAX_TOKENS`, and `llm_router.truncated`
  names the task and the budget that was too small.
- The scenario was FLIPPED, not deleted:
  `adversarial.a_truncated_model_response_is_refused` (v2). The `2026-09-22`
  harness paragraph "What the fault layer found on its first run, and has not
  been fixed" is SUPERSEDED by this.

## OPENTELEMETRY IS THE ONLY TRACER

Owner ruling. `services/tracing.py` (LangSmith) and its test are deleted.
`llm_router.invoke_llm` opens `otel.genai_span` and nothing else;
`agent_loop.run_loop` opens `otel.agent_loop_span`, an INTERNAL span with its
own allowlist (`_ALLOWED_LOOP_SPAN_ATTRIBUTES`) carrying counts, bounds, a
degraded flag and defect TYPES. **A defect's `detail` never reaches a span**:
it can quote the output it rejected. The LangSmith rule in the 2026-08-05
section is SUPERSEDED. `tests/test_langsmith_removed.py` sweeps the tree; its
one pending exemption is a comment in `api/assessments.py`, removed at merge.

## A REDIS CLIENT IS BOUND TO ITS LOOP, AND THERE IS ONE IMPLEMENTATION OF THAT

`core/redis_loop.LoopBoundRedis` is the one implementation of the 2026-09-16
rule. `core/cache`, `workers/status` and the web-search breaker all hold one.
`workers/status` and `web_research` cached a single process-wide client, and
every task body runs under its own `asyncio.run`, so on a warm Lambda the
SECOND invocation's reads and writes failed inside redis-py and were swallowed
at DEBUG. The job page's stage list and the outreach modal's delivery state
read PENDING for ever, with nothing in any log at the default level.

- **A build failure is latched for THAT LOOP ONLY** and logged at WARNING. The
  next loop tries again rather than inheriting an outage that may have ended.
- **Run-status read and write failures log at WARNING** with the run id and the
  exception class, never the payload (a recruiter's browser reads it).
- **`cache._redis()` stays a function**: it is the seam the harness's
  `redis_down` and a dozen tests substitute.
- The harness limit recorded on 2026-09-22 ("`workers/status` caches its client
  in a module global") is SUPERSEDED for status; `proctoring/state` still holds
  its own client until Phase 3 or Wave B moves it onto `LoopBoundRedis`.

## THE WORKSPACE CHOOSER LEFT THE CODE-LOGIN SERVICE, AND TWO THINGS CHANGED

`services/login_context` is the live half of the retired `services/otp.py`:
eligibility, resolution, context tokens, the chooser. Everything else in that
module, the three unrouted handlers in `api/auth.py`, the four schemas only
they used, `security.generate_otp/hash_otp/verify_otp`, the `AUDIENCE_INTERNAL`
alias and `components/otp-input.tsx` are deleted. `OTPVerifyOut` is now
`SessionOut`, same JSON minus `pending_channels`.

- **The dual-channel pending gate is gone from `select_context`.** It answered
  a `client` with no verified phone `pending_channels` and no cookies, a field
  no screen ever read, so a multi-workspace client was stranded at the chooser.
  A selection now activates an invited user exactly as `_finalize_single` does.
- **The single-use flag FAILS CLOSED.** One atomic `SET NX EX` through
  `core/cache`; a Redis that cannot answer is 503, the `auth_sessions` posture.
  The old limiter fell back to per-process memory, which made a context token
  replayable across API tasks during a Redis blip. Everything that can refuse a
  selection runs BEFORE the token is consumed, so a mismatched pick does not
  burn it.
- **`services/delivery_errors` owns the delivery failure taxonomy.** Both email
  transports imported it from the SMS module; `sms_service` re-exports it only
  for `workers/tasks.py` until Wave B deletes the SMS path.
- `tests/test_login_otp_removed.py` sweeps what is gone and RATCHETS what is
  left for Wave B (the OTP model and enum, `otp_challenges`, `pickready.send_sms`,
  `sms_service`, MSG91 config and secret) to a file list that can only shrink.

## A SHARED, WHITESPACE-NORMALISED REMOVAL SWEEP

`tests/removal_sweep.py` is the one implementation of the sweep
`test_company_dna_removed.py` was taught on 2026-09-23 (normalise whitespace,
map offsets back to a line). It covers backend `app/ tests/ scripts/ harness/`,
frontend `app/ components/ lib/`, `infra/`, the repository `scripts/`,
`.github/` and `.env.example`, skips `alembic/versions/` and `docs/history/`,
has no decode fallback, and takes every other exemption as a named path with a
reason. New removal sweeps use it rather than a copy of the loop.

## SMALLER RULINGS

- `tests/test_repo_hygiene.py` asks `git check-ignore --no-index` that every
  vendored skill path in `tools/design-tools.manifest.json`, `.codex/` and
  `.claude/vivekium-release/` stay ignored and untracked, and that `.coveragerc`
  names no removed feature.
- `app/scripts/migrate_resumes_to_gcs.py` is deleted: it could not migrate
  anything (it fetched through `fetch_resume_bytes`, which refuses every
  non-S3 row), and pilot holds no `gs://` rows.
- The landing-view counter uses `rate_limit.check` (fails open by design).
