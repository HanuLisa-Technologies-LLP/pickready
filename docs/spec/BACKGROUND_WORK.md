# Background work

**Status:** current. **Reviewed:** 2026-10-14. How background work is dispatched, routed, retried and scheduled, and what a person needs to know before adding a task, adding a sweep, or debugging one that did not run. Rules in short form: [../engineering/backend.md](../engineering/backend.md).

## 1. Shape

Short work is billed per invocation (Lambda); long work is one on-demand Fargate task per dispatch. There is no standing worker pool and no scheduler process to lose. A task's name, implementation, destination and retry policy are one declaration. The product's own economics have this shape: an assessment either is running or it is not.

## 2. The one way to start background work

```python
from app.workers.dispatch import dispatch

handle = dispatch("pickready.run_matching", args=[str(job.id)])
handle.id  # the polling id, available immediately
```

`handle.id` is generated client-side, so a request handler has the polling id before the invoke completes. The frontend's `task_id` and `task_ids` fields rely on that.

### A task about a row is dispatched after the row is committed

A request that writes the row a task reads uses

```python
from app.workers.dispatch import dispatch_after_commit

handle = dispatch_after_commit(session, "pickready.run_matching", args=[str(job.id)])
```

The handle comes back at once; the invoke runs from SQLAlchemy's `after_commit` through `core/after_commit.on_commit`. A rollback, or a session closed without committing, dispatches nothing. The task name, the backend and the JSON-ness of the arguments are checked at the call; a `DispatchError` from the invoke itself is logged at ERROR after the commit and never raised out of `commit()`, so each call site names the sweep that repairs a lost invoke. Never call it and then raise: the raise rolls the dispatch back. A callback registered inside a SAVEPOINT that later rolls back still fires on the outer commit, so register after the savepoint. `tests/test_dispatch_after_commit_sweep.py` refuses a new bare `dispatch` in `app/api`, `services/proctoring` and `services/video`; its legacy allowlist is empty. A test inspecting a dispatch inside a transaction it rolls back reads `after_commit.pending_labels(session)`.

### The three backends are three deployments

| `TASK_DISPATCH_BACKEND` | What happens | Where |
|---|---|---|
| `aws` | Invoke Lambda. Short work runs there; long work becomes one on-demand Fargate task. | Pilot, staging, production |
| `local` | Run the task in a daemon thread in this process. | `infra/docker-compose.yml` |
| `record` | Accept the dispatch, run nothing, remember it. | The test suite |

Nothing falls back to anything. The backend is chosen once from configuration, and a failure inside it raises: several call sites degrade visibly on an enqueue failure (a skipped outreach recipient says it could not be queued), and a dispatcher that swallowed the error would turn that into silence. `record` is refused in production by the dispatcher itself, because it accepts work and does not do it.

### Timeouts are explicit

An unreachable endpoint with no timeout does not fail, it hangs, which silently defeats every `try`/`except` around the call. botocore's defaults are a 60-second connect timeout with retries on top. Every client in `app/workers/dispatch.py` and `app/workers/agent_client.py` sets both timeouts short and caps the attempts.

## 3. The route is a cost decision, and it is data

`app/workers/registry.py`:

```python
@task(name="pickready.send_email", route=Route.LAMBDA, rls=...)
@task(name="pickready.run_matching", route=Route.ECS, rls=...)
```

**`Route.LAMBDA`** is work measured in seconds: delivery, resume parsing, the reconciliation sweeps. It runs in `readypick-task-worker`.

**`Route.ECS`** is work measured in minutes with a real ceiling risk: scoring a whole candidate pool, writing a PRISM Report, generating questions, processing a recording. One on-demand Fargate task per dispatch, started by `readypick-assessment-trigger`, which stops when the process exits. Lambda's fifteen-minute ceiling is the hard reason for the split: a task killed at a ceiling leaves half a pool scored with nothing saying so.

Delivery cannot wait behind an LLM chain because the two kinds of work do not share a pool. `backend/tests/test_email_delivery.py` asserts it against the registry rather than a hand-kept list.

**Every task declares its RLS scope** (`rls="tenant"` or `"bypass"`, no default; a bypass carries `rls_reason`), checked by `tests/test_worker_tenant_session.py`.

## 4. Retries have exactly one owner

`runtime.run_task` owns the loop. The platform's own asynchronous retry is set to zero in `aws_lambda_function_event_invoke_config`: three in-process attempts under two platform attempts would be nine sends of one email. A permanently failed invocation is published once to the alarm topic.

