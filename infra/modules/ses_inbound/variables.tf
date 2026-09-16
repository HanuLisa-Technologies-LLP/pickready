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
    mail only in the region its rule set is active in, so this and the
    provider's region are the same value and must stay so.

    PASSED IN RATHER THAN LOOKED UP, the same reason `account_id` records:
    `data "aws_region"` is a lookup, and the offline plan runs against a region
    that deliberately does not exist.
  EOT
  type        = string

  validation {
    # SES EMAIL RECEIVING EXISTS IN A SMALLER SET OF REGIONS THAN SES SENDING.
    # This module was first wired for the pilot's own region, where SES SENDS
    # perfectly well, and the failure would have been silent in the worst way:
    # `describe-active-receipt-rule-set` answers `InvalidAction` there because
    # the API does not exist in the region, and its `inbound-smtp` hostname
    # does not resolve AT ALL. The MX record would have pointed at a hostname
    # with no address, every employer's reply would have bounced at their own
    # mail server, and nothing in this account would have logged a thing.
    #
    # THE SET IS AN INPUT, NOT A LITERAL. Which regions can receive is an
    # operational fact that changes when AWS adds one, so it is a declared
    # variable with no default, the same rule every account-specific value in
    # this tree follows. An operator states it in a reviewed diff; nothing here
    # guesses it.
    #
    # Receiving and sending need not share a region: the MX points wherever the
    # rule set is, and the parser reaches the product over the public internet.
    condition     = contains(var.receiving_regions, var.region)
    error_message = "SES email receiving does not exist in this region, and its inbound-smtp hostname does not resolve. The MX record would point at nothing and every reply would bounce at the sender. Receiving may live in a different region from sending: pass a receiving region through a provider alias."
  }
}

variable "receiving_regions" {
  description = <<-EOT
    Every region where SES can RECEIVE mail, which is a smaller set than the
    regions it can send from.

    A VARIABLE WITH NO DEFAULT, like every other account-specific value here.
    A default would be this module guessing at a fact that changes whenever AWS
    adds a region, and the way that guess fails is silent: an MX record
    pointing at a hostname that does not resolve.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.receiving_regions) > 0
    error_message = "receiving_regions cannot be empty: an empty list refuses every region, including the one that works."
  }
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

variable "kms_key_arn" {
  description = <<-DESC
    The environment's CMK, used to encrypt the inbound-mail notification topic.

    Taken as an input rather than minted here because the environment already
    has a key whose policy names `sns.amazonaws.com` and `ses.amazonaws.com`
    with an account condition. A key of this module's own would be a second
    answer to "which key encrypts this environment's data at rest", with its
    own policy to keep in step with that one.
  DESC
  type        = string
}
