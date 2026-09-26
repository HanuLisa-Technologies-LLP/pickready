/**
 * Secrets Manager, and the per-service IAM scoping that is the point of it.
 *
 * spec-doc5 §D.4:
 *
 *   "IAM policies must be scoped per-service, not a shared broad role -- the
 *    DATABASE_URL exposure finding from the GCP phase (a composed DSN readable
 *    via a broad permission) is the exact class of mistake to design out here
 *    from the start rather than harden later."
 *
 * WHAT THAT FINDING ACTUALLY WAS, because the shape matters more than the
 * platform: a single runtime service account held read access across the whole
 * secret namespace. Every workload -- the API, the background worker, the
 * scheduler, the one-shot migration job -- ran as it, so the DSN was readable
 * from four places when it needed to be readable from two. Nothing was
 * misconfigured; the permission was simply wider than the need, and nobody
 * could see that it was, because a wildcard grant looks the same whether it is
 * over-broad or exactly right.
 *
 * HOW THIS DESIGNS IT OUT
 * ------------------------
 * `service_secrets` is a MAP from a service name to the exact list of secrets
 * that service may read. The module emits one IAM policy per service, and each
 * policy's Resource list is those secrets' ARNs -- enumerated, never a prefix,
 * never a `*`.
 *
 * The consequence is deliberate and slightly annoying, which is how you know it
 * is real: adding a secret to a service is a Terraform change with a plan you
 * can read, not a thing that already worked because the role was broad. A
 * reviewer looking at the plan sees "the worker can now read OPENAI_GPT_TERRA"
 * as a line, which is the whole point.
 *
 * `test_deploy_secret_hygiene.py` already asserts the codebase never inlines a
 * secret or composes one into a loggable env var. This is the other half:
 * nothing can READ one it does not need.
 *
 * VALUES ARE NOT IN TERRAFORM
 * ----------------------------
 * `aws_secretsmanager_secret` creates the container; `aws_secretsmanager_secret_version`
 * is deliberately absent for every application secret. A value in Terraform is a
 * value in the state file, and the state file is a JSON document with an S3
 * bucket policy in front of it rather than a vault. Values are put in by hand
 * or by a rotation Lambda, once, out of band.
 *
 * The RDS master password is the one exception and it is generated rather than
 * supplied -- see the `rds` module, which owns it.
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
    # For the machine-generated secrets below. See `random_password.generated`
    # for why a value is minted here when every other value in this module is
    # deliberately absent.
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
  }
}

locals {
  name = "${var.project}-${var.environment}"

  # Flattened so `for_each` can key on a stable "service/secret" string. A
  # nested for_each over a map of lists is not expressible in Terraform, and the
  # usual workaround -- one policy document built with a dynamic block -- makes
  # the plan much harder to read than one statement per service.
  service_names = keys(var.service_secrets)

  # A SECRET IS EITHER A HUMAN'S TO SUPPLY OR THIS MODULE'S TO MINT, never
  # both. The two sets are disjoint by construction, so no secret can end up
  # with a placeholder version AND a generated one racing to be AWSCURRENT.
  generated = toset(var.generated_secret_names)
  supplied = toset([
    for name in var.secret_names : name if !contains(var.generated_secret_names, name)
  ])
}

# ── The secrets ──────────────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "this" {
  for_each = toset(var.secret_names)

  name        = "${local.name}/${each.value}"
  description = "ReadyPick ${var.environment}: ${each.value}"

  # A CUSTOMER-MANAGED KEY, not the AWS-managed `aws/secretsmanager` default.
  # The difference that matters is not encryption strength -- both are AES-256 --
  # it is that a customer-managed key has its own key policy, so "who can
  # decrypt these" is a question with an auditable answer separate from "who has
  # IAM permissions on Secrets Manager".
  kms_key_id = var.kms_key_id

  # Seven days, not zero. A deleted secret with no recovery window is a deleted
  # secret, and the failure mode this protects against is a Terraform destroy
  # against the wrong workspace -- which is a thing that happens to people who
  # are tired rather than to people who are careless.
  recovery_window_in_days = var.environment == "production" ? 30 : 7

  tags = merge(var.tags, { Name = "${local.name}-${each.value}" })
}

# ── Every secret gets a VERSION, holding a sentinel ──────────────────────────
#
# NOT the value. The container is created here and the value is not, because a
# value in Terraform is a value in the state file and the state file is JSON
# behind a bucket policy rather than a vault. That rule is unchanged.
#
# What changed is that a secret with NO VERSION AT ALL is unusable in a way
# nobody expects: ECS fetches every secret in a task definition before the
# container starts, so ONE unpopulated secret stops the whole service with
#
#   ResourceNotFoundException: Secrets Manager can't find the specified secret
#   value for staging label: AWSCURRENT
#
# and the service reports "unable to place a task". That is the wrong failure.
# This product degrades when a credential is absent: the model router raises a
# documented error and its caller runs a deterministic fallback, the delivery
# preflight logs a warning rather than crashing, and the billing page reports
# checkout as unavailable. A missing Tavily key should cost the internet
# segment of AI Reach, not the entire API.
#
# The sentinel cannot authenticate to anything, and `app.core.config` maps it
# back to "" before any code reads it, so what runs is exactly what runs when
# the variable is unset. `backend/tests/test_placeholder_secret.py` pins that
# the two strings agree.
resource "aws_secretsmanager_secret_version" "placeholder" {
  # THE SUPPLIED HALF ONLY. A generated secret gets its real value one resource
  # down; writing a sentinel over it here would leave the two fighting for
  # AWSCURRENT on every apply, and the loser would be whichever one Terraform
  # happened to write second.
  for_each = local.supplied

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = var.placeholder_value

  lifecycle {
    # THE REAL VALUE MUST SURVIVE. `aws secretsmanager put-secret-value` creates
    # a new version, and a Terraform resource that owned the string would revert
    # it on the next apply -- silently, because the plan would read as a
    # one-line change to a sensitive attribute.
    ignore_changes = [secret_string]
  }
}

# ── A secret NOTHING OUTSIDE THIS PLATFORM ISSUES is minted here ────────────
#
# The rule above -- the container is created and the value is not -- is about
# credentials somebody else owns. A Voyage key, a Razorpay secret and an SMTP
# app password all exist before Terraform runs, in a vendor's console, and the
# only honest thing this module can do with them is make a container and wait.
#
# `INBOUND_WEBHOOK_SECRET` has no such issuer. It is a value one half of this
# platform shows to the other half, so the only question is who mints it, and a
# human minting it means the security control it enables ships DISABLED until
# somebody remembers. That is not hypothetical: `_require_relay_secret` treats
# an empty setting as "leave the route open and log that it is open", which is
# the correct behaviour for a value that might legitimately be absent and the
# wrong state to leave an environment in for ever. A secret left on
# PLACEHOLDER_NOT_CONFIGURED is a public write endpoint with a note attached.
#
# THE PRICE IS THAT THE VALUE IS IN THE STATE FILE, and that is a real cost
# rather than a technicality: the state is JSON behind an S3 bucket policy, not
# a vault. It is accepted for the same reason `modules/elasticache` accepts it
# for the Redis auth token -- the alternative is a credential travelling through
# a tfvars file, a CI variable and a shell, which is the shape
# `test_deploy_secret_hygiene.py` exists to refuse, and which is strictly worse
# because it has more copies in more places than the state bucket.
#
# ROTATION IS `terraform apply -replace=module.secrets.random_password.generated[...]`,
# which writes a new version and updates both consumers in the same apply.
#
# BE EXACT ABOUT THE WINDOW. The Lambda's environment and the API's mounted copy
# do not change at the same instant, and a running task keeps the value it was
# started with, so a reply arriving mid-rotation is answered 403. That is not a
# lost reply: the message is still in the inbound bucket, the invocation FAILS
# rather than reporting success, and the failure destination publishes it --
# the Lambda's own retry count is deliberately zero platform-wide. It is a
# visible, bounded failure, which is why `keepers` is left empty: rotation is a
# deliberate act with somebody watching, never a side effect of an unrelated
# apply.
resource "random_password" "generated" {
  for_each = local.generated

  # 48 characters drawn from [A-Za-z0-9]. Alphanumeric DELIBERATELY: this value
  # travels as an HTTP header, and a header whose value needs quoting is one
  # that can be mangled by any hop that normalises whitespace. The length is
  # what buys the entropy back -- 48 alphanumerics is about 285 bits, which is
  # not a number anybody is guessing against a route that also compares in
  # constant time.
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret_version" "generated" {
  for_each = local.generated

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = random_password.generated[each.key].result

  # NO `ignore_changes`, and that is the difference from the placeholder above.
  # There it protects a value a human put in by hand, which Terraform does not
  # know and must never revert. Here Terraform IS the authority: ignoring
  # changes would mean a replaced `random_password` silently failing to reach
  # Secrets Manager, so the API would keep checking the old value while the
  # Lambda sent the new one and every employer reply would 403.
}

# ── One policy per service, over an enumerated list of ARNs ──────────────────

data "aws_iam_policy_document" "service" {
  for_each = var.service_secrets

  statement {
    sid    = "ReadOnlyTheSecretsThisServiceNeeds"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    # ENUMERATED. Not a prefix, not a wildcard. This list IS the audit.
    resources = [
      for secret in each.value : aws_secretsmanager_secret.this[secret].arn
    ]
  }

  # Decrypting is a separate permission from reading, and it is scoped to the
  # one key and to Secrets Manager as the calling service. Without the
  # ViaService condition this grant would let the role decrypt anything else
  # that key protects.
  statement {
    sid       = "DecryptThoseSecretsOnly"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }

}

resource "aws_iam_policy" "service" {
  for_each = var.service_secrets

  name        = "${local.name}-${each.key}-secrets"
  description = "Exactly the secrets ${each.key} reads. Enumerated, never a prefix."
  policy      = data.aws_iam_policy_document.service[each.key].json

  tags = merge(var.tags, { Service = each.key })
}


# ── The WRITE grant is a SEPARATE policy, and it goes on the TASK role ───────
#
# The read policy above is attached to the EXECUTION role, which fetches
# secrets and injects them before the container starts. Writing is done by the
# application's own boto3 client, which runs as the TASK role, so a write
# statement added to the read policy is a grant the code can never use. That is
# not theoretical: `rotate-app-db-credential.sh` failed in pilot with
# AccessDeniedException on exactly this, because the statement had been put in
# the reviewable place rather than the effective one.
#
# Same split the `task_s3` attachment already makes, and for the same stated
# reason: S3 goes on the task role "because it is the application's own boto3
# client making the call".
#
# PutSecretValue only. Not DeleteSecret, not UpdateSecret, not RestoreSecret:
# replacing a value is the operation, and removing the container is not
# something a rotation job should be able to do at all.
data "aws_iam_policy_document" "service_writer" {
  for_each = var.service_secret_writers

  statement {
    sid     = "ReplaceTheValueOfTheSecretsThisServiceRotates"
    effect  = "Allow"
    actions = ["secretsmanager:PutSecretValue"]
    resources = [
      for secret in each.value : aws_secretsmanager_secret.this[secret].arn
    ]
  }

  # Writing a new version ENCRYPTS it, which is a different KMS action from
  # reading one, under the same ViaService condition so the key cannot be used
  # for anything but Secrets Manager acting on this role's behalf.
  statement {
    sid       = "EncryptTheVersionsThisServiceWrites"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey"]
    resources = [var.kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "service_writer" {
  for_each = var.service_secret_writers

  name        = "${local.name}-${each.key}-secret-writer"
  description = "The secrets ${each.key} may REPLACE. Enumerated, and attached to the task role because the application's own SDK makes the call."
  policy      = data.aws_iam_policy_document.service_writer[each.key].json

  tags = merge(var.tags, { Service = each.key })
}

# ── A guard against the thing this module exists to prevent ──────────────────
#
# An explicit DENY on every secret NOT in a service's list would be the belt to
# this braces, and it is deliberately NOT here: an explicit deny cannot be
# narrowed later by a resource policy, and a deny over `NotResource` is one of
# the easiest IAM constructs to get subtly wrong. The Allow list being
# enumerated is the guarantee; a deny would be a second, weaker statement of it
# that a reader might trust instead.
#
# What IS here is a check that the map does not name a secret that does not
# exist -- which would otherwise produce a policy granting access to an ARN
# nothing will ever create, and read as a working grant in a review.
resource "terraform_data" "validate_service_secrets" {
  lifecycle {
    precondition {
      condition = alltrue([
        for service, secrets in var.service_secrets :
        alltrue([for s in secrets : contains(var.secret_names, s)])
      ])
      error_message = "service_secrets names a secret that is not in secret_names. That would produce a policy granting access to an ARN nothing creates, which reads as a working grant in a review."
    }

    # A generated name that is not in `secret_names` creates no container, so
    # `aws_secretsmanager_secret.this[each.key]` would fail at apply with an
    # index error rather than at plan with a sentence. Checked here so the
    # failure names the mistake.
    precondition {
      condition = alltrue([
        for name in var.generated_secret_names : contains(var.secret_names, name)
      ])
      error_message = "generated_secret_names names a secret that is not in secret_names, so no container would be created to hold the generated value."
    }

    # The same check for the write map, plus one the read map does not need:
    # the policy documents are built with `for_each = var.service_secrets`, so
    # a WRITER naming a service that has no read entry would be dropped in
    # silence. A rotation job whose grant quietly did not exist would fail at
    # the last step, after it had already changed a database password.
    precondition {
      condition = alltrue([
        for service, secrets in var.service_secret_writers :
        contains(keys(var.service_secrets), service) &&
        alltrue([for s in secrets : contains(var.secret_names, s)])
      ])
      error_message = "service_secret_writers names a service with no service_secrets entry, or a secret that is not in secret_names. The first is silently dropped when the policies are built, which is worse than an error."
    }
  }
}
