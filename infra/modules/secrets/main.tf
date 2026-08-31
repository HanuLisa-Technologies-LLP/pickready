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
 * secret namespace. Every workload -- the API, the Celery worker, the beat
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
  }
}

locals {
  name = "${var.project}-${var.environment}"

  # Flattened so `for_each` can key on a stable "service/secret" string. A
  # nested for_each over a map of lists is not expressible in Terraform, and the
  # usual workaround -- one policy document built with a dynamic block -- makes
  # the plan much harder to read than one statement per service.
  service_names = keys(var.service_secrets)
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
  }
}
