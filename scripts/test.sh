#!/usr/bin/env bash
#
# One command from a clean clone to a green suite (spec-doc6 §3.2).
#
#   ./scripts/test.sh                 # backend suite against the test stack
#   ./scripts/test.sh integration     # only the tests that touch real infrastructure
#   ./scripts/test.sh all             # backend + skip inventory + frontend
#
# The Makefile targets `test`, `test-integration` and `test-all` are thin
# wrappers over this file. This file is the implementation, not the fallback:
# `make` is not installed on every machine this project is developed on
# (notably Git Bash on Windows), and a capability that only exists behind a tool
# half the team lacks is not a capability. See CONTRIBUTING.md.
#
# Flags:
#   --keep      leave the stack running afterwards (fast iteration)
#   --no-up     assume the stack is already running
#   -- ...      everything after a bare -- is passed through to pytest
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.test.yml"
COMPOSE=(docker compose -f "${COMPOSE_FILE}")

MODE="unit"
KEEP=0
BRING_UP=1
PYTEST_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    unit|integration|all) MODE="$1"; shift ;;
    --keep) KEEP=1; shift ;;
    --no-up) BRING_UP=0; shift ;;
    --) shift; PYTEST_ARGS=("$@"); break ;;
    *) echo "test.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

# ── The addresses of the test stack ──────────────────────────────────────────
#
# Deliberately NOT the defaults. On the machine this was written against a
# native Windows PostgreSQL service already held 0.0.0.0:5432; Docker's
# published port bound alongside it and lost, so every host-side connection
# reached the wrong server and 71 integration tests answered "no database
# reachable" and reported SKIPPED. Pinning the stack to ports nothing else
# claims is what makes a green suite mean something.
PGPORT_TEST=55432
REDIS_PORT_TEST=6381
S3_PORT_TEST=9101
DB_NAME=readypick_test
DB_USER=readypick_test
DB_PASSWORD=readypick_test

export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PGPORT_TEST}/${DB_NAME}"
export REDIS_URL="redis://127.0.0.1:${REDIS_PORT_TEST}/0"
# Not a secret. It signs tokens that exist for the duration of one test run
# against a database that lives in tmpfs, and it is committed so a clean clone
# needs no local file to produce a green suite. Long enough to clear the
# 32-byte HMAC recommendation so the run is not buried in warnings.
export JWT_SECRET="readypick-test-suite-signing-key-not-a-secret"

# S3_TEST_* rather than S3_* on purpose. Setting the APPLICATION's `S3_BUCKET`
# and `S3_ENDPOINT_URL` here would change `get_settings()` for the whole suite,
# including the tests that assert an unconfigured bucket is its own error class.
# The storage tests read these names and patch settings themselves.
export S3_TEST_ENDPOINT_URL="http://127.0.0.1:${S3_PORT_TEST}"
export S3_TEST_BUCKET="readypick-test-private"
export S3_TEST_ACCESS_KEY="readypick_test"
export S3_TEST_SECRET_KEY="readypick_test"
export AWS_DEFAULT_REGION="ap-south-1"

# DELIBERATELY UNSET: the model and embedding credentials. The suite must pass
# with no model credential at all (spec-doc6 D6), every generative path has a
# deterministic fallback, and a key here would let a vendor outage fail the
# build. `tests/test_ai_reach_semantic.py` skips one test for this reason and
# `docs/SKIPS.md` records it as the one legitimate skip in the inventory.
unset OPENAI_GPT_TERRA OPENAI_GPT_LUNA VOYAGE_CONTEXT_4 || true

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
  # The three long-running services are named explicitly. `up --wait` treats a
  # container that EXITS as a failure, including the one-shot `minio-init` that
  # exits 0 having done its job, so waiting on the whole project returns 1 on a
  # perfectly healthy stack.
  "${COMPOSE[@]}" up -d --wait postgres redis minio
fi

