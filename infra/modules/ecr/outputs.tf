output "repository_urls" {
  description = "{name -> registry URL}. CI pushes here, tagged by commit SHA."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "repository_arns" {
  description = "Used by the ECS execution role's pull policy, which is scoped to exactly these repositories rather than to ecr:*."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.arn }
}
