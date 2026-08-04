#!/bin/sh
# One image, four roles (ESD 15). Cloud Run passes the role as the container
# args, so the same digest backs the API service, the worker pool, beat and the
# migration job, and dependency drift between them is impossible.
#
# Usage: docker-entrypoint.sh [api|worker|beat|migrate] [extra args...]
set -eu

ROLE="${1:-api}"
[ $# -gt 0 ] && shift

# Cloud Run injects PORT and routes only to it. Falling back to 8000 keeps the
# local compose stack and the published port unchanged.
PORT="${PORT:-8000}"

# One uvicorn process by default: the app is async, and Cloud Run scales by
# adding INSTANCES rather than processes, so extra workers here just multiply
# the database pool (DB_POOL_SIZE) against a fixed Cloud SQL connection limit.
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

CELERY_APP="app.workers.celery_app:celery_app"

case "$ROLE" in
  api)
    # --workers is only passed when it would actually do something. uvicorn
    # treats --workers and --reload as mutually exclusive, and the local compose
    # stack appends --reload to this same role.
    #
    # --proxy-headers with a trusted-any allowlist is safe here BECAUSE the
    # only route to the container is Cloud Run's front end, which strips and
    # re-signs X-Forwarded-*. Without it every request appears to arrive over
    # http and the Secure auth cookies are refused.
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
  worker)
    # No -Q: consumes every queue in celery_app.task_queues, so this role keeps
    # draining mail as well and a deployment that never adds the mail-worker
    # pool below still delivers every message.
    exec celery -A "$CELERY_APP" worker \
      --loglevel="${CELERY_LOGLEVEL:-info}" \
      --concurrency="${CELERY_CONCURRENCY:-2}" \
      "$@"
    ;;
  mail-worker)
    # Delivery ONLY. An invitation must not wait behind an LLM chain: on
    # 2026-08-01 two wedged question-generation tasks took both slots of the
    # shared worker and a queued staff invite went undelivered while the API
    # had already reported it sent. Sending is IO against Gmail SMTP and cheap,
    # so this pool runs small and its slots can never be occupied by AI work.
    exec celery -A "$CELERY_APP" worker \
      --loglevel="${CELERY_LOGLEVEL:-info}" \
      --concurrency="${CELERY_MAIL_CONCURRENCY:-4}" \
      --queues=mail \
      "$@"
    ;;
  beat)
    # Beat writes its schedule file, so it needs a writable path. The image's
    # source tree is owned by root and must stay read-only to the runtime user.
    exec celery -A "$CELERY_APP" beat \
      --loglevel="${CELERY_LOGLEVEL:-info}" \
      --schedule "${CELERY_BEAT_SCHEDULE:-/tmp/celerybeat-schedule}" \
      "$@"
    ;;
  migrate)
    # Run as a Cloud Run Job, never on API startup: several instances boot at
    # once during a rollout and would race each other through the same
    # migration. Alembic takes a lock, so the losers would crash-loop.
    exec alembic upgrade head "$@"
    ;;
  *)
    # Anything else is run verbatim, which keeps `docker run ... sh` and
    # one-off management commands available.
    exec "$ROLE" "$@"
    ;;
esac
