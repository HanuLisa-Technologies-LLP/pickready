#!/usr/bin/env bash
# PickReady, staged deployment to Google Cloud Run.
#
# Builds and pushes both images, runs migrations to completion, then deploys
# every workload. With TRAFFIC_MODE=no-traffic (the default) the new HTTP
# revisions are created but serve NOBODY: they are reachable only on their
# revision tag URL, which is what scripts/smoke-test.sh probes. Live traffic
# moves later, and only from scripts/promote.sh.
#
#   IMAGE_TAG=$(git rev-parse HEAD) TRAFFIC_MODE=no-traffic ./scripts/deploy.sh
#
# Idempotent: every create is guarded by a describe, the image tag is the git
# SHA so a rerun of the same commit is a no-op rebuild, and re-tagging a
# revision moves the tag rather than erroring. Nothing here deletes.
#
# Pure bash. No PowerShell, no Windows path assumptions.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Config. Every value is overridable from the environment so the same script
# serves CI and a local operator.
# ---------------------------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-pick-ready-503913}"
REGION="${GCP_REGION:-asia-south1}"
REPO_NAME="${GCP_ARTIFACT_REPO:-pickready}"

SQL_INSTANCE="${SQL_INSTANCE:-pickready-postgres}"
SQL_DB="${SQL_DB:-pickready}"
SQL_USER="${SQL_USER:-pickready}"
REDIS_INSTANCE="${REDIS_INSTANCE:-pickready-redis}"

NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"

SVC_BACKEND="${SVC_BACKEND:-pickready-backend}"
SVC_FRONTEND="${SVC_FRONTEND:-pickready-frontend}"
WL_WORKER="${WL_WORKER:-pickready-worker}"
WL_BEAT="${WL_BEAT:-pickready-beat}"
JOB_MIGRATE="${JOB_MIGRATE:-pickready-migrate}"

RUNTIME_SA="${RUNTIME_SA:-pickready-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
IMAGE_BACKEND="${REGISTRY}/backend"
IMAGE_FRONTEND="${REGISTRY}/frontend"

# The git SHA, always. A moving tag like :latest makes a rollback ambiguous and
# makes two revisions of "the same" image possible.
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse HEAD 2>/dev/null || echo dev)}"
SHORT_SHA="$(printf '%s' "$IMAGE_TAG" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-12)"
[ -n "$SHORT_SHA" ] || SHORT_SHA="manual"

# no-traffic (default) stages the revision behind a tag; live promotes it on
# the spot and is only for a deliberate manual deploy.
TRAFFIC_MODE="${TRAFFIC_MODE:-no-traffic}"

# Cloud Run revision tags must match [a-z]([-a-z0-9]*[a-z0-9])?
STAGE_TAG="${STAGE_TAG:-staged-${SHORT_SHA}}"

# Where the staged URLs and revision names are written for the later jobs.
# GitHub Actions reads it back into step outputs; a local run just gets a file.
DEPLOY_OUT="${DEPLOY_OUT:-${REPO_ROOT}/.deploy-state.env}"

# Secret Manager entries that must NEVER become --set-secrets.
#
# DATABASE_URL LEFT THIS LIST ON 2026-08-24 and is now a secret MOUNT. It used
# to be composed here and passed as a plain env var, which meant the assembled
# DSN -- password included -- sat readable on the revision to anyone holding
# run.services.get. The password itself was always in Secret Manager and was
# never logged, so this was narrower than "plaintext credentials in production",
# but the composed DSN was still a credential materialised where it did not need
# to be.
#
# The reason it was an env var is real and still true: a name cannot be both a
# secret mount and an env var on the same revision, and Cloud Run rejects the
# whole deploy with a type conflict. So the switch is mutually exclusive, and
# `build_env` below no longer emits DATABASE_URL.
#
# The secret's version 1 was a STALE host/credential DSN that does not
# authenticate, which is exactly why this script refused to use it. Version 3
# holds the Cloud SQL socket DSN this script composes, byte-identical to what
# the running revision uses, so the switch is a behavioural no-op verified by
# hash before it was made.
#
# REDIS_URL stays an env var: it carries no credential. POSTGRES_PASSWORD stays
# excluded because the app never reads it. NEXT_PUBLIC_* are frontend BUILD
# arguments, not backend runtime config.
SECRET_EXCLUDE_RE='^(REDIS_URL|POSTGRES_PASSWORD|CLOUDINARY_URL|NEXT_PUBLIC_.*)$'

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 || die "gcloud is required."
command -v docker >/dev/null 2>&1 || die "docker is required."

