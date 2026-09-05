# Background work

**Status:** current. Supersedes the Celery arrangement described in the
2026-07-28 and 2026-08-06 sections of `claude.md`.
**Date:** 2026-09-05.

Celery is gone. This document is what replaced it, why each replacement is
shaped the way it is, and what a person needs to know before adding a task,
adding a sweep, or debugging one that did not run.

---

## 1. Why this changed

Celery ran two always-on containers: a worker pool and a beat scheduler. They
were sized for the busiest thing they might be asked to do, and they were
running whether or not anybody was being assessed. For a product whose expensive
work happens a few times an hour, that is a standing bill for idle capacity.

The replacement is per-invocation for short work and per-dispatch for long work,
which is the same shape the product's own economics have: an assessment either
is running or it is not.

Two things came with that and are worth stating plainly as gains rather than as
side effects:

- **A dispatch, a task's implementation and its destination are now one
  declaration.** Celery kept the name on a decorator and the queue in a routing
  table on the app object. A task added without a routing entry fell through to
  the default queue, which is how AI work and email delivery ended up sharing
  one pool.
- **There is no scheduler process to lose.** Beat was a singleton whose failure
  mode was silence.

And one thing was lost, which is stated in section 7.

---

## 2. The one way to start background work

```python
from app.workers.dispatch import dispatch

handle = dispatch("pickready.run_matching", args=[str(job.id)])
handle.id  # the polling id, available immediately
```

The signature mirrors `celery_app.send_task(name, args=[...])` so a reviewer can
see that only the transport changed.

`handle.id` is generated client-side, exactly as Celery's task ids were, so a
request handler has the polling id before the invoke completes. The frontend's
`task_id` and `task_ids` fields are unchanged.

### The three backends are three deployments

| `TASK_DISPATCH_BACKEND` | What happens | Where |
|---|---|---|
| `aws` | Invoke Lambda. Short work runs there; long work becomes one on-demand Fargate task. | Pilot, staging, production |
| `local` | Run the task in a daemon thread in this process. | `infra/docker-compose.yml` |
| `record` | Accept the dispatch, run nothing, remember it. | The test suite |

Nothing falls back to anything. The backend is chosen once from configuration,
and a failure inside it **raises**. Several call sites already caught the
enqueue failure and degraded visibly (a skipped outreach recipient says it could
not be queued); a dispatcher that swallowed the error would turn those into
silence.

`record` is **refused in production** by the dispatcher itself. It is the mode
that accepts work and does not do it, which is exactly the failure this product
has been bitten by, so it may only be reached deliberately.

### Timeouts are explicit

Publishing to Redis had no timeout by default, so an unreachable broker did not
fail, it hung, which silently defeated every `try`/`except` around an enqueue
because nothing was ever raised. botocore has the same shape of default: a
60-second connect timeout with retries on top. Every client in
`app/workers/dispatch.py` and `app/workers/agent_client.py` sets both timeouts
short and caps the attempts.

---

## 3. The route is a cost decision, and it is data

`app/workers/registry.py`:

```python
@task(name="pickready.send_email", route=Route.LAMBDA, ...)
@task(name="pickready.run_matching", route=Route.ECS, ...)
```

**`Route.LAMBDA`** is work measured in seconds: delivery, resume parsing, the
reconciliation sweeps. It runs in `readypick-task-worker`, which is billed per
invocation and has no standing capacity a slow task could occupy.

**`Route.ECS`** is work measured in minutes with a real ceiling risk: scoring a
whole candidate pool, compiling a Tatva matrix through seven stages, writing a
PRISM report. One on-demand Fargate task per dispatch, started by
`readypick-assessment-trigger`, which stops when the process exits.

Lambda's fifteen-minute ceiling is the hard reason for the split. A matching run
over a large pool legitimately outruns it, and a task killed at a ceiling leaves
half a pool scored with nothing saying so.

### What the mail-queue split guaranteed now holds structurally

On 2026-08-01 two wedged question-generation runs took both slots of a
`--concurrency=2` worker, and a staff invitation queued behind them went
undelivered for six minutes while the API had already answered 201 with
`email_dispatch: "queued"`. Celery answered that with a second queue and a
second pool.

Delivery can no longer wait behind an LLM chain because the two kinds of work do
not share a pool: neither of them has one.
`backend/tests/test_email_delivery.py` asserts both halves of it against the
registry rather than a hand-kept list.

