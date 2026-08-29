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

variable "account_id" {
  description = <<-EOT
    The AWS account this environment lives in.

    NO DEFAULT, and it is not discovered with `data "aws_caller_identity"`
    either. Two reasons, and the second is the one that decided it:

      1. spec-doc6 §D5: every account-specific value is a declared variable with
         no default. The codebase is complete except for these.
      2. `aws_caller_identity` is a live STS call, which is exactly what the
         offline plan in §13.3 cannot make.

    It is used in the access-log bucket policy's `aws:SourceAccount` condition,
    so a wrong value does not weaken the policy: it makes log delivery fail
    closed.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be the 12-digit AWS account number."
  }
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  description = "The load balancer's own subnets. Public, because it is the internet-facing edge; the tasks it forwards to stay private."
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "An Application Load Balancer requires subnets in at least two availability zones."
  }
}

variable "security_group_id" {
  description = "The ALB security group from `modules/network`. It is the only place in this VPC where 0.0.0.0/0 ingress exists, and only on 80 and 443."
  type        = string
}

variable "certificate_arn" {
  description = "From `modules/acm`. The HTTPS listener will not create without it, which is the intended ordering: there is no plaintext-only mode to fall back into."
  type        = string
}

variable "access_logs_bucket_name" {
  description = <<-EOT
    Globally unique bucket name for the load balancer's access logs.

    NO DEFAULT. S3 bucket names are global across every AWS account on earth, so
    a derived name is a name that may already belong to somebody else, and the
    failure arrives at apply time as a bare `BucketAlreadyExists`. Naming it
    here makes it the owner's decision and makes the collision a plan-time
    conversation instead.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.access_logs_bucket_name))
    error_message = "access_logs_bucket_name must be a valid S3 bucket name: lowercase, 3 to 63 characters."
  }
}

variable "access_logs_retention_days" {
  description = "How long an access log stays. Long enough to investigate an incident somebody noticed a fortnight late; not so long that request metadata accumulates for years without a reason."
  type        = number
  default     = 90

  validation {
    condition     = var.access_logs_retention_days >= 30
    error_message = "Access logs are the only record of who reached what. Fewer than 30 days makes an investigation of anything but this week impossible."
  }
}

variable "ssl_policy" {
  description = <<-EOT
    The negotiated TLS versions and cipher suites.

    The default is TLS 1.3 and TLS 1.2 only, ECDHE suites only. spec-doc6 §13.2
    asks for "TLS 1.2 minimum, modern cipher policy", and the validation below
    refuses any policy AWS ships that includes TLS 1.0 or 1.1, so relaxing it
    has to be a deliberate edit to this file rather than a variable somebody
    passes.
  EOT
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"

  validation {
    condition     = can(regex("TLS13-1-2|TLS-1-2", var.ssl_policy)) && !can(regex("TLS-1-0|TLS-1-1|-1-1-|2015-05|2016-08", var.ssl_policy))
    error_message = "ssl_policy must be a TLS 1.2-minimum policy. The 2015-05 and 2016-08 families and every TLS-1-0/TLS-1-1 policy still negotiate TLS 1.0."
  }
}

variable "enable_deletion_protection" {
  description = "Refuse to delete the load balancer through the API. True in production: a destroyed ALB takes the DNS alias target with it, so the outage outlasts the mistake."
  type        = bool
  default     = true
}

variable "idle_timeout_seconds" {
  description = "See the comment on `aws_lb.this`. Must exceed the longest interactive model call, which is `jd_generation` at 50 seconds total."
  type        = number
  default     = 65

  validation {
    condition     = var.idle_timeout_seconds >= 60
    error_message = "jd_generation is bounded at 50 seconds total by the router. An idle timeout below 60 would cut off a generation that is still legitimately in flight."
  }
}

variable "target_groups" {
  description = <<-EOT
    {key -> the target group's port and health check}.

    `health_path` MUST be an endpoint that proves the task's dependencies are
    reachable, not a static 200 (spec-doc6 §13.2). The API's `/health` resolves a
    pooled database session and issues a broker round trip for exactly this
    reason: the target group is the deploy gate, and a gate that a broken
    revision passes is not a gate.
  EOT
  type = map(object({
    port                    = number
    health_path             = string
    health_matcher          = optional(string, "200")
    health_interval_seconds = optional(number, 30)
    health_timeout_seconds  = optional(number, 10)
  }))

  validation {
    condition = alltrue([
      for key, group in var.target_groups :
      group.health_timeout_seconds < group.health_interval_seconds
    ])
    error_message = "A health check timeout at or above its interval overlaps the next probe, and AWS rejects it at apply time."
  }

  validation {
    condition = alltrue([
      for key, group in var.target_groups : startswith(group.health_path, "/")
    ])
    error_message = "health_path must be an absolute path."
  }
}

variable "default_target_group" {
  description = "The target group a request matching no rule reaches. The frontend, so an unknown page gets the application's own 404 rather than a JSON error body."
  type        = string
}

variable "public_target_group" {
  description = "The target group serving `public_path_patterns`. The API, because the public job read is an API route."
  type        = string
}

variable "public_path_patterns" {
  description = <<-EOT
    THE ENUMERATED UNAUTHENTICATED SURFACE. Read this before adding to it.

    RBAC §15 makes the published job page reachable without authentication:
    "A candidate visits the public URL. No authentication is required to view
    the job." RBAC §33 immediately constrains what that can mean: "Obscurity is
    NOT authorization", and knowing an id must not be sufficient to gain access.

    Both hold at once because the handler behind these paths
    (`GET /api/v{1,2}/jobs/public/{job_id}`) returns `PublicJobOut`, a projection
    with no status, no creator, no compensation and no approval trail, and it
    404s an unpublished, archived or expired job without revealing which. The
    load balancer contributes the narrower half of that: these patterns and
    nothing else reach the API without the application having first been asked.

    THE LIST IS A DECISION, NOT A CONVENIENCE. The validation below refuses a
    pattern that would widen it into the authenticated surface, and
    `backend/tests/test_deploy_secret_hygiene.py` reads it back out of the
    Terraform, so an addition here shows up in a diff and in a failing test
    rather than only in a listener rule nobody re-reads.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.public_path_patterns) <= 40
    error_message = "A listener supports 100 rules and the public band is 10..49. Keep this list small enough to read."
  }

  validation {
    # A pattern that is bare `/*`, `/api/*` or `/api/v1/*` routes the entire
    # authenticated surface through the band whose whole purpose is to be
    # exhaustively reviewable.
    condition = alltrue([
      for pattern in var.public_path_patterns :
      !contains(["/*", "/api/*", "/api/v1/*", "/api/v2/*", "*"], pattern)
    ])
    error_message = "A catch-all pattern in public_path_patterns routes the whole API through the unauthenticated band. Enumerate the specific public paths."
  }

  validation {
    condition = alltrue([
      for pattern in var.public_path_patterns : startswith(pattern, "/")
    ])
    error_message = "Every public path pattern must be absolute."
  }
}

variable "routes" {
  description = <<-EOT
    The rest of the listener rules: {key -> priority, target group, patterns}.

    Priorities live in the 100..199 band, above the public band, so no rule here
    can shadow a public path. The validation enforces the band rather than
    trusting the caller to remember it.
  EOT
  type = map(object({
    priority      = number
    target_group  = string
    path_patterns = list(string)
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, route in var.routes : route.priority >= 100 && route.priority < 200
    ])
    error_message = "Application routes belong in the 100..199 priority band. Below 100 is the public band, and a rule there could shadow a public path."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