case "$TRAFFIC_MODE" in
  no-traffic|live) : ;;
  *) die "TRAFFIC_MODE must be 'no-traffic' or 'live', got '${TRAFFIC_MODE}'." ;;
esac

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null

log "PickReady deploy"
info "project      ${PROJECT_ID}"
info "region       ${REGION}"
info "image tag    ${IMAGE_TAG}"
info "traffic mode ${TRAFFIC_MODE}"
info "stage tag    ${STAGE_TAG}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
service_exists() { gcloud run services describe "$1" --region="$REGION" >/dev/null 2>&1; }
job_exists()     { gcloud run jobs describe "$1" --region="$REGION" >/dev/null 2>&1; }
pool_exists()    { gcloud run worker-pools describe "$1" --region="$REGION" >/dev/null 2>&1; }

service_url() {
  gcloud run services describe "$1" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null | tr -d '\r'
}

latest_revision() {
  gcloud run services describe "$1" --region="$REGION" \
    --format='value(status.latestCreatedRevisionName)' 2>/dev/null | tr -d '\r'
}

# Cloud Run publishes a tagged revision at TAG---<service host>. Constructing it
# is deliberate: parsing status.traffic[] for the matching tag needs a JSON pass
# that gcloud's --format projections do not express cleanly, and the "---"
# prefix form is stable across both the legacy and the project-number run.app
# hostname layouts.
tagged_url() {  # tagged_url SERVICE TAG
  local base
  base="$(service_url "$1")"
  [ -n "$base" ] || return 1
  printf 'https://%s---%s' "$2" "${base#https://}"
}

# ---------------------------------------------------------------------------
# Connection strings. Resolved from the live instances rather than hardcoded, so
# a recreated Cloud SQL or Memorystore instance does not silently deploy a
# revision pointed at an address that no longer exists.
# ---------------------------------------------------------------------------
resolve_connection_strings() {
  log "Resolving managed-service addresses"

  SQL_CONNECTION_NAME="${SQL_CONNECTION_NAME:-$(
    gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)' | tr -d '\r'
  )}"
  [ -n "$SQL_CONNECTION_NAME" ] || die "cannot resolve the connection name for Cloud SQL instance ${SQL_INSTANCE}."
  info "cloudsql ${SQL_CONNECTION_NAME}"

  # KEEP THE DATABASE_URL SECRET CURRENT, rather than trusting that it is.
  #
  # DATABASE_URL is a secret MOUNT now, so this no longer composes an env var.
  # What it does instead is close a loop that was previously open: version 1 of
  # that secret was a stale DSN that did not authenticate, it sat there for a
  # month, and nothing noticed because nothing read it. A rotated
  # POSTGRES_PASSWORD would leave the mounted DSN just as stale, and the first
  # symptom would be production failing to reach its database.
  #
  # So: recompose the authoritative DSN from POSTGRES_PASSWORD, compare it to
  # the latest version, and add a new version only when it has drifted. Neither
  # value is ever echoed, written to a log, or passed through a temp file, and
  # only whether they matched is printed.
  local pw expected current
  pw="$(gcloud secrets versions access latest --secret=POSTGRES_PASSWORD 2>/dev/null | tr -d '
