#!/usr/bin/env bash
# PickReady, post-deploy smoke test.
#
# Probes a STAGED revision (the tag URL from scripts/deploy.sh) before any live
# traffic reaches it. A non-200 from any endpoint fails the script, which is
# what stops scripts/promote.sh from ever running: the point of the staged
# rollout is that a broken revision is discovered while it is serving nobody.
#
#   BACKEND_STAGED_URL=https://staged-abc123---pickready-backend-....run.app \
#   TEST_BEARER_TOKEN=eyJ... ./scripts/smoke-test.sh
#
# Pure bash. Only curl is required.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read the deploy state file when the caller did not pass URLs explicitly. This
# is what makes `deploy.sh && smoke-test.sh` work with no glue.
DEPLOY_OUT="${DEPLOY_OUT:-${REPO_ROOT}/.deploy-state.env}"
if [ -f "$DEPLOY_OUT" ]; then
  # shellcheck disable=SC1090
  set -a; . "$DEPLOY_OUT"; set +a
fi

# The URL under test. Positional argument wins, then the staged URL, then the
# live URL as a last resort for a manual check against production.
TARGET="${1:-${BACKEND_STAGED_URL:-${BACKEND_URL:-}}}"
TARGET="${TARGET%/}"

# Cold start on a scale-to-zero revision routinely exceeds a default curl
# connect budget, and a first-request timeout is not a broken build. Health is
# retried; everything after it is not, because by then the instance is warm.
HEALTH_RETRIES="${HEALTH_RETRIES:-12}"
HEALTH_RETRY_DELAY="${HEALTH_RETRY_DELAY:-5}"
CURL_TIMEOUT="${CURL_TIMEOUT:-45}"

# Optional: probe the staged frontend too. Skipped silently when unset.
FRONTEND_TARGET="${FRONTEND_STAGED_URL:-}"
FRONTEND_TARGET="${FRONTEND_TARGET%/}"

# The liveness probe. NOTE the path: the health route is mounted on the app
# root (backend/app/main.py), NOT under the /api/v1 prefix, so /api/v1/health
# is a 404 and would fail every deploy.
HEALTH_PATH="${HEALTH_PATH:-/health}"

# Authenticated endpoints, probed with TEST_BEARER_TOKEN. The capabilities
# endpoint is /api/v1/auth/me: it returns {user, capabilities[]} and there is
# no /api/v1/me/capabilities route in this codebase.
AUTHED_PATHS_DEFAULT="/api/v1/dashboard/summary /api/v1/jobs /api/v1/auth/me"
read -r -a AUTHED_PATHS <<< "${SMOKE_AUTHED_PATHS:-$AUTHED_PATHS_DEFAULT}"

pass() { printf '  \033[1;32mPASS\033[0m  %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m  %s\n' "$*"; }
log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required."

[ -n "$TARGET" ] || die "no target URL. Pass one as \$1, or set BACKEND_STAGED_URL, or run scripts/deploy.sh first."
[ -n "${TEST_BEARER_TOKEN:-}" ] || die "TEST_BEARER_TOKEN is required. CI mints one per run via scripts/mint-smoke-token.py; to run this by hand: TEST_BEARER_TOKEN=\$(JWT_SECRET=\$(gcloud secrets versions access latest --secret=JWT_SECRET) python3 scripts/mint-smoke-token.py)"

log "Smoke testing ${TARGET}"

FAILURES=0
BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

# probe PATH [AUTH]  -> echoes the status code, writes the body to $BODY_FILE
probe() {
  local path="$1" auth="${2:-}"
  local args=(
    --silent --show-error --location
    --max-time "$CURL_TIMEOUT"
    --output "$BODY_FILE"
    --write-out '%{http_code}'
    --header 'Accept: application/json'
  )
  # The token is passed via -H from a shell variable and never appears in the
  # URL: a query-string token lands in every access log between here and the
  # container.
  [ -n "$auth" ] && args+=( --header "Authorization: Bearer ${auth}" )
  # No `|| echo 000` fallback: curl writes %{http_code} unconditionally, and it
  # writes "000" itself on a connect failure with NO trailing newline, so a
  # fallback echo concatenates into "000000" and every comparison below reads
  # as a mysterious non-200. The ${out:-000} default covers curl producing
  # nothing at all.
  local out
  out="$(curl "${args[@]}" "${TARGET}${path}" 2>/dev/null || true)"
  out="$(printf '%s' "$out" | tr -cd '0-9')"
  printf '%s' "${out:-000}"
}

# ---------------------------------------------------------------------------
log "Liveness"
code=""
attempt=1
while [ "$attempt" -le "$HEALTH_RETRIES" ]; do
  code="$(probe "$HEALTH_PATH")"
  if [ "$code" = "200" ]; then
    pass "${HEALTH_PATH} 200"
    break
  fi
  printf '  ....  %s attempt %s/%s -> %s\n' "$HEALTH_PATH" "$attempt" "$HEALTH_RETRIES" "$code"
  attempt=$((attempt + 1))
  [ "$attempt" -le "$HEALTH_RETRIES" ] && sleep "$HEALTH_RETRY_DELAY"
