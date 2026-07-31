#!/usr/bin/env bash
# PickReady, traffic promotion.
#
# Shifts 100% of live traffic to the revision that scripts/smoke-test.sh just
# proved. Run ONLY after the smoke tests pass and a human has approved the
# GitHub "production" environment: this is the single step in the pipeline that
# real users can see.
#
#   BACKEND_REVISION=pickready-backend-00042-abc \
#   FRONTEND_REVISION=pickready-frontend-00019-xyz ./scripts/promote.sh
#
# Idempotent: promoting an already-live revision is a no-op that still exits 0.
# Nothing here deletes a revision; the previous one stays available for
# scripts/promote.sh ROLLBACK.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEPLOY_OUT="${DEPLOY_OUT:-${REPO_ROOT}/.deploy-state.env}"
if [ -f "$DEPLOY_OUT" ]; then
  # shellcheck disable=SC1090
  set -a; . "$DEPLOY_OUT"; set +a
fi

PROJECT_ID="${GCP_PROJECT_ID:-pick-ready-503913}"
REGION="${GCP_REGION:-asia-south1}"
SVC_BACKEND="${SVC_BACKEND:-pickready-backend}"
SVC_FRONTEND="${SVC_FRONTEND:-pickready-frontend}"
STAGE_TAG="${STAGE_TAG:-}"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 || die "gcloud is required."

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null

serving_revision() {
  gcloud run services describe "$1" --region="$REGION" \
    --format='value(status.traffic.revisionName)' 2>/dev/null | tr -d '\r' | tr ';' ' '
}

# promote SERVICE REVISION
#
# Promotes by REVISION NAME rather than --to-latest wherever one is known. They
# are not the same thing: --to-latest resolves at execution time, so a
# concurrent deploy landing between the smoke test and the approval would put
# an unproven revision live under a green check mark. Naming the revision makes
# the approval mean exactly what the reviewer read.
promote() {
  local svc="$1" rev="${2:-}"

  gcloud run services describe "$svc" --region="$REGION" >/dev/null 2>&1 \
    || die "service ${svc} does not exist in ${REGION}."

  log "Promoting ${svc}"
  info "currently serving: $(serving_revision "$svc")"

  if [ -n "$rev" ]; then
    gcloud run services update-traffic "$svc" \
      --region="$REGION" \
      --to-revisions="${rev}=100" >/dev/null
    info "traffic -> ${rev} (100%)"
  else
    warn "no revision name for ${svc}; falling back to the latest ready revision."
    gcloud run services update-traffic "$svc" \
      --region="$REGION" \
      --to-latest >/dev/null
    info "traffic -> latest (100%)"
  fi

  # Retire the staging tag once its revision is the live one. Tagged revisions
  # are pinned and accumulate against the per-service revision limit, and a
  # stale staged-<sha> URL left reachable is an unauthenticated door into an
  # old build.
  if [ -n "$STAGE_TAG" ]; then
    if gcloud run services update-traffic "$svc" \
         --region="$REGION" --remove-tags="$STAGE_TAG" >/dev/null 2>&1; then
      info "removed tag ${STAGE_TAG}"
    else
      warn "could not remove tag ${STAGE_TAG} from ${svc} (it may not exist)."
    fi
  fi

  info "now serving: $(serving_revision "$svc")"
}

# ---------------------------------------------------------------------------
# Rollback. Same machinery, opposite direction: name a revision and it serves.
#   ./scripts/promote.sh rollback pickready-backend-00041-prev
# ---------------------------------------------------------------------------
if [ "${1:-}" = "rollback" ]; then
  target_rev="${2:-}"
  [ -n "$target_rev" ] || die "usage: $0 rollback <revision-name> [service]"
  target_svc="${3:-$SVC_BACKEND}"
  log "ROLLBACK ${target_svc} -> ${target_rev}"
  gcloud run services update-traffic "$target_svc" \
    --region="$REGION" --to-revisions="${target_rev}=100" >/dev/null
  info "now serving: $(serving_revision "$target_svc")"
  exit 0
fi

log "PickReady traffic promotion"
info "project  ${PROJECT_ID}"
info "region   ${REGION}"
info "backend  revision ${BACKEND_REVISION:-<latest>}"
info "frontend revision ${FRONTEND_REVISION:-<latest>}"

# Backend first. The frontend proxies every API call to it, so a frontend
# serving new code against an old backend is the ordering that breaks; the
# reverse is what the additive-migration rule already guarantees is safe.
promote "$SVC_BACKEND" "${BACKEND_REVISION:-}"
promote "$SVC_FRONTEND" "${FRONTEND_REVISION:-}"

echo
printf '\033[1;32m==> Promotion complete. Both services are serving the new revision.\033[0m\n'
echo
info "Rollback, if needed:"
info "  ./scripts/promote.sh rollback <previous-revision> ${SVC_BACKEND}"
info "  ./scripts/promote.sh rollback <previous-revision> ${SVC_FRONTEND}"
info "List revisions:"
info "  gcloud run revisions list --service=${SVC_BACKEND} --region=${REGION}"
