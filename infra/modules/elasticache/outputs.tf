output "primary_endpoint" {
  description = "host:port for the primary. The application reads a full REDIS_URL from its own secret; this is what an operator populates it from, and the scheme must be rediss:// because transit encryption is on."
  value       = "${aws_elasticache_replication_group.this.primary_endpoint_address}:${aws_elasticache_replication_group.this.port}"
}

output "reader_endpoint" {
  description = "Null when there is no replica. Present so a caller can tell 'no replica configured' from 'replica endpoint unknown'."
  value       = try(aws_elasticache_replication_group.this.reader_endpoint_address, null)
}

output "replication_group_id" {
  value = aws_elasticache_replication_group.this.id
}

output "auth_token" {
  description = <<-EOT
    The generated AUTH token. SENSITIVE, so it is redacted in plan and apply
    output and must be read deliberately with `terraform output -raw`.

    The operator composes REDIS_URL as
    `rediss://:<token>@<primary endpoint>:6379/0` and writes THAT into Secrets
    Manager. Note `rediss` with two esses: transit encryption is on, and a
    client connecting with `redis://` hangs rather than refusing, which looks
    like a network problem and is not.
  EOT
  value       = random_password.auth_token.result
  sensitive   = true
}
