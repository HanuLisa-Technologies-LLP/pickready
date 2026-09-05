#!/usr/bin/env bash
#
# Format and validate every Terraform module and both environment roots,
# offline.
#
# THIS SCRIPT'S HEADER USED TO SAY `terraform plan` CANNOT COMPLETE. IT CAN.
# ---------------------------------------------------------------------------
# The previous phase concluded, correctly for what it had, that "the AWS
# provider calls STS GetCallerIdentity before it plans anything, so a plan needs
# a real credential". That is the DEFAULT behaviour and it is avoidable: the
# provider's four pre-flight calls each have a `skip_*` argument, and the
# environment roots now wire all four to `var.planning_profile` (spec-doc6
# §13.3).
#
# So the boundary moved, and the honest statement of where it sits now is:
#
#   ./infra/validate.sh        fmt + validate. Resolves every module, variable
#                              type, output reference and expression.
#
#   ./infra/plan-offline.sh    plan, every environment, no credentials. Also
#                              resolves the resource GRAPH and type-checks every
#                              argument against the provider schema, which
#                              `validate` cannot: it has no way to evaluate a
#                              `for_each` key against the contents of the map it
#                              indexes. That gap is not theoretical. The first
#                              offline plan run on this repository failed with
#                              `Invalid index` on
#                              `var.secret_policy_arns["frontend"]`, an
#                              apply-time failure that this script had been
#                              reporting as clean.
#
#   terraform apply            NOT RUN, AND NOT TO BE RUN IN THIS PHASE.
#                              spec-doc6 §D5 and §17: running `apply` against a
#                              real account here is a failure of scope, not an
#                              accomplishment.
#
# WHAT NEITHER SCRIPT PROVES: anything about a real AWS account. Not that the
# account can create these resources, not that quotas suffice, not that IAM
# behaves once something assumes a role. Neither has spoken to AWS.
# `docs/operations/DEPLOY_AWS.md` states this at length, and it is the thing not to let
# "the pipeline is green" quietly stand in for.
#
#   ./infra/validate.sh
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform is not on PATH. https://developer.hashicorp.com/terraform/install" >&2
  exit 127
fi

echo "terraform $(terraform version -json | grep -o '"terraform_version":"[^"]*"' | cut -d'"' -f4)"
echo

failures=0

check() {
  local dir="$1" label="$2"
  printf '%-34s' "$label"
  if ! (cd "$dir" && terraform init -backend=false -input=false -no-color >/dev/null 2>&1); then
    echo "INIT FAILED"
    failures=$((failures + 1))
    (cd "$dir" && terraform init -backend=false -input=false -no-color 2>&1 | tail -20)
    return
  fi
  if (cd "$dir" && terraform validate -no-color >/dev/null 2>&1); then
    echo "valid"
  else
    echo "INVALID"
    failures=$((failures + 1))
    (cd "$dir" && terraform validate -no-color 2>&1 | tail -20)
  fi
}

echo "── modules ──────────────────────────────────────────────"
for dir in "$ROOT"/modules/*/; do
  check "$dir" "$(basename "$dir")"
done

echo
echo "── environments ─────────────────────────────────────────"
for dir in "$ROOT"/environments/*/; do
  [ -f "$dir/main.tf" ] || continue
  check "$dir" "$(basename "$dir")"
done

echo
echo "── formatting ───────────────────────────────────────────"
printf '%-34s' "terraform fmt"
if terraform fmt -check -recursive "$ROOT" >/dev/null 2>&1; then
  echo "clean"
else
  echo "NEEDS FORMATTING"
  terraform fmt -check -recursive "$ROOT"
  failures=$((failures + 1))
fi

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed."
  exit 1
fi

cat <<'NOTE'
All modules and every environment are formatted and valid.

NOT PROVEN BY THIS RUN: the resource graph. Run `./infra/plan-offline.sh` for
that; it needs no credentials either. `validate` cannot evaluate a `for_each`
key against the map it indexes, and that is exactly the gap the offline plan
closed on this repository the first time it ran.

NOT PROVEN BY EITHER: anything about a real AWS account. No live AWS deployment
has been executed, and running one in this phase is a failure of scope rather
than an accomplishment (spec-doc6 §D5 and §17).
NOTE
