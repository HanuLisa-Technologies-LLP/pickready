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

output "writer_policy_arns" {
  description = "{service -> the IAM policy granting PutSecretValue on exactly that service's writable secrets}. Attached to the TASK role in the ecs module, because the application's own SDK makes the call; the read policy goes on the execution role."
  value       = { for service, policy in aws_iam_policy.service_writer : service => policy.arn }
}

output "generated_secret_values" {
  description = <<-EOT
    {name -> the value this module minted}. SENSITIVE.

    Exists for ONE consumer shape: a reader that cannot fetch from Secrets
    Manager at all. `readypick-inbound-email` is a zip of one file that imports
    the standard library and boto3 and reads `os.environ["WEBHOOK_SECRET"]`; it
    holds no database credential, no model key and no secret grant, and giving
    it one would widen the only function in this platform that anything on the
    open internet can reach.

    Every other consumer must take the ARN from `secret_arns` and let ECS
    inject it, or fetch it itself. A value read from here is a value that ends
    up somewhere Terraform can print.
  EOT
  value       = { for name, generated in random_password.generated : name => generated.result }
  sensitive   = true
}
