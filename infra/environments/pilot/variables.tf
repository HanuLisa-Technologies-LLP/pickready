/**
 * Pilot inputs.
 *
 * Every value with a default is a decision this file is making. Every value
 * without one is a fact about the world that Terraform cannot invent: the
 * account, the bucket names (global across every AWS account), the domain, and
 * who gets paged.
 */

variable "project" {
  description = "Name prefix for every resource. Also the prefix of the Lambda function names the application addresses by literal string, so changing it means changing app/workers/dispatch.py with it."
  type        = string
  default     = "readypick"
}

variable "account_id" {
  description = "Scopes the KMS key policy and the SNS topic policy. A service principal with no account condition is the confused-deputy shape: it reads as narrow because it names an AWS service, and it is reachable from any account using that service."
  type        = string
}

variable "region" {
  description = <<-EOT
    The AWS region. NO DEFAULT, for the same reason staging and production have
    none: a default is an assumption the next environment inherits without
    anybody deciding it.

    ap-south-2 (Hyderabad) is the locked decision for this pilot and it is
    passed in `terraform.tfvars`. It must match `app.core.config.Settings`,
    whose `aws_region` default IS ap-south-2, because every bucket, function,
    cluster and secret the application addresses lives there and a mismatch is a
    set of NoSuchBucket and ResourceNotFound errors that read like missing
    resources rather than like a wrong region.

    `backend.tf` names it as a literal, and that one is not a choice: Terraform
    does not evaluate variables inside a backend block.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "region must be an AWS region identifier, for example ap-south-2."
  }
}

variable "availability_zones" {
  description = "At least two, inside `region`, which is what an ALB and an RDS subnet group both require. No default, because a default would name zones in a region nobody has chosen yet. `aws ec2 describe-availability-zones --region <region>` lists them."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required by the RDS subnet group."
  }
}

variable "domain_name" {
  description = <<-EOT
    The public hostname, if there is one yet. Null or empty means no ACM
    certificate is requested and no Route 53 record is written, and the
    environment is reached at the load balancer's own AWS name.

    There is deliberately no plaintext-only mode to fall back into. The
    application sets Secure cookies and uvicorn runs with `--proxy-headers`, so
    over plain http every auth cookie is refused and nobody can sign in. An
    http-only environment is not a smaller product; it is a product with no
    login, which is why `fallback_certificate_arn` exists for the interim.
  EOT
  type        = string
  default     = null
}

variable "hosted_zone_id" {
  description = "The Route 53 zone holding `domain_name`. Only read when a domain is set; the ACM validation records and the alias record are both written into it."
  type        = string
  default     = null
}

variable "fallback_certificate_arn" {
  description = <<-EOT
    A certificate to attach to the HTTPS listener while no domain is
    configured. It exists because the listener cannot be created without one
    and there is no plaintext mode.

    A certificate that does not match the hostname a browser typed produces a
    warning the visitor must click through, which is a bad but HONEST state for
    a pre-domain pilot. It is a stopgap and it is named like one; setting
    `domain_name` retires it.
  EOT
  type        = string
  default     = null
}

variable "storage_bucket_name" {
  description = "NAMED, NOT DERIVED. S3 bucket names are global across every AWS account, so a derived name is a name that may already belong to somebody else."
  type        = string
}

variable "access_logs_bucket_name" {
  description = "The load balancer's access log bucket. Global for the same reason."
  type        = string
}

variable "image_tag" {
  description = <<-EOT
    What the task definitions and the image-based functions are pinned to.

    A SHA tag, or a digest where one is available. ECR tags are immutable in
    this account, which is what makes a SHA tag a permanent name for specific
    bytes; `latest` would make every task definition ambiguous and every
    rollback a guess.

    CI updates the running services past this value, and Terraform ignores that
    attribute on purpose, so this is the shape rather than the current release.
  EOT
  type        = string
  default     = "latest"
}

variable "alarm_emails" {
  description = <<-EOT
    Who is notified when an alarm fires.

    An email subscription is PENDING until its recipient clicks the
    confirmation link, and Terraform reports it as created either way. An empty
    list is allowed and means the alarms fire and change state with nobody
    subscribed, which is a real posture for a pilot and not a silent one: the
    console shows the topic with no subscribers.
  EOT
  type        = list(string)
  default     = []
}

variable "planning_profile" {
  description = <<-EOT
    Switches off the four calls the AWS provider makes before it plans anything
    (STS GetCallerIdentity, the region catalogue, the account id and the
    instance metadata endpoint), so `terraform plan` runs offline with dummy
    credentials.

    FALSE for anything real. An offline plan proves the configuration is
    internally consistent, the graph resolves, every module reference exists
    and every argument type-checks against the provider schema. It proves
    NOTHING about an account: not creatability, not quotas, not IAM behaviour,
    not that the chosen instance types exist in the chosen region. Do not let
    "plan succeeds" read as "ready to run".
  EOT
  type        = bool
  default     = false
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
