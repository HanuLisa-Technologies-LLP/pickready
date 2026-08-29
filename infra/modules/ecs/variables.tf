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
    # `beat` must be exactly one. Two schedulers double every scheduled task,
    # which for this platform means two reconciliation sweeps and two sets of
    # reminder emails.
    condition = alltrue([
      for name, service in var.services :
      !can(regex("beat", name)) || (service.desired_count == 1 && service.max_count <= 1)
    ])
    error_message = "A beat service must be exactly one task. Two schedulers double every scheduled task."
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
