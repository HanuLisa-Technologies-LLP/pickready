#!/usr/bin/env bash
# PickReady, complete CI/CD bootstrap.
#
# One command that does everything the terminal can do to make the "Deploy to
# Cloud Run" workflow (.github/workflows/deploy.yml) runnable, then hands you the
# exact values to paste into GitHub and the exact links to paste them at.
#
#   GITHUB_REPO=owner/name bash scripts/complete-cicd-setup.sh
#
# What it does:
#   1. Verifies prerequisites (gcloud auth, GITHUB_REPO, Docker, backend up).
#   2. Ensures Workload Identity Federation exists (idempotent; delegates the
#      creation to scripts/setup-wif-once.sh, which every guard already skips
#      on a rerun). Skipped entirely when the pool, provider and deploy SA are
#      already present.
#   3. Mints a real TEST_BEARER_TOKEN by calling create_access_token INSIDE the
#      running backend container, so the JWT is signed with the same secret the
#      deployed API verifies against.
#   4. Writes .github-secrets-setup.txt with the ACTUAL values, and prints the
#      copy-paste steps with the repo links filled in.
#
# It writes nothing to GitHub: the GitHub CLI is not assumed present and secret
# writes are a decision a human should make deliberately. Everything it cannot
# do from the terminal is spelled out at the end.
set -euo pipefail

# ---------------------------------------------------------------------------
# Locate the repo regardless of where this is invoked from.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/infra/docker-compose.yml"
OUTPUT_FILE="${REPO_ROOT}/.github-secrets-setup.txt"

# ---------------------------------------------------------------------------
# Config. Defaults match .github/workflows/deploy.yml and setup-wif-once.sh, so
# the three stay in lockstep; override via env if any of them ever diverges.
# ---------------------------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-pick-ready-503913}"
REGION="${GCP_REGION:-asia-south1}"
GITHUB_REPO="${GITHUB_REPO:-}"

POOL_ID="${WIF_POOL_ID:-github-pool}"
PROVIDER_ID="${WIF_PROVIDER_ID:-github-provider}"
DEPLOY_SA_NAME="${DEPLOY_SA_NAME:-github-deployer}"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# The test HR-manager identity the smoke test authenticates as. These match the
# seed data (app.scripts.seed_dev_data); hr_manager is an org role, so the token
# carries the default AUDIENCE_ORG and needs no audience override.
TEST_USER_ID="20000000-0000-4000-8000-000000000002"
TEST_ROLE="hr_manager"
TEST_TENANT_ID="10000000-0000-4000-8000-000000000001"

BACKEND_SERVICE="backend"

# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_RED=$'\033[1;31m'
  C_YELLOW=$'\033[1;33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_BLUE=; C_GREEN=; C_RED=; C_YELLOW=; C_DIM=; C_OFF=
fi
log()  { printf '\n%s==> %s%s\n' "$C_BLUE" "$*" "$C_OFF"; }
ok()   { printf '    %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    %s! %s%s\n' "$C_YELLOW" "$*" "$C_OFF"; }
die()  { printf '\n%sERROR: %s%s\n' "$C_RED" "$*" "$C_OFF" >&2; exit 1; }

# docker compose (v2) vs docker-compose (v1)
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  DC=()
fi

# ===========================================================================
# 1. Prerequisites
# ===========================================================================
log "1/5  Verifying prerequisites"

command -v gcloud >/dev/null 2>&1 \
  || die "gcloud is not installed. Install the Google Cloud CLI, then run 'gcloud auth login'."

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | tr -d '\r')"
[ -n "$ACTIVE_ACCOUNT" ] \
  || die "gcloud has no active account. Run:  gcloud auth login"
ok "gcloud authenticated as ${ACTIVE_ACCOUNT}"

[ -n "$GITHUB_REPO" ] \
  || die "GITHUB_REPO is required, in owner/name form. e.g.  GITHUB_REPO=manjuchro/pickready bash scripts/complete-cicd-setup.sh"
