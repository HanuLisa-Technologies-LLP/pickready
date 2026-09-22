#!/usr/bin/env bash
#
# The shell entry point for the Vivekium harness (docs/spec/HARNESS.md §10).
#
#   ./scripts/harness.sh run
#   ./scripts/harness.sh run --tier safety
#   ./scripts/harness.sh run --scenario integration.an_assessment_bills_once_and_scores_once
#   ./scripts/harness.sh replay <run_id>
#   ./scripts/harness.sh compare <run_id>
#   ./scripts/harness.sh baseline promote <run_id>
#   ./scripts/harness.sh gate
#   ./scripts/harness.sh list
#
# This mirrors scripts/test.sh deliberately, and for the same stated reason:
# `make` is not installed on every machine this project is developed on, so the
# shell script is the implementation rather than the fallback. It also means the
# harness and the suite address the SAME test stack on the SAME pinned ports,
# which matters more here than it does there: HARNESS.md §1 makes isolation the
# first requirement, and two definitions of "the test stack" is how a harness
# ends up pointed at something it was built never to touch.
#
# Flags:
#   --keep      leave the stack running afterwards (fast iteration)
#   --no-up     assume the stack is already running
#   everything else is passed through to `python -m harness`
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.test.yml"
COMPOSE=(docker compose -f "${COMPOSE_FILE}")

KEEP=0
BRING_UP=1
HARNESS_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --no-up) BRING_UP=0; shift ;;
    *) HARNESS_ARGS+=("$1"); shift ;;
  esac
done

if [[ "${#HARNESS_ARGS[@]}" -eq 0 ]]; then
  # No default command. `run` would be the obvious one and it is deliberately
  # not the default: an argumentless invocation that executes every tier is one
  # somebody runs by accident against a stack they were using for something
  # else.
  echo "harness.sh: name a command (run, replay, compare, baseline, gate, list)." >&2
  echo "            Try: ./scripts/harness.sh run --tier smoke" >&2
  exit 2
fi

# ── The addresses of the test stack ──────────────────────────────────────────
#
# The same pinned ports scripts/test.sh uses, and for the same reason recorded
# there: a native Windows PostgreSQL service already holds 5432 on the machine
# this was written against, so a harness on the conventional port would reach a
# server nobody intended and report a stack of connection errors that look like
# product failures.
PGPORT_TEST=55432
REDIS_PORT_TEST=6381
S3_PORT_TEST=9101
DB_NAME=readypick_test
DB_USER=readypick_test
DB_PASSWORD=readypick_test

export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PGPORT_TEST}/${DB_NAME}"
export REDIS_URL="redis://127.0.0.1:${REDIS_PORT_TEST}/0"
export JWT_SECRET="readypick-test-suite-signing-key-not-a-secret"

export S3_TEST_ENDPOINT_URL="http://127.0.0.1:${S3_PORT_TEST}"
export S3_TEST_BUCKET="readypick-test-private"
export S3_TEST_ACCESS_KEY="readypick_test"
export S3_TEST_SECRET_KEY="readypick_test"
export AWS_DEFAULT_REGION="ap-south-1"

# HARNESS.md §1, second structural guarantee. `record` accepts a dispatch, runs
# nothing and remembers it, and `dispatch.backend()` refuses it outright in
# production. A scenario asserts over what WOULD have been dispatched, which is
# the only way to make a dispatch assertion without standing up a worker.
export TASK_DISPATCH_BACKEND=record

# HARNESS.md §1, third structural guarantee: NO MODEL CREDENTIAL IS SET. Every
# generative path has a deterministic fallback, and a scenario that needs a
# model response gets it from the fault layer serving a vendor contract fixture.
# A key here would let a vendor outage fail the harness, and would let a
# scenario silently exercise a real vendor instead of the contract it declared.
unset OPENAI_GPT_TERRA OPENAI_GPT_LUNA VOYAGE_CONTEXT_4 VOYAGE_RERANK_2_5 || true

teardown() {
  if [[ "${KEEP}" -eq 1 ]]; then
    echo ""
    echo "Stack left running (--keep). Stop it with:"
    echo "  docker compose -f docker-compose.test.yml down -v"
    return
  fi
  echo ""
  echo "==> Tearing down the test stack"
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}

if [[ "${BRING_UP}" -eq 1 ]]; then
  trap teardown EXIT
  echo "==> Starting the test stack (postgres, redis, minio)"
  # Named explicitly, because `up --wait` on the whole project treats the
  # one-shot minio-init container exiting 0 as a failure.
  "${COMPOSE[@]}" up -d --wait postgres redis minio

  echo "==> Ensuring bucket ${S3_TEST_BUCKET}"
  "${COMPOSE[@]}" exec -T minio sh -c "
    mc alias set local http://127.0.0.1:9000 '${S3_TEST_ACCESS_KEY}' '${S3_TEST_SECRET_KEY}' >/dev/null &&
    mc mb --ignore-existing 'local/${S3_TEST_BUCKET}' >/dev/null &&
    mc ls 'local/${S3_TEST_BUCKET}' >/dev/null
  "

  # A fresh database, for the determinism reason scripts/test.sh records: a run
  # whose result depends on rows a previous run left cannot reproduce, and
  # reproduction is the entire point of HARNESS.md §6.
  echo "==> Recreating ${DB_NAME}"
  "${COMPOSE[@]}" exec -T postgres psql -U "${DB_USER}" -d postgres \
    -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE)" \
    -c "CREATE DATABASE ${DB_NAME}" >/dev/null

  echo "==> Flushing the test Redis"
  "${COMPOSE[@]}" exec -T redis redis-cli FLUSHALL >/dev/null

  echo "==> alembic upgrade head"
  ( cd "${REPO_ROOT}/backend" && python -m alembic upgrade head >/dev/null )
fi

echo "==> harness ${HARNESS_ARGS[*]}"
( cd "${REPO_ROOT}/backend" && python -m harness "${HARNESS_ARGS[@]}" )