')"
  [ -n "$pw" ] || die "secret POSTGRES_PASSWORD is empty or unreadable; the deployer needs roles/secretmanager.secretAccessor."
  # The Cloud SQL UNIX SOCKET form. A host:port DSN cannot work here:
  # --add-cloudsql-instances mounts a socket at /cloudsql/<CONNECTION_NAME>,
  # and the app's engine is asyncpg, so the driver is named explicitly.
  expected="postgresql+asyncpg://${SQL_USER}:${pw}@/${SQL_DB}?host=/cloudsql/${SQL_CONNECTION_NAME}"
  current="$(gcloud secrets versions access latest --secret=DATABASE_URL 2>/dev/null || true)"
  if [ "$current" != "$expected" ]; then
    warn "the DATABASE_URL secret has drifted from POSTGRES_PASSWORD; adding a new version"
    printf '%s' "$expected" | gcloud secrets versions add DATABASE_URL --data-file=- >/dev/null       || die "could not refresh the DATABASE_URL secret."
    info "database url secret refreshed (value withheld)"
  else
    info "database url secret is current (value withheld)"
  fi
  unset pw expected current

  if [ -z "${REDIS_URL:-}" ]; then
    local host port
    host="$(gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(host)' | tr -d '\r')"
    port="$(gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(port)' | tr -d '\r')"
    [ -n "$host" ] && [ -n "$port" ] || die "cannot resolve Memorystore instance ${REDIS_INSTANCE} in ${REGION}."
    REDIS_URL="redis://${host}:${port}/0"
  fi
  info "redis    ${REDIS_URL}"
}

# ---------------------------------------------------------------------------
# --set-secrets. Built by LISTING Secret Manager rather than from a checked-in
# roster, so a secret added to the project reaches the next deploy without a
# code change. Names that are not valid shell identifiers are skipped: they
# cannot be environment variables, and Cloud Run would reject the flag.
# ---------------------------------------------------------------------------
build_secret_flag() {
  log "Composing --set-secrets from Secret Manager"
  local name out=""
  while read -r name; do
    [ -n "$name" ] || continue
    if [[ "$name" =~ $SECRET_EXCLUDE_RE ]]; then
      info "skip   ${name} (plain env var or build-time only)"
      continue
    fi
    if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      warn "skipping secret '${name}': not a valid environment variable name"
      continue
    fi
    out="${out}${out:+,}${name}=${name}:latest"
  done < <(gcloud secrets list --format='value(name)' | tr -d '\r' | sort)

  SECRET_FLAG="$out"
  [ -n "$SECRET_FLAG" ] || die "no usable secrets found in Secret Manager for project ${PROJECT_ID}."
  info "$(printf '%s' "$SECRET_FLAG" | tr ',' '\n' | wc -l | tr -d ' ') secrets mounted"
}

