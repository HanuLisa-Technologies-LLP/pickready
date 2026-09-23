#!/usr/bin/env bash
#
# Mirror the three Judge0 CE images into this environment's ECR, by digest.
#
#   ./scripts/mirror-judge0-images.sh pilot
#
# WHY A MIRROR AT ALL
# -------------------
# The code sandbox host has NO route to the internet, by design: it runs
# candidate code, and a host that can reach Docker Hub can reach anywhere.
# So the images it runs are copied into the environment's own registries
# (created by infra/modules/code_sandbox, stage A1 of the runbook) and the host
# pulls them over the VPC endpoints, BY DIGEST.
#
# WHAT IT PRINTS
# --------------
# A `judge0_image_digests` block for the environment's terraform.tfvars. The
# digest is the one ECR reports for the pushed image, so what Terraform pins is
# exactly the bytes that landed. The upstream digest is printed beside it so a
# reviewer can compare it with Judge0's own release notes.
#
# RUN IT ON AN amd64 DOCKER HOST, AND NEVER BESIDE THE TEST SUITE. Judge0
# publishes amd64 images only, and a pull and push of several gigabytes is the
# kind of CPU and disk load CLAUDE.md records as turning a slow test run into
# an apparent hang.
#
# Nothing here reads or prints a secret. `docker login` takes the ECR token on
# stdin, never as an argument.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: mirror-judge0-images.sh pilot|staging|production" >&2
  exit 2
fi
ENVIRONMENT="$1"
PROJECT="${PROJECT:-readypick}"
REGION="${AWS_REGION:?set AWS_REGION to the region of the environment}"

case "$ENVIRONMENT" in
  pilot|staging|production) ;;
  *) echo "environment must be pilot, staging or production" >&2; exit 2 ;;
esac

# The upstream images the Judge0 CE 1.13.1 release composes. Changing one of
# these is a sandbox upgrade and goes through the runbook's AMI and image bump.
declare -A UPSTREAM=(
  [judge0]="docker.io/judge0/judge0:1.13.1"
  [judge0-postgres]="docker.io/library/postgres:16.2"
  [judge0-redis]="docker.io/library/redis:7.2.4"
)

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

declare -A PUSHED=()
for name in judge0 judge0-postgres judge0-redis; do
  source_ref="${UPSTREAM[$name]}"
  repository="$PROJECT-$ENVIRONMENT/$name"
  tag="${source_ref##*:}"
  target="$REGISTRY/$repository:$tag"

  echo "pulling $source_ref (linux/amd64)" >&2
  docker pull --platform linux/amd64 "$source_ref" >/dev/null
  upstream_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$source_ref")"

  # Tags are IMMUTABLE in these registries. A second run finds the tag already
  # there and reads its digest instead of pushing different bytes over it.
  if existing="$(aws ecr describe-images --region "$REGION" --repository-name "$repository" \
      --image-ids imageTag="$tag" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null)"; then
    echo "$repository:$tag already mirrored" >&2
    PUSHED[$name]="$existing"
  else
    docker tag "$source_ref" "$target"
    docker push "$target" >/dev/null
    PUSHED[$name]="$(aws ecr describe-images --region "$REGION" --repository-name "$repository" \
      --image-ids imageTag="$tag" --query 'imageDetails[0].imageDigest' --output text)"
  fi
  echo "$name upstream=$upstream_digest mirrored=${PUSHED[$name]}" >&2
done

cat <<TFVARS
judge0_image_digests = {
  "judge0"          = "${PUSHED[judge0]}"
  "judge0-postgres" = "${PUSHED[judge0-postgres]}"
  "judge0-redis"    = "${PUSHED[judge0-redis]}"
}
TFVARS
