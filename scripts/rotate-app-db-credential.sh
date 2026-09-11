#!/usr/bin/env bash
#
# Mint (or rotate) the application's database password, inside the VPC.
#
#   ./scripts/rotate-app-db-credential.sh pilot
#
# WHY THIS EXISTS
# ---------------
# On 2026-09-11 every database connection in the product failed at once: the
# API, the health probe, and therefore sign-in. `DATABASE_URL` held a
# hand-composed copy of the RDS MASTER credential, and `manage_master_user_password`
# hands that password to Secrets Manager to ROTATE on a schedule. Seven days
# after the instance was created AWS rotated it. Nothing had changed in the
# code, nothing had been deployed, and the product was down.
#
# `infra/modules/rds` had described the correct design in its header since the
# day it was written -- "THE APPLICATION DOES NOT USE THE MASTER CREDENTIAL.
# `DATABASE_URL` is a separate secret holding a least-privileged application
# role" -- and that role had simply never been created. This script creates it,
# and re-running it rotates its password.
#
# WHAT IT DOES NOT DO, DELIBERATELY
# ----------------------------------
# It does not take a password, generate one, print one, or read one back. The
# password is minted by `app.scripts.provision_app_db_role` INSIDE the task and
# written straight to Secrets Manager, so it never appears in a RunTask API
# call, in CloudTrail, in a CloudWatch log line or in a shell history. What this
# file handles is the part that has to happen from outside: starting the task in
# the right subnets, WAITING for it, and reading the exit code.
#
# AND IT WAITS. `aws ecs run-task` returns as soon as the task is ACCEPTED.
# Treating that as success is a failure this project has already had. Here it
# would be the expensive kind: a run that changed the database password and then
# failed before writing the secret would leave the product holding a credential
# that no longer works. The script the task runs proves the new credential can
# log in BEFORE it writes the secret, for exactly that reason, and this file
# refuses to report success on any exit code but zero.
#
# AFTER IT SUCCEEDS the running services still hold their old connections, which
# is correct: a pooled connection is unaffected until it is replaced. Roll them:
#
#   ./scripts/deploy-services.sh <env>
#   ./scripts/update-lambda-code.sh <env> <backend image reference>
#
# The Lambdas matter as much as the services and are easier to forget:
# `app/workers/secrets_bootstrap.py` fetches once per EXECUTION ENVIRONMENT, so
# a warm function keeps the old DSN until it is recycled.
set -euo pipefail

ENVIRONMENT="${1:-pilot}"
PROJECT="${PROJECT:-readypick}"
CLUSTER="${PROJECT}-${ENVIRONMENT}"

# Resolved, never assumed. A hardcoded default cost a release step on
# 2026-09-09: the pilot lives in ap-south-2, a default said ap-south-1, and the
# failure surfaced as "TaskDefinition not found", which reads as a broken deploy
# rather than as a lookup in an empty region.
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
if [ -z "$REGION" ]; then
  echo "No AWS region. Set AWS_REGION (this project's pilot is ap-south-2)." >&2
  exit 2
fi
TASK_FAMILY="${CLUSTER}-migrate"
TIMEOUT_SECONDS="${ROTATE_TIMEOUT:-600}"

command -v aws >/dev/null 2>&1 || { echo "aws CLI is required." >&2; exit 127; }
command -v terraform >/dev/null 2>&1 || { echo "terraform is required." >&2; exit 127; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ROOT}/infra/environments/${ENVIRONMENT}"
[ -d "$ENV_DIR" ] || { echo "No such environment: ${ENVIRONMENT}" >&2; exit 2; }

pushd "$ENV_DIR" >/dev/null
SUBNETS="$(terraform output -json private_subnet_ids 2>/dev/null | tr -d '[]" ' || true)"
SECURITY_GROUP="$(terraform output -raw ecs_security_group_id 2>/dev/null || true)"
popd >/dev/null

if [ -z "$SUBNETS" ] || [ -z "$SECURITY_GROUP" ]; then
  echo "Could not read the network from ${ENVIRONMENT}'s Terraform outputs." >&2
  exit 2
fi

OVERRIDES='{"containerOverrides":[{"name":"migrate","command":["python","-m","app.scripts.provision_app_db_role"]}]}'

echo "Rotating the application database credential on ${CLUSTER}."
echo "The password is generated inside the task and never leaves the VPC."

TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_FAMILY" \
  --launch-type FARGATE \
  --region "$REGION" \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SECURITY_GROUP}],assignPublicIp=DISABLED}" \
  --overrides "$OVERRIDES" \
  --query 'tasks[0].taskArn' --output text)"

if [ -z "$TASK_ARN" ] || [ "$TASK_ARN" = "None" ]; then
  echo "run-task returned no task ARN. Nothing was rotated." >&2
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
    # A TIMEOUT IS NOT A FAILURE REPORT. The task may still be mid-run, so the
    # only honest thing to say is that the outcome is unknown and where to look.
    echo "Rotation did not finish within ${TIMEOUT_SECONDS}s. Last status: ${status}." >&2
    echo "Read /ecs/${CLUSTER}/migrate for ${TASK_ID} before running this again." >&2
    exit 1
  fi
  sleep 6
done

EXIT_CODE="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --region "$REGION" --query 'tasks[0].containers[0].exitCode' --output text)"

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
  echo >&2
  echo "ROTATION FAILED. exit=${EXIT_CODE}" >&2
  echo "The task proves the new credential works before it writes the secret," >&2
  echo "so a failure here leaves the existing DSN in place and usable." >&2
  exit 1
fi

echo
echo "Rotated. The services keep their current connections until they restart:"
echo "  ./scripts/deploy-services.sh ${ENVIRONMENT}"
echo "  ./scripts/update-lambda-code.sh ${ENVIRONMENT} <backend image reference>"
