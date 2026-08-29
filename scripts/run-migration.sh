#!/usr/bin/env bash
#
# Run Alembic as a one-shot ECS task, and wait for it to actually finish.
#
#   ./scripts/run-migration.sh staging
#
# A ONE-SHOT TASK, NOT A STEP INSIDE THE SERVICE
# ------------------------------------------------
# Running migrations from the API container's entrypoint is the common shortcut
# and it has two failure modes this avoids. With more than one task, N replicas
# race on the same migration; and a migration failure becomes a crash-looping
# service rather than a failed deploy step, which reads as an application
# problem to whoever is paged.
#
# So this runs the backend image with `alembic upgrade head` as its command,
# under the `migrate` task role -- which holds ONE secret, the DSN, and nothing
# else. A migration job that could read the model credential is a migration job
# with more reach than its work needs.
#
# WAITING IS THE WHOLE SCRIPT
# ----------------------------
# `aws ecs run-task` returns as soon as the task is ACCEPTED. Treating that as
# success is exactly the shape of the failure this project has already had: a
# management job that "found 30 files then died at the 900s ceiling having
# written nothing", reported by a pipeline that had moved on. So this waits for
# `tasks-stopped`, reads the container's exit code, and fails on anything but
# zero.
#
# A NON-ZERO EXIT MUST STOP THE PIPELINE. A migration that half-applied and
# then failed leaves a schema no code version matches, and the next thing that
# happens must not be a deploy on top of it.
set -euo pipefail

ENVIRONMENT="${1:-staging}"
PROJECT="${PROJECT:-readypick}"
CLUSTER="${PROJECT}-${ENVIRONMENT}"
REGION="${AWS_REGION:-ap-south-1}"
TASK_FAMILY="${CLUSTER}-migrate"
TIMEOUT_SECONDS="${MIGRATION_TIMEOUT:-900}"

command -v aws >/dev/null 2>&1 || { echo "aws CLI is required." >&2; exit 127; }
command -v terraform >/dev/null 2>&1 || { echo "terraform is required." >&2; exit 127; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ROOT}/infra/environments/${ENVIRONMENT}"
[ -d "$ENV_DIR" ] || { echo "No such environment: ${ENVIRONMENT}" >&2; exit 2; }

# The private subnets and the ECS security group, read from the state that
# created them rather than hardcoded. A hardcoded subnet id is the thing that
# silently keeps working against a VPC that was replaced.
pushd "$ENV_DIR" >/dev/null
SUBNETS="$(terraform output -json private_subnet_ids 2>/dev/null | tr -d '[]" ' || true)"
SECURITY_GROUP="$(terraform output -raw ecs_security_group_id 2>/dev/null || true)"
popd >/dev/null

if [ -z "$SUBNETS" ] || [ -z "$SECURITY_GROUP" ]; then
  echo "Could not read the network from ${ENVIRONMENT}'s Terraform outputs." >&2
  echo "Run terraform apply for that environment first." >&2
  exit 2
fi

echo "Starting ${TASK_FAMILY} on ${CLUSTER}."

TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_FAMILY" \
  --launch-type FARGATE \
  --region "$REGION" \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SECURITY_GROUP}],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)"

if [ -z "$TASK_ARN" ] || [ "$TASK_ARN" = "None" ]; then
  echo "run-task returned no task ARN. The task was not started." >&2
  exit 1
fi

echo "Task ${TASK_ARN##*/} accepted. Waiting for it to STOP."
echo "  (Accepted is not finished. See the header: a job that was accepted and"
echo "   then died is exactly what a pipeline reports as success.)"

# `ecs wait tasks-stopped` polls for up to 100 attempts at 6s = 10 minutes. A
# longer migration needs the explicit loop below rather than a second wait,
# because a second wait would restart the attempt budget and hide how long this
# has actually been running.
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  status="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
    --region "$REGION" --query 'tasks[0].lastStatus' --output text)"
  [ "$status" = "STOPPED" ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "Migration did not finish within ${TIMEOUT_SECONDS}s. Last status: ${status}." >&2
    echo "NOT treating this as success. The schema may be half-applied." >&2
    exit 1
  fi
  sleep 6
done

EXIT_CODE="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --region "$REGION" --query 'tasks[0].containers[0].exitCode' --output text)"
STOP_REASON="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --region "$REGION" --query 'tasks[0].stoppedReason' --output text)"

echo
echo "Logs: /ecs/${CLUSTER}/migrate  (stream prefix migrate/${TASK_ARN##*/})"

if [ "$EXIT_CODE" != "0" ]; then
  echo "MIGRATION FAILED. exit=${EXIT_CODE} reason=${STOP_REASON}" >&2
  echo "Do not deploy on top of this. A half-applied migration leaves a schema" >&2
  echo "no code version matches." >&2
  exit 1
fi

echo "Migration completed. exit=0"
