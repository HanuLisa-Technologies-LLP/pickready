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
# The five conditions, and what each one is really asking:
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