---

## 4. Retries have exactly one owner

`runtime.run_task` owns the loop. The platform's own asynchronous retry is set
to **zero** in `aws_lambda_function_event_invoke_config`.

Two mechanisms stacked would multiply: three in-process attempts under two
platform attempts is nine sends of one email, and a duplicate invitation is
worse than a failure somebody can see. What replaces the platform retry is the
failure destination: a permanently failed invocation is published, once, to the
alarm topic.

- **The loop predicts.** Before starting attempt N+1 it asks whether that
  attempt could *finish* inside the remaining budget, using the longest attempt
  so far as the estimate and `context.get_remaining_time_in_millis()` as the
  budget. `elapsed >= deadline` is the check that sounds right and is not. Same
  rule `agent_loop` and `llm_router` already state.
- **There is no soft time limit any more, and that is a simplification.** Celery
  had two ceilings: a soft one that raised inside the task and a hard one that
  killed it. The soft limit existed to turn a hang into a retryable exception,
  and the lesson this codebase then recorded was that retrying a timeout is
  exactly wrong, so `SoftTimeLimitExceeded` had to be added to an exclusion list
  to undo the thing the soft limit was for. Now the ceiling belongs to the
  platform, the retry loop lives inside the invocation that gets killed, and the
  exclusion list is gone because nothing needs excluding.
- **A final failure is re-raised.** The invocation fails, the function's error
  metric moves, the alarm fires and the destination publishes. Swallowing it
  would leave the platform believing every invocation succeeded.

Delivery keeps its operator knob: `max_attempts_setting="delivery_max_retries"`
is resolved at run time, so changing it is an environment change rather than a
deploy.

---

## 5. Where a task actually runs

| Entry point | Serves | Packaging |
|---|---|---|
| `app.workers.entrypoints.lambda_worker.lambda_handler` | `readypick-task-worker` | Backend image |
| `app.workers.entrypoints.agents.jd_generation_handler` | `readypick-jd-gen` | Backend image |
| `app.workers.entrypoints.agents.company_profile_handler` | `readypick-company-profile` | Backend image |
| `app.workers.entrypoints.ecs_task` (`docker-entrypoint.sh agent`) | the on-demand Fargate task | Backend image |
| `lambda/assessment_trigger/handler.py` | `readypick-assessment-trigger` | Zip |

All of them converge on `runtime.run_task`. There is one implementation of
"resolve a name, open a session, run it, retry it, record what happened", so a
task cannot behave differently depending on which door it came through.

**The three image functions run the backend image because they import the
application**: the model router, the prompt registry, a database session.
Building a second artifact carrying the same code would let an agent and the API
disagree about what a prompt says or what a grade means. The infrastructure
brief suggested zip packaging unless a dependency forces otherwise; a dependency
forces otherwise.

**The trigger is the only zip, and it must stay thirty lines and boto3.** It is
the only thing in the account holding `iam:PassRole`, which is a
privilege-escalation primitive: anything that can pass a role can run code as
it. The API service is reachable from the internet through the load balancer and
has no business holding it. A function whose whole source fits on a screen can.

Its grant names all three of: the task definition families it may run (with a
revision wildcard, so a deploy that registers a new revision does not break it),
the cluster, and the two roles it may pass.

---

## 6. The two synchronous agents, and the one that does not exist

`readypick-jd-gen` and `readypick-company-profile` produce a draft the recruiter
is sitting and waiting for, so they are invoked with
`InvocationType="RequestResponse"` and the request handler blocks exactly as
long as it did before. What changed is which process spends the time: JD
generation has a 25-second per-attempt and 50-second total model budget, and
holding that open on an API task that is also serving every other request is
what this moves.

`agent_client` calls the same service function directly under `local` and
`record`, so there is one implementation of each agent and it lives in
`app/services`.

**There is no `readypick-resume-jd-match`.** The infrastructure brief names it
as a third short agent at 256MB and 300 seconds. This product's resume-to-JD
matching is `pickready.run_matching`: a batch over every candidate linked to a
job, with model calls per batch and a stage-by-stage progress display a
recruiter watches. That function could not finish it, and there is no
single-candidate caller in the product to give one instead, so building it would
mean inventing a caller for it. It runs as an on-demand Fargate task with the
assessment agent, which is the same pay-only-while-running model the brief is
buying.

