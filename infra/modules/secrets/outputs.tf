output "secret_arns" {
  description = "{name -> arn}. The ECS task definition reads these to mount a secret rather than composing it into an env var."
  value       = { for name, secret in aws_secretsmanager_secret.this : name => secret.arn }
}

output "policy_arns" {
  description = "{service -> the IAM policy granting exactly that service's secrets}. Attached to the task role in the ecs module."
  value       = { for service, policy in aws_iam_policy.service : service => policy.arn }
}

output "services" {
  description = "Every service that has a scoped policy. Used by the ecs module to fail loudly on a service with no grant, rather than silently running with none."
  value       = local.service_names
}
