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

# A 403 AND A 404 ARE DIFFERENT FACTS, AND THIS SCRIPT USED TO REPORT BOTH AS
# "the environment does not exist".
#
# `2>/dev/null || true` threw away the only thing that distinguished them, so
# any error produced an empty string and one message. That mattered, because a
# 403 was the case that actually happened: reading
# `/repos/{owner}/{repo}/environments/{name}` needs the `administration: read`
# scope and the job granted the workflow default of `contents: read`. Every run
# of this check reported a missing environment, correctly failed the build, and
# sent whoever read it to create an environment that already existed -- while
# never once looking at a reviewer configuration.
#
# It failed CLOSED, so nothing unsafe was deployed. A check that is right for
# the wrong reason is still not a check: the day somebody creates the
# environment and the message does not change, the next move is to delete the
# check.
error_output="$(mktemp)"
trap 'rm -f "$error_output"' EXIT

set +e
response="$(gh api "repos/${REPO}/environments/${ENVIRONMENT_NAME}" 2>"$error_output")"
api_status=$?
set -e

if [ "$api_status" -ne 0 ] || [ -z "$response" ]; then
  if grep -q "HTTP 403\|Resource not accessible" "$error_output"; then
    cat <<NOTE >&2

FAIL: this job cannot READ the '${ENVIRONMENT_NAME}' environment (HTTP 403).

Nothing is known about the gate either way. That is a failure and not a pass:
"we could not check" and "it is configured" are the two answers this script
exists to keep apart.

DO NOT "FIX" THIS BY ADDING `administration: read` TO THE JOB'S permissions
BLOCK. That is what an earlier version of this very message advised, somebody
followed it on 2026-09-17, and it broke every workflow run in the repository
for five days: `administration` is not one of the scopes `permissions:`
accepts, an unknown key there fails the workflow file at validation before any
job starts, and the pull_request trigger stops firing with it.

The scope is unobtainable from GITHUB_TOKEN by design. GET
/repos/{owner}/{repo}/environments/{name} requires administration:read, and an
Actions token cannot be granted it under any configuration.

So this check needs a PAT. Create a fine-grained token with Read access to
repository Administration, store it as the APPROVAL_GATE_TOKEN secret, and this
job passes it as GH_TOKEN.

$(cat "$error_output")
NOTE
    exit 1
  fi

  if grep -q "HTTP 404\|Not Found" "$error_output" || [ -z "$response" ]; then
    cat <<NOTE >&2

FAIL: the '${ENVIRONMENT_NAME}' environment does not exist (HTTP 404).

A job declaring \`environment: ${ENVIRONMENT_NAME}\` against a non-existent
environment runs WITHOUT a gate. The workflow file reads as gated and is not,
which is precisely the GCP-phase finding spec-doc5 §D.5 names.

Create it under Settings > Environments and add a required reviewer.
NOTE
    exit 1
  fi

  cat <<NOTE >&2

FAIL: could not read the '${ENVIRONMENT_NAME}' environment, and the reason is
neither a 403 nor a 404. Reported verbatim rather than guessed at:

$(cat "$error_output")
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
