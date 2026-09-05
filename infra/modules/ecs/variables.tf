variable "project" {
  type    = string
  default = "readypick"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["pilot", "staging", "production"], var.environment)
    error_message = "environment must be pilot, staging or production."
  }
}

variable "region" {
  type = string
}

variable "private_subnet_ids" {
  description = "Tasks run here. Egress through NAT, no inbound from the internet, no public IP."
  type        = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "vpc_id" {
  description = "The VPC the Cloud Map private DNS namespace is attached to. A namespace resolves inside exactly one VPC, which is what makes an internal service name unreachable from anywhere else."
  type        = string
}

variable "discovery_namespace" {
  description = <<-EOT
    The private DNS namespace internal services are registered in, for example
    `readypick-staging.internal`. A service with `discoverable = true` answers
    at `<service>.<namespace>`.

    PASSED IN RATHER THAN DERIVED HERE, because the environment root also has
    to build the URL it puts in another service's environment
    (`PROCTORING_ANALYSIS_SERVICE_URL`), and two places computing the same
    hostname from the same parts is two places that can drift. One string,
    owned by the caller.

    Empty creates no namespace at all, which is correct for an environment
    where nothing is discoverable.
  EOT
  type        = string
  default     = ""
}

variable "services" {
  description = <<-EOT
    One entry per service, and EACH ENTRY GETS ITS OWN TASK ROLE, EXECUTION
    ROLE AND SECRET POLICY. That is the point of the module -- see the
    docstring in main.tf, and spec-doc5 §D.4.

    `image` should be a DIGEST reference where one is available
    (`repo@sha256:...`), not a tag. spec-doc5 §D.6 asks for verification "by
    image digest, not by CI exit code", and pinning it here is what makes the
    verification mean anything: a task pinned by digest cannot be a different
    image that happens to share a tag.

    `secrets` is {ENV_NAME -> secret ARN}. ECS fetches and injects, so the value
    never passes through a shell, a startup script or a log line.

    `writable_paths` mounts an empty ephemeral volume at each path, which is
    what makes `readonly_root = true` usable by a container that has to write
    somewhere small: a library's import-time cache, a scratch directory. It is
    the narrow answer to a workload that would otherwise need a writable root
    filesystem for the sake of one directory. Fargate has no tmpfs, so this is
    a task volume rather than a memory one.

    `discoverable = true` registers the service in the Cloud Map namespace, so
    another task reaches it at `<service>.<namespace>`. For a service with no
    load balancer that is the only way it is addressable at all.

    `on_demand = true` produces the TASK DEFINITION AND ITS ROLES AND NOTHING
    ELSE: no `aws_ecs_service`, no autoscaling target, no desired count. It is
    how the assessment agent is deployed. A Lambda calls `RunTask` against the
    family when work arrives, the container does that one piece of work and
    exits, and the task stops. That is the whole cost argument for this
    architecture: Fargate does not scale to zero, so long AI work that runs a
    few times an hour must not be a standing service.

    `stop_timeout` is the container's grace period between SIGTERM and SIGKILL,
    in seconds. It is NOT a runtime ceiling and there is no such thing here: an
    on-demand task runs for as long as its process runs and stops when the
    process exits. This only applies when something else stops it -- a StopTask,
    a deployment, a capacity event.

    FARGATE REFUSES ANYTHING OVER 120, with a 400 at RegisterTaskDefinition. It
    is validated below rather than discovered at apply, because the error names
    the launch type rather than the entry that asked for it.
  EOT
  type = map(object({
    image               = string
    command             = optional(list(string), [])
    cpu                 = number
    memory              = number
    desired_count       = number
    max_count           = optional(number, 0)
    port                = optional(number, null)
    health_path         = optional(string, null)
    target_group_arn    = optional(string, null)
    environment         = optional(map(string), {})
    secrets             = optional(map(string), {})
    needs_s3            = optional(bool, false)
    readonly_root       = optional(bool, false)
    writable_paths      = optional(list(string), [])
    discoverable        = optional(bool, false)
    on_demand           = optional(bool, false)
    stop_timeout        = optional(number, null)
    min_healthy_percent = optional(number, 100)
    max_percent         = optional(number, 200)
  }))

  validation {
    # A service with a target group and no port cannot be registered, and the
    # error AWS returns for it is unhelpful enough to be worth catching here.
    condition = alltrue([
      for name, service in var.services :
      service.target_group_arn == null || service.port != null
    ])
    error_message = "A service behind a load balancer must declare a port."
  }

  validation {
    # A path that is not absolute produces a container definition AWS rejects,
    # with an error naming the mount rather than the path.
    condition = alltrue([
      for name, service in var.services :
      alltrue([for path in service.writable_paths : startswith(path, "/")])
    ])
    error_message = "Every writable_paths entry must be an absolute path."
  }

  validation {
    # Fargate's own hard limit. A 3600 here (which is what the infrastructure
    # brief asked for) is refused at RegisterTaskDefinition with a message that
    # names the launch type, not the service that set it.
    #
    # `coalesce` rather than a null guard on the left of an `||`: HCL evaluates
    # BOTH sides of a boolean operator, so `x == null || x >= 2` still compares
    # a null and fails the whole plan with "argument must not be null".
    condition = alltrue([
      for name, service in var.services :
      coalesce(service.stop_timeout, 30) >= 2 &&
      coalesce(service.stop_timeout, 30) <= 120
    ])
    error_message = "Fargate refuses a container stop timeout outside 2..120 seconds. It is a SIGTERM grace period, not a runtime ceiling: an on-demand task has no ceiling and runs until its process exits."
  }

  validation {
    # An on-demand entry has no service, so a desired count on it is a number
    # nothing reads. Refused rather than ignored: a reader who set it would
    # reasonably believe tasks were running, and they would not be.
    condition = alltrue([
      for name, service in var.services :
      !service.on_demand || (service.desired_count == 0 && service.max_count == 0)
    ])
    error_message = "An on_demand entry has no service, so desired_count and max_count must both be 0."
  }

  validation {
    # An on-demand task is started by RunTask, which passes no load balancer
    # and registers nothing in Cloud Map. Both would be silently inert.
    condition = alltrue([
      for name, service in var.services :
      !service.on_demand || (service.target_group_arn == null && !service.discoverable)
    ])
    error_message = "An on_demand entry cannot have a target group or service discovery: nothing routes to a task that has already been given its work."
  }

  validation {
    # Fargate's own floor. A standing service with a desired count of zero
    # cannot exist, and asking for one produces an error at apply time that
    # names the count rather than the reason.
    condition = alltrue([
      for name, service in var.services :
      service.on_demand || service.desired_count >= 1
    ])
    error_message = "Fargate does not scale to zero. A service with no tasks belongs on Lambda or as an on_demand task definition."
  }
}

variable "secret_policy_arns" {
  description = "{service -> the IAM policy from the `secrets` module}. Indexed rather than looked up with a default: a service with no scoped policy must fail the plan rather than run with none."
  type        = map(string)
}

variable "s3_policy_arn" {
  type = string
}

variable "ecr_repository_arns" {
  description = "Pull is scoped to exactly these. The managed AmazonECSTaskExecutionRolePolicy grants pull on every repository in the account, which is how a staging task ends up able to pull a production image."
  type        = list(string)
}

variable "common_environment" {
  description = "Non-secret settings every service shares. Anything sensitive belongs in `secrets`."
  type        = map(string)
  default     = {}
}

variable "cpu_architecture" {
  description = "ARM64 is roughly 20% cheaper per vCPU-hour on Fargate and the images are built multi-arch. X86_64 is the fallback for a dependency without an ARM wheel."
  type        = string
  default     = "ARM64"
  validation {
    condition     = contains(["ARM64", "X86_64"], var.cpu_architecture)
    error_message = "cpu_architecture must be ARM64 or X86_64."
  }
}

variable "enable_execute_command" {
  description = "ECS Exec: an operator shell in a running task. OFF BY DEFAULT. A shell in a container holding candidate data is a real capability and should be a decision rather than an inheritance."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "kms_key_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
