#!/usr/bin/env bash
# PickReady, Google Cloud Run deployment.
#
# Idempotent: every step checks for the resource before creating it, so a rerun
# after a failure resumes rather than duplicating. Nothing here deletes.
#
#   ./infra/gcp/deploy.sh preflight   # check tools, auth and config only
#   ./infra/gcp/deploy.sh infra       # APIs, registry, Cloud SQL, Redis, secrets
#   ./infra/gcp/deploy.sh images      # build and push backend + frontend
#   ./infra/gcp/deploy.sh services    # migrate, then deploy all four workloads
#   ./infra/gcp/deploy.sh all         # infra, images, services
#
# Read docs/DEPLOY_GCP.md before the first run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-pick-ready-503913}"
REGION="${GCP_REGION:-asia-south1}"
REPO_NAME="${GCP_ARTIFACT_REPO:-pickready}"
KEY_FILE="${GCP_KEY_FILE:-service-account-key.json}"

SQL_INSTANCE="${SQL_INSTANCE:-pickready-postgres}"
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"
SQL_DB="${SQL_DB:-pickready}"
SQL_USER="${SQL_USER:-pickready}"

REDIS_INSTANCE="${REDIS_INSTANCE:-pickready-redis}"
REDIS_SIZE="${REDIS_SIZE:-1}"

NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"

SVC_BACKEND="${SVC_BACKEND:-pickready-backend}"
SVC_FRONTEND="${SVC_FRONTEND:-pickready-frontend}"
POOL_WORKER="${POOL_WORKER:-pickready-worker}"
POOL_BEAT="${POOL_BEAT:-pickready-beat}"
JOB_MIGRATE="${JOB_MIGRATE:-pickready-migrate}"

RUNTIME_SA_NAME="pickready-runtime"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
IMAGE_BACKEND="${REGISTRY}/backend"
IMAGE_FRONTEND="${REGISTRY}/frontend"
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# gcloud shim. Uses a local install when present, otherwise Google's official
# CLI image, so no system-wide install is required. Credentials and the repo are
# mounted into the container; nothing is written outside this directory.
# ---------------------------------------------------------------------------
GCLOUD_CONFIG_DIR="${REPO_ROOT}/.gcloud"

if command -v gcloud >/dev/null 2>&1; then
  gc() { gcloud "$@"; }
else
  mkdir -p "$GCLOUD_CONFIG_DIR"
  gc() {
    docker run --rm -i \
      -v "${REPO_ROOT}:/workspace" \
      -v "${GCLOUD_CONFIG_DIR}:/root/.config/gcloud" \
      -w /workspace \
      gcr.io/google.com/cloudsdktool/google-cloud-cli:stable \
      gcloud "$@"
  }
fi

# ---------------------------------------------------------------------------
# Secrets. Read from .env by NAME, piped straight into Secret Manager without
# ever being echoed. Deliberately excludes DATABASE_URL and REDIS_URL, which are
# rebuilt below from the managed instances.
# ---------------------------------------------------------------------------
SECRET_KEYS=(
  JWT_SECRET LLM_KEY_ENCRYPTION_SECRET FIREBASE_SERVICE_ACCOUNT_JSON
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM_EMAIL SMTP_FROM_NAME
  RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET RAZORPAY_WEBHOOK_SECRET
  CLOUDINARY_URL TAVILY_API_KEY MSG91_API_KEY MSG91_SENDER_ID BGE_M3_ENDPOINT
  GROQ_API_KEY_1 GROQ_API_KEY_2 GROQ_API_KEY_3
  GROQ_API_KEY_4 GROQ_API_KEY_5 GROQ_API_KEY_6 GROQ_API_KEY_7
  GEMINI_API_KEY_1 GEMINI_API_KEY_2 GEMINI_API_KEY_3
  GEMINI_API_KEY_4 GEMINI_API_KEY_5 GEMINI_API_KEY_6 GEMINI_API_KEY_7
  OPENROUTER_API_KEY_1 OPENROUTER_API_KEY_2 OPENROUTER_API_KEY_3
  OPENROUTER_API_KEY_4 OPENROUTER_API_KEY_5 OPENROUTER_API_KEY_6 OPENROUTER_API_KEY_7
)

env_value() {  # env_value KEY  -> value from .env, empty if absent
  [ -f .env ] || return 0
  sed -n "s/^${1}=//p" .env | head -1
}

