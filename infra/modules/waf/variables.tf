variable "project" {
  type    = string
  default = "readypick"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "enabled" {
  description = <<-EOT
    THE ONE-LINE DECISION (spec-doc6 §13.2: "Build it; leave it disabled by
    variable so enabling is a one-line decision").

    False means this module creates nothing at all: no web ACL, no log group, no
    association. It is not a web ACL in a permissive mode, which would cost
    money and produce metrics while proving nothing.

    Before setting this true, read the module docstring and then
    `docs/DEPLOY_AWS.md`. The order is: enable with `count_only = true`, read a
    week of metrics against real resume and interview traffic, and only then
    turn counting off.
  EOT
  type        = bool
  default     = false
}

variable "count_only" {
  description = <<-EOT
    Evaluate every rule, record every match, block nothing.

    This is how the false-positive rate gets measured before it can reject a
    real candidate's upload. The managed rule sets inspect request bodies, and
    this product's request bodies are resumes, client-written job descriptions
    and interview answers, which is a corpus no generic rule set was tuned
    against.
  EOT
  type        = bool
  default     = true
}

variable "alb_arn" {
  description = "The load balancer the web ACL attaches to, from `modules/alb`."
  type        = string
}

variable "rate_limit_per_five_minutes" {
  description = <<-EOT
    Requests from one IP in a five-minute window before that IP is refused.

    This is the rule with essentially no false-positive surface: it counts
    requests and never inspects one. It is also the rule the public job path
    most needs, because that path is the only unauthenticated entry point in the
    product and therefore the one an unattended script finds first.

    The floor below is not arbitrary. A single recruiter working a candidate
    list fires a few hundred requests in five minutes from one office IP, and
    a whole office shares that IP.
  EOT
  type        = number
  default     = 2000

  validation {
    condition     = var.rate_limit_per_five_minutes >= 1000
    error_message = "Below 1000 per five minutes, one office's shared NAT address can exhaust the budget during ordinary recruiter use."
  }
}

variable "managed_rule_groups" {
  description = <<-EOT
    The baseline managed rule sets, with the specific rules excluded from each.

    EVERY EXCLUSION IS A NAMED RULE, never a missing group. See the module
    docstring: `SizeRestrictions_BODY` caps an inspected body at 8 KB and every
    resume upload exceeds it, and the body-inspecting XSS and RFI rules fire on
    a candidate WRITING ABOUT an attack rather than performing one, which is
    something this product actively asks people to do.

    Priorities start at 1 because the rate-based rule holds priority 0: a cheap
    counter should refuse a flood before an expensive body inspection runs on
    every request in it.
  EOT
  type = list(object({
    name           = string
    vendor_name    = string
    priority       = number
    excluded_rules = optional(list(string), [])
  }))

  default = [
    {
      name        = "AWSManagedRulesCommonRuleSet"
      vendor_name = "AWS"
      priority    = 1
      excluded_rules = [
        "SizeRestrictions_BODY",
        "CrossSiteScripting_BODY",
        "GenericRFI_BODY",
      ]
    },
    {
      name        = "AWSManagedRulesKnownBadInputsRuleSet"
      vendor_name = "AWS"
      priority    = 2
      # Nothing excluded. This group matches exploit signatures for named CVEs
      # rather than shapes of user input, so it has no quarrel with prose.
      excluded_rules = []
    },
    {
      name        = "AWSManagedRulesAmazonIpReputationList"
      vendor_name = "AWS"
      priority    = 3
      # Reputation only. It inspects no request content at all.
      excluded_rules = []
    },
  ]

  validation {
    condition     = length(distinct([for group in var.managed_rule_groups : group.priority])) == length(var.managed_rule_groups)
    error_message = "Two rule groups share a priority, and WAF refuses that at apply time."
  }

  validation {
    condition     = alltrue([for group in var.managed_rule_groups : group.priority > 0])
    error_message = "Priority 0 belongs to the rate-based rule: a counter should refuse a flood before body inspection runs on every request in it."
  }
}

variable "enable_logging" {
  description = "Log matched requests to CloudWatch, with the Authorization and Cookie headers redacted. On by default, because a web ACL whose decisions are not recorded cannot be tuned."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "kms_key_arn" {
  description = "Encrypts the WAF log group. The logs carry redacted requests, which still include paths and client addresses."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
