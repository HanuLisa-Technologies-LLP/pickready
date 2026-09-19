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

# ── The alarms added on 2026-09-17 ───────────────────────────────────────────
#
# Four conditions that existed on the DASHBOARD and nowhere else, which is the
# same thing as not existing: a widget is only read by somebody who already
# suspects a problem. Each one below answers a question the five original
# alarms cannot.

variable "alb_5xx_threshold" {
  description = <<-EOT
    Target 5xx responses in a five-minute period that counts as the API
    failing rather than as noise.

    A COUNT AND NOT A RATE, which is the opposite of the choice the Lambda
    alarm makes, and the difference is the denominator. A Lambda function has
    thousands of invocations in a period, so a rate is stable. These
    environments serve close to zero requests today, and a rate over a
    single-request period reads 100 percent, so a rate alarm here would fire on
    one health-check blip and be muted within a week.

    Ten in five minutes, sustained for two periods, is the defensible line: a
    handful of 5xx during a rolling deploy is ordinary and clears inside one
    period, and twenty over ten minutes is not something a retry explains.
    Raise it when real traffic makes it noisy, and convert it to a rate when
    the request count makes a rate meaningful.
  EOT
  type        = number
  default     = 10
}

variable "db_connection_alarm_threshold" {
  description = <<-EOT
    DatabaseConnections at which to alarm. REQUIRED, with no default, because
    the ceiling it is measured against is a property of the instance class and
    a default would be wrong for every environment but one.

    RDS derives `max_connections` from instance memory:
    `LEAST({DBInstanceClassMemory}/9531392, 5000)`. Set this to roughly 80
    percent of that, which is the point where a connection storm still has room
    to be investigated rather than to be triaged.

    This is not a theoretical ceiling here. `workers/runtime.worker_session`
    builds a fresh engine per Lambda invocation by design, so peak connections
    track LAMBDA CONCURRENCY and not the ECS pool size. Exhausting it does not
    degrade: a new connection is refused, and the request that needed it fails.
    An RDS Proxy would be the usual answer and was evaluated and refused
    (2026-09-10, see `infra/modules/rds`), so this alarm is the warning that
    replaces it.
  EOT
  type        = number
}

variable "redis_cluster_ids" {
  description = <<-EOT
    The ElastiCache node ids to alarm on, as `CacheClusterId` dimension values.

    THE CALLER COMPUTES THESE RATHER THAN READING THEM OFF THE MODULE, and that
    is a plan-time constraint, not a preference. A replication group's member
    cluster list is a resource attribute that is unknown until apply, and a
    `for_each` over an unknown set cannot be planned at all -- the same rule
    `enable_alb_alarms` and `invokable_function_keys` already follow. The ids
    are deterministic (`<replication group>-001`, `-002`, ...), so the caller
    derives them from values it already holds.

    `CacheClusterId` and not a replication-group dimension: the per-node
    dimension is the one every Redis metric here is published against, and an
    alarm on a dimension nothing publishes sits in INSUFFICIENT_DATA for ever
    and reads as quiet rather than as wrong.
  EOT
  type        = set(string)
}

variable "redis_memory_threshold_percent" {
  description = <<-EOT
    DatabaseMemoryUsagePercentage at which to alarm.

    THIS IS THE ALARM THIS PLATFORM MOST NEEDED AND DID NOT HAVE. Redis runs
    `maxmemory-policy = noeviction`, deliberately: LRU would silently evict a
    live assessment's proctoring warning counter and reset a candidate's
    warnings to zero. The cost of that choice is that memory exhaustion is not
    a cache miss, it is a WRITE FAILURE -- and `proctoring/gate` answers 503
    rather than silently not warning, so a full Redis refuses every assessment
    turn on the instance.

    75 percent leaves room to act. The gap between 75 and 100 on a
    `cache.t4g.micro` is small in absolute terms, which is the argument for
    alarming early rather than for alarming later.
  EOT
  type        = number
  default     = 75
}