# ---------------------------------------------------------------------------
preflight() {
  log "Preflight"
  command -v docker >/dev/null 2>&1 || die "docker is required"
  [ -f "$KEY_FILE" ] || die "$KEY_FILE not found. See docs/DEPLOY_GCP.md."
  [ -f .env ] || die ".env not found; secrets are read from it."
  [ -f frontend/.env.local ] || die "frontend/.env.local not found (Firebase web config)."

  log "Authenticating as the service account"
  gc auth activate-service-account --key-file="/workspace/${KEY_FILE}" >/dev/null
  gc config set project "$PROJECT_ID" >/dev/null
  gc config set run/region "$REGION" >/dev/null

  gc projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null \
    || die "cannot read project $PROJECT_ID with this key"

  echo "project=$PROJECT_ID region=$REGION tag=$TAG"
  log "Preflight OK"
}

enable_apis() {
  log "Enabling APIs (idempotent)"
  gc services enable \
    run.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com \
    redis.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com \
    compute.googleapis.com vpcaccess.googleapis.com servicenetworking.googleapis.com
}

runtime_service_account() {
  log "Runtime service account"
  if ! gc iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1; then
    gc iam service-accounts create "$RUNTIME_SA_NAME" \
      --display-name="PickReady Cloud Run runtime"
  fi
  # Least privilege for the RUNNING containers: read secrets, reach Cloud SQL.
  # Deployment rights stay with the deployer key, not with the workloads.
  for role in roles/secretmanager.secretAccessor roles/cloudsql.client; do
    gc projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${RUNTIME_SA}" --role="$role" \
      --condition=None >/dev/null
  done
}

artifact_registry() {
  log "Artifact Registry"
  if ! gc artifacts repositories describe "$REPO_NAME" --location="$REGION" >/dev/null 2>&1; then
    gc artifacts repositories create "$REPO_NAME" \
      --repository-format=docker --location="$REGION" \
      --description="PickReady images"
  fi
  # Docker on the HOST pushes the images, so the host daemon needs the
  # credential helper even though gcloud itself may be containerised.
  if command -v gcloud >/dev/null 2>&1; then
    gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
  else
    warn "No local gcloud: log the host docker daemon in with an access token."
    gc auth print-access-token \
      | docker login -u oauth2accesstoken --password-stdin "https://${REGION}-docker.pkg.dev"
  fi
}

cloud_sql() {
  log "Cloud SQL (Postgres + pgvector)"
  if ! gc sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
    # POINT-IN-TIME RECOVERY, not --enable-bin-log: that flag is MySQL-only and
    # is rejected outright on a Postgres instance.
    gc sql instances create "$SQL_INSTANCE" \
      --database-version=POSTGRES_16 \
      --tier="$SQL_TIER" \
      --region="$REGION" \
      --storage-auto-increase \
      --enable-point-in-time-recovery \
      --backup-start-time=02:00
  else
    echo "instance $SQL_INSTANCE already exists"
  fi

  gc sql databases describe "$SQL_DB" --instance="$SQL_INSTANCE" >/dev/null 2>&1 \
    || gc sql databases create "$SQL_DB" --instance="$SQL_INSTANCE"

  local pw
  pw="$(env_value POSTGRES_PASSWORD)"
  [ -n "$pw" ] || die "POSTGRES_PASSWORD must be set in .env before creating the SQL user."
  if ! gc sql users list --instance="$SQL_INSTANCE" --format='value(name)' | grep -qx "$SQL_USER"; then
    gc sql users create "$SQL_USER" --instance="$SQL_INSTANCE" --password="$pw"
  fi

  # pgvector and pg_trgm are created by alembic 0001_initial, which the migrate
  # job runs, so there is no manual extension step. Cloud SQL for Postgres 16
  # ships both, but the `vector` extension has to be allow-listed on some older
  # instance versions; if the migrate job fails on CREATE EXTENSION, that is why.
  echo "pgvector/pg_trgm are created by the migrate job (alembic 0001_initial)."
}

memorystore() {
  log "Memorystore (Redis)"
  if ! gc redis instances describe "$REDIS_INSTANCE" --region="$REGION" >/dev/null 2>&1; then
    gc redis instances create "$REDIS_INSTANCE" \
      --size="$REDIS_SIZE" --region="$REGION" \
      --redis-version=redis_7_0 --network="$NETWORK"
  else
    echo "instance $REDIS_INSTANCE already exists"
  fi
}