# ---------------------------------------------------------------------------
# Plain env vars. gcloud's ^DELIM^ form lets us pick a delimiter other than the
# default comma, because a connection string may legitimately contain a comma.
# The delimiter is PIPE, not '@': the Cloud SQL socket DSN is
# postgresql+asyncpg://user:pass@/db?host=/cloudsql/CONN, and an '@' delimiter
# split it AT that '@' into two broken variables (DATABASE_URL truncated at the
# password, and a bogus '/db?host' var). A pipe cannot appear in any of these
# values (URL userinfo/host/query, a Redis host:port, the literal "production").
# ---------------------------------------------------------------------------
build_env() {  # build_env [extra KEY=VALUE ...]
  # DATABASE_URL is deliberately ABSENT: it arrives as a secret mount now (see
  # SECRET_EXCLUDE_RE). Emitting it here as well would make the same name both
  # an env var and a secret on one revision, which Cloud Run rejects outright.
  local out="ENVIRONMENT=production|REDIS_URL=${REDIS_URL}"
  local kv
  for kv in "$@"; do
    [ -n "$kv" ] || continue
    out="${out}|${kv}"
  done
  printf '^|^%s' "$out"
}

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
firebase_build_args() {
  # Firebase web config is inlined into the bundle by the compiler (see
  # frontend/Dockerfile), so it is a BUILD input and cannot be supplied at
  # deploy time. Precedence: process env, then frontend/.env.local for a local
  # run, then Secret Manager for CI.
  local keys=(
    NEXT_PUBLIC_FIREBASE_API_KEY
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
    NEXT_PUBLIC_FIREBASE_PROJECT_ID
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID
    NEXT_PUBLIC_FIREBASE_APP_ID
  )

  if [ -f frontend/.env.local ]; then
    # shellcheck disable=SC1091
    set -a; . ./frontend/.env.local; set +a
  fi

  local key value
  FIREBASE_ARGS=()
  for key in "${keys[@]}"; do
    value="${!key:-}"
    if [ -z "$value" ]; then
      value="$(gcloud secrets versions access latest --secret="$key" 2>/dev/null | tr -d '\r\n' || true)"
    fi
    if [ -z "$value" ]; then
      case "$key" in
        # These two are what the Dockerfile hard-fails on, and a bundle without
        # them ships an app nobody can sign in to. The rest degrade quietly.
        NEXT_PUBLIC_FIREBASE_API_KEY|NEXT_PUBLIC_FIREBASE_PROJECT_ID)
          die "${key} is not set and is not in Secret Manager. The frontend bundle cannot authenticate without it. See SETUP_INSTRUCTIONS.md." ;;
        *) warn "${key} is empty; the frontend will build without it." ;;
      esac
    fi
    FIREBASE_ARGS+=( "--build-arg" "${key}=${value}" )
  done
}

build_and_push() {
  log "Authenticating docker against Artifact Registry"
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

  log "Building backend ${IMAGE_BACKEND}:${IMAGE_TAG}"
  # --platform is explicit: an arm64 host (an Apple laptop) otherwise produces
  # an image Cloud Run cannot start, and the failure surfaces as a crash loop
  # rather than as a build error.
  docker build --platform linux/amd64 \
    -t "${IMAGE_BACKEND}:${IMAGE_TAG}" \
    -t "${IMAGE_BACKEND}:latest" \
    backend

  log "Building frontend ${IMAGE_FRONTEND}:${IMAGE_TAG}"
  firebase_build_args
  docker build --platform linux/amd64 \
    "${FIREBASE_ARGS[@]}" \
    -t "${IMAGE_FRONTEND}:${IMAGE_TAG}" \
    -t "${IMAGE_FRONTEND}:latest" \
    frontend

  log "Pushing images"
  docker push "${IMAGE_BACKEND}:${IMAGE_TAG}"
  docker push "${IMAGE_FRONTEND}:${IMAGE_TAG}"
  docker push "${IMAGE_BACKEND}:latest"
  docker push "${IMAGE_FRONTEND}:latest"
}

