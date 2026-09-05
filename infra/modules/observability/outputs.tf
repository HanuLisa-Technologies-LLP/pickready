output "dashboard_name" {
  value = aws_cloudwatch_dashboard.this.dashboard_name
}

output "alarm_names" {
  description = "Every alarm this module owns, so a smoke test can assert the set rather than trusting the apply."
  value = concat(
    [for a in aws_cloudwatch_metric_alarm.unhealthy_targets : a.alarm_name],
    [
      aws_cloudwatch_metric_alarm.rds_cpu.alarm_name,
      aws_cloudwatch_metric_alarm.rds_storage.alarm_name,
      aws_cloudwatch_metric_alarm.agent_failures.alarm_name,
    ],
    [for a in aws_cloudwatch_metric_alarm.lambda_errors : a.alarm_name],
  )
}
