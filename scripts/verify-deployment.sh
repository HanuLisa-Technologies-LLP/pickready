#!/usr/bin/env bash
#
# Confirm what is ACTUALLY RUNNING, by image digest.
#
#   ./scripts/verify-deployment.sh staging
#
# WHY THIS EXISTS
# ---------------
# spec-doc5 §D.2 says the Cloud Run revision-verification tooling needs
# "AWS-equivalent replacements, not deletion-without-replacement, since that
# verification discipline (confirm the deployed artifact by digest, not by
# trusting the pipeline's exit code) is worth keeping". This is that
# replacement.
#
# The discipline came out of a real release. On 2026-08-04 every deploy was
# green, every revision was promoted, production was serving the newest commit,
# and three reported features did not work. `claude.md` records the conclusion:
# "'The pipeline passed' is not evidence that anything works. A green run means
# the service answers HTTP."
#
# So this asks a different question from the pipeline's exit code. Terraform
# returning zero proves Terraform finished. This proves the bytes serving
# traffic are the bytes that were built and tested, which is a fact about the
# world rather than about the tool.
#
# WHAT IT COMPARES, AND WHY IT IS THE RUNNING TASK RATHER THAN THE SERVICE
# -------------------------------------------------------------------------
# A service's task definition says what SHOULD be running. A running task says
# what IS. The gap between them is a deployment that is still rolling, or one
# whose new tasks failed their health check and were rolled back by the circuit
# breaker -- and in the second case the service definition points at the new
# image while every task is still the old one. Asking the service would report
# success for exactly the failure this script exists to catch.
#
# So: describe the RUNNING TASKS, read each container's `imageDigest`, and
# require every one of them to match.
set -euo pipefail

ENVIRONMENT="${1:-staging}"
PROJECT="${PROJECT:-readypick}"
CLUSTER="${PROJECT}-${ENVIRONMENT}"
REGION="${AWS_REGION:-ap-south-1}"

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI is not on PATH." >&2
  exit 127
fi

fail=0

# Map service -> the digest it must be running. Set by CI from the build job's
# outputs; a service with no expected digest is REPORTED AND SKIPPED rather
# than silently passing, because "we did not check" and "we checked and it was
# fine" must not look the same.
declare -A EXPECTED=(
  ["api"]="${EXPECTED_BACKEND_DIGEST:-}"
  ["worker"]="${EXPECTED_BACKEND_DIGEST:-}"
  ["beat"]="${EXPECTED_BACKEND_DIGEST:-}"
  ["frontend"]="${EXPECTED_FRONTEND_DIGEST:-}"
  ["analysis"]="${EXPECTED_ANALYSIS_DIGEST:-}"
)

echo "Verifying ${CLUSTER} in ${REGION} by image digest."
echo

for service in api worker beat frontend analysis; do
  expected="${EXPECTED[$service]}"
  full_name="${CLUSTER}-${service}"

  if [ -z "$expected" ]; then
    echo "  ${service}: SKIPPED, no expected digest supplied"
    echo "    A skipped check is not a passed check. Set EXPECTED_BACKEND_DIGEST,"
    echo "    EXPECTED_FRONTEND_DIGEST and EXPECTED_ANALYSIS_DIGEST, or this"
    echo "    proves nothing about ${service}."
    fail=1
    continue
  fi

  task_arns="$(aws ecs list-tasks \
    --cluster "$CLUSTER" \
    --service-name "$full_name" \
    --desired-status RUNNING \
    --region "$REGION" \
    --query 'taskArns' --output text 2>/dev/null || true)"

  if [ -z "$task_arns" ] || [ "$task_arns" = "None" ]; then
    echo "  ${service}: NO RUNNING TASKS"
    fail=1
    continue
  fi

  # shellcheck disable=SC2086
  digests="$(aws ecs describe-tasks \
    --cluster "$CLUSTER" \
    --tasks $task_arns \
    --region "$REGION" \
    --query 'tasks[].containers[].imageDigest' --output text)"

  count=0
  mismatched=0
  for digest in $digests; do
    count=$((count + 1))
    if [ "$digest" != "$expected" ]; then
      mismatched=$((mismatched + 1))
      echo "  ${service}: MISMATCH on one task"
      echo "    running:  ${digest}"
      echo "    expected: ${expected}"
    fi
  done

  if [ "$mismatched" -gt 0 ]; then
    # A PARTIAL MATCH IS A FAILURE, not a rolling deploy to wait out. By the
    # time this runs the apply has returned, so a task still on the old image
    # is one the new deployment did not replace -- which is what a rolled-back
    # circuit breaker looks like from the outside.
    echo "  ${service}: ${mismatched} of ${count} running task(s) are NOT the built image."
    fail=1
  else
    echo "  ${service}: ${count} task(s), all running ${expected}"
  fi
done

echo
if [ "$fail" -ne 0 ]; then
  cat <<'NOTE'
VERIFICATION FAILED.

Do not describe this as deployed. A green pipeline means the tooling finished;
it does not mean the running tasks are the image that was tested. That
distinction is the whole reason this script exists -- see the header.
NOTE
  exit 1
fi

echo "Every running task is the image this build produced."
