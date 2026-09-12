variable "project" {
  description = "Resource name prefix, e.g. \"readypick\"."
  type        = string
}

variable "environment" {
  description = "Environment name, e.g. \"pilot\"."
  type        = string
}

variable "account_id" {
  description = <<-EOT
    The AWS account receiving this mail. It is the condition on the bucket and
    topic policies: `ses.amazonaws.com` is a SHARED principal, so without it
    any SES customer in the region could write into the bucket.

    PASSED IN RATHER THAN LOOKED UP, for the reason the `lambda` module's own
    `account_id` records: `data "aws_caller_identity"` calls STS, and the
    offline plan has never contacted AWS.
  EOT
  type        = string
}

variable "region" {
  description = <<-EOT
    The region whose SES inbound endpoint the MX record points at. SES receives
    mail only in the region the rule set is active in, so this and the
    provider's region are the same value and must stay so.

    PASSED IN RATHER THAN LOOKED UP, the same reason `account_id` records:
    `data "aws_region"` is a lookup, and the offline plan runs against a region
    that deliberately does not exist.
  EOT
  type        = string
}

variable "reply_domain" {
  description = <<-EOT
    The subdomain SES receives mail for, e.g. "reply.readypick.ai". It is also
    the domain the application builds a thread's Reply-To on, so it must match
    INBOUND_EMAIL_DOMAIN on the API exactly or replies arrive at an address
    nothing is listening to.

    A SUBDOMAIN, never the apex: receiving mail means owning the MX record, and
    the apex's MX belongs to whatever mailbox the company actually reads.
  EOT
  type        = string

  validation {
    # Two labels is a bare apex. Refused here rather than discovered by the
    # company's own mail stopping.
    condition     = length(split(".", var.reply_domain)) >= 3
    error_message = "reply_domain must be a subdomain, not the apex: taking the apex's MX would route the company's own mail into an S3 bucket."
  }
}

variable "hosted_zone_id" {
  description = "Route53 zone holding the product's domain. The MX and the SES verification TXT are written into it."
  type        = string
}

variable "lambda_function_arn" {
  description = "The function SNS delivers to. Built by the `lambda` module; passed in rather than created here so one module owns every function's role, log group and retention."
  type        = string
}

variable "lambda_function_name" {
  description = "The same function's name, for the invoke permission."
  type        = string
}

variable "retention_days" {
  description = <<-EOT
    How long a raw received message stays in S3. The product's copy is the
    parsed message in Postgres; this object is transport, and it holds a third
    party's words and any file they attached.

    Thirty days is long enough to re-drive a message whose delivery failed and
    short enough that the bucket is not a second, unmanaged archive of
    correspondence.
  EOT
  type        = number
  default     = 30

  validation {
    condition     = var.retention_days >= 1 && var.retention_days <= 365
    error_message = "retention_days must be between 1 and 365."
  }
}

variable "activate_rule_set" {
  description = <<-EOT
    Whether this environment's rule set becomes THE ACTIVE ONE.

    SES allows exactly one active receipt rule set per region per account, so
    two environments setting this in the same region silently steal each
    other's mail, with no error and no log line. It defaults to false so an
    environment has to say it is the one receiving, and a second environment
    claiming it is a merge conflict rather than an outage nobody can see.
  EOT
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