done

if [ "$code" != "200" ]; then
  fail "${HEALTH_PATH} never became healthy (last status ${code})"
  head -c 500 "$BODY_FILE" >&2 || true
  echo >&2
  # No point probing authenticated routes against a revision that is not up.
  die "revision is not serving; refusing to continue."
fi

# ---------------------------------------------------------------------------
log "Authenticated endpoints"
for path in "${AUTHED_PATHS[@]}"; do
  code="$(probe "$path" "$TEST_BEARER_TOKEN")"
  if [ "$code" = "200" ]; then
    pass "${path} 200"
  else
    fail "${path} returned ${code}"
    # CI mints this token seconds before calling us (scripts/mint-smoke-token.py),
    # so "it expired" is no longer the likely explanation and pointing there
    # sends the reader somewhere already ruled out. A 401 now means the signing
    # secret and the verifying secret disagree, or the identity in the token no
    # longer resolves; a 403 means the audience is wrong for the route.
    if [ "$code" = "401" ] || [ "$code" = "403" ]; then
      printf '        (token is minted per-run; a 401 points at JWT_SECRET drift\n'
      printf '         or SMOKE_USER_ID/SMOKE_TENANT_ID no longer existing)\n'
    fi
    head -c 500 "$BODY_FILE" || true
    echo
    FAILURES=$((FAILURES + 1))
  fi
done

# ---------------------------------------------------------------------------
# Contract check. A 200 that does not carry the capabilities array means the
# token authenticated but the permission resolution returned nothing, which
# renders an empty portal rather than an error page and would otherwise
# promote cleanly.
log "Response shape"
code="$(probe "/api/v1/auth/me" "$TEST_BEARER_TOKEN")"
if [ "$code" = "200" ] && grep -q '"capabilities"' "$BODY_FILE"; then
  pass "/api/v1/auth/me carries a capabilities array"
else
  fail "/api/v1/auth/me did not return a capabilities array (status ${code})"
  FAILURES=$((FAILURES + 1))
fi

# ---------------------------------------------------------------------------
# ROUTE CONTRACT. Proves the revision under test is carrying THIS release, not
# merely answering HTTP.
#
# This is the check that would have caught 2026-08-04, when every deploy was
# green and three reported features did not work: a green run means the service
# is up, and every probe above passes identically against the previous image.
# `/openapi.json` is what the router actually registered, so an absent route is
# absent and a present one is present -- no fixture, no job id, no auth.
log "Route contract"
code="$(probe "/openapi.json")"
if [ "$code" != "200" ]; then
  fail "/openapi.json returned ${code}; cannot verify the route contract"
  FAILURES=$((FAILURES + 1))
else
  # WITHDRAWN 2026-08-06: the Company Portal's preset technical question bank.
  # A company can no longer create, edit, store or assign technical questions;
  # they are written per candidate during the assessment. If these are still
  # registered, the old image is serving.
  for gone in \
    '/api/v2/assessments/jobs/{job_id}/questions' \
    '/api/v2/assessments/jobs/{job_id}/finalize'
  do
    if grep -qF "\"${gone}\"" "$BODY_FILE"; then
      fail "${gone} is still registered; this revision predates the 2026-08-06 release"
      FAILURES=$((FAILURES + 1))
    else
      pass "${gone} is gone"
    fi
  done

  # ADDED in the same release. Asserted POSITIVELY as well, because "the old
  # routes are absent" is also true of an image where the whole router failed
  # to load.
  for present in \
    '/api/v2/assessments/transcripts/links/{link_id}' \
    '/api/v2/assessments/reports/links/{link_id}/pdf'
  do
    if grep -qF "\"${present}\"" "$BODY_FILE"; then
      pass "${present} is registered"
    else
      fail "${present} is missing; the assessment router did not load this release"
      FAILURES=$((FAILURES + 1))
    fi
  done
fi

# ---------------------------------------------------------------------------
if [ -n "$FRONTEND_TARGET" ]; then
  log "Frontend ${FRONTEND_TARGET}"
  code="$(curl --silent --show-error --location --max-time "$CURL_TIMEOUT" \
            --output "$BODY_FILE" --write-out '%{http_code}' \
            "${FRONTEND_TARGET}/" 2>/dev/null || echo "000")"
  if [ "$code" = "200" ]; then
    pass "/ 200"
  else
    fail "/ returned ${code}"
    FAILURES=$((FAILURES + 1))
  fi
fi

# ---------------------------------------------------------------------------
echo
if [ "$FAILURES" -gt 0 ]; then
  die "${FAILURES} smoke test(s) failed. Traffic will NOT be promoted."
fi
printf '\033[1;32m==> All smoke tests passed. Revision is safe to promote.\033[0m\n'