case "$GITHUB_REPO" in
  */*) : ;;
  *)   die "GITHUB_REPO must be owner/name, got '${GITHUB_REPO}'." ;;
esac
ok "GitHub repo: ${GITHUB_REPO}"

command -v docker >/dev/null 2>&1 \
  || die "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 \
  || die "the Docker daemon is not running. Start Docker Desktop and try again."
ok "Docker daemon is running"

[ ${#DC[@]} -gt 0 ] \
  || die "neither 'docker compose' nor 'docker-compose' is available."
[ -f "$COMPOSE_FILE" ] \
  || die "compose file not found at ${COMPOSE_FILE}."

# Find the running backend container through compose so we do not hardcode the
# generated container name (project prefix can vary).
BACKEND_CID="$("${DC[@]}" -f "$COMPOSE_FILE" ps -q "$BACKEND_SERVICE" 2>/dev/null | head -n1 | tr -d '\r')"
[ -n "$BACKEND_CID" ] \
  || die "the '${BACKEND_SERVICE}' container is not running. Start the stack first:
       docker compose -f infra/docker-compose.yml up -d ${BACKEND_SERVICE}"

BACKEND_RUNNING="$(docker inspect -f '{{.State.Running}}' "$BACKEND_CID" 2>/dev/null | tr -d '\r')"
[ "$BACKEND_RUNNING" = "true" ] \
  || die "the '${BACKEND_SERVICE}' container (${BACKEND_CID}) exists but is not running. Start it:
       docker compose -f infra/docker-compose.yml up -d ${BACKEND_SERVICE}"
ok "backend container is up (${BACKEND_CID:0:12})"

# ===========================================================================
# 2. Workload Identity Federation
# ===========================================================================
log "2/5  Workload Identity Federation"

gcloud config set project "$PROJECT_ID" >/dev/null 2>&1 || true
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)' 2>/dev/null | tr -d '\r')"
[ -n "$PROJECT_NUMBER" ] \
  || die "cannot read project ${PROJECT_ID}. Is the active account authorized on it?  (gcloud projects describe ${PROJECT_ID})"
info "project ${PROJECT_ID} (number ${PROJECT_NUMBER})"

pool_exists() {
  gcloud iam workload-identity-pools describe "$POOL_ID" --location=global >/dev/null 2>&1
}
provider_exists() {
  gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
    --location=global --workload-identity-pool="$POOL_ID" >/dev/null 2>&1
}
deploy_sa_exists() {
  gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1
}

if pool_exists && provider_exists && deploy_sa_exists; then
  ok "pool, provider and deploy service account already exist, skipping creation"
else
  warn "one or more WIF resources are missing, running scripts/setup-wif-once.sh"
  SETUP_WIF="${SCRIPT_DIR}/setup-wif-once.sh"
  [ -f "$SETUP_WIF" ] || die "expected ${SETUP_WIF} to exist, cannot create WIF resources."
  # setup-wif-once.sh is itself idempotent and fails loudly; -e propagates its
  # exit so a WIF failure stops the whole bootstrap here rather than producing a
  # half-configured pipeline.
  GCP_PROJECT_ID="$PROJECT_ID" GCP_REGION="$REGION" GITHUB_REPO="$GITHUB_REPO" \
    WIF_POOL_ID="$POOL_ID" WIF_PROVIDER_ID="$PROVIDER_ID" DEPLOY_SA_NAME="$DEPLOY_SA_NAME" \
    bash "$SETUP_WIF" \
    || die "WIF setup failed. Fix the error above (commonly: the runtime service account does not exist yet) and rerun."
  ok "WIF setup completed"
fi

WIF_PROVIDER_PATH="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
info "provider: ${WIF_PROVIDER_PATH}"
info "deploy SA: ${DEPLOY_SA}"

# ===========================================================================
# 3. Mint the test bearer token inside the backend container
# ===========================================================================
log "3/5  Minting TEST_BEARER_TOKEN in the backend container"

MINT_PY="from app.core.security import create_access_token
print(create_access_token('${TEST_USER_ID}', '${TEST_ROLE}', '${TEST_TENANT_ID}'))"

TOKEN_ERR="$(mktemp)"
trap 'rm -f "$TOKEN_ERR"' EXIT
if ! TEST_BEARER_TOKEN="$(docker exec "$BACKEND_CID" python -c "$MINT_PY" 2>"$TOKEN_ERR" | tr -d '\r\n')"; then
  printf '%s\n' "$C_RED"; cat "$TOKEN_ERR" >&2; printf '%s' "$C_OFF"
  die "token minting failed. The backend container returned the error above."
fi
if [ -z "$TEST_BEARER_TOKEN" ]; then
  cat "$TOKEN_ERR" >&2
  die "token minting produced no output. See the container error above (if any)."
fi
# A JWT is three base64url segments separated by dots; a bare word means the
# call printed something other than a token.
case "$TEST_BEARER_TOKEN" in
  *.*.*) : ;;
  *) printf '%sunexpected output:%s %s\n' "$C_YELLOW" "$C_OFF" "$TEST_BEARER_TOKEN" >&2
     die "the container did not return a JWT. Check that create_access_token still lives in app.core.security." ;;
esac
ok "token minted for ${TEST_ROLE} (${TEST_BEARER_TOKEN:0:16}...)"

# ===========================================================================
# 4. Write the copy-paste file with real values
# ===========================================================================
log "4/5  Writing ${OUTPUT_FILE##*/}"

