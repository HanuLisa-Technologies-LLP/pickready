#!/usr/bin/env bash
# PickReady, one-time Workload Identity Federation setup.
#
# Run this ONCE, locally, as a project owner. It creates the deploy service
# account that GitHub Actions impersonates, and the WIF pool/provider that lets
# GitHub prove who it is WITHOUT a downloaded JSON key. A key file in a repo
# secret never expires, never rotates and is exfiltrable by any workflow that
# can read secrets; a WIF token lives for minutes and is scoped to one
# repository by the attribute condition set below.
#
#   GITHUB_REPO=owner/name ./scripts/setup-wif-once.sh
#
# Idempotent: every create is guarded by a describe, so a rerun after a failure
# resumes rather than erroring. Nothing here deletes.
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-pick-ready-503913}"
REGION="${GCP_REGION:-asia-south1}"

# owner/name of the GitHub repository allowed to impersonate the deployer.
# REQUIRED: there is no safe default, and a wrong value here would either lock
# the pipeline out or let another repository deploy to this project.
GITHUB_REPO="${GITHUB_REPO:-}"

POOL_ID="${WIF_POOL_ID:-github-pool}"
PROVIDER_ID="${WIF_PROVIDER_ID:-github-provider}"

DEPLOY_SA_NAME="${DEPLOY_SA_NAME:-github-deployer}"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# The account the CONTAINERS run as. The deployer is granted actAs on it so it
# can say --service-account=... at deploy time; it is deliberately a different
# identity, so a compromised runtime cannot deploy and a compromised deployer
# does not automatically hold the runtime's data access.
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-pickready-runtime}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 \
  || die "gcloud is required. Install the Google Cloud CLI and run 'gcloud auth login'."

[ -n "$GITHUB_REPO" ] \
  || die "GITHUB_REPO is required, in owner/name form. e.g. GITHUB_REPO=hanulisa/pickready $0"