# ---------------------------------------------------------------------------
# Migrations. A Cloud Run JOB, run to completion BEFORE any service revision is
# created, never on API startup: several instances boot at once during a
# rollout and would race each other through the same migration, and Alembic
# takes a lock, so the losers crash-loop.
# ---------------------------------------------------------------------------
run_migrations() {
  log "Migration job ${JOB_MIGRATE}"
  local args=(
    --image="${IMAGE_BACKEND}:${IMAGE_TAG}"
    --region="$REGION"
    --service-account="$RUNTIME_SA"
    --set-cloudsql-instances="$SQL_CONNECTION_NAME"
    # VPC egress, same as every service. `alembic upgrade` itself does not need
    # it -- Cloud SQL arrives through the connector above -- but this job is
    # also how one-off management commands are run against production, and it is
    # handed REDIS_URL like everything else.
    #
    # Without a route to the private range, anything touching Redis does not
    # fail, it HANGS: `celery_app.send_task` sits on a private IP with nowhere
    # to go until Cloud Run kills the task at its 900s ceiling. Observed exactly
    # that while seeding the demo candidates -- the corpus was found, thirty
    # files were listed, and then fifteen minutes of silence and a terminated
    # task, with not one candidate written, because the FIRST enqueue never
    # returned. A job carrying REDIS_URL with no way to reach Redis is a trap
    # laid for whoever runs the next management command.
    --network="$NETWORK"
    --subnet="$SUBNET"
    --vpc-egress=private-ranges-only
    "--set-env-vars=$(build_env)"
    "--set-secrets=${SECRET_FLAG}"
    --args=migrate
    --max-retries=1
    --task-timeout=900s
  )
  if job_exists "$JOB_MIGRATE"; then
    gcloud run jobs update "$JOB_MIGRATE" "${args[@]}" >/dev/null
    info "updated"
  else
    gcloud run jobs create "$JOB_MIGRATE" "${args[@]}" >/dev/null
    info "created"
  fi

  log "Running migrations to completion"
  # --wait blocks until the execution finishes and exits non-zero if it failed,
  # which under set -e aborts the deploy before a single revision is created.
  # A schema the new code needs must exist before that code can serve.
  gcloud run jobs execute "$JOB_MIGRATE" --region="$REGION" --wait
  info "migrations applied"
}

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
# --no-traffic is rejected when a service is being CREATED (there is no prior
# revision to keep serving), so the first-ever deploy of a service necessarily
# goes live. Every subsequent one stages.
traffic_flags() {  # traffic_flags SERVICE
  if [ "$TRAFFIC_MODE" = "live" ]; then
    printf '%s' "--tag=${STAGE_TAG}"
    return
  fi
  if service_exists "$1"; then
    printf '%s' "--no-traffic --tag=${STAGE_TAG}"
  else
    warn "service $1 does not exist yet; the first revision cannot be staged and will serve immediately."
    printf '%s' "--tag=${STAGE_TAG}"
  fi
}

deploy_backend() {  # deploy_backend FRONTEND_URL
  local frontend_url="$1"
  log "Backend service ${SVC_BACKEND}"

  local tflags
  tflags="$(traffic_flags "$SVC_BACKEND")"

  # Public because Razorpay posts webhooks straight to it; the application does
  # its own authentication on every route.
  # shellcheck disable=SC2086 -- tflags must word-split into separate flags
  gcloud run deploy "$SVC_BACKEND" \
    --image="${IMAGE_BACKEND}:${IMAGE_TAG}" \
    --region="$REGION" --platform=managed \
    --service-account="$RUNTIME_SA" \
    --allow-unauthenticated \
    --memory=2Gi --cpu=2 --timeout=300 \
    --min-instances=1 --max-instances=10 --cpu-boost \
    --network="$NETWORK" --subnet="$SUBNET" --vpc-egress=private-ranges-only \
    --add-cloudsql-instances="$SQL_CONNECTION_NAME" \
    "--set-env-vars=$(build_env "FRONTEND_URL=${frontend_url}")" \
    "--set-secrets=${SECRET_FLAG}" \
    --args=api \
    $tflags >/dev/null

  BACKEND_REVISION="$(latest_revision "$SVC_BACKEND")"
  BACKEND_URL="$(service_url "$SVC_BACKEND")"
  BACKEND_STAGED_URL="$(tagged_url "$SVC_BACKEND" "$STAGE_TAG")"
  info "revision ${BACKEND_REVISION}"
  info "staged   ${BACKEND_STAGED_URL}"
}

