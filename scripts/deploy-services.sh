#!/usr/bin/env bash
#
# Point each ECS service at the newest revision of its task definition, and WAIT
# for the rollout to finish.
#
#   ./scripts/deploy-services.sh pilot
#
# WHY THIS STEP EXISTS AT ALL
# ---------------------------
# `modules/ecs` sets `ignore_changes = [task_definition, desired_count]` on every
# service, which is the standard arrangement when a pipeline deploys and
# Terraform owns the shape: without it, Terraform would revert to whatever the
# last apply pinned every time CI deployed.
#
# The half that was missing is this one. `terraform apply` REGISTERS a new task
# definition revision and then, correctly, does not touch the service. So
# nothing pointed the service at it, and the service kept running the first
# revision it was ever created with, indefinitely, while every apply reported
# success and every new image sat in ECR unused.
#
# WHY IT WAITS, AND WHY IT READS THE RESULT
# ------------------------------------------
# `update-service` returns as soon as the request is accepted. What happens
# after that is a rolling deployment with `deployment_circuit_breaker
# { rollback = true }`, and the case worth catching is exactly the one the
# breaker handles: tasks that will not become healthy, a rollback, and a service
# that ends up running the PREVIOUS image while the deploy job exits zero.
#
# `wait services-stable` covers the timing. The rollback is caught by
# `scripts/verify-deployment.sh`, which reads the digest of the RUNNING TASKS
# rather than the service definition, and is the step that runs after this one.
set -euo pipefail

ENVIRONMENT="${1:-}"
PROJECT="${PROJECT:-readypick}"
REGION="${AWS_REGION:-ap-south-2}"
CLUSTER="${PROJECT}-${ENVIRONMENT}"

[ -n "$ENVIRONMENT" ] || { echo "usage: $0 <environment>" >&2; exit 2; }
command -v aws >/dev/null 2>&1 || { echo "aws CLI is required." >&2; exit 127; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ROOT}/infra/environments/${ENVIRONMENT}"
[ -d "$ENV_DIR" ] || { echo "No such environment: ${ENVIRONMENT}" >&2; exit 2; }

# DISCOVERED, not listed. A service added to the composition is deployed by this
# script without anybody remembering to add it here, and a hand-kept list is
# exactly what silently stops covering the newest service.
services="$(aws ecs list-services --cluster "$CLUSTER" --region "$REGION" \
  --query 'serviceArns[]' --output text | tr '\t' '\n' | sed 's#.*/##')"

if [ -z "$services" ]; then
  echo "No services on ${CLUSTER}. Run terraform apply first." >&2
  exit 2
fi

updated=""
for service in $services; do
  # The FAMILY, from the service's current task definition, so this works
  # whatever revision it happens to be on. `:latest` is not a thing for a task
  # definition, so the family name alone is what resolves to the newest ACTIVE
  # revision.
  current="$(aws ecs describe-services --cluster "$CLUSTER" --services "$service" \
    --region "$REGION" --query 'services[0].taskDefinition' --output text)"
  family="${current##*/}"
  family="${family%:*}"

  newest="$(aws ecs describe-task-definition --task-definition "$family" \
    --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)"

  if [ "$current" = "$newest" ]; then
    echo "==> ${service}: already on ${newest##*/}"
    continue
  fi

  echo "==> ${service}: ${current##*/} -> ${newest##*/}"
  aws ecs update-service --cluster "$CLUSTER" --service "$service" \
    --task-definition "$newest" --region "$REGION" \
    --query 'service.serviceName' --output text >/dev/null
  updated="${updated} ${service}"
done

if [ -z "$updated" ]; then
  echo
  echo "Every service was already on its newest task definition."
  exit 0
fi

echo
echo "Waiting for:${updated}"
echo "  (Accepted is not deployed. A rolling deployment whose tasks never become"
echo "   healthy is rolled back by the circuit breaker, and the service then runs"
echo "   the PREVIOUS image. scripts/verify-deployment.sh is what catches that,"
echo "   by reading the digest of the running tasks.)"

# shellcheck disable=SC2086 -- the list is deliberately word-split
if aws ecs wait services-stable --cluster "$CLUSTER" --services $updated --region "$REGION"; then
  echo
  echo "Stable. Now verify by digest:"
  echo "  ./scripts/verify-deployment.sh ${ENVIRONMENT}"
else
  echo
  echo "NOT STABLE within the wait. This is not a timeout to shrug at: it means" >&2
  echo "the tasks did not become healthy, which the circuit breaker will have" >&2
  echo "rolled back. Read the events:" >&2
  # shellcheck disable=SC2086
  for service in $updated; do
    echo >&2
    echo "  ${service}:" >&2
    aws ecs describe-services --cluster "$CLUSTER" --services "$service" \
      --region "$REGION" --query 'services[0].events[0:5].message' --output text \
      | tr '\t' '\n' | sed 's/^/    /' >&2
  done
  exit 1
fi
