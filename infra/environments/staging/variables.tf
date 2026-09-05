/**
 * EVERY ACCOUNT-SPECIFIC VALUE IN THIS FILE HAS NO DEFAULT, AND THAT IS THE
 * DELIVERABLE.
 *
 * spec-doc6 §D5: "no account ID, no region confirmation, no domain name is
 * required from the product owner in this phase. Do not ask for them again."
 * Therefore "every account-specific value (account ID, region, domain, hosted
 * zone ID, certificate ARN, bucket names) is a declared Terraform variable with
 * no default... The codebase must be complete except for those values."
 *
 * A default here would be an invention. `region` in particular USED to default
 * to `ap-south-1`, and §D5 removes that assumption by name: "Region assumption
 * `ap-south-1` is removed as an assumption and becomes a required variable. Do
 * not hardcode it anywhere." The reasoning that made ap-south-1 the likely
 * answer is not lost, it has moved to `docs/DEPLOY_AWS.md` where it is
 * presented as the owner's decision rather than as a value already chosen.
 *
 * Every one of these is documented with the command that produces it in
 * `infra/environments/staging/README.md`.
 */

variable "project" {
  type    = string
  default = "readypick"
}

# ── Account-specific: no defaults ────────────────────────────────────────────

variable "account_id" {
  description = "The 12-digit AWS account this environment lives in. See README.md; there is no default and no `aws_caller_identity` lookup, because that lookup is a live STS call the offline plan cannot make."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be the 12-digit AWS account number."
  }
}

variable "region" {
  description = "The AWS region. NO DEFAULT: spec-doc6 §D5 removes `ap-south-1` as an assumption and makes this the owner's decision. `docs/DEPLOY_AWS.md` records why ap-south-1 is the likely answer for an India-billed tenant and why that is still a decision rather than a value."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "region must be an AWS region identifier, for example ap-south-1 or eu-west-2."
  }
}

variable "availability_zones" {
  description = "At least two, inside `region`. No default, because a default would name zones in a region nobody has chosen yet. `aws ec2 describe-availability-zones --region <region>` lists them."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required by the RDS subnet group."
  }
}

variable "domain_name" {
  description = "The hostname this environment is served on. No default: spec-doc6 §D5 says no domain name is available in this phase and one must not be invented."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,}$", var.domain_name))
    error_message = "domain_name must be a lowercase fully qualified domain name."
  }
}

variable "hosted_zone_id" {
  description = "The EXISTING Route53 zone id for `domain_name`. An id rather than a name lookup: see `modules/dns`, where creating or name-resolving a zone is the most expensive mistake this layer can make."
  type        = string

  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "hosted_zone_id must be a Route53 zone id, which begins with Z."
  }
}

variable "storage_bucket_name" {
  description = "Globally unique bucket for resumes and compliance documents. S3 names are global across every account, so a derived name is one that may already belong to somebody else and the collision arrives at apply time."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.storage_bucket_name))
    error_message = "storage_bucket_name must be a valid S3 bucket name: lowercase, 3 to 63 characters."
  }
}

variable "access_logs_bucket_name" {
  description = "Globally unique bucket for the load balancer's access logs. Separate from the storage bucket because it must be SSE-S3 encrypted, not CMK: see `modules/alb`."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.access_logs_bucket_name))
    error_message = "access_logs_bucket_name must be a valid S3 bucket name: lowercase, 3 to 63 characters."
  }
}

# ── Deploy-time ──────────────────────────────────────────────────────────────

variable "image_tag" {
  description = <<-EOT
    The image CI pushed, tagged by commit SHA, never `latest`.

    spec-doc6 §13.4 keeps SHA tagging and digest verification. `sha-placeholder`
    is the default so `terraform validate` and the offline plan work without a
    build; a real apply is always passed the commit's own tag.
  EOT
  type        = string
  default     = "sha-placeholder"
}

# ── The planning profile (spec-doc6 §13.3) ───────────────────────────────────

variable "planning_profile" {
  description = <<-EOT
    Configure the AWS provider so `terraform plan` completes with no
    credentials, no account and no network (spec-doc6 §13.3).

    WHAT IT DOES: sets `skip_credentials_validation`,
    `skip_requesting_account_id`, `skip_region_validation` and
    `skip_metadata_api_check` on the provider. Those four are the calls the
    provider makes BEFORE it plans anything, and they are the only reason the
    previous phase concluded that a plan "cannot complete". They can simply be
    switched off.

    WHAT A PLAN IN THIS MODE PROVES: the configuration is internally consistent,
    the resource graph resolves, every module input and output reference is
    real, and every resource argument type-checks against the provider schema.

    WHAT IT DOES NOT PROVE, AND THIS IS THE HALF THAT MATTERS: nothing about the
    account. Not that the account can create these resources, not that its
    service quotas suffice, not that the IAM roles behave as written, not that
    the domain resolves, not that the instance types are offered in the chosen
    region. A plan in this mode has never spoken to AWS. "Plan succeeds" does
    not read as "ready to run", and `docs/DEPLOY_AWS.md` says so at length.

    NO CREDENTIAL IS PASSED AS A TERRAFORM VARIABLE. The dummy static
    credentials §13.3 mentions are supplied to CI as the ordinary
    `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables, which
    the provider reads directly. A credential passed as a variable would be
    written into the plan file and the state file, which is the exact shape
    `backend/tests/test_deploy_secret_hygiene.py` exists to refuse.

    FALSE BY DEFAULT, so a real apply cannot inherit it by accident: with these
    checks skipped, an apply against a misconfigured profile fails later and
    less clearly than it otherwise would.
  EOT
  type        = bool
  default     = false
}

variable "alarm_emails" {
  description = <<-EOT
    Who is notified when an alarm fires.

    An email subscription is PENDING until its recipient clicks the
    confirmation link, and Terraform reports it as created either way. An empty
    list is allowed and means the alarms fire with nobody subscribed, which is
    not a silent state: the console shows the topic with no subscribers.
  EOT
  type        = list(string)
  default     = []
}

variable "reserve_lambda_concurrency" {
  description = <<-EOT
    Whether to apply the per-function concurrency ceilings.

    FALSE, because this account's TOTAL Lambda concurrency is 10 -- the
    new-account default, not the usual 1000 -- and AWS refuses any reservation
    that would leave fewer than 10 unreserved. Every reservation is therefore
    impossible, and the apply fails with a message about
    `UnreservedConcurrentExecution` rather than about the quota.

    Nothing is unprotected in the meantime. The ceilings exist because each
    concurrent task-worker invocation opens its own database engine against a
    `db.t4g.micro` connection limit, and an account cap of 10 is a HARDER
    ceiling than the 20 that was being asked for.

    Turn it on after raising the quota:

      aws service-quotas request-service-quota-increase \
        --service-code lambda --quota-code L-B99A9384 --desired-value 1000

    Check it first, because a quota increase is not instant:

      aws lambda get-account-settings \
        --query 'AccountLimit.ConcurrentExecutions'
  EOT
  type        = bool
  default     = false
}