deploy_frontend() {  # deploy_frontend BACKEND_URL
  local backend_url="$1"
  log "Frontend service ${SVC_FRONTEND}"

  local tflags
  tflags="$(traffic_flags "$SVC_FRONTEND")"

  # BACKEND_INTERNAL_URL is what the same-origin proxy forwards to
  # (frontend/next.config.js rewrites, frontend/lib/api.ts). The browser never
  # sees it, which is what keeps every API call same-origin and the
  # SameSite=Strict auth cookies attached.
  #
  # NEXT_PUBLIC_API_URL is deliberately NOT set here by default. It is a
  # build-time inline, so setting it at deploy time cannot reach the client
  # bundle anyway, and an absolute value would push the browser cross-origin
  # and drop the auth cookies. SET_PUBLIC_API_URL=true is an escape hatch for a
  # deliberate split-origin deployment, and it must be paired with a rebuild.
  local extra=""
  if [ "${SET_PUBLIC_API_URL:-false}" = "true" ]; then
    extra="NEXT_PUBLIC_API_URL=${backend_url}/api/v1"
    warn "SET_PUBLIC_API_URL=true: the frontend will call the backend cross-origin."
  fi

  # shellcheck disable=SC2086
  gcloud run deploy "$SVC_FRONTEND" \
    --image="${IMAGE_FRONTEND}:${IMAGE_TAG}" \
    --region="$REGION" --platform=managed \
    --service-account="$RUNTIME_SA" \
    --allow-unauthenticated \
    --memory=1Gi --cpu=1 \
    --min-instances=1 --max-instances=10 --cpu-boost \
    "--set-env-vars=^@^NODE_ENV=production@BACKEND_INTERNAL_URL=${backend_url}${extra:+@${extra}}" \
    $tflags >/dev/null

  FRONTEND_REVISION="$(latest_revision "$SVC_FRONTEND")"
  FRONTEND_URL="$(service_url "$SVC_FRONTEND")"
  FRONTEND_STAGED_URL="$(tagged_url "$SVC_FRONTEND" "$STAGE_TAG")"
  info "revision ${FRONTEND_REVISION}"
  info "staged   ${FRONTEND_STAGED_URL}"
}

# The backend needs the public frontend origin for links in outbound email and
# for the CORS allowlist. It is resolved BEFORE the backend deploys (a service
# URL is stable for the life of the service), so the normal path is one
# revision per workload. This second pass only fires when the frontend URL
# actually changed, which in practice means the very first deploy, and it
# stages exactly like the first pass so it cannot leak traffic to an unproven
# revision.
reconcile_frontend_url() {
  local expected="$1"
  local actual="$2"
  [ "$expected" = "$actual" ] && { info "FRONTEND_URL already current"; return 0; }

  log "Backend: republishing FRONTEND_URL=${actual}"
  local tflags
  tflags="$(traffic_flags "$SVC_BACKEND")"
  # shellcheck disable=SC2086
  gcloud run services update "$SVC_BACKEND" --region="$REGION" \
    --update-env-vars="FRONTEND_URL=${actual}" \
    $tflags >/dev/null

  BACKEND_REVISION="$(latest_revision "$SVC_BACKEND")"
  BACKEND_STAGED_URL="$(tagged_url "$SVC_BACKEND" "$STAGE_TAG")"
  info "revision ${BACKEND_REVISION}"
}