---

## 7. The schedule, and what was genuinely lost

`app/workers/schedule.py` is the source of truth. Each environment mirrors it as
EventBridge Scheduler rules, and `backend/tests/test_schedule_parity.py` reads
both and fails on drift.

A schedule invokes `readypick-task-worker` with the same payload a dispatch
sends. A sweep is an ordinary task and arrives through the ordinary door;
giving the scheduler its own entry point would let it fire something the registry
does not have, which is exactly how a beat entry outlived the module it called
(`pickready.probe_llm_models`, hourly, for a whole release).

The two halves fail differently and both matter:

- A rule in Terraform with no entry in Python costs one CloudWatch error every
  interval, for ever, with nobody reading it.
- **An entry in Python with no rule in Terraform is a sweep that never runs**,
  and that one is silent by construction: a reconciliation sweep does nothing
  when there is nothing to repair, so "not running" and "nothing to do" produce
  the same empty log.

### What was lost: the sweeps do not run locally

A laptop has no EventBridge Scheduler. `reconcile_job_setup`, the credit
reconciler and the proctoring sweeps do **not** fire under
`infra/docker-compose.yml`. This is stated in the compose file rather than
papered over, because a developer who believes they are running will debug the
wrong thing. Run one by hand:

```bash
docker compose -f infra/docker-compose.yml exec backend \
  python -c "from app.workers.runtime import run_task; \
             run_task({'task': 'pickready.reconcile_job_setup', \
                       'args': [], 'kwargs': {}, 'run_id': ''})"
```

---

## 8. Run status, which replaced the result backend

`app/workers/status.py`, in Redis, with a six-hour TTL. It answers the two live
questions the product actually asks: the matching run's stage list on the job
page, and the per-recipient delivery state in the outreach modal.

- **An unknown id reads as `PENDING`**, exactly as `AsyncResult` behaved. The
  alternative is worse: reporting "unknown" would make the job page stop polling
  a run that is about to start. The honest consequence is that a run whose
  status expired reads as pending for ever, which is why the TTL is generous
  relative to the longest task.
- **A `FAILURE` records the exception class name and an empty payload.** A
  message can quote a row, and this payload is read by a recruiter's browser.
  The Celery endpoint read `result.info`, which is the stage payload only while
  the task is in `PROGRESS` and is the exception on failure.
- **Reading the outreach state needs both halves.** `send_email` returns
  `{"status": "failed"}` for a permanent failure it deliberately did not retry,
  which is a run that succeeded and an email that did not arrive.

The permanent record of what happened is elsewhere and unaffected: `email_log`
for delivery, `audit_logs` for the trigger, and the rows the task itself wrote.

---

## 9. Redis is not a broker, and the health probe matters more

What Redis still carries:

| | What losing it costs |
|---|---|
| The proctoring warning counter | `proctoring/gate` answers 503 rather than silently not warning, so every assessment turn is refused |
| The rate limiter | Nothing: it fails open by design |
| The run-status record | A finished run reads as pending for ever |
| The caches | A slower read |

The counter is the one that decides it. `/health` probes Redis as well as the
database, so a task that has lost it leaves the target group instead of serving
assessments it cannot monitor.

`maxmemory-policy` stays `noeviction`, for a new reason. It used to be "LRU
would evict queued tasks". It is now "LRU would evict a live assessment's
warning counter and silently reset a candidate's warnings to zero, and evict the
run-status record a recruiter is watching".

---

## 10. Adding a task

1. Write the function in `backend/app/workers/tasks.py`. It is a plain
   synchronous function; use `_run(...)` and `_worker_session()` for async
   service code.
2. Decorate it with `@task(name="pickready.<name>", route=...)`. The route is
   the cost decision in section 3, not a preference.
3. Dispatch it from wherever it belongs with
   `dispatch("pickready.<name>", args=[...])`.
4. If it is periodic, add an entry to `app/workers/schedule.py` **and** to every
   environment's `module "scheduler"` block. `test_schedule_parity.py` fails if
   you do one and not the other, and `test_task_registry.py` fails if a
   scheduled task needs arguments a scheduler cannot send.

`backend/tests/test_task_registry.py` checks that every task's deferred imports
resolve, that every retry policy terminates, and that a task declaring
`bind=True` actually takes a context.
