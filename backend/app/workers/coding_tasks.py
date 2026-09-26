"""The coding execution tasks: the final submission, the sweep, the probe, the
operator's sandbox verification.

`workers/tasks.py` imports this module, so importing the registry registers
every task here. The bodies live in `services/coding_assessment`; these are
the doors, and each says where it runs and how it retries.

ROUTES. All four are `Route.LAMBDA`: seconds of work each. A submission polls
the sandbox for at most `coding_submission_poll_deadline_seconds` per attempt
and its ticket is committed before it polls, so an attempt the platform kills
loses nothing that the next attempt, or the sweep, does not collect.

RETRIES. Only `ExecutionUnavailable` is retried in the invocation: the sandbox
was briefly unreachable, busy or slow. A `SubmissionDefect` (a refused
request, a key that fails its digest, code that no longer matches what was
accepted) becomes `PermanentTaskFailure`, because retrying it produces the
same refusal; the row stays open and the sweep reports it as stuck.
"""
from __future__ import annotations

import logging
import uuid

from app.services.code_execution import ExecutionUnavailable
from app.workers.dispatch import dispatch
from app.workers.registry import Route, task
from app.workers.runtime import PermanentTaskFailure, _run
from app.workers.runtime import worker_session as _worker_session

logger = logging.getLogger(__name__)


@task(
    name="pickready.execute_coding_submission",
    route=Route.LAMBDA,
    rls="bypass",
    rls_reason=(
        "one tenant's work, not yet proven under tenant_worker_session (PLAN-p7 3.3 "
        "converts only with a real-Postgres read-back test)"
    ),
    max_attempts=3,
    backoff_seconds=5.0,
    backoff_max_seconds=60.0,
    retry_on=(ExecutionUnavailable,),
)
def execute_coding_submission(submission_id: str):
    """Run a final coding answer against its hidden tests, then review it.

    Dispatched after the candidate's submit commits
    (`coding_assessment.submissions.accept_final`) and re-dispatched by the
    sweep. Idempotent: every stage checks the row before it writes, under the
    submission's advisory lock.
    """
    from app.services.coding_assessment import submissions

    async def _task():
        async with _worker_session() as session:
            try:
                report = await submissions.execute_submission(session, uuid.UUID(str(submission_id)))
            except submissions.SubmissionDefect as exc:
                raise PermanentTaskFailure(f"coding submission {submission_id}: {exc}") from exc
        logger.info(
            "coding_submission.task submission_id=%s outcome=%s execution=%s review=%s scoring=%s",
            report.submission_id, report.outcome, report.execution_status,
            report.review_status, report.scoring_dispatched,
        )
        return {
            "outcome": report.outcome,
            "execution_status": report.execution_status,
            "review_status": report.review_status,
            "scoring_dispatched": report.scoring_dispatched,
        }

    return _run(_task())


@task(
    name="pickready.reconcile_coding_submissions",
    route=Route.LAMBDA,
    rls="bypass",
    rls_reason="a sweep: one run reads every tenant's rows",
)
def reconcile_coding_submissions():
    """Every fifteen minutes: re-dispatch coding work nothing is working on.

    Asks the TABLE (`coding_assessment.sweeps.reconcile`), never a timestamp on
    something else. Never gives up: a row past `ATTEMPTS_BEFORE_ALARM` is
    logged at ERROR as `coding.submission_stuck` and re-dispatched anyway. A
    completed conversation whose coding work is done, or has waited past
    `coding_execution_max_wait_hours`, is handed to scoring.
    """
    from app.services.coding_assessment import sweeps

    async def _task():
        async with _worker_session() as session:
            plan = await sweeps.reconcile(session)
        for submission_id in plan.stuck:
            logger.error(
                "coding.submission_stuck submission_id=%s attempts_at_least=%d",
                submission_id, sweeps.ATTEMPTS_BEFORE_ALARM,
            )
        for submission_id in plan.redispatch:
            dispatch("pickready.execute_coding_submission", args=[submission_id])
        for link_id in plan.score_links:
            dispatch("pickready.run_functional_assessment", args=[link_id])
        result = {
            "redispatched": len(plan.redispatch),
            "stuck": len(plan.stuck),
            "scoring_dispatched": len(plan.score_links),
        }
        logger.info("coding.reconciled %s", result)
        return result

    return _run(_task())


@task(
    name="pickready.probe_code_execution",
    route=Route.LAMBDA,
    rls="bypass",
    rls_reason="an operator probe of the code sandbox; it belongs to no tenant",
)
def probe_code_execution():
    """Every five minutes: one canary through the sandbox, and its health.

    Logs `code_execution.probe status=ok|failed|disabled` with the latency and
    nothing else; the observability alarm counts the `failed` lines. Never
    raises for a sandbox failure, because reporting it is the whole job.
    """
    from app.services.coding_assessment import sweeps

    return _run(sweeps.probe())


@task(
    name="pickready.verify_code_execution_sandbox",
    route=Route.LAMBDA,
    rls="bypass",
    rls_reason="an operator probe of the code sandbox; it belongs to no tenant",
)
def verify_code_execution_sandbox():
    """The operator's acceptance check for the sandbox. NOT scheduled.

    Invoked by hand (`aws lambda invoke --invocation-type RequestResponse`)
    before `CODE_EXECUTION_BACKEND` is turned on. Returns the structured
    verdict; only `status: green` means every check ran and passed.
    """
    from app.services.coding_assessment import sweeps

    async def _task():
        return (await sweeps.verify_sandbox()).as_dict()

    return _run(_task())
