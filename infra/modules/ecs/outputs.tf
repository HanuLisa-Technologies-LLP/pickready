output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "service_names" {
  value = { for name, service in aws_ecs_service.this : name => service.name }
}

output "task_definition_arns" {
  description = "What CI updates on deploy. Terraform owns the SHAPE and ignores changes to this attribute, so the two do not fight."
  value       = { for name, td in aws_ecs_task_definition.this : name => td.arn }
}

output "task_role_arns" {
  description = "What each service's own code runs as. One per service -- the whole point of the module."
  value       = { for name, role in aws_iam_role.task : name => role.arn }
}

output "execution_role_arns" {
  description = "What the ECS agent does on each service's behalf, before the container starts. Deliberately separate from the task role."
  value       = { for name, role in aws_iam_role.execution : name => role.arn }
}

output "log_group_names" {
  value = { for name, group in aws_cloudwatch_log_group.this : name => group.name }
}

output "discovery_service_names" {
  description = "{service -> the internal hostname other tasks reach it on}. Empty when nothing is discoverable."
  value       = { for name, service in aws_service_discovery_service.this : name => "${service.name}.${var.discovery_namespace}" }
}