push_secrets() {
  log "Secret Manager"
  local key value
  for key in "${SECRET_KEYS[@]}"; do
    value="$(env_value "$key")"
    if [ -z "$value" ]; then
      echo "  skip  $key (absent from .env)"
      continue
    fi
    if gc secrets describe "$key" >/dev/null 2>&1; then
      printf '%s' "$value" | gc secrets versions add "$key" --data-file=- >/dev/null
      echo "  update $key"
    else
      printf '%s' "$value" | gc secrets create "$key" --data-file=- --replication-policy=automatic >/dev/null
      echo "  create $key"
    fi
  done
}

build_images() {
  log "Building images for linux/amd64"
  docker build --platform linux/amd64 \
    -t "${IMAGE_BACKEND}:${TAG}" -t "${IMAGE_BACKEND}:latest" backend

  # Firebase web config is inlined into the bundle at BUILD time (see
  # frontend/Dockerfile), so it must be supplied here, not at deploy time.
  set -a; . ./frontend/.env.local; set +a
  docker build --platform linux/amd64 \
    --build-arg NEXT_PUBLIC_FIREBASE_API_KEY="${NEXT_PUBLIC_FIREBASE_API_KEY:-}" \
    --build-arg NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN="${NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN:-}" \
    --build-arg NEXT_PUBLIC_FIREBASE_PROJECT_ID="${NEXT_PUBLIC_FIREBASE_PROJECT_ID:-}" \
    --build-arg NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET="${NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET:-}" \
    --build-arg NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID="${NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID:-}" \
    --build-arg NEXT_PUBLIC_FIREBASE_APP_ID="${NEXT_PUBLIC_FIREBASE_APP_ID:-}" \
    -t "${IMAGE_FRONTEND}:${TAG}" -t "${IMAGE_FRONTEND}:latest" frontend

  log "Pushing"
  docker push "${IMAGE_BACKEND}:${TAG}"
  docker push "${IMAGE_FRONTEND}:${TAG}"
}

# Connection string for the Cloud SQL UNIX SOCKET. A host:port DSN cannot work
# here: --add-cloudsql-instances mounts a socket at /cloudsql/<CONNECTION_NAME>,
# and the app's engine is asyncpg, so the driver must be named explicitly.
db_url() {
  local conn pw
  conn="$(gc sql instances describe "$SQL_INSTANCE" --format='value(connectionName)' | tr -d '\r')"
  pw="$(env_value POSTGRES_PASSWORD)"
  printf 'postgresql+asyncpg://%s:%s@/%s?host=/cloudsql/%s' "$SQL_USER" "$pw" "$SQL_DB" "$conn"
}

redis_url() {
  local host port
  host="$(gc redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(host)' | tr -d '\r')"
  port="$(gc redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(port)' | tr -d '\r')"
  printf 'redis://%s:%s/0' "$host" "$port"
}

sql_conn() { gc sql instances describe "$SQL_INSTANCE" --format='value(connectionName)' | tr -d '\r'; }

# Every secret in .env becomes ENV_NAME=SECRET_NAME:latest for --set-secrets.
secret_flags() {
  local key out=""
  for key in "${SECRET_KEYS[@]}"; do
    [ -n "$(env_value "$key")" ] || continue
    out="${out}${out:+,}${key}=${key}:latest"
  done
  printf '%s' "$out"
}

