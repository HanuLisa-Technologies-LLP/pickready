output "client_security_group_id" {
  description = "Attach to the API service, the task worker Lambda and the on-demand agent (the runbook's Apply B), and to nothing else."
  value       = aws_security_group.client.id
}

output "host_security_group_id" {
  description = "Pass to the network module's `endpoint_client_security_group_ids` so the host can reach ECR, Secrets Manager and Logs."
  value       = aws_security_group.host.id
}

output "token_secret_arn" {
  description = "JUDGE0_AUTH_TOKEN. Mount it on the callers as that name."
  value       = aws_secretsmanager_secret.token.arn
}

output "token_read_policy_arn" {
  description = "Attach to the callers' task roles alongside the mount."
  value       = aws_iam_policy.token_read.arn
}

output "repository_urls" {
  description = "Where scripts/mirror-judge0-images.sh pushes the three images."
  value       = module.images.repository_urls
}

output "sandbox_url" {
  description = "The value for JUDGE0_URL. Resolves inside this VPC only."
  value       = var.register_in_namespace ? "http://judge0.${var.discovery_namespace_name}:${local.sandbox_port}" : null
}

output "instance_id" {
  description = "Null until `create_instance` is true."
  value       = one(aws_instance.host[*].id)
}

output "host_log_group_name" {
  value = aws_cloudwatch_log_group.host.name
}