- **The loop predicts.** Before attempt N+1 it asks whether that attempt could finish inside the remaining budget, using the longest attempt so far as the estimate and `context.get_remaining_time_in_millis()` as the budget. `elapsed >= deadline` sounds right and is not.
- **The ceiling belongs to the platform** (the Lambda timeout, the Fargate `stopTimeout`); the retry loop lives inside the invocation that gets killed, so a task that outran its budget is not retried.
- **A final failure is re-raised**, so the error metric moves, the alarm fires and the destination publishes.
- Delivery keeps its operator knob: `max_attempts_setting="delivery_max_retries"` is resolved at run time.

## 5. Where a task actually runs

| Entry point | Serves | Packaging |
|---|---|---|
| `app.workers.entrypoints.lambda_worker.lambda_handler` | `readypick-task-worker` | Backend image |
| `app.workers.entrypoints.agents.jd_generation_handler` | `readypick-jd-gen` | Backend image |
| `app.workers.entrypoints.agents.company_profile_handler` | `readypick-company-profile` | Backend image |
| `app.workers.entrypoints.ecs_task` (`docker-entrypoint.sh agent`) | the on-demand Fargate task | Backend image |
| `lambda/assessment_trigger/handler.py` | `readypick-assessment-trigger` | Zip |

All converge on `runtime.run_task`: one implementation of "resolve a name, open a session, run it, retry it, record what happened". The three image functions run the backend image because they import the application (model router, prompt registry, database session); a second artifact with the same code would let an agent and the API disagree about what a prompt says or a grade means. **The trigger is the only zip, and it must stay thirty lines and boto3.** It is the only holder of `iam:PassRole`, a privilege-escalation primitive; the internet-facing API holds none. Its grant names the task definition families (with a revision wildcard), the cluster and the two roles it may pass.

## 6. The two synchronous agents

`readypick-jd-gen` and `readypick-company-profile` produce a draft a recruiter is waiting for, so they are invoked with `InvocationType="RequestResponse"`. JD generation has a 25-second per-attempt and 50-second total model budget; the function moves that wait off the API task. `agent_client` calls the same service function directly under `local` and `record`, so there is one implementation of each agent and it lives in `app/services`. There is no single-candidate resume match function: matching is `pickready.run_matching`, a batch over every candidate linked to a job with stage-by-stage progress, which runs as an on-demand Fargate task.

## 7. The schedule

`app/workers/schedule.py` is the source of truth. Each environment mirrors it as EventBridge Scheduler rules, and `backend/tests/test_schedule_parity.py` reads both and fails on drift. A schedule invokes `readypick-task-worker` with the same payload a dispatch sends; giving the scheduler its own entry point would let it fire something the registry does not have.

The two halves fail differently:

- A rule in Terraform with no entry in Python costs one CloudWatch error every interval, for ever, with nobody reading it.
- **An entry in Python with no rule in Terraform is a sweep that never runs**, and that one is silent by construction: a reconciliation sweep does nothing when there is nothing to repair, so "not running" and "nothing to do" produce the same empty log.

**The sweeps do not run locally.** A laptop has no EventBridge Scheduler: `reconcile_job_setup`, the credit reconciler and the proctoring sweeps do not fire under `infra/docker-compose.yml`. Run one by hand:

```bash
docker compose -f infra/docker-compose.yml exec backend \
  python -c "from app.workers.runtime import run_task; \
             run_task({'task': 'pickready.reconcile_job_setup', \
                       'args': [], 'kwargs': {}, 'run_id': ''})"
```

## 8. Run status

`app/workers/status.py`, in Redis, six-hour TTL. It answers the two live questions the product asks: the matching run's stage list on the job page, and the per-recipient delivery state in the outreach modal.

- **An unknown id reads as `PENDING`**, so the job page keeps polling a run that is about to start. A run whose status expired reads as pending for ever, which is why the TTL is generous relative to the longest task.
- **A `FAILURE` records the exception class name and an empty payload.** A message can quote a row, and this payload is read by a recruiter's browser.
- **Reading the outreach state needs both halves.** `send_email` returns `{"status": "failed"}` for a permanent failure it deliberately did not retry: a run that succeeded and an email that did not arrive.

The permanent record of what happened is elsewhere: `email_log` for delivery, `audit_log` for the trigger, and the rows the task itself wrote.

## 9. Redis and the health probe

| What Redis carries | What losing it costs |
|---|---|
| The proctoring warning counter | `proctoring/gate` answers 503 rather than silently not warning, so every assessment turn is refused |
| Auth sessions | 503, never fail-open |
| The rate limiter | Nothing: it fails open by design |
| The run-status record | A finished run reads as pending for ever |
| The caches | A slower read |

`/health` probes Redis as well as the database, so a task that has lost it leaves the target group instead of serving assessments it cannot monitor. `maxmemory-policy` stays `noeviction`: LRU would evict a live assessment's warning counter and silently reset a candidate's warnings to zero, and evict the run-status record a recruiter is watching.
