output "endpoint" {
  description = "host:port. The application reads a full DSN from its own secret; this is for the migration job and for an operator."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  value = aws_db_instance.this.address
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "master_user_secret_arn" {
  description = <<-EOT
    The AWS-managed secret holding the generated master password.

    NOT what the application reads. `DATABASE_URL` is a separate,
    hand-populated secret carrying a least-privileged application role. This one
    exists for migrations and for an operator, and only the `migrate` service's
    policy should ever be allowed near it.
  EOT
  value       = try(aws_db_instance.this.master_user_secret[0].secret_arn, null)
}

output "instance_arn" {
  value = aws_db_instance.this.arn
}
