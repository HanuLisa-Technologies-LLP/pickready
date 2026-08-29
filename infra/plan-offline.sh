#!/usr/bin/env bash
#
# `terraform plan` for staging AND production, with no credentials, no account,
# and no network (spec-doc6 §13.3).
#
# WHAT CHANGED, AND WHY THE PREVIOUS ANSWER WAS INCOMPLETE
# ---------------------------------------------------------
# The previous phase concluded that a plan "cannot complete without
# credentials", and wrote that into `validate.sh`. It was true by default and it
# was avoidable. The AWS provider makes four calls BEFORE it plans anything:
# STS GetCallerIdentity, an account-id lookup, a region-catalogue check, and the
# EC2 instance metadata endpoint. Each has a `skip_*` argument, and the
# environment roots wire all four to `var.planning_profile`.
#
# So a plan runs here. That is a genuine improvement over `validate` alone,
# because `validate` cannot evaluate a `for_each` key against a map's contents
# and a plan can. That difference is not theoretical: the first offline plan run
# on this repository failed with `Invalid index` on
# `var.secret_policy_arns["frontend"]`, an apply-time failure that seven
# modules' worth of `terraform validate` had reported as clean.
#
# ══════════════════════════════════════════════════════════════════════════════
# WHAT A GREEN RUN HERE PROVES
#
#   The configuration is internally consistent. The resource graph resolves.
#   Every module input and output reference exists. Every resource argument
#   type-checks against the provider schema. Every `for_each` key resolves
#   against the collection it indexes. Every variable validation passes for the
#   values given.
#
# WHAT IT DOES NOT PROVE, AND THIS IS THE HALF THAT MATTERS
#
#   Nothing about any real AWS account. Not that an account can create these
#   resources. Not that its service quotas suffice. Not that the IAM roles
#   behave as written once something assumes them. Not that the instance and
#   node types are offered in the chosen region. Not that the domain resolves or
#   that the hosted zone is the one the registrar delegates to.
#
#   THIS RUN HAS NEVER SPOKEN TO AWS. It is run against
#   `environments/offline-plan.tfvars`, whose account is all zeros, whose region
#   `xx-plan-1` does not exist, and whose domain is under the RFC 2606
#   `.invalid` TLD reserved so that it can never resolve.
#
#   "Plan succeeds" does not read as "ready to run". `docs/DEPLOY_AWS.md` §
#   "What the offline plan proves" says the same thing at more length, and the
#   ordered runbook there is what a real deployment follows.
# ══════════════════════════════════════════════════════════════════════════════
#
#   ./infra/plan-offline.sh            # both environments, human-readable
#   ./infra/plan-offline.sh --artifact # also writes plan-<env>.txt for CI
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
ARTIFACT_DIR="${PLAN_ARTIFACT_DIR:-$ROOT/plan-output}"
WANT_ARTIFACT=0
[ "${1:-}" = "--artifact" ] && WANT_ARTIFACT=1

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform is not on PATH. https://developer.hashicorp.com/terraform/install" >&2
  exit 127
fi

# DUMMY STATIC CREDENTIALS, FROM THE ENVIRONMENT, exactly as §13.3 asks.
#
# They are set here rather than passed as Terraform variables on purpose: a
# credential passed as a variable is written into the plan file and the state
# file, which is the shape `backend/tests/test_deploy_secret_hygiene.py` exists
# to refuse. The provider reads these directly and never validates them, because
# the planning profile skips the call that would.
#
# Any value already in the environment wins, so a developer with a real profile
# does not have it silently replaced.
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-offline-plan-not-a-real-key}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-offline-plan-not-a-real-secret}"
# Cleared, not set: a session token from a real profile would be sent to an
# endpoint in a region that does not exist.
unset AWS_SESSION_TOKEN AWS_PROFILE || true

VARS="$ROOT/environments/offline-plan.tfvars"
[ -f "$VARS" ] || { echo "missing $VARS" >&2; exit 1; }

[ "$WANT_ARTIFACT" -eq 1 ] && mkdir -p "$ARTIFACT_DIR"

failures=0

for env in staging production; do
  dir="$ROOT/environments/$env"
  echo "── terraform plan: $env ─────────────────────────────────────────"

  # -backend=false is not used here: without a backend block the environment
  # roots default to the LOCAL backend, which is what §13.3 asks for ("a local
  # backend for the planning profile so no remote state is required"). No
  # bucket, no lock table, no network.
  if ! (cd "$dir" && terraform init -input=false -no-color >/dev/null); then
    echo "$env: INIT FAILED"
    failures=$((failures + 1))
    continue
  fi

  # -lock=false because the local state file may not exist at all on a fresh
  # checkout, and there is nothing to race with: this plan is never applied.
  out="$ARTIFACT_DIR/plan-$env.txt"
  if [ "$WANT_ARTIFACT" -eq 1 ]; then
    if (cd "$dir" && terraform plan -input=false -no-color -lock=false -var-file="$VARS") > "$out" 2>&1; then
      grep -E "^Plan:" "$out" || echo "$env: planned with no changes"
      echo "        written to ${out#"$ROOT"/}"
    else
      echo "$env: PLAN FAILED"
      tail -30 "$out"
      failures=$((failures + 1))
    fi
  else
    if ! (cd "$dir" && terraform plan -input=false -no-color -lock=false -var-file="$VARS" | grep -E "^Plan:|^No changes"); then
      echo "$env: PLAN FAILED"
      failures=$((failures + 1))
    fi
  fi
  echo
done

if [ "$failures" -gt 0 ]; then
  echo "$failures environment(s) failed to plan."
  exit 1
fi

cat <<'NOTE'
Both environments plan.

READ THIS BEFORE REPEATING IT ANYWHERE. A plan in the planning profile has
never contacted AWS. It proves the configuration is internally consistent and
that the graph resolves; it proves nothing about whether an account can create
these resources, whether quotas suffice, or whether IAM behaves. It was produced
against an account of all zeros, a region that does not exist, and a domain
under the RFC 2606 `.invalid` TLD.

No live AWS deployment has been executed, and running one in this phase is a
failure of scope rather than an accomplishment (spec-doc6 §D5 and §17).
NOTE
