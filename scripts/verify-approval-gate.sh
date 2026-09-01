#!/usr/bin/env bash
#
# The gate that checks the gate.
#
#   ./scripts/verify-approval-gate.sh
#
# WHY A SCRIPT AND NOT A LINE IN A RUNBOOK
# -----------------------------------------
# spec-doc5 §D.5:
#
#   "The earlier GCP-phase finding -- that an environment with no required
#    reviewer auto-promotes to production -- is exactly what the human-approval
#    gate above exists to prevent. Configure the production environment's
#    required reviewer explicitly; do not assume a default."
#
# And the §D acceptance list: "The pipeline stops at the human-approval gate
# before production apply -- confirmed by the environment's required-reviewer
# configuration, not by convention."
#
# The failure mode is specific and quiet. A workflow job with
# `environment: production` LOOKS gated in the YAML. If that environment has no
# protection rule, the job simply runs -- no warning, no log line, nothing in
# the run summary distinguishing "approved" from "there was nobody to ask". A
# reviewer reading the workflow file sees a gate; the pipeline does not have
# one. Every artifact a person would consult agrees with the belief, and the
# belief is wrong.
#
# So the gate's existence is a CHECK on every run rather than a setup step
# somebody did once. That is the only way "we have an approval gate" becomes a
# fact instead of a memory.
set -euo pipefail

ENVIRONMENT_NAME="${1:-production}"
REPO="${GITHUB_REPOSITORY:-}"

if [ -z "$REPO" ]; then
  echo "GITHUB_REPOSITORY is unset. This runs inside Actions." >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is not on PATH." >&2
  exit 127
fi

echo "Checking that the '${ENVIRONMENT_NAME}' environment has a required reviewer."

response="$(gh api "repos/${REPO}/environments/${ENVIRONMENT_NAME}" 2>/dev/null || true)"

if [ -z "$response" ]; then
  cat <<NOTE >&2

FAIL: the '${ENVIRONMENT_NAME}' environment does not exist.

A job declaring \`environment: ${ENVIRONMENT_NAME}\` against a non-existent
environment runs WITHOUT a gate. The workflow file reads as gated and is not,
which is precisely the GCP-phase finding spec-doc5 §D.5 names.

Create it under Settings > Environments and add a required reviewer.
NOTE
  exit 1
fi

reviewer_count="$(printf '%s' "$response" \
  | grep -o '"type"[[:space:]]*:[[:space:]]*"required_reviewers"' | wc -l | tr -d ' ')"

if [ "$reviewer_count" -eq 0 ]; then
  cat <<NOTE >&2

FAIL: '${ENVIRONMENT_NAME}' exists but has NO required reviewer.

This is the exact configuration the finding describes: the job declares the
environment, the workflow reads as gated, and the deploy promotes instantly and
silently because there is nobody to ask.

Settings > Environments > ${ENVIRONMENT_NAME} > Required reviewers.
NOTE
  exit 1
fi

# `prevent_self_review` is the other half, and its absence is a weaker but real
# problem: an approval gate an author can satisfy themselves is a speed bump.
# Reported as a WARNING rather than a failure, because it is not what the spec
# asks for and turning it into a hard failure would be this script inventing a
# requirement.
if printf '%s' "$response" | grep -q '"prevent_self_review"[[:space:]]*:[[:space:]]*false'; then
  echo
  echo "  WARNING: self-review is permitted on '${ENVIRONMENT_NAME}'."
  echo "  An approval gate the author can satisfy themselves is a speed bump."
  echo "  Not a failure here -- spec-doc5 asks for a required reviewer, and there is one."
fi

echo "OK: '${ENVIRONMENT_NAME}' has a required reviewer. The production apply cannot start without one."
