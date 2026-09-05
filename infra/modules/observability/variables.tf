variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "alarm_topic_arn" {
  description = <<-EOT
    Where an alarm publishes. Created by the environment root, because the
    `lambda` module publishes to the same topic and the alarms here depend on
    that module's outputs: owning it in either module makes a cycle.
  EOT
  type        = string
}

# THE FOUR TARGETS ARE REQUIRED, not optional, and that is a plan-time
# constraint as much as a design one: `count = var.x == null ? 0 : 1` cannot be
# evaluated when `x` is an ARN suffix that does not exist until apply. Every
# caller has all four, so "optional" was buying an unplannable conditional in
# exchange for flexibility nobody uses.

variable "enable_alb_alarms" {
  description = <<-EOT
    Whether this environment has a load balancer to alarm on.

    A BOOL rather than a null check on the ARN suffix below, and the difference
    is not style: a `count` keyed on an ARN cannot be evaluated at plan time,
    because the ARN does not exist until apply. A bool derived from a variable
    can. The caller sets it from whether a domain is configured, since the load
    balancer and its certificate arrive together.
  EOT
  type        = bool
}

variable "load_balancer_arn_suffix" {
  description = "The LoadBalancer dimension value, which is the ARN SUFFIX and not the ARN. A full ARN produces an alarm that evaluates against a dimension nothing publishes, so it sits in INSUFFICIENT_DATA for ever and reads as quiet rather than as wrong. Read only when `enable_alb_alarms` is true."
  type        = string
  default     = null
}

variable "target_group_arn_suffix" {
  description = "The TargetGroup dimension value. Same shape and same trap as the load balancer's. Read only when `enable_alb_alarms` is true."
  type        = string
  default     = null
}

variable "db_instance_id" {
  description = "The DBInstanceIdentifier dimension. Not the ARN."
  type        = string
}

variable "function_names" {
  description = "{key -> deployed function name}. One error-rate alarm each, never one aggregate: a single rate would be dominated by whichever function is busiest."
  type        = map(string)
}

variable "agent_log_group_name" {
  description = "The on-demand agent's log group. A metric filter over `ecs_task.failed` is the only source for a Fargate task that exited non-zero, because the Lambda that started it returned as soon as RunTask was accepted."
  type        = string
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