# The bucket, asserted rather than assumed. `minio-init` provisions it on a
# plain `docker compose up`, but this script does not wait on that container
# (see above), and "the object store is ready" means "the bucket the suite
# writes to exists", not "a container that usually creates it has started".
echo "==> Ensuring bucket ${S3_TEST_BUCKET:-readypick-test-private}"
"${COMPOSE[@]}" exec -T minio sh -c "
  mc alias set local http://127.0.0.1:9000 '${S3_TEST_ACCESS_KEY}' '${S3_TEST_SECRET_KEY}' >/dev/null &&
  mc mb --ignore-existing 'local/${S3_TEST_BUCKET}' >/dev/null &&
  mc ls 'local/${S3_TEST_BUCKET}' >/dev/null
"

# A fresh database every run, even when --keep left the last one behind.
# Determinism is a requirement here (spec-doc6 §11.2): a suite whose result
# depends on rows a previous run left is a suite that cannot be trusted to
# reproduce. The data directory is tmpfs, so this costs about a second.
echo "==> Recreating ${DB_NAME}"
"${COMPOSE[@]}" exec -T postgres psql -U "${DB_USER}" -d postgres \
  -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE)" \
  -c "CREATE DATABASE ${DB_NAME}" >/dev/null

# The same determinism argument, applied to Redis. The database is recreated
# but a cache key carries no database identity, so anything a previous run
# cached (rbac's role_permissions rows cache their payload for 120 seconds,
# keyed by tenant and role) can answer for rows that no longer exist. The
# 2026-09-01 dashboard-403 investigation ruled Redis OUT as that day's cause,
# and the flush stays anyway: a cache that outlives the data it caches is a
# reproducibility hole waiting for a fixed-identifier key to fall into it.
echo "==> Flushing the test Redis"
"${COMPOSE[@]}" exec -T redis redis-cli FLUSHALL >/dev/null

echo "==> alembic upgrade head"
( cd "${REPO_ROOT}/backend" && python -m alembic upgrade head >/dev/null )

run_backend() {
  echo "==> Backend suite: $*"
  ( cd "${REPO_ROOT}/backend" && python -m pytest "$@" )
}

case "${MODE}" in
  unit)
    run_backend -q --no-header -rs "${PYTEST_ARGS[@]}"
    ;;

  integration)
    # Selected by what a test REACHES FOR, not by a hand-maintained list. A
    # literal file list in this script is a list that goes stale the first time
    # somebody adds an integration test and forgets to come back here.
    mapfile -t INTEGRATION_FILES < <(
      cd "${REPO_ROOT}/backend" &&
      grep -rl -E 'create_async_engine|object_storage|S3_TEST_ENDPOINT_URL' tests/*.py |
      sort
    )
    if [[ "${#INTEGRATION_FILES[@]}" -eq 0 ]]; then
      echo "test.sh: no integration tests matched. That is a defect in this" >&2
      echo "         selector, not an empty suite." >&2
      exit 1
    fi
    printf '    %s\n' "${INTEGRATION_FILES[@]}"
    run_backend -q --no-header -rs "${INTEGRATION_FILES[@]}" "${PYTEST_ARGS[@]}"
    ;;

  all)
    run_backend -q --no-header -rs "${PYTEST_ARGS[@]}"

    echo "==> Agent evaluation gates"
    ( cd "${REPO_ROOT}/backend" \
      && python -m app.scripts.eval_interview \
      && python -m app.scripts.eval_agents )

    if [[ -d "${REPO_ROOT}/frontend/node_modules" ]]; then
      echo "==> Frontend suite"
      ( cd "${REPO_ROOT}/frontend" && npm test --silent )
    else
      # Stated rather than skipped silently. A step that prints nothing when it
      # does not run is a step everybody believes ran.
      echo "==> Frontend suite SKIPPED: frontend/node_modules is absent."
      echo "    Run 'npm ci' in frontend/ and re-run, or use 'make test' for"
      echo "    the backend alone. This is not a pass."
      exit 1
    fi
    ;;
esac

echo ""
echo "==> Done (${MODE})"
