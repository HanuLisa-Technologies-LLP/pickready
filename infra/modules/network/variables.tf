variable "project" {
  description = "Product slug, used as a resource-name prefix."
  type        = string
  default     = "readypick"
}

variable "environment" {
  description = "staging | production. Part of every resource name, so two environments in one account cannot collide."
  type        = string

  validation {
    # A typo here would silently build a THIRD environment rather than failing.
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  type = string
}

variable "cidr_block" {
  description = "VPC CIDR. /16, because the module carves three /24 tiers per AZ out of it."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrsubnet(var.cidr_block, 8, 30))
    error_message = "cidr_block must be large enough for three tiers across the AZs (a /16 or wider)."
  }
}

variable "availability_zones" {
  description = "At least two. RDS requires a subnet group spanning two AZs even for a single-AZ instance, so this is a floor rather than a resilience preference."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required by the RDS subnet group."
  }
}

variable "single_nat_gateway" {
  description = "One NAT for the whole VPC. True in staging (cost), false in production (an AZ failure must not cost every task its egress, which means losing the model provider)."
  type        = bool
  default     = false
}

variable "application_ports" {
  description = <<-EOT
    Every port an ECS task listens on, and therefore every port the load
    balancer is allowed to reach.

    A LIST RATHER THAN ONE NUMBER, because this platform runs two listening
    services behind one load balancer: the FastAPI backend on 8000 and the
    Next.js frontend on 3000. The single-number form this replaced opened 8000
    only, so the frontend target group's health check would have been dropped by
    the task security group and the frontend would have sat permanently
    unhealthy while every rule read as correct. A silent failure, because the
    ALB reports "unhealthy" identically whether the application is broken or the
    packet never arrived.

    Each port becomes its own ingress rule referencing the ALB security group.
    Never a port RANGE: 3000-8000 would also open every port in between, which
    is the whole class of thing this list exists to avoid.
  EOT
  type        = list(number)
  default     = [8000, 3000]

  validation {
    condition     = length(var.application_ports) > 0
    error_message = "At least one application port must be reachable, or the load balancer can reach nothing."
  }
}

variable "internal_service_ports" {
  description = <<-EOT
    Every port one ECS task reaches ANOTHER ECS task on, and therefore every
    port opened from the task security group to itself.

    Today that is 8100, the proctoring analysis service: the api and worker
    tasks post audio chunks to it over the private network and it is behind
    no load balancer. The tasks all share one security group, so a
    self-referencing ingress rule is what lets a task reach a peer at all; the
    group otherwise admits only the load balancer, on `application_ports`.

    One rule per port, never a range, for the same reason as `application_ports`.
  EOT
  type        = list(number)
  default     = [8100]
}

variable "interface_endpoints" {
  description = "Interface VPC endpoints. Without them every image pull and secret read goes out through NAT and back in -- a cost and latency problem rather than a security one."
  type        = list(string)
  default = [
    "ecr.api",
    "ecr.dkr",
    "secretsmanager",
    "logs",
  ]
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "kms_key_arn" {
  description = "Encrypts the flow log group. Flow logs record which addresses inside this VPC talked to which, which is a map of the deployment."
  type        = string
}

variable "flow_log_retention_days" {
  description = "Flow logs are the only record of what actually reached what. Long enough to investigate an incident noticed weeks later; they are also the highest-volume log this platform produces, so this is a real cost line."
  type        = number
  default     = 90
}