SECRETS_URL="https://github.com/${GITHUB_REPO}/settings/secrets/actions/new"
SECRETS_LIST_URL="https://github.com/${GITHUB_REPO}/settings/secrets/actions"
ENV_NEW_URL="https://github.com/${GITHUB_REPO}/settings/environments/new"
ACTIONS_URL="https://github.com/${GITHUB_REPO}/actions"

cat > "$OUTPUT_FILE" <<EOF
======== GITHUB SECRETS TO ADD ========

Add each of these at:
  ${SECRETS_URL}

Secret 1:
Name: GCP_WIF_PROVIDER
Value: ${WIF_PROVIDER_PATH}

Secret 2:
Name: GCP_DEPLOY_SA
Value: ${DEPLOY_SA}

Secret 3:
Name: TEST_BEARER_TOKEN
Value: ${TEST_BEARER_TOKEN}

======== GITHUB ENVIRONMENT SETUP ========

1. Go to: ${ENV_NEW_URL}
2. Environment name: production
3. Check "Required reviewers"
4. Add yourself as a required reviewer
5. Save

======== FINAL STEP ========

Re-run (or start) the deploy workflow:
  ${ACTIONS_URL}

The bearer token above is short-lived and signed with the LOCAL JWT secret.
For the deployed API it only needs to be valid at smoke-test time; regenerate it
by rerunning this script if a run reports a 401 from the token.
EOF
ok "wrote ${OUTPUT_FILE}"
info "(gitignored: it contains a live bearer token)"

# ===========================================================================
# 5. Summary
# ===========================================================================
printf '\n%s========================================================%s\n' "$C_GREEN" "$C_OFF"
printf '%s CI/CD bootstrap complete. 3 manual web steps remain.%s\n' "$C_GREEN" "$C_OFF"
printf '%s========================================================%s\n' "$C_GREEN" "$C_OFF"

cat <<EOF

${C_BLUE}STEP 1 - Add three repository secrets${C_OFF}
  Open: ${SECRETS_URL}
  (list view: ${SECRETS_LIST_URL})

    GCP_WIF_PROVIDER
      ${WIF_PROVIDER_PATH}

    GCP_DEPLOY_SA
      ${DEPLOY_SA}

    TEST_BEARER_TOKEN
      ${TEST_BEARER_TOKEN}

${C_BLUE}STEP 2 - Create the "production" environment with a required reviewer${C_OFF}
  Open: ${ENV_NEW_URL}
    - Environment name: production
    - Check "Required reviewers", add yourself, Save
  Without a reviewer the promote job shifts live traffic the instant the
  smoke tests pass, with no approval gate.

${C_BLUE}STEP 3 - Run the workflow${C_OFF}
  Open: ${ACTIONS_URL}
    - Push to main, or use "Run workflow" on "Deploy to Cloud Run".

All three values are also saved to:
  ${OUTPUT_FILE}
${C_DIM}(gitignored, contains a live bearer token)${C_OFF}

EOF
