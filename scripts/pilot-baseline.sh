#!/usr/bin/env bash
#
# Read the AI programme's baseline out of a real environment's database.
#
#   ./scripts/pilot-baseline.sh pilot
#
# WHY THIS EXISTS
# ---------------
# RPN-AI-UP-001 W0 requires `docs/verification/AI_UPGRADE_BASELINE.md` to carry
# live row counts "from the pilot database, not from a local seed", and its
# Definition of Done asks the same question again later ("SELECT count(*) FROM
# context_chunks in production is non zero, and grows when a resume is parsed").
# The data subnets have no route to the internet in either direction, which is
# the correct design and also means there is no psql from a laptop. Before this
# script the only thing that had ever run inside that boundary was
# `alembic upgrade head`.
#
# THE SAME SHAPE AS run-migration.sh, AND FOR THE SAME REASONS
# -------------------------------------------------------------
# It runs the backend image as a one-shot task on the `migrate` task
# definition, whose task role holds ONE secret, the DSN. A probe that could
# read the model credential would have more reach than its work needs.
#
# And it WAITS. `aws ecs run-task` returns as soon as the task is ACCEPTED, and
# treating that as success is the failure this project has already had: a
# management job that "found 30 files then died at the 900s ceiling having
# written nothing", reported by a pipeline that had moved on.
#
# IT IS A READ, NOT A WRITE, AND THE EXIT CODES DIFFER FOR THAT REASON.
# `run-migration.sh` must stop a pipeline on a non-zero exit, because a
# half-applied migration leaves a schema no code version matches. This one
# reports. A probe whose table does not exist yet is a row of the report.
#
# WHAT IT PRINTS
# --------------
# The container's own stdout, which is the JSON block `app.scripts.ai_baseline`
# emits between AI_BASELINE_JSON_START and AI_BASELINE_JSON_END. Integer counts
# and version strings. No row content and no candidate data crosses this
# boundary, so the output is safe to paste into a checked-in document, which is
# precisely what W0 asks for.
set -euo pipefail

ENVIRONMENT="${1:-pilot}"
PROJECT="${PROJECT:-readypick}"
CLUSTER="${PROJECT}-${ENVIRONMENT}"

# THE REGION IS RESOLVED, NEVER ASSUMED. A hardcoded default here cost a
# release step on 2026-09-09: the pilot lives in ap-south-2, a default said
# ap-south-1, and the failure surfaced as "TaskDefinition not found", which
# reads as a broken deploy rather than as a lookup in an empty region.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
if [ -z "$REGION" ]; then
  echo "No AWS region. Set AWS_REGION (this project's pilot is ap-south-2)." >&2
  exit 2
fi
TASK_FAMILY="${CLUSTER}-migrate"
TIMEOUT_SECONDS="${BASELINE_TIMEOUT:-600}"

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
  exit 2
fi

OVERRIDES='{"containerOverrides":[{"name":"migrate","command":["python","-m","app.scripts.ai_baseline"]}]}'

echo "Starting ${TASK_FAMILY} on ${CLUSTER} with the baseline probe."

TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_FAMILY" \
  --launch-type FARGATE \
  --region "$REGION" \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SECURITY_GROUP}],assignPublicIp=DISABLED}" \
  --overrides "$OVERRIDES" \
  --query 'tasks[0].taskArn' --output text)"

if [ -z "$TASK_ARN" ] || [ "$TASK_ARN" = "None" ]; then
  echo "run-task returned no task ARN. The probe was not started." >&2
  exit 1
fi

TASK_ID="${TASK_ARN##*/}"
echo "Task ${TASK_ID} accepted. Waiting for it to STOP."

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while :; do
  status="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
    --region "$REGION" --query 'tasks[0].lastStatus' --output text)"
  [ "$status" = "STOPPED" ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "Probe did not finish within ${TIMEOUT_SECONDS}s. Last status: ${status}." >&2
    exit 1
  fi
  sleep 6
done

EXIT_CODE="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --region "$REGION" --query 'tasks[0].containers[0].exitCode' --output text)"

# The log stream only exists once the container has written; a task that failed
# to start has none, and reporting "no output" is more honest than retrying.
LOG_GROUP="/ecs/${CLUSTER}/migrate"
STREAM="migrate/migrate/${TASK_ID}"
echo
aws logs get-log-events \
  --log-group-name "$LOG_GROUP" \
  --log-stream-name "$STREAM" \
  --region "$REGION" --start-from-head \
  --query 'events[*].message' --output text 2>/dev/null \
  | tr '\t' '\n' \
  || echo "No log events on ${LOG_GROUP} / ${STREAM}."

if [ "$EXIT_CODE" != "0" ]; then
  echo "BASELINE PROBE FAILED TO RUN. exit=${EXIT_CODE}" >&2
  exit 1
fi