deploy_services() {
  local DB REDIS CONN SECRETS
  DB="$(db_url)"; REDIS="$(redis_url)"; CONN="$(sql_conn)"; SECRETS="$(secret_flags)"

  log "Migration job"
  local job_args=(
    --image="${IMAGE_BACKEND}:${TAG}"
    --region="$REGION"
    --service-account="$RUNTIME_SA"
    --set-cloudsql-instances="$CONN"
    --set-env-vars="ENVIRONMENT=production,DATABASE_URL=${DB},REDIS_URL=${REDIS}"
    --set-secrets="$SECRETS"
    --args=migrate
    --max-retries=1
    --task-timeout=900s
  )
  if gc run jobs describe "$JOB_MIGRATE" --region="$REGION" >/dev/null 2>&1; then
    gc run jobs update "$JOB_MIGRATE" "${job_args[@]}"
  else
    gc run jobs create "$JOB_MIGRATE" "${job_args[@]}"
  fi
  log "Running migrations"
  gc run jobs execute "$JOB_MIGRATE" --region="$REGION" --wait

  log "Backend service"
  # Public because Razorpay posts webhooks straight to it; the application does
  # its own authentication on every route.
  gc run deploy "$SVC_BACKEND" \
    --image="${IMAGE_BACKEND}:${TAG}" \
    --region="$REGION" --platform=managed \
    --service-account="$RUNTIME_SA" \
    --allow-unauthenticated \
    --memory=2Gi --cpu=2 --timeout=300 \
    --min-instances=0 --max-instances=10 \
    --network="$NETWORK" --subnet="$SUBNET" --vpc-egress=private-ranges-only \
    --add-cloudsql-instances="$CONN" \
    --set-env-vars="ENVIRONMENT=production,DATABASE_URL=${DB},REDIS_URL=${REDIS}" \
    --set-secrets="$SECRETS" \
    --args=api

  local BACKEND_URL
  BACKEND_URL="$(gc run services describe "$SVC_BACKEND" --region="$REGION" --format='value(status.url)' | tr -d '\r')"

  log "Frontend service"
  # BACKEND_INTERNAL_URL is what the same-origin proxy forwards to
  # (frontend/app/api/[...path]/route.ts). The browser never sees it.
  gc run deploy "$SVC_FRONTEND" \
    --image="${IMAGE_FRONTEND}:${TAG}" \
    --region="$REGION" --platform=managed \
    --service-account="$RUNTIME_SA" \
    --allow-unauthenticated \
    --memory=1Gi --cpu=1 \
    --min-instances=0 --max-instances=10 \
    --set-env-vars="BACKEND_INTERNAL_URL=${BACKEND_URL},NODE_ENV=production"

  local FRONTEND_URL
  FRONTEND_URL="$(gc run services describe "$SVC_FRONTEND" --region="$REGION" --format='value(status.url)' | tr -d '\r')"

  # The backend needs the public origin for links in outbound email and for the
  # CORS allowlist. Only knowable after the frontend exists, hence a second pass.
  log "Backend: publishing FRONTEND_URL=${FRONTEND_URL}"
  gc run services update "$SVC_BACKEND" --region="$REGION" \
    --update-env-vars="FRONTEND_URL=${FRONTEND_URL}"

  log "Worker pool"
  # Worker pools, NOT services: a Celery worker serves no HTTP, so a Cloud Run
  # service would never pass its startup probe and every revision would roll back.
  local pool_common=(
    --image="${IMAGE_BACKEND}:${TAG}"
    --region="$REGION"
    --service-account="$RUNTIME_SA"
    --network="$NETWORK" --subnet="$SUBNET"
    --add-cloudsql-instances="$CONN"
    --set-env-vars="ENVIRONMENT=production,DATABASE_URL=${DB},REDIS_URL=${REDIS},FRONTEND_URL=${FRONTEND_URL}"
    --set-secrets="$SECRETS"
  )
  gc run worker-pools deploy "$POOL_WORKER" "${pool_common[@]}" \
    --memory=2Gi --cpu=2 --instances=1 --args=worker

  log "Beat worker pool"
  # Exactly one instance, always. Two beats mean every scheduled task fires twice.
  gc run worker-pools deploy "$POOL_BEAT" "${pool_common[@]}" \
    --memory=512Mi --cpu=1 --instances=1 --args=beat

  log "Deployed"
  echo "  Frontend : $FRONTEND_URL"
  echo "  Backend  : $BACKEND_URL"
  echo
  echo "Post-deploy, both are manual and required:"
  echo "  1. Add ${FRONTEND_URL#https://} to Firebase Console > Authentication > Settings > Authorised domains."
  echo "  2. Point the Razorpay webhook at ${BACKEND_URL}/api/v1/billing/webhook."
}

case "${1:-all}" in
  preflight) preflight ;;
  infra)     preflight; enable_apis; runtime_service_account; artifact_registry; cloud_sql; memorystore; push_secrets ;;
  images)    preflight; artifact_registry; build_images ;;
  services)  preflight; deploy_services ;;
  all)       preflight; enable_apis; runtime_service_account; artifact_registry; cloud_sql; memorystore; push_secrets; build_images; deploy_services ;;
  *)         die "unknown step '${1}'. Use: preflight | infra | images | services | all" ;;
esac