# ---------------------------------------------------------------------------
# Worker and beat. Neither serves HTTP, so neither takes traffic and neither
# can be staged: whatever is deployed here starts consuming the queue at once.
# That is a real limitation of the staged rollout and it is why migrations are
# additive (claude.md rule: extend, never replace) - a worker on the new image
# must be able to process a task enqueued by the old one.
#
# The workload KIND is detected rather than assumed. This repo's original
# infra/gcp/deploy.sh provisions worker pools; an environment provisioned as
# Cloud Run jobs is equally valid, and guessing wrong would create a duplicate
# workload beside the running one rather than updating it.
# ---------------------------------------------------------------------------
deploy_async_workload() {  # deploy_async_workload NAME ROLE MEMORY CPU
  local name="$1" role="$2" mem="$3" cpu="$4"

  local env_flag secret_flag
  env_flag="$(build_env "FRONTEND_URL=${FRONTEND_URL}")"
  secret_flag="${SECRET_FLAG}"

  if pool_exists "$name"; then
    log "Worker pool ${name} (${role})"
    gcloud run worker-pools deploy "$name" \
      --image="${IMAGE_BACKEND}:${IMAGE_TAG}" \
      --region="$REGION" \
      --service-account="$RUNTIME_SA" \
      --network="$NETWORK" --subnet="$SUBNET" \
      --add-cloudsql-instances="$SQL_CONNECTION_NAME" \
      "--set-env-vars=${env_flag}" \
      "--set-secrets=${secret_flag}" \
      --memory="$mem" --cpu="$cpu" --instances=1 \
      --args="$role" >/dev/null
    info "worker pool updated"
    return
  fi

  log "Cloud Run job ${name} (${role})"
  local args=(
    --image="${IMAGE_BACKEND}:${IMAGE_TAG}"
    --region="$REGION"
    --service-account="$RUNTIME_SA"
    --set-cloudsql-instances="$SQL_CONNECTION_NAME"
    # Memorystore is only reachable over the VPC. Without these the job starts,
    # Celery blocks on the broker, and the only symptom is a queue that never
    # drains.
    --network="$NETWORK" --subnet="$SUBNET"
    "--set-env-vars=${env_flag}"
    "--set-secrets=${secret_flag}"
    --memory="$mem" --cpu="$cpu"
    --args="$role"
    --max-retries=0
    --task-timeout=86400s
  )
  if job_exists "$name"; then
    gcloud run jobs update "$name" "${args[@]}" >/dev/null
    info "job updated"
  else
    gcloud run jobs create "$name" "${args[@]}" >/dev/null
    info "job created"
  fi
}

# ---------------------------------------------------------------------------
# Output for the smoke-test and promote stages
# ---------------------------------------------------------------------------
write_state() {
  {
    echo "IMAGE_TAG=${IMAGE_TAG}"
    echo "STAGE_TAG=${STAGE_TAG}"
    echo "BACKEND_URL=${BACKEND_URL}"
    echo "FRONTEND_URL=${FRONTEND_URL}"
    echo "BACKEND_STAGED_URL=${BACKEND_STAGED_URL}"
    echo "FRONTEND_STAGED_URL=${FRONTEND_STAGED_URL}"
    echo "BACKEND_REVISION=${BACKEND_REVISION}"
    echo "FRONTEND_REVISION=${FRONTEND_REVISION}"
  } > "$DEPLOY_OUT"

  # GitHub Actions step outputs, when running there.
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    cat "$DEPLOY_OUT" >> "$GITHUB_OUTPUT"
  fi

  log "Deployed"
  info "backend  live=${BACKEND_URL}  staged=${BACKEND_STAGED_URL}"
  info "frontend live=${FRONTEND_URL} staged=${FRONTEND_STAGED_URL}"
  info "state written to ${DEPLOY_OUT}"
  if [ "$TRAFFIC_MODE" = "no-traffic" ]; then
    echo
    info "No live traffic has moved. Run scripts/smoke-test.sh against the staged"
    info "URLs, then scripts/promote.sh to shift traffic."
  fi
}

# ---------------------------------------------------------------------------
main() {
  resolve_connection_strings
  build_secret_flag
  build_and_push
  run_migrations

  # Both URLs are resolved up front. A Cloud Run service URL is fixed for the
  # life of the service, so on every deploy after the first this is exact and
  # each workload needs exactly one revision.
  local pre_frontend_url pre_backend_url
  pre_frontend_url="$(service_url "$SVC_FRONTEND" || true)"
  pre_backend_url="$(service_url "$SVC_BACKEND" || true)"

  deploy_backend "${pre_frontend_url:-}"
  deploy_frontend "${BACKEND_URL:-$pre_backend_url}"
  reconcile_frontend_url "${pre_frontend_url:-}" "$FRONTEND_URL"

  deploy_async_workload "$WL_WORKER" worker 2Gi 2
  # Exactly one instance, always. Two beats mean every scheduled task fires twice.
  deploy_async_workload "$WL_BEAT" beat 512Mi 1

  write_state
}

main "$@"