case "$GITHUB_REPO" in
  */*) : ;;
  *)   die "GITHUB_REPO must be owner/name, got '${GITHUB_REPO}'." ;;
esac

GITHUB_OWNER="${GITHUB_REPO%%/*}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)' | tr -d '\r')"
[ -n "$PROJECT_NUMBER" ] || die "cannot read project ${PROJECT_ID}. Are you authenticated?"

log "Project ${PROJECT_ID} (number ${PROJECT_NUMBER}), repo ${GITHUB_REPO}"

# ---------------------------------------------------------------------------
# APIs. sts + iamcredentials are the two the federation itself runs on: without
# them the pool can be created and every token exchange still fails at runtime,
# which reads as an authentication bug rather than a missing API.
# ---------------------------------------------------------------------------
log "Enabling APIs"
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com

# ---------------------------------------------------------------------------
# Deploy service account
# ---------------------------------------------------------------------------
log "Deploy service account: ${DEPLOY_SA}"
if gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1; then
  info "already exists"
else
  gcloud iam service-accounts create "$DEPLOY_SA_NAME" \
    --display-name="PickReady GitHub Actions deployer" \
    --description="Impersonated by GitHub Actions via Workload Identity Federation. No key is ever downloaded."
  info "created"
fi

log "Project roles for the deployer"
# run.admin              deploy services, worker pools and jobs, shift traffic
# artifactregistry.writer push images
# cloudsql.client        describe + attach the Cloud SQL instance to a revision
# redis.viewer           describe the Memorystore instance for host:port, which
#                        deploy.sh resolves into REDIS_URL at deploy time
# secretmanager.viewer   LIST the secret names that become --set-secrets
# secretmanager.secretAccessor  read a secret VALUE (POSTGRES_PASSWORD) to
#                        compose DATABASE_URL. viewer lists, accessor reads;
#                        neither implies the other and deploy.sh needs BOTH
#                        (omitting viewer fails at `gcloud secrets list`).
for role in \
  roles/run.admin \
  roles/artifactregistry.writer \
  roles/cloudsql.client \
  roles/redis.viewer \
  roles/secretmanager.viewer \
  roles/secretmanager.secretAccessor
do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="$role" \
    --condition=None >/dev/null
  info "granted ${role}"
done

# serviceAccountUser is bound on the RUNTIME SA, not on the project. Bound at
# project level it would let the deployer impersonate every service account in
# the project, including any future one with broader rights; bound here it can
# act as exactly the one identity the workloads run as.
log "actAs on the runtime service account"
if gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/iam.serviceAccountUser" \
    --condition=None >/dev/null
  info "granted roles/iam.serviceAccountUser on ${RUNTIME_SA}"
else
  # Falling back to the project scope would silently widen the grant, so this
  # stops instead and names the fix.
  die "runtime service account ${RUNTIME_SA} does not exist. Create it first (infra/gcp/deploy.sh infra), then rerun."
fi

# ---------------------------------------------------------------------------
# Workload Identity pool + provider
# ---------------------------------------------------------------------------
log "Workload Identity pool: ${POOL_ID}"
if gcloud iam workload-identity-pools describe "$POOL_ID" --location=global >/dev/null 2>&1; then
  info "already exists"
else
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --location=global \
    --display-name="GitHub Actions" \
    --description="Federated identities for GitHub Actions workflows"
  info "created"
fi

log "OIDC provider: ${PROVIDER_ID}"
# The attribute-condition is the security boundary and is NOT optional: without
# it, ANY GitHub repository on the public runner fleet can mint a token this
# pool accepts, and only the service-account binding below stands between them
# and this project. gcloud refuses to create a github.com provider without one.
ATTR_MAPPING="google.subject=assertion.sub"
ATTR_MAPPING="${ATTR_MAPPING},attribute.actor=assertion.actor"
ATTR_MAPPING="${ATTR_MAPPING},attribute.repository=assertion.repository"
ATTR_MAPPING="${ATTR_MAPPING},attribute.repository_owner=assertion.repository_owner"
ATTR_MAPPING="${ATTR_MAPPING},attribute.ref=assertion.ref"

if gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
     --location=global --workload-identity-pool="$POOL_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER_ID" \
    --location=global \
    --workload-identity-pool="$POOL_ID" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$ATTR_MAPPING" \
    --attribute-condition="assertion.repository_owner == '${GITHUB_OWNER}'" >/dev/null
  info "updated"
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --location=global \
    --workload-identity-pool="$POOL_ID" \
    --display-name="GitHub Actions OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$ATTR_MAPPING" \
    --attribute-condition="assertion.repository_owner == '${GITHUB_OWNER}'"
  info "created"
fi

POOL_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"
PROVIDER_PATH="${POOL_NAME}/providers/${PROVIDER_ID}"

# ---------------------------------------------------------------------------
# Bind the repository to the deploy service account
# ---------------------------------------------------------------------------
log "Binding ${GITHUB_REPO} to ${DEPLOY_SA}"
# principalSet on attribute.repository, NOT on the pool as a whole: the pool
# accepts every repo under the owner (that is what the condition allows), and
# this binding narrows impersonation to the one repository.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPO}" \
  --condition=None >/dev/null
info "granted roles/iam.workloadIdentityUser"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32m==> WIF setup complete\033[0m')

Add these to the GitHub repository, Settings > Secrets and variables > Actions:

  Secret  GCP_WIF_PROVIDER
          ${PROVIDER_PATH}

  Secret  GCP_DEPLOY_SA
          ${DEPLOY_SA}

  Secret  TEST_BEARER_TOKEN
          A valid PickReady access JWT for a test hiring-manager account.
          See SETUP_INSTRUCTIONS.md for how to mint one.

Then create the GitHub Environment named "production" with at least one
required reviewer: that environment is the approval gate the promote job waits
on, and without it traffic would shift the moment the smoke tests pass.

Verify the binding took effect (it can take up to a minute to propagate):

  gcloud iam service-accounts get-iam-policy ${DEPLOY_SA}

EOF
