#!/usr/bin/env bash
#
# Point the Lambda functions at a new image, and WAIT until they are actually
# running it.
#
#   ./scripts/update-lambda-code.sh pilot <backend-image-uri>
#
# WHY THIS IS A SCRIPT AND NOT A TERRAFORM ARGUMENT
# --------------------------------------------------
# `modules/lambda` sets `ignore_changes = [image_uri, filename,
# source_code_hash]`, the same arrangement the ECS services use: Terraform owns
# the SHAPE and CI owns the CODE, so a pipeline that deploys and a Terraform
# that reverts do not fight on every apply.
#
# WHY IT WAITS, AND WHY WAITING IS NOT ENOUGH ON ITS OWN
# -------------------------------------------------------
# `update-function-code` returns as soon as the request is accepted. The
# function is then in `LastUpdateStatus: InProgress`, and an invocation during
# that window runs the OLD code or fails outright with
# ResourceConflictException. A deploy step that returned here would report
# success for a function still serving the previous release.
#
# So this waits for `Successful`, and then it VERIFIES: it reads back the image
# the function is configured with and compares it to what was asked for. This
# platform has been burned by the difference before, on the other side of the
# same question -- "every deploy was green, every revision was promoted, and
# production was serving the newest commit while three features did not work".
# A wait proves the update finished; only the read-back proves what it finished
# with.
#
# THE ZIP FUNCTION IS DELIBERATELY NOT TOUCHED HERE.
# `readypick-assessment-trigger` is packaged by Terraform from
# `lambda/assessment_trigger/`, because it is thirty lines and boto3 and has no
# build step. Terraform's `archive_file` hashes the source, so an edit to that
# handler is deployed by `terraform apply` and not by this script. Two things
# deploying one function is how a function ends up running neither version.
set -euo pipefail

ENVIRONMENT="${1:-}"
IMAGE_URI="${2:-}"
PROJECT="${PROJECT:-readypick}"
# THE REGION IS RESOLVED, NEVER ASSUMED.
#
# This script defaulted to a hardcoded region, and on 2026-09-09 that cost a
# release step: the pilot lives in ap-south-2, the default said ap-south-1, and
# the failure surfaced as "TaskDefinition not found" -- which reads as a broken
# deploy rather than as a lookup in an empty region. spec-doc6 D5 removes the
# assumption by name: "Region assumption `ap-south-1` is removed as an
# assumption and becomes a required variable. Do not hardcode it anywhere."
#
# So the order is AWS_REGION, then AWS_DEFAULT_REGION, then whatever the CLI
# itself is configured with -- and then a REFUSAL naming the variable. A
# default here is a silent answer to a question only the operator can answer,
# and the wrong answer looks identical to an outage.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
if [ -z "$REGION" ]; then
  echo "No AWS region. Set AWS_REGION (this project's pilot is ap-south-2)." >&2
  exit 2
fi
TIMEOUT_SECONDS="${LAMBDA_UPDATE_TIMEOUT:-300}"

usage() {
  echo "usage: $0 <environment> <backend-image-uri>" >&2
  echo "  e.g. $0 pilot 016617990245.dkr.ecr.ap-south-2.amazonaws.com/readypick-pilot/backend:sha-abc123def456" >&2
  exit 2
}

[ -n "$ENVIRONMENT" ] || usage
[ -n "$IMAGE_URI" ] || usage
command -v aws >/dev/null 2>&1 || { echo "aws CLI is required." >&2; exit 127; }

# The three functions that run the BACKEND IMAGE. Named rather than discovered,
# because discovery would silently include a function added later whose code
# does not come from this image, and pointing that at the backend image would
# break it in a way whose cause is one directory away from where it shows up.
FUNCTIONS=(
  "${PROJECT}-task-worker"
  "${PROJECT}-jd-gen"
  "${PROJECT}-company-profile"
)

failures=0

for fn in "${FUNCTIONS[@]}"; do
  echo "==> ${fn}"

  if ! aws lambda update-function-code \
      --function-name "$fn" \
      --image-uri "$IMAGE_URI" \
      --region "$REGION" \
      --publish \
      --output text --query 'FunctionArn' >/dev/null; then
    echo "    update-function-code FAILED" >&2
    failures=$((failures + 1))
    continue
  fi

  # Accepted is not applied. See the header.
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  status=""
  while :; do
    status="$(aws lambda get-function-configuration \
      --function-name "$fn" --region "$REGION" \
      --query 'LastUpdateStatus' --output text)"
    [ "$status" = "Successful" ] && break
    if [ "$status" = "Failed" ]; then
      reason="$(aws lambda get-function-configuration \
        --function-name "$fn" --region "$REGION" \
        --query 'LastUpdateStatusReason' --output text)"
      echo "    update FAILED: ${reason}" >&2
      break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "    still ${status} after ${TIMEOUT_SECONDS}s. NOT treating this as success." >&2
      break
    fi
    sleep 5
  done

  if [ "$status" != "Successful" ]; then
    failures=$((failures + 1))
    continue
  fi

  # THE READ-BACK. A wait proves the update finished; this proves what it
  # finished with.
  running="$(aws lambda get-function \
    --function-name "$fn" --region "$REGION" \
    --query 'Code.ImageUri' --output text)"
  if [ "$running" != "$IMAGE_URI" ]; then
    echo "    MISMATCH: configured with ${running}, expected ${IMAGE_URI}" >&2
    failures=$((failures + 1))
    continue
  fi

  echo "    running ${running}"
done

if [ "$failures" -gt 0 ]; then
  echo >&2
  echo "${failures} function(s) are NOT running the requested image." >&2
  exit 1
fi

echo
echo "All ${#FUNCTIONS[@]} image-backed functions are running ${IMAGE_URI}."
echo "readypick-assessment-trigger is deployed by terraform apply, not by this"
echo "script: it is packaged from lambda/assessment_trigger/ by archive_file."
