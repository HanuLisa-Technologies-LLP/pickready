# Alarms and one dashboard.
#
# WHAT AN ALARM IS FOR HERE
# -------------------------
# Every alarm below exists because the condition it watches is one this
# platform CANNOT see any other way. That is the filter, and it is why the list
# is short: an alarm nobody acts on trains its reader to ignore the next one,
# and this product has already been bitten by a check that reported success
# while three features did not work.
#
# THE LIST GREW ON 2026-09-17 and the filter above did not change. Four more
# conditions were added below the dashboard section, each one already visible
# as a widget and therefore not actually monitored. Read that section for what
# each of them catches that the five here cannot.
#
# The five original conditions, and what each one is really asking:
#
#   unhealthy targets   the API is in the target group and failing its health
#                       check, which now probes the database and Redis. The
#                       deployment circuit breaker rolls a bad release back on
#                       its own; this catches the case where a HEALTHY release
#                       stops being healthy afterwards.
#   RDS CPU             the one resource in this deployment with no autoscaling
#                       and a real ceiling.
#   RDS free storage    gp3 autoscales to a cap. Past the cap the database
#                       stops accepting writes, and the failure at that point
#                       is total.
#   Lambda errors       an ASYNCHRONOUS invocation has nobody to return an
#                       error to. Without this alarm a task worker failing every
#                       invocation looks exactly like a quiet afternoon.
#   agent task failures the same problem one level up: a Fargate task that
#                       exits non-zero is not reported to whoever asked for the
#                       work, because the thing that asked for it was a
#                       fire-and-forget Lambda invocation that already returned.
#
# TREAT MISSING DATA AS "NOT BREACHING", EXCEPT WHERE ABSENCE IS THE SYMPTOM
# --------------------------------------------------------------------------
# Lambda publishes Errors only when there have been invocations, so a quiet
# function has no datapoints and `missing` would flip its alarm to INSUFFICIENT
# and then, on many configurations, page. The agent-failure alarm is the same
# shape. Every alarm here therefore reads missing data as fine, and the thing
# that catches "nothing is running at all" is not an alarm on a gap: it is the
# reconciliation sweeps, which repair the work rather than reporting on it.

locals {
  name = "${var.project}-${var.environment}"
}

# ── Where an alarm goes ──────────────────────────────────────────────────────
#
# The topic is created by the ENVIRONMENT ROOT, not here, and passed in. It is
# a shared endpoint: this module alarms to it and the `lambda` module publishes
# permanently failed asynchronous invocations to it. Owning it here would make
# that a dependency cycle, because the per-function alarms below need the
# lambda module's outputs. Same reasoning as the KMS key, which is shared by
# five modules and therefore belongs to none of them.

# ── The load balancer ────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  count = var.enable_alb_alarms ? 1 : 0

  alarm_name          = "${local.name}-api-unhealthy-targets"
  alarm_description   = "One or more API tasks are failing the health check, which probes the database and Redis as well as the process."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = var.target_group_arn_suffix
    LoadBalancer = var.load_balancer_arn_suffix
  }

  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── The database ─────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name}-rds-cpu-high"
  alarm_description   = "RDS CPU above 80 percent. The one resource here with no autoscaling and a real ceiling."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions    = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name        = "${local.name}-rds-free-storage-low"
  alarm_description = "Less than 10 GB of free storage. Past the autoscaling cap the database stops accepting writes, and at that point the failure is total."
  namespace         = "AWS/RDS"
  metric_name       = "FreeStorageSpace"
  statistic         = "Minimum"
  period            = 300
  # Two periods rather than one: the metric is sampled, and a single dip is not
  # a trend. Ten minutes is still far inside the time it takes to fill 10 GB.
  evaluation_periods  = 2
  threshold           = 10 * 1024 * 1024 * 1024
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions    = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── The functions ────────────────────────────────────────────────────────────
#
# One alarm PER FUNCTION rather than one across all of them. The functions do
# different jobs at wildly different rates, so a single aggregate error rate
# would be dominated by whichever is busiest: the task worker running every
# email in the product could hide the JD writer failing every single call.
#
# The expression is a RATE, not a count. A count alarms on one failure in ten
# thousand invocations, which for a delivery worker with a permanent-failure
# path is ordinary. `IF(invocations > 0, ...)` keeps a quiet period from
# dividing by zero and producing a NaN datapoint the alarm cannot evaluate.

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = var.function_names

  alarm_name          = "${local.name}-${each.key}-error-rate"
  alarm_description   = "More than 1 percent of ${each.value} invocations failed. An asynchronous invocation has nobody to return an error to, so without this a function failing every call looks like a quiet afternoon."
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "rate"
    expression  = "IF(invocations > 0, 100 * errors / invocations, 0)"
    label       = "Error rate (percent)"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      period      = 300
      stat        = "Sum"
      dimensions  = { FunctionName = each.value }
    }
  }

  metric_query {
    id = "invocations"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      period      = 300
      stat        = "Sum"
      dimensions  = { FunctionName = each.value }
    }
  }

  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = merge(var.tags, { Function = each.key })
}

