output "schedule_names" {
  value = [for name, schedule in aws_scheduler_schedule.this : schedule.name]
}

output "role_arn" {
  value = aws_iam_role.this.arn
}
