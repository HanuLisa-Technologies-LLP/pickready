output "cluster_name" {
  value = module.ecs.cluster_name
}

output "service_names" {
  value = module.ecs.service_names
}

output "bucket_name" {
  value = module.s3.bucket_name
}

output "rds_endpoint" {
  description = "For the migration job and for an operator. The application reads a full DSN from its own secret."
  value       = module.rds.endpoint
}

output "redis_endpoint" {
  description = "Transit encryption is on, so REDIS_URL must use rediss://."
  value       = module.elasticache.primary_endpoint
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "secret_arns" {
  description = "Containers only. Values are populated out of band -- a value in Terraform is a value in the state file."
  value       = module.secrets.secret_arns
}

output "per_service_secret_policies" {
  description = <<-EOT
    {service -> the IAM policy granting exactly that service's secrets}.

    Surfaced as an output ON PURPOSE. spec-doc5 §D acceptance asks that "IAM
    policies are scoped per-service; no shared broad role holds every secret",
    and this makes that answerable from `terraform output` rather than by
    reading five policy documents.
  EOT
  value       = module.secrets.policy_arns
}

# ── Traffic layer ────────────────────────────────────────────────────────────

output "alb_dns_name" {
  description = "The load balancer's own hostname. Useful during a cutover: it answers before the DNS alias has propagated, so a smoke test can distinguish a broken application from a DNS record that has not landed yet."
  value       = module.alb.dns_name
}

output "public_url" {
  description = "The origin the product is served on. `scripts/smoke-test.sh` reads this."
  value       = "https://${var.domain_name}"
}

output "certificate_arn" {
  description = "The ISSUED certificate, read from the validation resource. An ARN here means ACM has seen its DNS records, not merely that a request exists."
  value       = module.acm.certificate_arn
}

output "public_listener_rules" {
  description = <<-EOT
    {path pattern -> the listener rule serving it WITHOUT authentication}.

    Surfaced for the same reason `per_service_secret_policies` is: spec-doc6
    §13.2 asks that the unauthenticated public job path be assertable at the
    listener-rule level, and this makes it answerable from `terraform output`
    rather than by reading a rule set. An unexpected entry is a widened public
    surface.
  EOT
  value       = module.alb.public_listener_rules
}

output "waf_enforcing" {
  description = "Whether a web ACL exists AND is refusing anything. Distinct from whether one is attached, because a count-only WAF on a compliance checklist reads as a control and blocks nothing."
  value       = module.waf.enforcing
}

output "alb_access_logs_bucket" {
  value = module.alb.access_logs_bucket
}