# ── The on-demand agent ──────────────────────────────────────────────────────
#
# A Fargate task that exits non-zero reports to nobody: the thing that started
# it was a fire-and-forget Lambda invocation that returned the moment RunTask
# was accepted. There is no ECS metric for "a task exited non-zero", so this
# reads the agent's own log group instead, where `ecs_task.failed` is written
# by the entry point at exactly that moment.
#
# A metric filter over a log line the application controls is the honest source
# here, and it is deliberately anchored on a line this repository owns: an
# alarm built on somebody else's log format breaks silently when they change it.

resource "aws_cloudwatch_log_metric_filter" "agent_failures" {
  name           = "${local.name}-agent-task-failures"
  log_group_name = var.agent_log_group_name
  pattern        = "ecs_task.failed"

  metric_transformation {
    name      = "AssessmentAgentFailures"
    namespace = "ReadyPick/${var.environment}"
    value     = "1"
    # Zero rather than nothing, so the metric has datapoints during a healthy
    # period. Without it the alarm sits in INSUFFICIENT_DATA whenever the agent
    # has not run, which is most of the time and is not a problem.
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "agent_failures" {
  alarm_name          = "${local.name}-agent-task-failures"
  alarm_description   = "An assessment agent task exited non-zero. Nothing else reports this: the Lambda that started it returned as soon as RunTask was accepted."
  namespace           = "ReadyPick/${var.environment}"
  metric_name         = aws_cloudwatch_log_metric_filter.agent_failures.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── The semantic index repair sweep (PLAN-p5 WP5-E) ──────────────────────────
#
# `pickready.repair_semantic_index` runs hourly on the task worker and writes
# `rag.repair.degraded` when a pass could not embed: the chunks it selected are
# left exactly as they were and the next pass retries them. One degraded pass is
# a provider blip; the same line hour after hour is an index that has quietly
# stopped being searchable by meaning, which no request fails over, because the
# keyword half of retrieval keeps answering. So the alarm needs three
# consecutive degraded hours, and it reads the repository's own log line for the
# reason the agent filter above gives.

resource "aws_cloudwatch_log_metric_filter" "rag_repair_degraded" {
  name           = "${local.name}-rag-repair-degraded"
  log_group_name = var.task_worker_log_group_name
  pattern        = "\"rag.repair.degraded\""

  metric_transformation {
    name          = "SemanticRepairDegraded"
    namespace     = "ReadyPick/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "rag_repair_degraded" {
  alarm_name          = "${local.name}-rag-repair-degraded"
  alarm_description   = "The semantic index repair sweep could not embed for three consecutive hours. Retrieval is keyword-only for every chunk it selected, and nothing else reports it because retrieval keeps answering."
  namespace           = "ReadyPick/${var.environment}"
  metric_name         = aws_cloudwatch_log_metric_filter.rag_repair_degraded.metric_transformation[0].name
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── The four conditions that were on the dashboard and nowhere else ──────────
#
# ADDED 2026-09-17, BEFORE THE FIRST PRODUCTION APPLY.
# `HTTPCode_Target_5XX_Count` and `DatabaseConnections` were already drawn as
# widgets, and there was no ElastiCache alarm of any kind. A widget is not a
# monitor: it is read by somebody who already suspects a problem, which is the
# one moment they do not need it.
#
# Each of the four answers a question the five alarms above structurally
# cannot. Unhealthy targets catches a task failing its health check; it does
# NOT catch a task that is perfectly healthy and returning 500 to every
# request, because a 500 is a response. RDS CPU and free storage catch two
# ceilings; the connection ceiling is a third and it fails harder than either,
# because a refused connection is not slow, it is an error. And nothing at all
# watched Redis.

resource "aws_cloudwatch_metric_alarm" "alb_target_5xx" {
  count = var.enable_alb_alarms ? 1 : 0

  alarm_name        = "${local.name}-api-5xx"
  alarm_description = "The API is answering requests with 5xx. The unhealthy-target alarm cannot see this: a task returning 500 to every request still passes its health check when the health check itself succeeds."

  namespace   = "AWS/ApplicationELB"
  metric_name = "HTTPCode_Target_5XX_Count"
  statistic   = "Sum"
  period      = 300
  # Two periods. One period of 5xx is what a rolling deploy looks like from the
  # outside; ten minutes of it is not something a retry explains.
  evaluation_periods  = 2
  threshold           = var.alb_5xx_threshold
  comparison_operator = "GreaterThanThreshold"
  # No requests means no datapoints, and "nobody used the product for five
  # minutes" is not an incident. What catches a dead service is the
  # unhealthy-target alarm above, which reads a metric that is always published.
  treat_missing_data = "notBreaching"

  dimensions = {
    TargetGroup  = var.target_group_arn_suffix
    LoadBalancer = var.load_balancer_arn_suffix
  }

  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name        = "${local.name}-rds-connections-high"
  alarm_description = "Approaching the instance max_connections. Exhausting it does not degrade gracefully: the next connection is refused and the request that needed it fails. Peak connections here track Lambda concurrency, because the worker builds a fresh engine per invocation by design."

  namespace   = "AWS/RDS"
  metric_name = "DatabaseConnections"
  # MAXIMUM, not Average. A connection storm is a spike, and an average over
  # five minutes is exactly the statistic that hides one.
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.db_connection_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions    = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── Lambda throttles ─────────────────────────────────────────────
#
# A SEPARATE ALARM FROM THE ERROR RATE, because a throttle is not an error and
# does not appear in that metric at all. `Errors` counts invocations that ran
# and failed; `Throttles` counts invocations that never started because the
# concurrency ceiling was already full. The two have different causes and
# different fixes, and an environment that sets `reserve_lambda_concurrency`
# has deliberately created a ceiling that can be hit.
#
# A COUNT AND NOT A RATE, unlike the error alarm, and threshold zero. One
# throttle means work was held behind a ceiling. That is worth knowing the
# first time, not the hundredth.

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = var.function_names

  alarm_name        = "${local.name}-${each.key}-throttles"
  alarm_description = "${each.value} was throttled: an invocation never started because the concurrency ceiling was full. This does not appear in the error rate, because a throttled invocation did not run."

  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions    = { FunctionName = each.value }
  alarm_actions = [var.alarm_topic_arn]
  tags          = merge(var.tags, { Function = each.key })
}

# ── Redis ─────────────────────────────────────────────────────
#
# THERE WAS NO ELASTICACHE ALARM AT ALL, and this is the gap that mattered
# most, because of a decision made deliberately elsewhere.
# `maxmemory-policy` is `noeviction` (see `infra/modules/elasticache`): LRU
# would evict a live assessment's proctoring warning counter under memory
# pressure and silently reset a candidate's warnings to zero.
#
# The price of refusing to evict is that a full Redis does not degrade. Writes
# FAIL, and `proctoring/gate` answers 503 rather than silently not warning, so
# every assessment turn on the instance is refused. There is no cache-miss
# stage between healthy and broken. Filling up has to be seen before it
# completes, and only an alarm does that.
#
# ONE ALARM PER NODE. The ids are passed in rather than read off the
# replication group, because a `for_each` over a resource attribute that is
# unknown until apply cannot be planned.

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  for_each = var.redis_cluster_ids

  alarm_name        = "${local.name}-redis-memory-${each.value}"
  alarm_description = "Redis memory above ${var.redis_memory_threshold_percent} percent. The policy is noeviction, so filling up is not a cache miss: writes fail, and the proctoring gate then answers 503 for every assessment turn rather than silently not warning."

  namespace           = "AWS/ElastiCache"
  metric_name         = "DatabaseMemoryUsagePercentage"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.redis_memory_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  # Redis publishes this continuously while the node exists, so an absence of
  # datapoints is the node being gone, which the replication group's own
  # failover handles and this alarm should not duplicate.
  treat_missing_data = "notBreaching"

  dimensions    = { CacheClusterId = each.value }
  alarm_actions = [var.alarm_topic_arn]
  ok_actions    = [var.alarm_topic_arn]
  tags          = merge(var.tags, { CacheCluster = each.value })
}

# EVICTIONS MUST BE ZERO, AND THAT IS THE WHOLE POINT OF THE ALARM.
#
# Under `noeviction` Redis refuses the write instead of evicting, so this
# metric is structurally always zero. A non-zero value therefore does not mean
# "memory is tight"; it means the parameter group is no longer `noeviction` and
# something has been silently discarded. That is the failure the policy was
# chosen to prevent, and the evidence for it is a counter nobody looks at.
resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  for_each = var.redis_cluster_ids

  alarm_name        = "${local.name}-redis-evictions-${each.value}"
  alarm_description = "Redis evicted a key. Under noeviction this is structurally impossible, so a non-zero value means the maxmemory-policy has changed and something was silently discarded, which may be a live assessment's warning counter."

  namespace           = "AWS/ElastiCache"
  metric_name         = "Evictions"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions    = { CacheClusterId = each.value }
  alarm_actions = [var.alarm_topic_arn]
  tags          = merge(var.tags, { CacheCluster = each.value })
}

# ── A PRISM statement withheld for human review (PLAN-p5 P5-D8, WP5-C) ──────
#
# Siddhi no longer fails the scoring task over a statement it cannot cite: it
# WITHHOLDS the statement, writes the report flagged for review, and logs
# `prism.statement_withheld_for_review` at ERROR from the scoring task, which
# runs on the on-demand agent. The flag reaches a person only when somebody
# opens the report; this alarm is what reaches one before then. A withheld
# statement is a writer that produced a sentence it could not trace, so ONE is
# worth a page: it is either a prompt regression or a wiring defect, and both
# repeat on every report until fixed. The token is `siddhi.report.
# WITHHELD_LOG_EVENT`, and `tests/test_prism_withheld_alarm.py` pins the two
# together, because renaming one without the other stops the count in silence.

resource "aws_cloudwatch_log_metric_filter" "prism_statement_withheld" {
  name           = "${local.name}-prism-statement-withheld"
  log_group_name = var.agent_log_group_name
  pattern        = "\"prism.statement_withheld_for_review\""

  metric_transformation {
    name          = "PrismStatementWithheld"
    namespace     = "ReadyPick/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "prism_statement_withheld" {
  alarm_name          = "${local.name}-prism-statement-withheld"
  alarm_description   = "A PRISM Report statement could not be traced to the candidate's evidence and was withheld; the report was written flagged for human review. Read the report's review findings and the agent log line for the section and item."
  namespace           = "ReadyPick/${var.environment}"
  metric_name         = aws_cloudwatch_log_metric_filter.prism_statement_withheld.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── A PRISM Report written with skills Not assessed (PLAN-p5 P5-D4, WP5-D) ──
#
# A scoring run that could not assess a skill writes no report and is retried
# by the hourly held-assessment sweep. On the final attempt
# (`miti_not_assessed_attempts`) the report IS written, those skills stated
# "Not assessed", and the scoring task logs `miti.not_assessed_final_report`
# at ERROR on the agent log group. A candidate's permanent record now carries
# a hole the platform made, so ONE is worth a page. The token is pinned to
# `functional_assessment.run_assessment` by `tests/test_miti_not_assessed.py`.

resource "aws_cloudwatch_log_metric_filter" "miti_not_assessed_final" {
  name           = "${local.name}-miti-not-assessed-final"
  log_group_name = var.agent_log_group_name
  pattern        = "\"miti.not_assessed_final_report\""

  metric_transformation {
    name          = "MitiNotAssessedFinalReport"
    namespace     = "ReadyPick/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "miti_not_assessed_final" {
  alarm_name          = "${local.name}-miti-not-assessed-final"
  alarm_description   = "A PRISM Report was written with one or more skills Not assessed after the final scoring attempt; it is flagged for human review. Read the evaluation's not_assessed skills and the agent log line."
  namespace           = "ReadyPick/${var.environment}"
  metric_name         = aws_cloudwatch_log_metric_filter.miti_not_assessed_final.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_topic_arn]
  tags          = var.tags
}

# ── One dashboard ────────────────────────────────────────────────────────────
#
# The five things above, on one page, so the first question during an incident
# ("what else is unhappy") is answered by looking rather than by remembering
# which console to open.

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = local.name

  dashboard_body = jsonencode({
    widgets = concat(
      !var.enable_alb_alarms ? [] : [
        {
          type   = "metric"
          width  = 12
          height = 6
          properties = {
            title  = "API requests and errors"
            region = var.region
            stat   = "Sum"
            period = 300
            metrics = [
              ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.load_balancer_arn_suffix],
              [".", "HTTPCode_Target_5XX_Count", ".", "."],
              [".", "HTTPCode_ELB_5XX_Count", ".", "."],
            ]
          }
        },
        {
          type   = "metric"
          width  = 12
          height = 6
          properties = {
            title  = "API healthy targets"
            region = var.region
            stat   = "Minimum"
            period = 60
            metrics = [
              ["AWS/ApplicationELB", "HealthyHostCount", "TargetGroup", var.target_group_arn_suffix, "LoadBalancer", var.load_balancer_arn_suffix],
              [".", "UnHealthyHostCount", ".", ".", ".", "."],
            ]
          }
        },
      ],
      [
        {
          type   = "metric"
          width  = 12
          height = 6
          properties = {
            title  = "Database"
            region = var.region
            period = 300
            metrics = [
              ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_instance_id, { stat = "Average" }],
              [".", "FreeStorageSpace", ".", ".", { stat = "Minimum", yAxis = "right" }],
              [".", "DatabaseConnections", ".", ".", { stat = "Maximum" }],
            ]
          }
        },
      ],
      [
        {
          type   = "metric"
          width  = 12
          height = 6
          properties = {
            title  = "Background work: invocations and errors"
            region = var.region
            stat   = "Sum"
            period = 300
            # `concat` of two lists, NOT `flatten` of a list of pairs.
            # `flatten` is RECURSIVE: it collapses the metric arrays themselves
            # into one flat list of strings, and CloudWatch answers 400 with
            # "Should be array" for every entry.
            metrics = concat(
              [
                for key, fn in var.function_names :
                ["AWS/Lambda", "Invocations", "FunctionName", fn, { label = "${key} invocations" }]
              ],
              [
                for key, fn in var.function_names :
                ["AWS/Lambda", "Errors", "FunctionName", fn, { label = "${key} errors" }]
              ],
            )
          }
        },
        {
          type   = "metric"
          width  = 12
          height = 6
          properties = {
            title  = "Background work: duration"
            region = var.region
            stat   = "p95"
            period = 300
            metrics = [
              for key, fn in var.function_names :
              ["AWS/Lambda", "Duration", "FunctionName", fn, { label = key }]
            ]
          }
        },
      ],
      [
        {
          type   = "metric"
          width  = 24
          height = 6
          properties = {
            title  = "Assessment agent task failures"
            region = var.region
            stat   = "Sum"
            period = 300
            metrics = [
              ["ReadyPick/${var.environment}", "AssessmentAgentFailures"],
            ]
          }
        },
      ],
    )
  })
}
