variable "hosted_zone_id" {
  description = <<-EOT
    The EXISTING Route53 hosted zone this environment's records go into.

    NO DEFAULT, and no `data` lookup. See the module docstring for the failure
    mode a created or name-resolved zone produces, and spec-doc6 §13.3 for why a
    live lookup is out of the question here at all.
  EOT
  type        = string

  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "hosted_zone_id must be a Route53 zone id, which begins with Z."
  }
}

variable "hostnames" {
  description = <<-EOT
    Every name that should resolve to this environment's load balancer.

    NO DEFAULT. spec-doc6 §D5: no domain name is available in this phase, and
    nothing here invents one. Each name must sit inside the hosted zone the id
    above identifies; a name outside it creates a record nothing resolves.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.hostnames) > 0
    error_message = "At least one hostname, or this module routes nothing and the load balancer is reachable only by its own AWS hostname."
  }

  validation {
    condition = alltrue([
      for name in var.hostnames :
      can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,}$", name))
    ])
    error_message = "Every hostname must be a lowercase fully qualified domain name, and must not be a wildcard: an alias record cannot be created for one."
  }
}

variable "alb_dns_name" {
  description = "From `modules/alb`. The alias target's hostname."
  type        = string
}

variable "alb_zone_id" {
  description = "From `modules/alb`. The LOAD BALANCER's hosted zone, which is an AWS-owned zone and is not the product domain's zone. Passing the product zone here produces a record that resolves to nothing."
  type        = string
}

variable "create_ipv6_records" {
  description = "AAAA records alongside the A records. On by default: without them, a client on an IPv6-only mobile network cannot reach the product at all, and that failure is invisible from any office network."
  type        = bool
  default     = true
}
