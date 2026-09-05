/**
 * What a human or a deploy pipeline needs after an apply.
 *
 * No secret VALUES here and none anywhere: an output is written to state, and
 * state is JSON behind a bucket policy rather than a vault. ARNs and names
 * only, which is what the deploy commands take anyway.
 */

output "alb_dns_name" {
  description = "Where the environment answers. Null only when there is neither a domain nor an imported certificate: there is no plaintext mode, so the load balancer and a certificate arrive together."
  value       = local.has_public_entry ? module.alb[0].dns_name : null
}

output "backend_url" {
  description = "Where the API answers. The same host as the frontend: the load balancer routes /api/*, /docs and /openapi.json to the API target group and everything else to the frontend."
  value       = local.has_public_entry ? "${local.frontend_url}/api/v1" : null
}

output "api_docs_url" {
  description = "FastAPI's own documentation, behind the same listener rule as /api/*."
  value       = local.has_public_entry ? "${local.frontend_url}/docs" : null
}

output "tls_is_a_stopgap" {
  description = "True when the listener is using an IMPORTED self-signed certificate rather than an ACM-issued one. A visitor gets an issuer warning. Setting domain_name retires it."
  value       = local.has_public_entry && !local.has_domain
}

output "frontend_url" {
  description = "The origin the product believes it is served on. `jobs.public_job_url` builds candidate-facing links from it and app/main.py keys its CORS allowlist on it."
  value       = local.frontend_url
}

output "ecr_repository_urls" {
  description = "Where CI pushes. One per image; the backend repository backs the API, the on-demand agent and all three image-based functions."
  value       = module.ecr.repository_urls
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ecs_service_names" {
  description = "The standing services. The assessment agent is NOT here: it is a task definition with no service, started per dispatch."
  value       = module.ecs.service_names
}

output "agent_task_family" {
  description = "What the trigger Lambda calls RunTask against. A family rather than a revision, so a deploy that registers a new task definition takes effect without a matching Lambda update."
  value       = module.ecs.on_demand_task_families["agent"]
}

output "lambda_function_names" {
  description = "What CI passes to `aws lambda update-function-code`."
  value       = module.lambda.function_names
}

output "scheduled_rules" {
  description = "The periodic sweeps. Mirrors backend/app/workers/schedule.py, which the parity test compares against."
  value       = module.scheduler.schedule_names
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "dashboard_name" {
  value = module.observability.dashboard_name
}

output "secret_arns" {
  description = <<-EOT
    The CONTAINERS, not the values. Terraform creates these empty, and a
    service started against an empty secret starts and then fails on first use.

    Populate each one before deploying anything that reads it:

      aws secretsmanager put-secret-value --region ap-south-2 \\
        --secret-id <arn> --secret-string '<value>'

    DEPLOYMENT_LOG.md lists which of these need a human and which are written
    by the apply itself.
  EOT
  value       = module.secrets.secret_arns
}

output "database_endpoint" {
  description = "For composing DATABASE_URL. Not a credential: the database sits in subnets with no route to the internet in either direction."
  value       = module.rds.endpoint
}

output "redis_endpoint" {
  description = "For composing REDIS_URL. Same reasoning as the database endpoint."
  value       = module.elasticache.primary_endpoint
}

# ── What `scripts/run-migration.sh` reads ────────────────────────────────────
#
# The subnets and the security group, from the state that created them rather
# than hardcoded in the script. A hardcoded subnet id is the thing that silently
# keeps working against a VPC that was replaced.

output "private_subnet_ids" {
  description = "Where a one-shot task runs. No route in from the internet; egress through the single NAT."
  value       = module.network.private_subnet_ids
}

output "ecs_security_group_id" {
  description = "The security group a one-shot task joins, so it can reach RDS and Redis."
  value       = module.network.ecs_security_group_id
}

output "migrate_task_family" {
  description = "The one-shot family `scripts/run-migration.sh` starts. It has no service: it runs once and exits."
  value       = module.ecs.on_demand_task_families["migrate"]
}

# THE ONE SENSITIVE OUTPUT, and it exists because the elasticache module says
# it must: "the operator composes REDIS_URL from
# `terraform output -raw redis_auth_token` and writes it into Secrets Manager".
# No environment exposed it, so that documented step was impossible.
#
# `sensitive` means it is redacted in `terraform output` and in every plan and
# apply line, and readable only by asking for it by name with `-raw`. It is not
# a new exposure: the token is generated by `random_password` and has been in
# the state file since the first apply. State is the encrypted, access-
# controlled bucket the runbook creates in its first step.
output "redis_auth_token" {
  description = "Compose REDIS_URL as rediss://:<token>@<primary endpoint>:6379/0 and put THAT in Secrets Manager. Transit encryption is on, so the scheme is rediss and not redis."
  value       = module.elasticache.auth_token
  sensitive   = true
}
