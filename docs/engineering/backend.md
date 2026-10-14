# Backend rules

**Status:** current. **Reviewed:** 2026-10-14 (distilled from the superseded `CLAUDE.md` rulings; enforcing tests are named per rule, not re-run).
**Scope:** `backend/app/` (FastAPI, async SQLAlchemy, asyncpg, Pydantic v2). Loaded on demand by `.claude/rules/backend.md`.
Product behaviour lives in [../spec/](../spec/); decisions in [../decisions/REGISTER.md](../decisions/REGISTER.md).

## Requests and routes

- Python 3.12, `async def` handlers, Pydantic v2 for every request and response. No bare dict crosses the API boundary. Declare every response field: an undeclared field is silently dropped by Pydantic (the old ranked table lost seven columns this way). Assert the response JSON, not the serializer.
- Authorize with `require_capability(...)` or `rbac.authorize`, never `if role == ...`. A new capability constant is half a change: the seeding migration is the other half, and removing one removes its `role_permissions` rows (global and per tenant) in the same change. `test_capability_seed_parity.py` fails a database missing one.
- Authorization that depends on the body or the row (a skill bucket, a job's state) runs in the handler. A state refusal is 409 with one server sentence, not 403.
- A GET writes nothing and dispatches nothing.
- A route nothing calls is deleted, or declared in `tests/test_route_callers.py` (`OPERATOR_SURFACE` or `EXTERNAL_CALLERS`) with a reason. A redirect, alias or deprecated projection is deleted unless a stored row needs it.

## Slow work

- Slow work is dispatched, never run in a handler. The one recorded exception is Save Skills' single bounded Sutra call (`skills.save`).
- A task about a row the request writes uses `dispatch_after_commit(session, "pickready.x", args=[...])`. Never call it and then raise. A callback registered inside a SAVEPOINT that later rolls back still fires, so register after the savepoint. A `DispatchError` after commit is logged, never raised out of `commit()`; each site names the sweep that repairs a lost invoke. `BackgroundTasks` is not "after the commit".
- No Celery. `@task(name=..., route=..., rls=...)` in `workers/registry.py`: `Route.LAMBDA` for seconds, `Route.ECS` for minutes. `rls="tenant"` needs a real-Postgres read-back test; `rls="bypass"` needs `rls_reason` in words. `tenant_worker_session` scopes by asyncpg startup parameters on a private engine: do not copy it into a shared pool.
- A new task lives in `workers/tasks.py` or a `tasks_*.py` module (registered by the import at the bottom of `tasks.py`). A helper imported by name is patched by name in every module that bound it.
- A scheduled sweep is added to `workers/schedule.py` and to every environment's `module "scheduler"`. `test_schedule_parity.py` compares them; one without the other never runs.
- Retries live in `runtime.run_task` only (platform retry is zero). The loop predicts: it refuses an attempt that cannot finish in the remaining budget. A final failure is re-raised so the alarm fires. Every boto3 client has explicit connect and read timeouts and a bounded retry count; an unreachable endpoint that hangs defeats every `try/except`.
- Backends: `aws`, `local` (thread, compose), `record` (tests; refused in production). Never a fallback chain.
- Run progress is published from both sides of a loop (`runtime.TaskContext.publish`). Run-status is Redis with a six-hour TTL; an unknown id reads as PENDING; a failure records the exception class name only.

## Writes, locks, idempotency

- `audit()` flushes its INSERT. Write every audit column in that one insert (`record_action`, `record_agent_action`); assigning to the returned row later issues an `UPDATE audit_log`, which the application role cannot do, and the COMMIT rolls back after the response was sent. `ck_audit_log_agent_has_principal` forbids an agent name without a human principal.
- Every ledger and money write carries a unique `idempotency_key` built from stable logical inputs (never a timestamp or per-attempt UUID). A webhook is recorded with `INSERT ... ON CONFLICT ... DO NOTHING RETURNING`; any other failure answers 5xx so the sender retries.
- `services/locks` is the one lock implementation. Keys come from BLAKE2b, never `hash()` (salted per process). Use `pg_try_advisory_xact_lock` and return when held; use the blocking `advisory_xact_lock(SKILLS, job_id)` for skills writes, grade changes and starts. **Never hold a lock across a model call.** One scoring run per application; the task returns on an existing report under the lock.
- A SQLAlchemy `default=` lands at INSERT, not `__init__`; a fresh row carries `None`. A parameter used in both the SELECT list and the WHERE of an `INSERT ... SELECT` gets two types in asyncpg. A statement that has only met a stub has not been tested.
- Absorb only `SQLAlchemyError` inside a savepoint. A `TypeError` is never an operational failure.
- A Redis client is bound to its loop (`core/redis_loop.LoopBoundRedis`); proctoring state fails closed (503).
- The realtime hub is stopped by the lifespan before the engine is disposed; never guard on `task.done()` (a task on a closed loop is never done); a scheduled publish from an `after_commit` handler must not raise into `commit()`. The socket accepts no frame that writes.

## Models and prompts

- Every model call goes through `services/llm_router.invoke_llm(task_type, ...)`. Policy is data in `config/llm_providers.py` (task type, tier, timeout, budget, temperature, max tokens, retry budget). One vendor, two judge/extract ids and one embedding id (register I-03); `claude-*` is forbidden in executable source. A credential failure trips the breaker on the first occurrence. The key for the called model is required; never fall back to the other key.
- JSON mode is native `response_format`; every JSON caller parses a top-level object. A truncated response is `FAILURE_TRUNCATED` (one retry at double `max_completion_tokens`, then `ResponseTruncated`). Interactive tasks cap at 15s per attempt and 30s total; `jd_generation` and Save Skills' `assessment_context` get the 25s/50s tier; background tasks are uncapped. The generative interactive tier is capped at two tasks.
- Every generative task runs in `services/agent_loop.run_loop` (plan, execute, evaluate, reflect, improve, verify) with deterministic success criteria, never an LLM judge. Bounded by attempts and by a predictive deadline. It never raises; it returns the fallback with `degraded=True`.
- A degradation is recorded, never silent. Template output is never presented as generation. A failed model run is a state, not a substitute score.
- Prompts are `app/prompts/*.txt`; bump `# version:` on a change. A generation prompt carries a good, a fenced bad and an edge example; sufficiency is decided first by `services/generation_sufficiency` and an insufficient verdict returns a fixed `EMPTY_STATE_COPY` key. No prompt lets the model describe its own confidence or sourcing. **Register every prompt builder in `tests/test_ctc_never_in_prompt.py`.**
- Telemetry logs labels, keys and timings, never answer or question text.

## Email and communications

- One writer of candidate email: `email_outbox.queue_candidate_email`, which resolves the sender (named active sender, else tenant default, else platform). A send is claimed (`queued` to `processing`) before the transport; a claimed row is never resent. Automatic emails are idempotent on `email_log.dedupe_key`. `email_log` is a delivery record, not a feed.
- The candidate feed (`services/candidate_updates.py`) is a fixed catalogue: no model, no score, no number, relative link only. `apply_transition` writes it.
- Transport today is `email_transport` = `smtp` or `ses`. **Decided (I-20), pending:** Amazon SES only, no SMTP; `noreply@readypick.ai` for security codes, `contact@readypick.ai` for other platform mail. Do not describe SES-only as built.

## Quality bar

- No silent fallbacks: no bare `except`, no default substituted for a failed retrieval. A broad handler must re-raise, log or read what it caught (`test_no_silent_degradation.py`).
- One implementation per concept. No dual code paths for one behaviour. No dead code: delete, do not deprecate. No commented-out code, no `TODO` or "for now" prose.
- No em dash in any string, in either language, including seeded and generated content.
- A new setting is in `.env.example` or `INTERNAL_ONLY` with a reason (`test_env_example_parity.py`). Every environment variable name and `pickready.*` task name is kept.
- A removal ships with a sweep test using `tests/removal_sweep.py` (whitespace-normalised; named exemptions with reasons; ratchets that only shrink).
- A timestamp is not evidence that work happened: ask the table. A stored value derived from other facts is computed at read time (posting status, profile age, BGV status, rank key).
- Docstrings state provenance. A test whose only assertion is that a mock was called is not a test.
