#!/bin/sh
# One image, five roles. The same digest backs the API service, the on-demand
# assessment agent, the Lambda functions that run the backend code, the local
# in-process task runner and the migration job, so dependency drift between
# them is impossible.
#
# Usage: docker-entrypoint.sh [api|agent|lambda|migrate] [extra args...]
#
# The Celery `worker`, `mail-worker` and `beat` roles were removed on
# 2026-09-04. Short work now runs in the `readypick-task-worker` Lambda, long
# work as one on-demand Fargate task per dispatch (the `agent` role below), and
# the periodic sweeps are EventBridge Scheduler rules invoking the worker with
# the same payload a dispatch sends. What the mail-worker split existed to
# guarantee -- that a staff invitation can never wait behind an LLM chain --
# now holds structurally: the two kinds of work do not share a pool, because
# neither of them has a pool.
set -eu

ROLE="${1:-api}"
[ $# -gt 0 ] && shift

# The platform injects PORT and routes only to it. Falling back to 8000 keeps
# the local compose stack and the published port unchanged.
PORT="${PORT:-8000}"

# One uvicorn process by default: the app is async, and the service scales by
# adding TASKS rather than processes, so extra workers here just multiply the
# database pool (DB_POOL_SIZE) against a fixed RDS connection limit.
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

case "$ROLE" in
  api)
    # --workers is only passed when it would actually do something. uvicorn
    # treats --workers and --reload as mutually exclusive, and the local compose
    # stack appends --reload to this same role.
    #
    # --proxy-headers with a trusted-any allowlist is safe here BECAUSE the
    # only route to the container is the ALB, which strips and re-signs
    # X-Forwarded-*. Without it every request appears to arrive over http and
    # the Secure auth cookies are refused.
    WORKER_FLAG=""
    if [ "$UVICORN_WORKERS" -gt 1 ]; then
      WORKER_FLAG="--workers $UVICORN_WORKERS"
    fi
    # Deliberately unquoted: empty must expand to no argument at all.
    # shellcheck disable=SC2086
    exec uvicorn app.main:app \
      --host 0.0.0.0 --port "$PORT" \
      --proxy-headers --forwarded-allow-ips='*' \
      $WORKER_FLAG "$@"
    ;;
  agent)
    # One dispatched task, then exit. The Fargate task stops when this process
    # ends, and that is when the meter stops. There is deliberately no loop:
    # a process that waited for more work would be the always-on pool this
    # architecture exists to remove.
    exec python -m app.workers.entrypoints.ecs_task "$@"
    ;;
  lambda)
    # The Lambda runtime interface client, for the three functions that run
    # this image: the generic task worker and the two request/response agents.
    # The handler is passed as an argument, which is how one image serves all
    # three without a per-function build.
    #
    # AWS_LAMBDA_RUNTIME_API is set by the Lambda service and by nothing else,
    # so its absence means this container is being run locally against the RIE
    # or by hand. Refusing loudly beats starting a client that will block
    # forever on a runtime API that is not there.
    if [ -z "${AWS_LAMBDA_RUNTIME_API:-}" ]; then
      echo "docker-entrypoint: the 'lambda' role needs AWS_LAMBDA_RUNTIME_API." >&2
      echo "Run it under the Lambda service or the runtime interface emulator." >&2
      exit 64
    fi
    exec python -m awslambdaric "$@"
    ;;
  migrate)
    # Run as a one-shot task, never on API startup: several tasks boot at once
    # during a rollout and would race each other through the same migration.
    # Alembic takes a lock, so the losers would crash-loop.
    exec alembic upgrade head "$@"
    ;;
  *)
    # Anything else is run verbatim, which keeps `docker run ... sh` and
    # one-off management commands available.
    exec "$ROLE" "$@"
    ;;
esac
